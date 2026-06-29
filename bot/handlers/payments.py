from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
from core.models import User, Subscription, Payment, PaymentStatus, SubscriptionStatus
from core.config import PLANS
from core.yoomoney import yoomoney
from core.remnawave import remnawave
from bot.keyboards.main import plans_keyboard, payment_keyboard

router = Router()


@router.message(F.text == "💳 Купить подписку")
async def cmd_buy(message: Message, user: User):
    await message.answer(
        "💳 <b>Выберите тарифный план:</b>",
        reply_markup=plans_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy_plan(callback: CallbackQuery, user: User, session: AsyncSession):
    plan_key = callback.data.split(":")[1]
    plan = PLANS.get(plan_key)
    if not plan:
        await callback.answer("Неверный тариф")
        return

    label = yoomoney.generate_label()
    payment = Payment(
        user_id=user.id,
        plan_key=plan_key,
        amount=plan["price"],
        label=label,
    )
    session.add(payment)
    await session.commit()

    pay_url = yoomoney.create_payment_url(
        amount=plan["price"],
        label=label,
        comment=f"VPN подписка {plan['name']}",
    )

    await callback.message.edit_text(
        f"💳 <b>Оплата подписки</b>\n\n"
        f"📦 Тариф: {plan['name']}\n"
        f"💰 Сумма: {plan['price']} ₽\n\n"
        f"Нажмите кнопку ниже для оплаты через ЮМани.\n"
        f"После оплаты нажмите <b>✅ Я оплатил(а)</b>",
        reply_markup=payment_keyboard(pay_url, label),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("check_payment:"))
async def cb_check_payment(callback: CallbackQuery, user: User, session: AsyncSession):
    label = callback.data.split(":")[1]
    result = await session.execute(
        select(Payment).where(Payment.label == label, Payment.user_id == user.id)
    )
    payment = result.scalar_one_or_none()

    if not payment:
        await callback.answer("Платёж не найден", show_alert=True)
        return

    if payment.status == PaymentStatus.SUCCESS:
        await callback.answer("✅ Платёж уже обработан!", show_alert=True)
        return

    # Для ручной проверки — ЮМани пришлёт уведомление автоматически
    await callback.answer(
        "⏳ Платёж ещё не поступил.\n"
        "Обычно это занимает 1-5 минут после оплаты.",
        show_alert=True,
    )


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer("Отменено")


async def activate_subscription(user: User, payment: Payment, session: AsyncSession):
    """Активация подписки после успешной оплаты (вызывается из вебхука)"""
    plan = PLANS[payment.plan_key]
    now = datetime.utcnow()

    # Создать или продлить пользователя в Remnawave
    if user.remnawave_uuid:
        await remnawave.extend_user(user.remnawave_uuid, plan["days"])
        config_data = await remnawave.get_user_config(user.remnawave_uuid)
    else:
        username = f"user_{user.id}_{user.telegram_id or user.email}"
        rw_user = await remnawave.create_user(username, plan["days"])
        user.remnawave_uuid = rw_user["uuid"]
        config_data = await remnawave.get_user_config(rw_user["uuid"])

    config_link = config_data.get("subscriptionUrl") or config_data.get("link", "")

    # Создать запись о подписке
    subscription = Subscription(
        user_id=user.id,
        plan_key=payment.plan_key,
        status=SubscriptionStatus.ACTIVE,
        starts_at=now,
        expires_at=now + timedelta(days=plan["days"]),
        remnawave_sub_id=user.remnawave_uuid,
        config_link=config_link,
    )
    payment.status = PaymentStatus.SUCCESS
    payment.paid_at = now
    session.add(subscription)
    await session.commit()

    return subscription, config_link
