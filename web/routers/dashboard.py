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
from core.models import User, PaymentStatus, SubscriptionStatus, EmailToken
from core.plans import get_active_plans, get_all_plans
from core.remnawave import remnawave
from core.version import APP_VERSION
from core.timezone import to_local
from core.telegram_login import create_token as create_tg_login_token, get_token_data as get_tg_login_data, consume_token as consume_tg_login_token
from web.routers.auth import require_user, get_bot_username, _check_login_email_rate_limit

logger = logging.getLogger(__name__)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EXPIRING_SOON_DAYS = 3  # тот же порог, что и у напоминания в scheduler.notify_expiring_soon

router = APIRouter(prefix="/dashboard")
templates = Jinja2Templates(directory="web/templates")
templates.env.globals["app_version"] = APP_VERSION
templates.env.filters["localtime"] = to_local


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(User)
        .where(User.id == user.id)
        .options(selectinload(User.subscriptions))
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

    all_plans = await get_all_plans(session)
    active_plans = await get_active_plans(session)

    from core.addons import get_addon_name_map, parse_addon_keys
    addon_names = await get_addon_name_map(session)

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

    # Названия купленных доп.опций по подпискам - для маленького бейджа в карточке
    # (например "Белые списки"), см. core/addons.py.
    sub_addon_names = {
        s.id: [addon_names.get(k, k) for k in parse_addon_keys(s.addon_keys)]
        for s in user.subscriptions
    }

    return templates.TemplateResponse(request, "dashboard.html", {
        "user": user,
        "active_subs": active_subs,
        "paused_subs": paused_subs,
        "plans": active_plans,
        "all_plans": all_plans,
        "sub_addon_names": sub_addon_names,
        "now": now,
        "usage_map": usage_map,
        "expiring_soon": expiring_soon,
    })


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(User).where(User.id == user.id).options(selectinload(User.payments))
    )
    user = result.scalar_one()
    total_spent = sum(p.amount for p in user.payments if p.status == PaymentStatus.SUCCESS)
    recent_payments = sorted(user.payments, key=lambda p: p.created_at, reverse=True)[:10]
    all_plans = await get_all_plans(session)
    return templates.TemplateResponse(request, "profile.html", {
        "user": user,
        "total_spent": total_spent,
        "recent_payments": recent_payments,
        "all_plans": all_plans,
    })


@router.get("/plans", response_class=HTMLResponse)
async def plans_page(request: Request, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    from core.addons import get_active_addons
    plans = await get_active_plans(session)
    addons = await get_active_addons(session)
    return templates.TemplateResponse(request, "plans.html", {
        "user": user,
        "plans": plans,
        "addons": addons,
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


@router.post("/subscriptions/{sub_id}/give-up")
async def give_up_subscription(sub_id: int, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    """
    Пользователь сам отказывается от истёкшей подписки, не дожидаясь автоудаления
    планировщиком (см. DELETE_AFTER_DAYS в scheduler.py) - удаляет VPN-аккаунт
    в Remnawave немедленно. Подписка остаётся в БД для истории (как и при обычном
    автоудалении), просто remnawave_sub_id обнуляется - именно по этому полю
    карточка в кабинете и бот понимают, что аккаунт больше не существует.
    """
    from core.models import Subscription, SubscriptionStatus

    result = await session.execute(
        select(Subscription).where(Subscription.id == sub_id, Subscription.user_id == user.id)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        return JSONResponse({"ok": False, "error": "Подписка не найдена"}, status_code=404)
    if sub.status != SubscriptionStatus.EXPIRED:
        return JSONResponse({"ok": False, "error": "Отказаться можно только от истёкшей подписки"}, status_code=400)

    if sub.remnawave_sub_id:
        try:
            await remnawave.delete_user(sub.remnawave_sub_id)
            sub.remnawave_sub_id = None
            sub.config_link = None
        except Exception as e:
            # Не мешаем пользователю "отказаться" из-за временной недоступности Remnawave -
            # remnawave_sub_id намеренно НЕ трогаем, чтобы штатная очистка в scheduler.py
            # (delete_old_expired_accounts) подхватила аккаунт позже и не потеряла его.
            logger.warning(f"give_up_subscription: remnawave delete failed for sub {sub_id}: {e}")

    await session.commit()
    logger.info(f"give_up_subscription: user {user.id} gave up sub {sub_id}")
    return JSONResponse({"ok": True})
