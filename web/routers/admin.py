from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from core.database import get_db
from core.models import User, Subscription, Payment, SubscriptionStatus, PaymentStatus
from core.remnawave import remnawave
from web.routers.auth import require_admin

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="web/templates")


@router.get("", response_class=HTMLResponse)
async def admin_index(request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    total_users = (await session.execute(select(func.count(User.id)))).scalar()
    active_subs = (await session.execute(
        select(func.count(Subscription.id)).where(Subscription.status == SubscriptionStatus.ACTIVE)
    )).scalar()
    total_revenue = (await session.execute(
        select(func.sum(Payment.amount)).where(Payment.status == PaymentStatus.SUCCESS)
    )).scalar() or 0

    return templates.TemplateResponse("admin/index.html", {
        "request": request,
        "admin": admin,
        "total_users": total_users,
        "active_subs": active_subs,
        "total_revenue": total_revenue,
    })


@router.get("/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    page: int = 1,
):
    per_page = 20
    offset = (page - 1) * per_page
    result = await session.execute(
        select(User)
        .options(selectinload(User.subscriptions))
        .order_by(User.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    users = result.scalars().all()
    total = (await session.execute(select(func.count(User.id)))).scalar()

    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "admin": admin,
        "users": users,
        "page": page,
        "total": total,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    })


@router.post("/users/{user_id}/ban")
async def ban_user(user_id: int, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404)
    user.is_banned = not user.is_banned
    if user.remnawave_uuid:
        if user.is_banned:
            await remnawave.disable_user(user.remnawave_uuid)
        else:
            await remnawave.enable_user(user.remnawave_uuid)
    await session.commit()
    return RedirectResponse(f"/admin/users", status_code=302)


@router.get("/subscriptions", response_class=HTMLResponse)
async def admin_subscriptions(
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    page: int = 1,
):
    per_page = 20
    offset = (page - 1) * per_page
    result = await session.execute(
        select(Subscription)
        .options(selectinload(Subscription.user))
        .order_by(Subscription.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    subs = result.scalars().all()
    total = (await session.execute(select(func.count(Subscription.id)))).scalar()

    from core.config import PLANS
    return templates.TemplateResponse("admin/subscriptions.html", {
        "request": request,
        "admin": admin,
        "subscriptions": subs,
        "plans": PLANS,
        "page": page,
        "total": total,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    })
