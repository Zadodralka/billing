from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, Boolean, DateTime, ForeignKey, Text, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from core.database import Base
import enum


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    PENDING = "pending"
    CANCELLED = "cancelled"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class GiftCodeStatus(str, enum.Enum):
    ISSUED = "issued"      # оплачен, ждёт погашения получателем
    REDEEMED = "redeemed"  # получатель уже активировал подписку по коду


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    terms_accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    balance: Mapped[int] = mapped_column(Integer, default=0)  # баланс в рублях
    referral_code: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True, index=True)
    # SET NULL, не CASCADE: удаление реферера не должно удалять пользователей, которых он привёл
    referred_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    referral_bonus_paid: Mapped[bool] = mapped_column(Boolean, default=False)  # начислен ли бонус за реферала
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Remnawave (legacy, не используется как источник истины - см. Subscription.remnawave_sub_id)
    remnawave_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user")
    payments: Mapped[list["Payment"]] = relationship(back_populates="user")

    @property
    def display_name(self):
        return self.telegram_username or self.email or f"User#{self.id}"

    @property
    def active_subscription(self):
        now = datetime.utcnow()
        for sub in self.subscriptions:
            if sub.status == SubscriptionStatus.ACTIVE and sub.expires_at > now:
                return sub
        return None


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_key: Mapped[str] = mapped_column(String(10))  # 1m, 3m, 6m, 1y
    traffic_gb: Mapped[int] = mapped_column(Integer, default=50)  # 0 = безлимит
    status: Mapped[SubscriptionStatus] = mapped_column(SAEnum(SubscriptionStatus), default=SubscriptionStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Remnawave data - каждая подписка имеет СВОЙ независимый аккаунт
    remnawave_sub_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_link: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="subscriptions")

    @property
    def is_active(self):
        return self.status == SubscriptionStatus.ACTIVE and (
            self.expires_at is None or self.expires_at > datetime.utcnow()
        )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_key: Mapped[str] = mapped_column(String(10))
    traffic_gb: Mapped[int] = mapped_column(Integer, default=50)  # 0 = безлимит
    amount: Mapped[int] = mapped_column(Integer)  # в рублях
    status: Mapped[PaymentStatus] = mapped_column(SAEnum(PaymentStatus), default=PaymentStatus.PENDING)
    label: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # для ЮМани
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    renew_subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True)

    # Скидки
    original_amount: Mapped[int] = mapped_column(Integer, default=0)  # цена до скидок
    promo_discount: Mapped[int] = mapped_column(Integer, default=0)   # скидка промокода (руб)
    balance_spent: Mapped[int] = mapped_column(Integer, default=0)    # списано с баланса (руб)
    promo_code_id: Mapped[int | None] = mapped_column(ForeignKey("promo_codes.id", ondelete="SET NULL"), nullable=True)

    # Подарок: если задано - при активации создаётся не подписка покупателю,
    # а GiftCode, отправляемый на email получателя
    is_gift: Mapped[bool] = mapped_column(Boolean, default=False)
    gift_recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped["User"] = relationship(back_populates="payments")


class EmailToken(Base):
    __tablename__ = "email_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class PlanSetting(Base):
    """Редактируемые цены тарифов (хранятся в БД, можно менять прямо из админки)"""
    __tablename__ = "plan_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_key: Mapped[str] = mapped_column(String(10), unique=True, index=True)  # 1m, 3m, 6m, 1y
    name: Mapped[str] = mapped_column(String(64))
    days: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)
    traffic_gb: Mapped[int] = mapped_column(Integer, default=50)  # 0 = безлимит по умолчанию
    unlimited_extra: Mapped[int] = mapped_column(Integer, default=0)  # доплата за безлимит
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)  # «Популярный выбор» — выделяется на витрине
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)



