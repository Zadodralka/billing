from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from core.database import get_db
from core.models import User, Subscription, Payment
from core.config import PLANS
from web.routers.auth import require_user

router = APIRouter(prefix="/dashboard")
templates = Jinja2Templates(directory="web/templates")


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    # Подгрузить подписки и платежи
    result = await session.execute(
        select(User)
        .where(User.id == user.id)
        .options(selectinload(User.subscriptions), selectinload(User.payments))
    )
    user = result.scalar_one()

    from datetime import datetime
    now = datetime.utcnow()
    active_subs = [s for s in user.subscriptions if s.is_active]
    past_subs = [s for s in user.subscriptions if not s.is_active]
    recent_payments = sorted(user.payments, key=lambda p: p.created_at, reverse=True)[:10]

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "active_subs": active_subs,
        "past_subs": past_subs,
        "recent_payments": recent_payments,
        "plans": PLANS,
        "now": now,
    })
