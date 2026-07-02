from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta
from core.database import get_db
from core.models import User, Subscription, Payment, SubscriptionStatus
from core.plans import get_active_plans, get_all_plans
from web.routers.auth import require_user

router = APIRouter(prefix="/dashboard")
templates = Jinja2Templates(directory="web/templates")


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

    return templates.TemplateResponse(request, "dashboard.html", {
        "user": user,
        "active_subs": active_subs,
        "paused_subs": paused_subs,
        "recent_payments": recent_payments,
        "plans": active_plans,
        "all_plans": all_plans,
        "now": now,
    })


@router.get("/plans", response_class=HTMLResponse)
async def plans_page(request: Request, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    plans = await get_active_plans(session)
    return templates.TemplateResponse(request, "plans.html", {
        "user": user,
        "plans": plans,
    })