class TicketStatus(str, enum.Enum):
    OPEN = "open"          # новый или пользователь ответил - ждёт реакции админа
    ANSWERED = "answered"  # админ ответил - ждёт реакции пользователя
    CLOSED = "closed"


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(200))
    status: Mapped[TicketStatus] = mapped_column(SAEnum(TicketStatus), default=TicketStatus.OPEN, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship()
    messages: Mapped[list["SupportMessage"]] = relationship(back_populates="ticket", order_by="SupportMessage.created_at")


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id", ondelete="CASCADE"), index=True)
    is_from_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    author_name: Mapped[str | None] = mapped_column(String(100), nullable=True)  # имя админа, если ответ от поддержки
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    ticket: Mapped["SupportTicket"] = relationship(back_populates="messages")


class Article(Base):
    """Статья/инструкция в формате Markdown"""
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    slug: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    excerpt: Mapped[str | None] = mapped_column(String(500), nullable=True)  # краткое описание
    content: Mapped[str] = mapped_column(Text, default="")  # Markdown
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ───────────── Баланс и реферальная система ─────────────

class BalanceTransaction(Base):
    """Лог всех операций с балансом пользователя"""
    __tablename__ = "balance_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    amount: Mapped[int] = mapped_column(Integer)  # положительный = зачисление, отрицательный = списание (руб)
    type: Mapped[str] = mapped_column(String(30))  # referral_bonus, promo_bonus, payment_spend
    description: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ───────────── Промокоды ─────────────

class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    discount_percent: Mapped[int] = mapped_column(Integer)  # 1-100
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = безлимит
    uses_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    usages: Mapped[list["PromoCodeUsage"]] = relationship(back_populates="promo_code")


class PromoCodeUsage(Base):
    __tablename__ = "promo_code_usages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    promo_code_id: Mapped[int] = mapped_column(ForeignKey("promo_codes.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id", ondelete="SET NULL"), nullable=True)
    discount_amount: Mapped[int] = mapped_column(Integer)  # сколько рублей скидки было применено
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    promo_code: Mapped["PromoCode"] = relationship(back_populates="usages")


# ───────────── Подарочные подписки ─────────────

class GiftCode(Base):
    """
    Код подарочной подписки. Создаётся при успешной оплате Payment(is_gift=True) -
    план/трафик/срок снимаются на момент покупки (снимок, а не ссылка на PlanSetting),
    чтобы последующее изменение или удаление тарифа в админке не сломало уже купленный
    подарок. Погашается получателем по коду на /gift/redeem/{code} - код не привязан
    к конкретному email получателя, обладание кодом достаточно для активации
    (как у обычной подарочной карты).
    """
    __tablename__ = "gift_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    # payment_id: CASCADE - если платёж-первоисточник удаляют (админ полностью зачищает
    # покупателя), запись-подарок вместе с ним теряет смысл; сама Subscription получателя
    # (если код уже погашен) при этом не трогается - у неё нет FK на gift_codes.
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"))
    # buyer_user_id/redeemed_by_user_id/subscription_id: SET NULL - удаление покупателя,
    # получателя или его подписки не должно быть заблокировано ссылкой из gift_codes,
    # достаточно потерять эту часть аудита, а не мешать штатному удалению в админке.
    buyer_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    recipient_email: Mapped[str] = mapped_column(String(255), index=True)

    plan_key: Mapped[str] = mapped_column(String(10))
    plan_name: Mapped[str] = mapped_column(String(64))
    days: Mapped[int] = mapped_column(Integer)
    traffic_gb: Mapped[int] = mapped_column(Integer, default=50)

    # Строка, а не SAEnum: избегаем истории с TicketStatus (рассинхрон нативного
    # Postgres ENUM и модели) - GiftCodeStatus сам по себе str, сравнения работают как есть.
    status: Mapped[str] = mapped_column(String(20), default=GiftCodeStatus.ISSUED.value, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    redeemed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True)

    buyer: Mapped["User"] = relationship(foreign_keys=[buyer_user_id])
