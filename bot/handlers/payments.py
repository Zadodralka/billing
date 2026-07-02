from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
import secrets
from core.models import User, Subscription, Payment, PaymentStatus, SubscriptionStatus
from core.plans import get_active_plans, get_plan
from core.yoomoney import yoomoney
from core.remnawave import remnawave
from bot.keyboards.main import payment_keyboard

router = Router()


@router.message(F.text == "💳 Купить подписку")
async def cmd_buy(message: Message, user: User, session: AsyncSession):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    plans = await get_active_plans(session)
    buttons = []
    for key, plan in plans.items():
        buttons.append([
            InlineKeyboardButton(text=f"{plan['name']} — {plan['price']} ₽", callback_data=f"buy:{key}")
        ])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])

    await message.answer(
        "💳 <b>Выберите тарифный план:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy_plan(callback: CallbackQuery, user: User, session: AsyncSession):
    plan_key = callback.data.split(":")[1]
    plan = await get_plan(session, plan_key)
    if not plan:
        await callback.answer("Неверный тариф")
        return

    label = yoomoney.generate_label()
    payment = Payment(
        user_id=user.id,
        plan_key=plan_key,
        traffic_gb=plan.get("traffic_gb", 50),
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
    """Активация подписки после успешной оплаты (вызывается из вебхука).
    Если payment.renew_subscription_id задан - продлевает существующую подписку
    (тот же VPN-аккаунт, новая дата истечения). Иначе создаёт новую независимую подписку."""
    import logging
    logger = logging.getLogger("bot.payments")

    plan = await get_plan(session, payment.plan_key)
    if not plan:
        raise Exception(f"Тариф '{payment.plan_key}' не найден (был удалён после оплаты?)")

    now = datetime.utcnow()
    traffic_gb = payment.traffic_gb if payment.traffic_gb is not None else plan.get("traffic_gb", 50)

    # ===== Продление существующей подписки =====
    if payment.renew_subscription_id:
        result = await session.execute(
            select(Subscription).where(Subscription.id == payment.renew_subscription_id)
        )
        sub = result.scalar_one_or_none()
        if not sub:
            raise Exception(f"Подписка для продления (id={payment.renew_subscription_id}) не найдена")

        base = sub.expires_at if sub.expires_at and sub.expires_at > now else now
        sub.expires_at = base + timedelta(days=plan["days"])
        sub.status = SubscriptionStatus.ACTIVE

        if sub.remnawave_sub_id:
            try:
                await remnawave.extend_user(sub.remnawave_sub_id, plan["days"])
                await remnawave.enable_user(sub.remnawave_sub_id)
                logger.info(f"activate_subscription: renewed sub {sub.id}, remnawave extended successfully")
            except Exception as e:
                logger.warning(f"activate_subscription: Remnawave renew failed for sub {sub.id}: {e}")
        else:
            logger.warning(f"activate_subscription: sub {sub.id} has no remnawave_sub_id, cannot extend in Remnawave")

        payment.status = PaymentStatus.SUCCESS
        payment.paid_at = now
        await session.commit()

        # Применяем промокод (отмечаем использование)
        if payment.promo_code_id:
            await _apply_promo_usage(payment, session)

        # Реферальный бонус
        await _process_referral(user, payment, session)

        return sub, sub.config_link or ""

    # ===== Покупка новой независимой подписки =====
    username = f"user_{user.id}_{secrets.token_hex(4)}"
    rw_user = await remnawave.create_user(
        username,
        plan["days"],
        traffic_limit_gb=traffic_gb,
        telegram_id=user.telegram_id,
        email=user.email,
    )
    remnawave_uuid = rw_user["uuid"]
    config_data = rw_user if rw_user.get("subscriptionUrl") else await remnawave.get_user_config(remnawave_uuid)
    config_link = config_data.get("subscriptionUrl") or config_data.get("link", "")

    subscription = Subscription(
        user_id=user.id,
        plan_key=payment.plan_key,
        traffic_gb=traffic_gb,
        status=SubscriptionStatus.ACTIVE,
        starts_at=now,
        expires_at=now + timedelta(days=plan["days"]),
        remnawave_sub_id=remnawave_uuid,
        config_link=config_link,
    )
    payment.status = PaymentStatus.SUCCESS
    payment.paid_at = now
    session.add(subscription)
    await session.commit()

    # Применяем промокод (отмечаем использование)
    if payment.promo_code_id:
        await _apply_promo_usage(payment, session)

    # Реферальный бонус при первой покупке
    await _process_referral(user, payment, session)

    return subscription, config_link


async def _apply_promo_usage(payment: Payment, session: AsyncSession):
    """Отмечает факт использования промокода в статистике"""
    try:
        from core.models import PromoCode, PromoCodeUsage
        promo_result = await session.execute(
            select(PromoCode).where(PromoCode.id == payment.promo_code_id)
        )
        promo = promo_result.scalar_one_or_none()
        if promo:
            usage = PromoCodeUsage(
                promo_code_id=promo.id,
                user_id=payment.user_id,
                payment_id=payment.id,
                discount_amount=payment.promo_discount,
            )
            promo.uses_count += 1
            session.add(usage)
            await session.commit()
    except Exception as e:
        import logging
        logging.getLogger("bot.payments").warning(f"_apply_promo_usage failed: {e}")


async def _process_referral(user: User, payment: Payment, session: AsyncSession):
    """Начисляет реферальные бонусы при первой покупке"""
    try:
        from core.promo_referral import process_referral_bonus
        await process_referral_bonus(user, payment, session)
        await session.commit()
    except Exception as e:
        import logging
        logging.getLogger("bot.payments").warning(f"_process_referral failed: {e}")
