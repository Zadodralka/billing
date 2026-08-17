import string
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from datetime import datetime, timedelta
import secrets
from core.models import User, Subscription, Payment, PaymentStatus, SubscriptionStatus, GiftCode, GiftCodeStatus
from core.config import settings
from core.plans import get_active_plans, get_plan
from core.yoomoney import yoomoney
from core.remnawave import remnawave
from bot.keyboards.main import payment_keyboard, pending_payment_keyboard, main_menu
from bot.handlers.start import WELCOME_TEXT
from bot.handlers.subscriptions import has_active_subscription
from core.pending_payment import get_pending_payment, cancel_pending_payment

router = Router()

GIFT_CODE_CHARS = string.ascii_uppercase + string.digits


def _traffic_label(traffic_gb: int) -> str:
    return "Безлимит" if traffic_gb == 0 else f"{traffic_gb} GB"


async def _create_payment_and_show(
    callback: CallbackQuery, user: User, session: AsyncSession,
    plan_key: str, plan: dict, traffic_gb: int, title: str,
    payment_comment: str, renew_subscription_id: int | None = None,
    addon_keys: list[str] | None = None,
):
    # Не даём плодить счета - пока не оплачен или не отменён предыдущий, новый
    # через "Купить"/"Продлить" не создаётся (иначе повторные нажатия кнопки
    # плодят бесконечные PENDING-платежи, см. core/pending_payment.py).
    pending = await get_pending_payment(user.id, session)
    if pending:
        pending_plan = await get_plan(session, pending.plan_key)
        pending_traffic_label = _traffic_label(pending.traffic_gb)
        pending_url = yoomoney.create_payment_url(
            amount=pending.amount,
            label=pending.label,
            comment=f"VPN {pending_plan['name'] if pending_plan else pending.plan_key} ({pending_traffic_label})",
        )
        await callback.message.edit_text(
            f"⚠️ <b>У вас уже есть неоплаченный счёт</b>\n\n"
            f"📦 Тариф: {pending_plan['name'] if pending_plan else pending.plan_key}\n"
            f"📊 Трафик: {pending_traffic_label}\n"
            f"💰 Сумма: {pending.amount} ₽\n\n"
            f"Оплатите его или отмените, чтобы выбрать другой тариф.",
            reply_markup=pending_payment_keyboard(pending_url, pending.label, pending.id),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    label = yoomoney.generate_label()
    payment = Payment(
        user_id=user.id,
        plan_key=plan_key,
        traffic_gb=traffic_gb,
        addon_keys=",".join(addon_keys) if addon_keys else None,
        amount=plan["price"],
        label=label,
        renew_subscription_id=renew_subscription_id,
    )
    session.add(payment)
    await session.commit()

    pay_url = yoomoney.create_payment_url(
        amount=plan["price"],
        label=label,
        comment=payment_comment,
    )

    addons_line = ""
    if addon_keys:
        from core.addons import get_addons_by_keys
        addons = await get_addons_by_keys(session, addon_keys)
        if addons:
            addons_line = f"➕ Опции: {', '.join(a['name'] for a in addons)}\n"

    await callback.message.edit_text(
        f"💳 <b>{title}</b>\n\n"
        f"📦 Тариф: {plan['name']}\n"
        f"📊 Трафик: {_traffic_label(traffic_gb)}\n"
        f"{addons_line}"
        f"💰 Сумма: {plan['price']} ₽\n\n"
        f"Нажмите кнопку ниже для оплаты через ЮМани.\n"
        f"После оплаты нажмите <b>✅ Я оплатил(а)</b>",
        reply_markup=payment_keyboard(pay_url, label),
        parse_mode="HTML",
    )
    await callback.answer()


# ===== Покупка — шаг 1: выбор тарифа уже сделан, выбираем объём трафика =====
@router.callback_query(F.data.startswith("buy_plan:"))
async def cb_buy_plan_traffic(callback: CallbackQuery, user: User, session: AsyncSession):
    plan_key = callback.data.split(":")[1]
    plan = await get_plan(session, plan_key)
    if not plan or not plan.get("is_active", True):
        await callback.answer("Тариф недоступен")
        return

    base_traffic = plan.get("traffic_gb", 50)
    unlimited_extra = plan.get("unlimited_extra", 0)

    from core.addons import get_active_addons
    has_addons = bool(await get_active_addons(session))

    def _next_step_callback_data(traffic: int) -> str:
        # Если доп.опций нет вообще (сейчас так по умолчанию) - шаг buy_addons не
        # нужен, ведём себя ровно как раньше: buy:<plan>:<traffic> сразу создаёт счёт.
        if has_addons:
            return f"buy_addons:{plan_key}:{traffic}:0"  # 0 - маска выбранных опций, пока ничего не выбрано
        return f"buy:{plan_key}:{traffic}"

    # Тариф уже безлимитный сам по себе - трафик выбирать нечего
    if base_traffic == 0:
        if has_addons:
            await _render_addons_step(callback, session, plan_key, traffic_gb=0, mask=0)
        else:
            await _create_payment_and_show(
                callback, user, session, plan_key, plan,
                traffic_gb=0,
                title="Оплата подписки",
                payment_comment=f"VPN подписка {plan['name']}",
            )
        return

    unlimited_price = plan["price"] + unlimited_extra
    buttons = [
        [InlineKeyboardButton(
            text=f"{base_traffic} GB — {plan['price']} ₽",
            callback_data=_next_step_callback_data(base_traffic),
        )],
        [InlineKeyboardButton(
            text=f"♾ Безлимит — {unlimited_price} ₽",
            callback_data=_next_step_callback_data(0),
        )],
        [InlineKeyboardButton(text="← Назад", callback_data="menu:buy")],
    ]
    await callback.message.edit_text(
        f"📦 <b>{plan['name']}</b>\n\nВыберите объём трафика:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


# ===== Покупка — шаг 1.5 (только если есть активные доп.опции): тумблеры опций =====
async def _render_addons_step(callback: CallbackQuery, session: AsyncSession, plan_key: str, traffic_gb: int, mask: int):
    from core.addons import get_active_addons
    plan = await get_plan(session, plan_key)
    addons = await get_active_addons(session)
    if not plan or not plan.get("is_active", True):
        await callback.answer("Тариф недоступен")
        return

    traffic_extra = plan.get("unlimited_extra", 0) if traffic_gb == 0 else 0
    base_price = plan["price"] + traffic_extra
    selected = [a for i, a in enumerate(addons) if mask & (1 << i)]
    total = base_price + sum(a["price"] for a in selected)

    buttons = []
    for i, addon in enumerate(addons):
        checked = bool(mask & (1 << i))
        mark = "✅" if checked else "⬜"
        new_mask = mask ^ (1 << i)
        buttons.append([InlineKeyboardButton(
            text=f"{mark} {addon['name']} (+{addon['price']} ₽)",
            callback_data=f"buy_addons:{plan_key}:{traffic_gb}:{new_mask}",
        )])
    buttons.append([InlineKeyboardButton(
        text=f"Оплатить — {total} ₽",
        callback_data=f"buy_confirm:{plan_key}:{traffic_gb}:{mask}",
    )])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data=f"buy_plan:{plan_key}")])

    await callback.message.edit_text(
        f"📦 <b>{plan['name']}</b>\n\n"
        f"Дополнительные опции (необязательно, доплата за весь срок):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buy_addons:"))
