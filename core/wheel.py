"""
Колесо фортуны: раз в сутки пользователь крутит колесо и получает случайный
приз (дни подписки, GB трафика, баланс, персональный скидочный код или пусто).

Важное архитектурное решение: приз всегда определяется здесь, на сервере, ДО
того как клиент увидит анимацию вращения - клиент лишь анимирует остановку на
уже известном секторе (см. web/routers/wheel.py и wheel.html). Никакого
клиентского рандома, иначе кто угодно с открытой консолью браузера мог бы
подделать результат.

Антифрод (доступ разрешён "любому зарегистрированному", раз в сутки - осознанный
выбор с более высоким риском фарма мультиаккаунтов, поэтому здесь два защитных
барьера сверх кулдауна):
  - аккаунту должно быть не меньше WHEEL_MIN_ACCOUNT_AGE_HOURS - отсекает
    мгновенный фарм "зарегистрировался -> крутанул -> новый аккаунт";
  - пока у пользователя не было ни одной успешной оплаты, суммарные призы типа
    "дни"/"трафик" ограничены WHEEL_FREE_TIER_MAX_* - дальше в розыгрыше для
    него участвуют только пусто/баланс/промокод. Лимит снимается после первой
    оплаченной подписки.
"""
import logging
import random
import secrets
import string
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import (
    User, Subscription, SubscriptionStatus, Payment, PaymentStatus,
    PromoCode, WheelPrize, WheelSpin, WheelPrizeType,
)
from core.promo_referral import add_balance
from core.remnawave import remnawave

logger = logging.getLogger("wheel")

WHEEL_MIN_ACCOUNT_AGE_HOURS = 24
WHEEL_SPIN_COOLDOWN_HOURS = 24
WHEEL_FREE_TIER_MAX_DAYS = 5          # суммарно дней от приза DAYS для никогда не плативших
WHEEL_FREE_TIER_MAX_TRAFFIC_GB = 20   # суммарно GB от приза TRAFFIC_GB для никогда не плативших

_DAYS = WheelPrizeType.DAYS.value
_TRAFFIC = WheelPrizeType.TRAFFIC_GB.value
_BALANCE = WheelPrizeType.BALANCE.value
_PROMO = WheelPrizeType.PROMO.value
_EMPTY = WheelPrizeType.EMPTY.value


async def get_active_prizes(session: AsyncSession) -> list[WheelPrize]:
    """Полный список активных призов для отрисовки секторов колеса - независимо
    от того, кто сейчас смотрит на страницу. Секторы визуально одинаковы для
    всех, элигибельность конкретного пользователя учитывается только при
    самом розыгрыше (eligible_prizes_for_user)."""
    result = await session.execute(
        select(WheelPrize).where(WheelPrize.is_active == True).order_by(WheelPrize.sort_order)
    )
    return list(result.scalars().all())


