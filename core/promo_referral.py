"""
Сервис промокодов и реферальной программы.
Настройка бонусов — через .env:
  REFERRAL_BONUS_REFERRER=100  # рублей рефереру за каждую оплату реферала
  REFERRAL_BONUS_REFERRED=50   # рублей новому пользователю за первую покупку
"""
import secrets
import string
import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.models import User, PromoCode, PromoCodeUsage, BalanceTransaction, Payment
from core.config import settings

logger = logging.getLogger("promo_referral")

REFERRAL_CODE_CHARS = string.ascii_uppercase + string.digits


def generate_referral_code(length: int = 8) -> str:
    return "".join(secrets.choice(REFERRAL_CODE_CHARS) for _ in range(length))


async def ensure_referral_code(user: User, session: AsyncSession) -> str:
    """Генерирует реферальный код для пользователя если ещё нет"""
    if not user.referral_code:
        while True:
            code = generate_referral_code()
            existing = await session.execute(select(User).where(User.referral_code == code))
            if not existing.scalar_one_or_none():
                break
        user.referral_code = code
        await session.commit()
    return user.referral_code


async def validate_promo_code(code: str, user_id: int, session: AsyncSession) -> dict:
    """
    Проверяет промокод. Возвращает:
    {"valid": True, "discount_percent": N, "promo_code": <obj>}  — если валиден
    {"valid": False, "error": "текст ошибки"}                    — если не валиден
    """
    result = await session.execute(
        select(PromoCode).where(PromoCode.code == code.upper().strip(), PromoCode.is_active == True)
    )
    promo = result.scalar_one_or_none()

    if not promo:
        return {"valid": False, "error": "Промокод не найден или недействителен"}

    if promo.expires_at and promo.expires_at < datetime.utcnow():
        return {"valid": False, "error": "Срок действия промокода истёк"}

    if promo.max_uses and promo.uses_count >= promo.max_uses:
        return {"valid": False, "error": "Промокод уже использован максимальное количество раз"}

    # Проверка: не использовал ли этот пользователь уже этот промокод
    used = await session.execute(
        select(PromoCodeUsage).where(
            PromoCodeUsage.promo_code_id == promo.id,
            PromoCodeUsage.user_id == user_id,
        )
    )
    if used.scalar_one_or_none():
        return {"valid": False, "error": "Вы уже использовали этот промокод"}

    return {"valid": True, "discount_percent": promo.discount_percent, "promo_code": promo}


async def apply_promo_code(promo_code: PromoCode, user_id: int, payment_id: int, discount_amount: int, session: AsyncSession):
    """Фиксирует использование промокода"""
    usage = PromoCodeUsage(
        promo_code_id=promo_code.id,
        user_id=user_id,
        payment_id=payment_id,
        discount_amount=discount_amount,
    )
    promo_code.uses_count += 1
    session.add(usage)


async def add_balance(user: User, amount: int, tx_type: str, description: str, session: AsyncSession):
    """Начисляет баланс пользователю и записывает транзакцию"""
    user.balance += amount
    tx = BalanceTransaction(
        user_id=user.id,
        amount=amount,
        type=tx_type,
        description=description,
    )
    session.add(tx)
    logger.info(f"Balance +{amount} RUB → user {user.id} ({tx_type}): {description}")


async def spend_balance(user: User, amount: int, description: str, session: AsyncSession) -> int:
    """Списывает баланс. Возвращает фактически списанную сумму (не больше остатка)"""
    actual = min(amount, user.balance)
    if actual > 0:
        user.balance -= actual
        tx = BalanceTransaction(
            user_id=user.id,
            amount=-actual,
            type="payment_spend",
            description=description,
        )
        session.add(tx)
        logger.info(f"Balance -{actual} RUB ← user {user.id}: {description}")
    return actual


async def process_referral_bonus(referred_user: User, payment: Payment, session: AsyncSession):
    """
    Начисляет реферальные бонусы при первой успешной оплате нового пользователя.
    Вызывается из activate_subscription().
    """
    if referred_user.referral_bonus_paid:
        return  # уже начислялся за этого пользователя

    if not referred_user.referred_by_id:
        return  # нет реферера

    referrer_result = await session.execute(
        select(User).where(User.id == referred_user.referred_by_id)
    )
    referrer = referrer_result.scalar_one_or_none()
    if not referrer:
        return

    bonus_referrer = getattr(settings, "referral_bonus_referrer", 100)
    bonus_referred = getattr(settings, "referral_bonus_referred", 50)

    # Бонус рефереру
    await add_balance(
        referrer,
        bonus_referrer,
        "referral_bonus",
        f"Реферальный бонус за приглашение пользователя {referred_user.display_name}",
        session,
    )

    # Бонус новому пользователю
    await add_balance(
        referred_user,
        bonus_referred,
        "referral_bonus",
        "Бонус за регистрацию по реферальной ссылке",
        session,
    )

    referred_user.referral_bonus_paid = True

    await _notify_bonus(
        referrer, bonus_referrer,
        "Пользователь, которого вы пригласили, оформил подписку.",
    )
    await _notify_bonus(
        referred_user, bonus_referred,
        "Вы получили бонус за регистрацию по реферальной ссылке.",
    )

    logger.info(f"Referral bonuses paid: referrer={referrer.id} +{bonus_referrer}, referred={referred_user.id} +{bonus_referred}")


async def _notify_bonus(user: User, amount: int, reason_text: str):
    """Уведомляет о начислении бонуса на баланс - в Telegram (если привязан) и на
    email (если есть), независимо друг от друга. Раньше уведомлялся только реферер
    и только в Telegram - приглашённый пользователь о своём бонусе не узнавал вовсе,
    а реферер без привязанного Telegram не узнавал о начислении никак."""
    if user.telegram_id:
        from core.notify import send_telegram
        await send_telegram(
            user.telegram_id,
            f"🎉 <b>Начислен бонус на баланс!</b>\n\n"
            f"{reason_text}\n"
            f"Вам начислено <b>{amount} ₽</b> на баланс.\n\n"
            f"💰 Текущий баланс: <b>{user.balance} ₽</b>",
        )

    if user.email:
        try:
            from core.email import send_balance_bonus_email
            await send_balance_bonus_email(user.email, amount, reason_text, user.balance)
        except Exception as e:
            logger.warning(f"_notify_bonus (email) failed for {user.email}: {e}")
