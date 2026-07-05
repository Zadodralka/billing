import re
import secrets
import asyncio
import logging
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta
from core.database import get_db
from core.models import User, Subscription, Payment, PaymentStatus, SubscriptionStatus, EmailToken
from core.plans import get_active_plans, get_all_plans
from core.remnawave import remnawave
from core.version import APP_VERSION
from core.telegram_login import create_token as create_tg_login_token, get_token_data as get_tg_login_data, consume_token as consume_tg_login_token
from web.routers.auth import require_user, get_bot_username, _check_login_email_rate_limit

logger = logging.getLogger(__name__)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EXPIRING_SOON_DAYS = 3  # тот же порог, что и у напоминания в scheduler.notify_expiring_soon

router = APIRouter(prefix="/dashboard")
templates = Jinja2Templates(directory="web/templates")
templates.env.globals["app_version"] = APP_VERSION


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(User)
        .where(User.id == user.id)
        .options(selectinload(User.subscriptions), selectinload(User.payments))
    )
    user = result.scalar_one()

    now = datetime.utcnow()
    recent_cutoff = now - timedelta(days=30)  # истёкшие <= 30 дней назад тоже показываем

    # Разделяем подписки на три группы:
    # 1. Активные (нормальный доступ)
    active_subs = [s for s in user.subscriptions if s.status == SubscriptionStatus.ACTIVE and (not s.expires_at or s.expires_at > now)]
    # 2. Приостановленные (доступ заблокирован вручную) или недавно истёкшие — клиент должен их видеть
    paused_subs = [s for s in user.subscriptions if
        s.status == SubscriptionStatus.CANCELLED or
        (s.status == SubscriptionStatus.EXPIRED and s.expires_at and s.expires_at >= recent_cutoff)
    ]
    # 3. Старые истёкшие — в историю
    recent_payments = sorted(user.payments, key=lambda p: p.created_at, reverse=True)[:10]

    all_plans = await get_all_plans(session)
    active_plans = await get_active_plans(session)

    # Расход трафика по активным подпискам - запросы к Remnawave идут параллельно,
    # а не по очереди, иначе при нескольких подписках открытие кабинета ждало бы
    # каждый запрос последовательно (см. аналогичный фикс для бота).
    usage_map = {}
    active_uuids = [s.remnawave_sub_id for s in active_subs if s.remnawave_sub_id]
    if active_uuids:
        usage_results = await asyncio.gather(*(remnawave.get_traffic_usage_gb(u) for u in active_uuids))
        usage_map = dict(zip(active_uuids, usage_results))

    expiring_soon = [
        s for s in active_subs
        if s.expires_at and s.expires_at <= now + timedelta(days=EXPIRING_SOON_DAYS)
    ]

    total_spent = sum(p.amount for p in user.payments if p.status == PaymentStatus.SUCCESS)

    return templates.TemplateResponse(request, "dashboard.html", {
        "user": user,
        "active_subs": active_subs,
        "paused_subs": paused_subs,
        "recent_payments": recent_payments,
        "plans": active_plans,
        "all_plans": all_plans,
        "now": now,
        "usage_map": usage_map,
        "expiring_soon": expiring_soon,
        "total_spent": total_spent,
    })


@router.get("/plans", response_class=HTMLResponse)
async def plans_page(request: Request, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    plans = await get_active_plans(session)
    return templates.TemplateResponse(request, "plans.html", {
        "user": user,
        "plans": plans,
    })


# ───────────── Привязка Telegram/email к текущему аккаунту ─────────────

@router.post("/link-telegram/start")
async def link_telegram_start(user: User = Depends(require_user)):
    if user.telegram_id:
        raise HTTPException(400, "Telegram уже привязан к этому аккаунту")
    bot_username = await get_bot_username()
    if not bot_username:
        raise HTTPException(503, "Бот временно недоступен, попробуйте позже")
    token = await create_tg_login_token(purpose="link", user_id=user.id)
    return {"deep_link": f"https://t.me/{bot_username}?start=tglogin_{token}"}


@router.get("/link-telegram/status/{token}")
async def link_telegram_status(token: str, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    data = await get_tg_login_data(token)
    if not data:
        return {"status": "expired"}
    if data.get("status") != "confirmed":
        return {"status": "pending"}
    if data.get("user_id") != user.id:
        # Токен создавался для другой сессии - не должно происходить в норме, но не доверяем чужому подтверждению
        return {"status": "error", "error": "Токен принадлежит другой сессии"}

    tg_id = data["telegram_id"]
    existing = await session.execute(select(User).where(User.telegram_id == tg_id))
    existing_user = existing.scalar_one_or_none()
    if existing_user and existing_user.id != user.id:
        await consume_tg_login_token(token)
        return {"status": "error", "error": "Этот Telegram уже привязан к другому аккаунту. Обратитесь в поддержку."}

    result = await session.execute(select(User).where(User.id == user.id))
    fresh_user = result.scalar_one()
    fresh_user.telegram_id = tg_id
    fresh_user.telegram_username = data.get("username")
    await session.commit()
    await consume_tg_login_token(token)
    return {"status": "confirmed"}


@router.post("/link-email")
async def link_email(user: User = Depends(require_user), email: str = Form(...), session: AsyncSession = Depends(get_db)):
    if user.email:
        raise HTTPException(400, "Email уже привязан к этому аккаунту")

    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Введите корректный email")

    existing = await session.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Этот email уже привязан к другому аккаунту")

    if not await _check_login_email_rate_limit(email):
        raise HTTPException(429, "Слишком много запросов для этого email. Попробуйте позже.")

    token = secrets.token_urlsafe(32)
    email_token = EmailToken(email=email, token=token, purpose="link", link_user_id=user.id)
    session.add(email_token)
    await session.commit()

    from core.email import send_magic_link
    try:
        await send_magic_link(email, token)
    except Exception as e:
        logger.error(f"link_email: failed to send confirmation to {email}: {e}")
        raise HTTPException(500, "Не удалось отправить письмо, попробуйте позже")

    return JSONResponse({"ok": True, "message": f"Письмо с подтверждением отправлено на {email}"})
