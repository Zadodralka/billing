from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from datetime import datetime
import logging, traceback
from core.database import get_db
from core.models import PromoCode, PromoCodeUsage
from core.version import APP_VERSION
from core.timezone import to_local
from web.routers.auth import require_admin, User

logger = logging.getLogger("admin.promo")
router = APIRouter(prefix="/admin/promo")
templates = Jinja2Templates(directory="web/templates")
templates.env.globals["app_version"] = APP_VERSION
templates.env.filters["localtime"] = to_local


@router.get("", response_class=HTMLResponse)
async def admin_promo_list(request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(PromoCode).order_by(PromoCode.created_at.desc()))
    promos = result.scalars().all()
    return templates.TemplateResponse(request, "admin/promo_list.html", {"user": admin, "promos": promos})


@router.post("/create")
async def admin_promo_create(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    code: str = Form(...),
    discount_percent: int = Form(...),
    max_uses: str = Form(""),
    expires_at: str = Form(""),
    description: str = Form(""),
):
    try:
        code = code.strip().upper()
        existing = await session.execute(select(PromoCode).where(PromoCode.code == code))
        if existing.scalar_one_or_none():
            return JSONResponse({"ok": False, "error": f"Промокод '{code}' уже существует"}, status_code=400)

        if not (1 <= discount_percent <= 100):
            return JSONResponse({"ok": False, "error": "Скидка должна быть от 1 до 100%"}, status_code=400)

        promo = PromoCode(
            code=code,
            discount_percent=discount_percent,
            max_uses=int(max_uses) if max_uses.strip() else None,
            expires_at=datetime.fromisoformat(expires_at) if expires_at.strip() else None,
            description=description.strip() or None,
        )
        session.add(promo)
        await session.commit()
        return JSONResponse({"ok": True})
    except Exception as e:
        await session.rollback()
        logger.error(f"create promo failed: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/{promo_id}/toggle")
async def admin_promo_toggle(promo_id: int, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(PromoCode).where(PromoCode.id == promo_id))
    promo = result.scalar_one_or_none()
    if not promo:
        return JSONResponse({"ok": False, "error": "Не найден"}, status_code=404)
    promo.is_active = not promo.is_active
    await session.commit()
    return JSONResponse({"ok": True, "is_active": promo.is_active})


@router.post("/{promo_id}/delete")
async def admin_promo_delete(promo_id: int, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    try:
        result = await session.execute(select(PromoCode).where(PromoCode.id == promo_id))
        promo = result.scalar_one_or_none()
        if not promo:
            return JSONResponse({"ok": False, "error": "Не найден"}, status_code=404)
        from sqlalchemy import delete
        await session.execute(delete(PromoCodeUsage).where(PromoCodeUsage.promo_code_id == promo_id))
        await session.execute(delete(PromoCode).where(PromoCode.id == promo_id))
        await session.commit()
        return JSONResponse({"ok": True})
    except Exception as e:
        await session.rollback()
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
