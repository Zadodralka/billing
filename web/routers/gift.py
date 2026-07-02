import re
import logging
from datetime import datetime
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from core.database import get_db
from core.models import User, Payment, PaymentStatus, GiftCode, GiftCodeStatus
from core.plans import get_active_plans, get_plan
from core.yoomoney import yoomoney
from web.routers.auth import require_user, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gift")
templates = Jinja2Templates(directory="web/templates")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.get("", response_class=HTMLResponse)
async def gift_page(request: Request, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    plans = await get_active_plans(session)
    return templates.TemplateResponse(request, "gift.html", {"user": user, "plans": plans})


@router.post("/create")
async def create_gift_payment(request: Request, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    data = await request.json()
    plan_key = data.get("plan_key")
    recipient_email = (data.get("recipient_email") or "").strip().lower()

    if not recipient_email or not EMAIL_RE.match(recipient_email):
        raise HTTPException(400, "Введите корректный email получателя")

    plan = await get_plan(session, plan_key)
    if not plan or not plan.get("is_active", True):
        raise HTTPException(400, "Invalid plan")

    amount = plan["price"]
    traffic_gb = plan.get("traffic_gb", 50)

    label = yoomoney.generate_label()
    payment = Payment(
        user_id=user.id,
        plan_key=plan_key,
        traffic_gb=traffic_gb,
        amount=amount,
        original_amount=amount,
        label=label,
        is_gift=True,
        gift_recipient_email=recipient_email,
    )
    session.add(payment)
    await session.commit()

    if amount <= 0:
        from bot.handlers.payments import activate_subscription
        try:
            await activate_subscription(user, payment, session)
        except Exception as e:
            logger.error(f"Free gift activation failed for payment {payment.id}: {e}")
            raise HTTPException(500, "Не удалось оформить подарок")
        return JSONResponse({
            "payment_url": None, "label": label, "amount": 0,
            "plan_name": plan["name"], "recipient_email": recipient_email, "free": True,
        })

    pay_url = yoomoney.create_payment_url(
        amount=amount, label=label,
        comment=f"Подарок VPN {plan['name']} для {recipient_email}",
    )
    return JSONResponse({
        "payment_url": pay_url, "label": label, "amount": amount,
        "plan_name": plan["name"], "recipient_email": recipient_email, "free": False,
    })


@router.get("/status/{label}")
async def gift_payment_status(label: str, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(Payment).where(Payment.label == label, Payment.user_id == user.id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(404)
    return {"paid": payment.status == PaymentStatus.SUCCESS}


@router.get("/redeem/{code}", response_class=HTMLResponse)
async def redeem_page(code: str, request: Request, session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(GiftCode).where(GiftCode.code == code))
    gift = result.scalar_one_or_none()
    if not gift:
        return templates.TemplateResponse(request, "gift_redeem.html", {
            "error": "Подарочный код не найден. Проверьте, что вы скопировали его полностью.",
        }, status_code=404)

    if gift.status == GiftCodeStatus.REDEEMED.value:
        return templates.TemplateResponse(request, "gift_redeem.html", {
            "error": "Этот подарок уже активирован.",
        })

    current_user = await get_current_user(request, session)
    if not current_user:
        # Запоминаем код в сессии - после входа по email-ссылке auth.verify_email
        # вернёт сюда же, а не в /dashboard.
        request.session["pending_gift_code"] = code

    return templates.TemplateResponse(request, "gift_redeem.html", {
        "gift": gift,
        "user": current_user,
    })


@router.post("/redeem/{code}")
async def redeem_gift(code: str, request: Request, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(GiftCode).where(GiftCode.code == code))
    gift = result.scalar_one_or_none()
    if not gift:
        raise HTTPException(404, "Подарочный код не найден")

    # Атомарно "застолбить" код одним UPDATE ... WHERE status='issued' (тот же приём, что и для
    # лимита промокодов в _apply_promo_usage) - иначе два параллельных запроса на один код
    # (двойной клик, два таба) оба проскочат проверку и создадут по VPN-аккаунту каждый:
    # create_new_vpn_subscription коммитит сам по себе, освобождая блокировку строки раньше,
    # чем мы успели бы пометить код погашенным в этой же транзакции.
    claim = await session.execute(
        update(GiftCode)
        .where(GiftCode.id == gift.id, GiftCode.status == GiftCodeStatus.ISSUED.value)
        .values(status=GiftCodeStatus.REDEEMED.value, redeemed_by_user_id=user.id, redeemed_at=datetime.utcnow())
    )
    await session.commit()
    if claim.rowcount == 0:
        return JSONResponse({"ok": False, "error": "Этот подарок уже активирован"}, status_code=400)

    try:
        from bot.handlers.payments import create_new_vpn_subscription
        subscription, _ = await create_new_vpn_subscription(user, gift.plan_key, gift.days, gift.traffic_gb, session)
        gift.subscription_id = subscription.id
        await session.commit()
    except Exception as e:
        logger.error(
            f"Gift {code} claimed by user {user.id} but VPN provisioning failed: {e}. "
            f"Code is marked redeemed without a subscription - needs manual fix."
        )
        return JSONResponse({
            "ok": False,
            "error": "Код принят, но не удалось создать VPN-аккаунт. Напишите в поддержку — мы разберёмся.",
        }, status_code=500)

    return JSONResponse({"ok": True, "redirect": "/dashboard"})
