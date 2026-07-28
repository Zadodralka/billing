"""
Защита от накопления неоплаченных счетов: пока у пользователя есть один
"висящий" PENDING-платёж (покупка или продление подписки), новый через это же
действие не создаётся - сначала нужно оплатить или отменить существующий.
Иначе повторные нажатия "Купить"/"Продлить" плодят бесконечные записи Payment.

Общая логика для веба (web/routers/payments.py) и бота (bot/handlers/payments.py),
чтобы отмена платежа (в т.ч. возврат списанного на него баланса) не дублировалась
в двух местах. На подарки (web/routers/gift.py) не распространяется - там уже
есть отдельный rate-limit, и получатель у каждого подарка свой.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.models import Payment, PaymentStatus, User


async def get_pending_payment(user_id: int, session: AsyncSession) -> Payment | None:
    result = await session.execute(
        select(Payment).where(
            Payment.user_id == user_id,
            Payment.status == PaymentStatus.PENDING,
            Payment.is_gift == False,
        )
    )
    return result.scalars().first()


async def cancel_pending_payment(payment: Payment, session: AsyncSession) -> None:
    """Помечает счёт отменённым и возвращает списанный на него баланс, если был.
    Не коммитит - вызывающий код коммитит сам."""
    payment.status = PaymentStatus.FAILED

    if payment.balance_spent > 0:
        from core.promo_referral import add_balance
        result = await session.execute(select(User).where(User.id == payment.user_id).with_for_update())
        user = result.scalar_one()
        await add_balance(
            user, payment.balance_spent, "payment_refund",
            f"Возврат за отменённый платёж #{payment.id}", session,
        )
        payment.balance_spent = 0
