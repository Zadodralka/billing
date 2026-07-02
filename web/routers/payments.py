from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import logging
from core.database import get_db
from core.models import User, Payment, PaymentStatus
from core.yoomoney import yoomoney
from core.config import settings
from aiogram import Bot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payment")
templates = Jinja2Templates(directory="web/templates")


@router.post("/webhook/yoomoney")
async def yoomoney_webhook(request: Request, session: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    data = dict(form_data)

    if not yoomoney.verify_notification(data):
        raise HTTPException(400, "Invalid signature")

    label = data.get("label")
    if not label:
        return Response(status_code=200)

    # SELECT ... FOR UPDATE: блокирует строку платежа до commit'а в activate_subscription,
    # так что параллельный повторный webhook (YooMoney ретраит доставку) дождётся коммита
    # и увидит уже актуальный status == SUCCESS вместо повторной активации.
    result = await session.execute(
        select(Payment).where(Payment.label == label).with_for_update()
    )
    payment = result.scalar_one_or_none()

    if not payment or payment.status == PaymentStatus.SUCCESS:
        return Response(status_code=200)

    try:
        received_amount = float(data.get("amount", "0"))
    except (TypeError, ValueError):
        received_amount = 0.0

    # Небольшой допуск на округление; если пришло меньше ожидаемого - не активируем.
    if received_amount < payment.amount - 0.01:
        logger.error(
            f"Payment {payment.id} (label={label}): received amount {received_amount} "
            f"is less than expected {payment.amount}. Notification ignored."
        )
        return Response(status_code=200)

    result = await session.execute(select(User).where(User.id == payment.user_id))
    user = result.scalar_one_or_none()
    if not user:
        return Response(status_code=200)

    from bot.handlers.payments import activate_subscription
    subscription, config_link = await activate_subscription(user, payment, session)

    if user.telegram_id:
        try:
            bot = Bot(token=settings.bot_token)
            from core.plans import get_plan
            plan = await get_plan(session, payment.plan_key) or {}
            expires = subscription.expires_at.strftime("%d.%m.%Y")
            await bot.send_message(
                user.telegram_id,
                f"✅ <b>Оплата получена!</b>\n\n"
                f"📦 Тариф: {plan.get('name', payment.plan_key)}\n"
                f"📅 Активна до: {expires}\n\n"
                f"🔑 Ваш конфиг:\n<code>{config_link}</code>\n\n"
                f"Или откройте раздел <b>🔑 Мои конфиги</b> в боте.",
                parse_mode="HTML",
            )
            await bot.session.close()
        except Exception as e:
            logger.warning(f"Failed to notify user {user.telegram_id}: {e}")

    return Response(status_code=200)


@router.get("/success", response_class=HTMLResponse)
async def payment_success(request: Request, label: str = ""):
    return templates.TemplateResponse(request, "payment_success.html", {"label": label})


@router.post("/validate-promo")
async def validate_promo(request: Request, session: AsyncSession = Depends(get_db)):
    """AJAX-валидация промокода — возвращает скидку без создания платежа"""
    from web.routers.auth import require_user
    user = await require_user(request, session)
    data = await request.json()
    promo_code = data.get("promo_code", "").strip()

    if not promo_code:
        return JSONResponse({"valid": False, "error": "Введите промокод"})

    from core.promo_referral import validate_promo_code
    result = await validate_promo_code(promo_code, user.id, session)
    if result["valid"]:
        return JSONResponse({"valid": True, "discount_percent": result["discount_percent"]})
    return JSONResponse({"valid": False, "error": result["error"]})


@router.post("/create")
async def create_payment_web(request: Request, session: AsyncSession = Depends(get_db)):
    from web.routers.auth import require_user
    from core.promo_referral import validate_promo_code, spend_balance
    user = await require_user(request, session)

    # Перезагружаем пользователя с балансом, блокируя строку - без этого два
    # параллельных запроса (двойной клик / две вкладки) могут прочитать один и тот же
    # остаток и оба списать баланс (lost update).
    from sqlalchemy import select as sa_select
    result = await session.execute(sa_select(User).where(User.id == user.id).with_for_update())
    user = result.scalar_one()

    data = await request.json()
    plan_key = data.get("plan_key")
    traffic_gb = data.get("traffic_gb", 50)
    renew_subscription_id = data.get("renew_subscription_id")
    promo_code_str = data.get("promo_code", "").strip().upper()
    use_balance = data.get("use_balance", False)

    from core.plans import get_plan
    plan = await get_plan(session, plan_key)
    if not plan or not plan.get("is_active", True):
        raise HTTPException(400, "Invalid plan")

    if renew_subscription_id:
        from core.models import Subscription
        sub_result = await session.execute(
            select(Subscription).where(Subscription.id == renew_subscription_id, Subscription.user_id == user.id)
        )
        if not sub_result.scalar_one_or_none():
            raise HTTPException(404, "Подписка не найдена")

    # Базовая цена
    base_price = plan["price"] + (plan["unlimited_extra"] if traffic_gb == 0 else 0)
    traffic_label = "Безлимит" if traffic_gb == 0 else f"{traffic_gb}GB"
    original_amount = base_price
    promo_discount = 0
    promo_obj = None

    # Применяем промокод
    if promo_code_str:
        promo_result = await validate_promo_code(promo_code_str, user.id, session)
        if promo_result["valid"]:
            promo_obj = promo_result["promo_code"]
            promo_discount = int(base_price * promo_obj.discount_percent / 100)

    amount_after_promo = max(0, base_price - promo_discount)

    # Списываем баланс (если запрошено и есть)
    balance_spent = 0
    if use_balance and user.balance > 0:
        balance_spent = min(user.balance, amount_after_promo)

    final_amount = max(0, amount_after_promo - balance_spent)

    label = yoomoney.generate_label()
    payment = Payment(
        user_id=user.id,
        plan_key=plan_key,
        traffic_gb=traffic_gb,
        amount=final_amount,
        original_amount=original_amount,
        promo_discount=promo_discount,
        balance_spent=balance_spent,
        promo_code_id=promo_obj.id if promo_obj else None,
        label=label,
        renew_subscription_id=renew_subscription_id,
    )
    session.add(payment)

    # Списываем баланс сразу (до оплаты — холдируем)
    if balance_spent > 0:
        from core.promo_referral import spend_balance
        await spend_balance(user, balance_spent, f"Оплата {plan['name']}", session)

    await session.commit()

    # Если итоговая сумма 0 — оплата полностью покрыта балансом/промокодом,
    # активируем подписку сразу, без перехода на ЮМани.
    if final_amount <= 0:
        from bot.handlers.payments import activate_subscription
        try:
            await activate_subscription(user, payment, session)
        except Exception as e:
            logger.error(f"Free activation failed for payment {payment.id}: {e}")
            raise HTTPException(500, "Не удалось активировать подписку")
        return JSONResponse({
            "payment_url": None,
            "label": label,
            "amount": 0,
            "plan_name": f"{plan['name']} · {traffic_label}",
            "free": True,
        })

    pay_url = yoomoney.create_payment_url(
        amount=final_amount,
        label=label,
        comment=f"VPN {plan['name']} ({traffic_label})",
    )
    return JSONResponse({
        "payment_url": pay_url,
        "label": label,
        "amount": final_amount,
        "original_amount": original_amount,
        "promo_discount": promo_discount,
        "balance_spent": balance_spent,
        "plan_name": f"{plan['name']} · {traffic_label}",
        "free": False,
    })


@router.get("/status/{label}")
async def check_payment_status(label: str, session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(Payment).where(Payment.label == label))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(404)
    return {"paid": payment.status == PaymentStatus.SUCCESS}


@router.post("/{payment_id}/cancel")
async def cancel_payment(payment_id: int, request: Request, session: AsyncSession = Depends(get_db)):
    from web.routers.auth import require_user
    try:
        user = await require_user(request, session)
    except HTTPException:
        return JSONResponse({"ok": False, "error": "Не авторизован"}, status_code=401)

    try:
        result = await session.execute(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )
        payment = result.scalar_one_or_none()
        if not payment or payment.user_id != user.id:
            return JSONResponse({"ok": False, "error": "Платёж не найден"}, status_code=404)
        if payment.status == PaymentStatus.SUCCESS:
            return JSONResponse({"ok": False, "error": "Нельзя отменить уже оплаченный платёж"}, status_code=400)
        if payment.status == PaymentStatus.FAILED:
            return JSONResponse({"ok": True})  # уже отменён, идемпотентно

        payment.status = PaymentStatus.FAILED

        # Возвращаем баланс, списанный при создании этого платежа
        if payment.balance_spent > 0:
            from core.promo_referral import add_balance
            result = await session.execute(select(User).where(User.id == user.id).with_for_update())
            fresh_user = result.scalar_one()
            await add_balance(
                fresh_user, payment.balance_spent, "payment_refund",
                f"Возврат за отменённый платёж #{payment.id}", session,
            )
            payment.balance_spent = 0

        await session.commit()
        return JSONResponse({"ok": True})
    except Exception as e:
        await session.rollback()
        logger.error(f"Failed to cancel payment {payment_id}: {e}")
        return JSONResponse({"ok": False, "error": "Внутренняя ошибка сервера"}, status_code=500)
