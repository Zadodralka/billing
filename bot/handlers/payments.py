import string
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from datetime import datetime, timedelta
import secrets
from core.models import User, Subscription, Payment, PaymentStatus, SubscriptionStatus, GiftCode, GiftCodeStatus
from core.plans import get_active_plans, get_plan
from core.yoomoney import yoomoney
from core.remnawave import remnawave
from bot.keyboards.main import payment_keyboard

router = Router()

GIFT_CODE_CHARS = string.ascii_uppercase + string.digits


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
    if not plan or not plan.get("is_active", True):
        await callback.answer("Тариф недоступен")
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
    Если payment.is_gift - деньги покупателя, но подписка никому из существующих
    пользователей не создаётся: вместо этого выпускается GiftCode на email получателя.
    Если payment.renew_subscription_id задан - продлевает существующую подписку
    (тот же VPN-аккаунт, новая дата истечения). Иначе создаёт новую независимую подписку."""
    import logging
    logger = logging.getLogger("bot.payments")

    if payment.is_gift:
        return await _activate_gift(user, payment, session)

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
    subscription, config_link = await create_new_vpn_subscription(user, payment.plan_key, plan["days"], traffic_gb, session)

    payment.status = PaymentStatus.SUCCESS
    payment.paid_at = now
    await session.commit()

    # Применяем промокод (отмечаем использование)
    if payment.promo_code_id:
        await _apply_promo_usage(payment, session)

    # Реферальный бонус при первой покупке
    await _process_referral(user, payment, session)

    return subscription, config_link


async def create_new_vpn_subscription(user: User, plan_key: str, days: int, traffic_gb: int, session: AsyncSession):
    """
    Создаёт новый независимый VPN-аккаунт в Remnawave и Subscription-запись для user.
    Вынесено из activate_subscription() отдельной функцией, т.к. переиспользуется также
    при погашении подарочного кода (там нет Payment - оплата уже прошла раньше, при покупке
    подарка), где нужно ровно то же самое создание аккаунта без Payment-специфичной логики.
    """
    username = f"user_{user.id}_{secrets.token_hex(4)}"
    rw_user = await remnawave.create_user(
        username,
        days,
        traffic_limit_gb=traffic_gb,
        telegram_id=user.telegram_id,
        email=user.email,
    )
    remnawave_uuid = rw_user["uuid"]
    config_data = rw_user if rw_user.get("subscriptionUrl") else await remnawave.get_user_config(remnawave_uuid)
    config_link = config_data.get("subscriptionUrl") or config_data.get("link", "")

    subscription = Subscription(
        user_id=user.id,
        plan_key=plan_key,
        traffic_gb=traffic_gb,
        status=SubscriptionStatus.ACTIVE,
        starts_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(days=days),
        remnawave_sub_id=remnawave_uuid,
        config_link=config_link,
    )
    session.add(subscription)
    await session.commit()
    return subscription, config_link


def _generate_gift_code() -> str:
    group = lambda: "".join(secrets.choice(GIFT_CODE_CHARS) for _ in range(4))
    return f"{group()}-{group()}-{group()}"


async def _activate_gift(user: User, payment: Payment, session: AsyncSession):
    """Оформляет купленный подарок: выпускает GiftCode и отправляет письмо получателю.
    Подписка покупателю не создаётся - её получит тот, кто погасит код на /gift/redeem."""
    import logging
    logger = logging.getLogger("bot.payments")

    plan = await get_plan(session, payment.plan_key)
    if not plan:
        raise Exception(f"Тариф '{payment.plan_key}' не найден (был удалён после оплаты?)")

    now = datetime.utcnow()
    traffic_gb = payment.traffic_gb if payment.traffic_gb is not None else plan.get("traffic_gb", 50)

    # Уникальность кода: коллизия почти невозможна (36^12 вариантов), но проверяем на всякий случай
    for _ in range(5):
        code = _generate_gift_code()
        exists = await session.execute(select(GiftCode).where(GiftCode.code == code))
        if not exists.scalar_one_or_none():
            break
    else:
        raise Exception("Не удалось сгенерировать уникальный код подарка")

    gift = GiftCode(
        code=code,
        payment_id=payment.id,
        buyer_user_id=user.id,
        recipient_email=payment.gift_recipient_email,
        plan_key=payment.plan_key,
        plan_name=plan["name"],
        days=plan["days"],
        traffic_gb=traffic_gb,
        status=GiftCodeStatus.ISSUED.value,
    )
    session.add(gift)

    payment.status = PaymentStatus.SUCCESS
    payment.paid_at = now
    await session.commit()

    if payment.promo_code_id:
        await _apply_promo_usage(payment, session)
    await _process_referral(user, payment, session)

    try:
        from core.email import send_gift_email
        await send_gift_email(payment.gift_recipient_email, code, plan["name"], plan["days"])
    except Exception as e:
        logger.error(
            f"Failed to send gift email for payment {payment.id} (code={code}) "
            f"to {payment.gift_recipient_email}: {e}. Code is valid and can be resent/shared manually."
        )

    return None, None


async def _apply_promo_usage(payment: Payment, session: AsyncSession):
    """Отмечает факт использования промокода в статистике.
    Инкремент uses_count делается атомарным UPDATE с условием по max_uses, чтобы
    конкурентные оплаты, прошедшие валидацию одновременно, не превысили лимит."""
    import logging
    logger = logging.getLogger("bot.payments")
    try:
        from core.models import PromoCode, PromoCodeUsage
        promo_result = await session.execute(
            select(PromoCode).where(PromoCode.id == payment.promo_code_id)
        )
        promo = promo_result.scalar_one_or_none()
        if not promo:
            return

        result = await session.execute(
            update(PromoCode)
            .where(
                PromoCode.id == promo.id,
                (PromoCode.max_uses.is_(None)) | (PromoCode.uses_count < PromoCode.max_uses),
            )
            .values(uses_count=PromoCode.uses_count + 1)
        )
        if result.rowcount == 0:
            logger.warning(
                f"Promo code {promo.id} max_uses reached by concurrent payments; "
                f"payment {payment.id} discount already granted, usage not counted"
            )

        usage = PromoCodeUsage(
            promo_code_id=promo.id,
            user_id=payment.user_id,
            payment_id=payment.id,
            discount_amount=payment.promo_discount,
        )
        session.add(usage)
        await session.commit()
    except Exception as e:
        await session.rollback()
        logger.warning(f"_apply_promo_usage failed: {e}")


async def _process_referral(user: User, payment: Payment, session: AsyncSession):
    """Начисляет реферальные бонусы при первой покупке"""
    import logging
    logger = logging.getLogger("bot.payments")
    try:
        from core.promo_referral import process_referral_bonus
        await process_referral_bonus(user, payment, session)
        await session.commit()
    except Exception as e:
        await session.rollback()
        logger.warning(f"_process_referral failed: {e}")