async def cb_buy_addons(callback: CallbackQuery, session: AsyncSession):
    _, plan_key, traffic_str, mask_str = callback.data.split(":")
    await _render_addons_step(callback, session, plan_key, int(traffic_str), int(mask_str))


@router.callback_query(F.data.startswith("buy_confirm:"))
async def cb_buy_confirm(callback: CallbackQuery, user: User, session: AsyncSession):
    _, plan_key, traffic_str, mask_str = callback.data.split(":")
    traffic_gb = int(traffic_str)
    mask = int(mask_str)

    plan = await get_plan(session, plan_key)
    if not plan or not plan.get("is_active", True):
        await callback.answer("Тариф недоступен")
        return

    from core.addons import get_active_addons, addons_price
    addons = await get_active_addons(session)
    selected = [a for i, a in enumerate(addons) if mask & (1 << i)]

    price = plan["price"] + (plan.get("unlimited_extra", 0) if traffic_gb == 0 else 0) + addons_price(selected)
    plan_for_payment = {**plan, "price": price}

    await _create_payment_and_show(
        callback, user, session, plan_key, plan_for_payment,
        traffic_gb=traffic_gb,
        title="Оплата подписки",
        payment_comment=f"VPN подписка {plan['name']}" + (" (безлимит)" if traffic_gb == 0 else ""),
        addon_keys=[a["key"] for a in selected],
    )


