from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.database import get_db
from core.models import User, Payment, PaymentStatus
from core.yoomoney import yoomoney
from core.config import settings
from aiogram import Bot

router = APIRouter(prefix="/payment")
templates = Jinja2Templates(directory="web/templates")


@router.post("/webhook/yoomoney")
async def yoomoney_webhook(request: Request, session: AsyncSession = Depends(get_db)):
    """Вебхук уведомлений от ЮМани"""
    form_data = await request.form()
    data = dict(form_data)

    # Верификация подписи
    if not yoomoney.verify_notification(data):
        raise HTTPException(400, "Invalid signature")

    label = data.get("label")
    if not label:
        return Response(status_code=200)

    # Найти платёж
    result = await session.execute(select(Payment).where(Payment.label == label))
    payment = result.scalar_one_or_none()

    if not payment or payment.status == PaymentStatus.SUCCESS:
        return Response(status_code=200)

    # Получить пользователя
    result = await session.execute(select(User).where(User.id == payment.user_id))
    user = result.scalar_one_or_none()
    if not user:
        return Response(status_code=200)

    # Активировать подписку
    from bot.handlers.payments import activate_subscription
    subscription, config_link = await activate_subscription(user, payment, session)

    # Уведомить пользователя в Telegram
    if user.telegram_id:
        try:
            bot = Bot(token=settings.bot_token)
            from core.config import PLANS
            plan = PLANS.get(payment.plan_key, {})
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
            print(f"Failed to notify user {user.telegram_id}: {e}")

    return Response(status_code=200)


@router.get("/success", response_class=HTMLResponse)
async def payment_success(request: Request, label: str = ""):
    return templates.TemplateResponse("payment_success.html", {
        "request": request,
        "label": label,
    })


@router.post("/create")
async def create_payment_web(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    from web.routers.auth import require_user
    user = await require_user(request, session)
    data = await request.json()
    plan_key = data.get("plan_key")

    from core.config import PLANS
    plan = PLANS.get(plan_key)
    if not plan:
        raise HTTPException(400, "Invalid plan")

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
        comment=f"VPN {plan['name']}",
    )
    return {"payment_url": pay_url, "label": label, "amount": plan["price"], "plan_name": plan["name"]}


@router.get("/status/{label}")
async def check_payment_status(label: str, session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(Payment).where(Payment.label == label))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(404)
    return {"paid": payment.status == PaymentStatus.SUCCESS}
