import re
import logging
from datetime import datetime
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from core.database import get_db
from core.models import User, Payment, PaymentStatus, GiftCode, GiftCodeStatus
from core.plans import get_plan
from core.yoomoney import yoomoney
from core.rate_limit import check_rate_limit
from core.version import APP_VERSION
from web.routers.auth import require_user, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gift")
templates = Jinja2Templates(directory="web/templates")
templates.env.globals["app_version"] = APP_VERSION

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Ограничение на создание счетов на подарок - получатель узнаёт о подарке письмом
# на свой email, не должно быть возможности заспамить произвольный адрес счетами.
GIFT_CREATE_RATE_LIMIT = 10
GIFT_CREATE_RATE_WINDOW_SECONDS = 3600


@router.get("")
async def gift_page(request: Request):
    # Страница /gift объединена с /dashboard/plans (переключатель "Себе / В подарок") -
    # оставляем редирект, чтобы старые ссылки/закладки не превращались в 404.
    return RedirectResponse("/dashboard/plans")


@router.post("/create")
async def create_gift_payment(request: Request, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    data = await request.json()
    plan_key = data.get("plan_key")
    recipient_email = (data.get("recipient_email") or "").strip().lower()

    if not recipient_email or not EMAIL_RE.match(recipient_email):
        raise HTTPException(400, "Введите корректный email получателя")

    if not await check_rate_limit(f"gift_create:{user.id}", GIFT_CREATE_RATE_LIMIT, GIFT_CREATE_RATE_WINDOW_SECONDS):
        raise HTTPException(429, "Слишком много подарков подряд. Попробуйте позже.")

    plan = await get_plan(session, plan_key)
    if not plan or not plan.get("is_active", True):
        raise HTTPException(400, "Invalid plan")

    # Трафик выбирается тем же переключателем, что и при покупке себе (см. plans.html) -
    # логика цены совпадает с web.routers.payments.create_payment_web.
    traffic_gb = data.get("traffic_gb", plan.get("traffic_gb", 50))
    amount = plan["price"] + (plan.get("unlimited_extra", 0) if traffic_gb == 0 else 0)

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
        # Код подарка приходит только на recipient_email - обладание им такое же
        # доказательство владения почтой, как переход по обычной magic-link ссылке.
        # Поэтому кнопка в письме сразу логинит получателя, без второго письма для входа
        # (find-or-create по email - тот же приём, что и в auth.login_email).
        result = await session.execute(select(User).where(User.email == gift.recipient_email))
        current_user = result.scalar_one_or_none()
        if not current_user:
            current_user = User(email=gift.recipient_email)
            session.add(current_user)
            await session.commit()
            await session.refresh(current_user)
        request.session["user_id"] = current_user.id

    if current_user.is_banned:
        return templates.TemplateResponse(request, "gift_redeem.html", {
            "error": "Доступ к аккаунту получателя заблокирован. Обратитесь в поддержку.",
        }, status_code=403)

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