# ===== Покупка — шаг 2 (без доп.опций): тариф и трафик выбраны, создаём счёт =====
@router.callback_query(F.data.startswith("buy:"))
async def cb_buy_plan(callback: CallbackQuery, user: User, session: AsyncSession):
    _, plan_key, traffic_str = callback.data.split(":")
    traffic_gb = int(traffic_str)

    plan = await get_plan(session, plan_key)
    if not plan or not plan.get("is_active", True):
        await callback.answer("Тариф недоступен")
        return

    price = plan["price"] + (plan.get("unlimited_extra", 0) if traffic_gb == 0 else 0)
    plan_for_payment = {**plan, "price": price}

    await _create_payment_and_show(
        callback, user, session, plan_key, plan_for_payment,
        traffic_gb=traffic_gb,
        title="Оплата подписки",
        payment_comment=f"VPN подписка {plan['name']}" + (" (безлимит)" if traffic_gb == 0 else ""),
    )


# ===== Продление существующей подписки (кнопка "Продлить" в "Мои подписки") =====
@router.callback_query(F.data.startswith("sub:renew:"))
async def cb_sub_renew_start(callback: CallbackQuery, user: User, session: AsyncSession):
    sub_id = int(callback.data.split(":")[2])
    result = await session.execute(
        select(Subscription).where(Subscription.id == sub_id, Subscription.user_id == user.id)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        await callback.answer("Подписка не найдена", show_alert=True)
        return

    # Аккаунт в Remnawave мог быть уже удалён планировщиком (см. scheduler.delete_old_expired_accounts,
    # если подписка была заблокирована больше DELETE_AFTER_DAYS дней назад) - в этом случае продлить
    # старую запись нечем, extend_user/enable_user отработают вникуда и пользователь оплатит без VPN.
    if not sub.remnawave_sub_id:
        await callback.answer(
            "Эта подписка больше не может быть продлена (аккаунт был удалён после долгого простоя). "
            "Оформите новую подписку или напишите в поддержку.",
            show_alert=True,
        )
        return

    plans = await get_active_plans(session)
    buttons = []
    for key, plan in plans.items():
        buttons.append([InlineKeyboardButton(
            text=f"{plan['name']} — {plan['price']} ₽",
            callback_data=f"renew_buy:{sub_id}:{key}",
        )])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="menu:subs")])

    await callback.message.answer(
        "🔁 <b>Продление подписки</b>\n\nВыберите тариф для продления (срок добавится к текущему):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_buy:"))