async def _has_ever_paid(user_id: int, session: AsyncSession) -> bool:
    result = await session.execute(
        select(Payment.id).where(Payment.user_id == user_id, Payment.status == PaymentStatus.SUCCESS).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _target_subscription(user_id: int, session: AsyncSession) -> Subscription | None:
    """Подписка, на которую упадёт приз DAYS/TRAFFIC_GB. Пользователь может иметь
    несколько независимых подписок (см. grant_subscription в admin.py) - при
    нескольких активных выбираем ту, что истекает раньше остальных (ей это
    нужнее). Если активных нет вовсе - берём последнюю созданную любого статуса,
    чтобы приз мог реактивировать/продлить хотя бы что-то, а не потеряться."""
    now = datetime.utcnow()
    result = await session.execute(
        select(Subscription)
        .where(Subscription.user_id == user_id, Subscription.status == SubscriptionStatus.ACTIVE, Subscription.expires_at > now)
        .order_by(Subscription.expires_at.asc())
    )
    sub = result.scalars().first()
    if sub:
        return sub

    result = await session.execute(
        select(Subscription).where(Subscription.user_id == user_id).order_by(Subscription.created_at.desc())
    )
    return result.scalars().first()


async def _cumulative_prize_value(user_id: int, prize_type: str, session: AsyncSession) -> int:
    from sqlalchemy import func
    result = await session.execute(
        select(func.coalesce(func.sum(WheelSpin.prize_value), 0))
        .where(WheelSpin.user_id == user_id, WheelSpin.prize_type == prize_type)
    )
    return result.scalar_one()


async def eligible_prizes_for_user(user: User, session: AsyncSession) -> list[WheelPrize]:
    """Подмножество активных призов, которые реально можно выдать этому
    пользователю прямо сейчас - именно из него делается взвешенный выбор."""
    prizes = await get_active_prizes(session)

    sub = await _target_subscription(user.id, session)
    if sub is None:
        # Нечего продлевать и некуда добавить трафик
        prizes = [p for p in prizes if p.prize_type not in (_DAYS, _TRAFFIC)]
    elif sub.traffic_gb == 0:
        # Уже безлимит - GB добавлять некуда (но дни всё ещё продлевают эту подписку)
        prizes = [p for p in prizes if p.prize_type != _TRAFFIC]

    if not await _has_ever_paid(user.id, session):
        if await _cumulative_prize_value(user.id, _DAYS, session) >= WHEEL_FREE_TIER_MAX_DAYS:
            prizes = [p for p in prizes if p.prize_type != _DAYS]
        if await _cumulative_prize_value(user.id, _TRAFFIC, session) >= WHEEL_FREE_TIER_MAX_TRAFFIC_GB:
            prizes = [p for p in prizes if p.prize_type != _TRAFFIC]

    return prizes


def pick_weighted_prize(prizes: list[WheelPrize]) -> WheelPrize | None:
    if not prizes:
        return None
    total_weight = sum(max(p.weight, 0) for p in prizes)
    if total_weight <= 0:
        return random.choice(prizes)
    roll = random.uniform(0, total_weight)
    upto = 0
    for p in prizes:
        upto += max(p.weight, 0)
        if roll <= upto:
            return p
    return prizes[-1]  # подстраховка от погрешности округления float


async def can_spin(user: User, session: AsyncSession) -> dict:
    """{"can_spin": bool, "retry_after_seconds": int, "reason": str|None}"""
    now = datetime.utcnow()

    account_age = now - user.created_at
    min_age = timedelta(hours=WHEEL_MIN_ACCOUNT_AGE_HOURS)
    if account_age < min_age:
        return {
            "can_spin": False,
            "retry_after_seconds": int((min_age - account_age).total_seconds()),
            "reason": "account_too_new",
        }

    result = await session.execute(
        select(WheelSpin.created_at).where(WheelSpin.user_id == user.id).order_by(WheelSpin.created_at.desc()).limit(1)
    )
    last_spin_at = result.scalar_one_or_none()
    if last_spin_at:
        cooldown = timedelta(hours=WHEEL_SPIN_COOLDOWN_HOURS)
        elapsed = now - last_spin_at
        if elapsed < cooldown:
            return {
                "can_spin": False,
                "retry_after_seconds": int((cooldown - elapsed).total_seconds()),
                "reason": "cooldown",
            }

    return {"can_spin": True, "retry_after_seconds": 0, "reason": None}


def _reason_message(reason: str | None) -> str:
    if reason == "account_too_new":
        return f"Колесо доступно аккаунтам старше {WHEEL_MIN_ACCOUNT_AGE_HOURS} ч."
    if reason == "cooldown":
        return "Вы уже крутили колесо сегодня - приходите завтра"
    return "Колесо сейчас недоступно"


async def _generate_wheel_promo_code(session: AsyncSession) -> str:
    while True:
        code = "WHEEL-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        existing = await session.execute(select(PromoCode.id).where(PromoCode.code == code))
        if not existing.scalar_one_or_none():
            return code


async def apply_prize(user: User, prize: WheelPrize, session: AsyncSession) -> dict:
    """Выдаёт приз. Возвращает {"warning": str|None} - сбой похода в Remnawave
    не должен ронять весь спин: запись о выигрыше и изменения в БД (даты/баланс)
    уже применены к session и будут закоммичены вызывающим кодом (spin()), иначе
    пользователь при временном сбое внешнего API не получил бы вообще ничего,
    но кулдаун бы не сработал и он тут же попробовал снова."""
    if prize.prize_type == _EMPTY:
        return {"warning": None}

    if prize.prize_type == _BALANCE:
        await add_balance(user, prize.value, "wheel_prize", f"Приз колеса фортуны: +{prize.value}₽", session)
        return {"warning": None}

    if prize.prize_type == _PROMO:
        code = await _generate_wheel_promo_code(session)
        session.add(PromoCode(
            code=code,
            discount_percent=prize.value,
            max_uses=1,
            owner_user_id=user.id,
            description="Приз колеса фортуны",
            is_active=True,
        ))
        return {"warning": None}

    if prize.prize_type in (_DAYS, _TRAFFIC):
        sub = await _target_subscription(user.id, session)
        if not sub:
            # eligible_prizes_for_user() должен был исключить DAYS/TRAFFIC_GB
            # ещё до розыгрыша при отсутствии подписки - если мы всё же здесь,
            # это гонка (подписку успели удалить между розыгрышем и выдачей)
            # или баг фильтра элигибельности, не тихая деградация.
            logger.error(f"apply_prize: no target subscription for user {user.id}, prize={prize.prize_type}")
            return {"warning": "Не удалось найти подписку для начисления приза - обратитесь в поддержку"}

        if prize.prize_type == _DAYS:
            base = sub.expires_at if sub.expires_at and sub.expires_at > datetime.utcnow() else datetime.utcnow()
            sub.expires_at = base + timedelta(days=prize.value)
            sub.status = SubscriptionStatus.ACTIVE
            sub.expiry_reminder_sent = False
            if sub.remnawave_sub_id:
                try:
                    await remnawave.extend_user(sub.remnawave_sub_id, prize.value)
                    await remnawave.enable_user(sub.remnawave_sub_id)
                except Exception as e:
                    logger.warning(f"apply_prize: remnawave extend failed for sub {sub.id}: {e}")
                    return {"warning": f"Дни начислены, но Remnawave вернула ошибку: {e}"}
        else:  # _TRAFFIC
            sub.traffic_gb += prize.value
            if sub.remnawave_sub_id:
                try:
                    result = await remnawave.add_traffic_gb(sub.remnawave_sub_id, prize.value)
                    if result is None:
                        logger.info(f"apply_prize: sub {sub.id} unlimited on Remnawave side, traffic_gb prize recorded in DB only")
                except Exception as e:
                    logger.warning(f"apply_prize: remnawave add_traffic_gb failed for sub {sub.id}: {e}")
                    return {"warning": f"Трафик начислен, но Remnawave вернула ошибку: {e}"}

    return {"warning": None}


async def spin(user: User, session: AsyncSession) -> dict:
    """Оркестратор одного кручения. Возвращает:
    ok=False: {"ok": False, "error": str, "retry_after_seconds": int}
    ok=True:  {"ok": True, "prize_id", "prize_label", "prize_type", "prize_value", "warning"}
    """
    eligibility = await can_spin(user, session)
    if not eligibility["can_spin"]:
        return {
            "ok": False,
            "error": _reason_message(eligibility["reason"]),
            "retry_after_seconds": eligibility["retry_after_seconds"],
        }

    prizes = await eligible_prizes_for_user(user, session)
    prize = pick_weighted_prize(prizes)
    if prize is None:
        logger.error(f"spin: no eligible prizes for user {user.id} - каталог призов пуст или все выключены в админке")
        return {"ok": False, "error": "Колесо временно недоступно, попробуйте позже", "retry_after_seconds": 0}

    apply_result = await apply_prize(user, prize, session)

    session.add(WheelSpin(
        user_id=user.id,
        prize_id=prize.id,
        prize_label=prize.label,
        prize_type=prize.prize_type,
        prize_value=prize.value,
    ))
    await session.commit()

    logger.info(f"spin: user={user.id} prize_id={prize.id} type={prize.prize_type} value={prize.value}")

    return {
        "ok": True,
        "prize_id": prize.id,
        "prize_label": prize.label,
        "prize_type": prize.prize_type,
        "prize_value": prize.value,
        "warning": apply_result.get("warning"),
    }