async def cb_renew_buy_plan(callback: CallbackQuery, user: User, session: AsyncSession):
    _, sub_id_str, plan_key = callback.data.split(":")
    sub_id = int(sub_id_str)

    result = await session.execute(
        select(Subscription).where(Subscription.id == sub_id, Subscription.user_id == user.id)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        await callback.answer("Подписка не найдена", show_alert=True)
        return

    plan = await get_plan(session, plan_key)
    if not plan or not plan.get("is_active", True):
        await callback.answer("Тариф недоступен")
        return

    await _create_payment_and_show(
        callback, user, session, plan_key, plan,
        traffic_gb=sub.traffic_gb,
        title="Продление подписки",
        payment_comment=f"Продление VPN {plan['name']}",
        renew_subscription_id=sub.id,
    )


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
async def cb_cancel(callback: CallbackQuery, user: User, session: AsyncSession):
    # Раньше здесь было callback.message.delete() - сообщение с клавиатурой пропадало,
    # а никакого нового меню не показывалось, и пользователь оставался без единой кнопки
    # в чате (приходилось вручную набирать /start). Правим на возврат в главное меню.
    await callback.message.edit_text(
        "❌ Платёж отменён.\n\n" + WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=main_menu(
            is_admin=user.telegram_id in settings.admin_ids,
            has_active_sub=await has_active_subscription(user.id, session),
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cancel_pending_payment:"))
async def cb_cancel_pending_payment(callback: CallbackQuery, user: User, session: AsyncSession):
    payment_id = int(callback.data.split(":")[1])
    result = await session.execute(
        select(Payment).where(Payment.id == payment_id, Payment.user_id == user.id).with_for_update()
    )
    payment = result.scalar_one_or_none()
    if not payment:
        await callback.answer("Платёж не найден", show_alert=True)
        return
    if payment.status == PaymentStatus.SUCCESS:
        await callback.answer("Этот платёж уже оплачен", show_alert=True)
        return
    if payment.status != PaymentStatus.FAILED:
        await cancel_pending_payment(payment, session)
        await session.commit()

    await callback.message.edit_text(
        "❌ Покупка отменена.\n\n" + WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=main_menu(
            is_admin=user.telegram_id in settings.admin_ids,
            has_active_sub=await has_active_subscription(user.id, session),
        ),
    )
    await callback.answer()


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
        sub.expiry_reminder_sent = False
        sub.traffic_exhausted_notified = False

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
    from core.addons import parse_addon_keys
    subscription, config_link = await create_new_vpn_subscription(
        user, payment.plan_key, plan["days"], traffic_gb, session,
        addon_keys=parse_addon_keys(payment.addon_keys),
    )

    payment.status = PaymentStatus.SUCCESS
    payment.paid_at = now
    await session.commit()

    # Применяем промокод (отмечаем использование)
    if payment.promo_code_id:
        await _apply_promo_usage(payment, session)

    # Реферальный бонус при первой покупке
    await _process_referral(user, payment, session)

    return subscription, config_link


async def create_new_vpn_subscription(
    user: User, plan_key: str, days: int, traffic_gb: int, session: AsyncSession,
    addon_keys: list[str] | None = None,
):
    """
    Создаёт новый независимый VPN-аккаунт в Remnawave и Subscription-запись для user.
    Вынесено из activate_subscription() отдельной функцией, т.к. переиспользуется также
    при погашении подарочного кода (там нет Payment - оплата уже прошла раньше, при покупке
    подарка), где нужно ровно то же самое создание аккаунта без Payment-специфичной логики.

    addon_keys - выбранные покупателем доп.опции (см. core/addons.py, например
    "белые списки"): squad'ы этих опций ДОБАВЛЯЮТСЯ к squad'ам тарифа, а не заменяют их.
    """
    # Стратегия сброса трафика и squad'ы - настройки самого тарифа (см. core/plans.py,
    # /admin/plans), берём текущие значения по ключу, а не то, что было на момент
    # покупки/подарка (days/traffic_gb, наоборот, намеренно приходят снаружи снимком).
    plan = await get_plan(session, plan_key)
    traffic_reset_strategy = (plan or {}).get("traffic_reset_strategy") or "MONTH"
    squad_uuids = (plan or {}).get("squad_uuids") or None

    from core.addons import get_addons_by_keys, addons_squad_uuids
    addons = await get_addons_by_keys(session, addon_keys or [])
    extra_squad_uuids = addons_squad_uuids(addons) or None

    username = f"user_{user.id}_{secrets.token_hex(4)}"
    rw_user = await remnawave.create_user(
        username,
        days,
        traffic_limit_gb=traffic_gb,
        telegram_id=user.telegram_id,
        email=user.email,
        traffic_reset_strategy=traffic_reset_strategy,
        squad_uuids=squad_uuids,
        extra_squad_uuids=extra_squad_uuids,
    )
    remnawave_uuid = rw_user["uuid"]
    config_data = rw_user if rw_user.get("subscriptionUrl") else await remnawave.get_user_config(remnawave_uuid)
    config_link = config_data.get("subscriptionUrl") or config_data.get("link", "")

    subscription = Subscription(
        user_id=user.id,
        plan_key=plan_key,
        traffic_gb=traffic_gb,
        addon_keys=",".join(a["key"] for a in addons) or None,
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
        addon_keys=payment.addon_keys,
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
