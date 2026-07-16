from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
import logging, traceback
from core.database import get_db
from core.models import WheelPrize, WheelSpin, WheelPrizeType
from core.version import APP_VERSION
from core.timezone import to_local
from web.routers.auth import require_admin, User

logger = logging.getLogger("admin.wheel")
router = APIRouter(prefix="/admin/wheel")
templates = Jinja2Templates(directory="web/templates")
templates.env.globals["app_version"] = APP_VERSION
templates.env.filters["localtime"] = to_local

_VALID_TYPES = {t.value for t in WheelPrizeType}


@router.get("", response_class=HTMLResponse)
async def admin_wheel_list(request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    prizes_result = await session.execute(select(WheelPrize).order_by(WheelPrize.sort_order))
    prizes = prizes_result.scalars().all()

    spins_result = await session.execute(
        select(WheelSpin).options(selectinload(WheelSpin.user)).order_by(WheelSpin.created_at.desc()).limit(50)
    )
    spins = spins_result.scalars().all()

    return templates.TemplateResponse(request, "admin/wheel.html", {
        "user": admin,
        "prizes": prizes,
        "spins": spins,
    })


@router.post("/create")
async def admin_wheel_create(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    label: str = Form(...),
    prize_type: str = Form(...),
    value: int = Form(0),
    weight: int = Form(1),
    color: str = Form("#3ddc84"),
):
    try:
        label = label.strip()
        if not label:
            return JSONResponse({"ok": False, "error": "Укажите название приза"}, status_code=400)
        if prize_type not in _VALID_TYPES:
            return JSONResponse({"ok": False, "error": f"Неизвестный тип приза: {prize_type}"}, status_code=400)
        if weight < 0:
            return JSONResponse({"ok": False, "error": "Вес не может быть отрицательным"}, status_code=400)
        if value < 0:
            return JSONResponse({"ok": False, "error": "Значение не может быть отрицательным"}, status_code=400)

        max_order = await session.execute(select(WheelPrize.sort_order).order_by(WheelPrize.sort_order.desc()).limit(1))
        next_order = (max_order.scalar() or 0) + 1

        prize = WheelPrize(
            label=label,
            prize_type=prize_type,
            value=value,
            weight=weight,
            color=color.strip() or "#3ddc84",
            sort_order=next_order,
        )
        session.add(prize)
        await session.commit()
        logger.info(f"Admin {admin.id} created wheel prize '{label}' ({prize_type}, value={value}, weight={weight})")
        return JSONResponse({"ok": True})
    except Exception as e:
        await session.rollback()
        logger.error(f"admin_wheel_create failed: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/{prize_id}/update")
async def admin_wheel_update(
    prize_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    label: str = Form(...),
    prize_type: str = Form(...),
    value: int = Form(0),
    weight: int = Form(1),
    color: str = Form("#3ddc84"),
):
    try:
        result = await session.execute(select(WheelPrize).where(WheelPrize.id == prize_id))
        prize = result.scalar_one_or_none()
        if not prize:
            return JSONResponse({"ok": False, "error": "Приз не найден"}, status_code=404)

        label = label.strip()
        if not label:
            return JSONResponse({"ok": False, "error": "Укажите название приза"}, status_code=400)
        if prize_type not in _VALID_TYPES:
            return JSONResponse({"ok": False, "error": f"Неизвестный тип приза: {prize_type}"}, status_code=400)
        if weight < 0 or value < 0:
            return JSONResponse({"ok": False, "error": "Вес и значение не могут быть отрицательными"}, status_code=400)

        prize.label = label
        prize.prize_type = prize_type
        prize.value = value
        prize.weight = weight
        prize.color = color.strip() or "#3ddc84"

        await session.commit()
        logger.info(f"Admin {admin.id} updated wheel prize {prize_id}: {label}, {prize_type}, value={value}, weight={weight}")
        return JSONResponse({"ok": True})
    except Exception as e:
        await session.rollback()
        logger.error(f"admin_wheel_update failed: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/{prize_id}/toggle")
async def admin_wheel_toggle(prize_id: int, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(WheelPrize).where(WheelPrize.id == prize_id))
    prize = result.scalar_one_or_none()
    if not prize:
        return JSONResponse({"ok": False, "error": "Приз не найден"}, status_code=404)
    prize.is_active = not prize.is_active
    await session.commit()
    return JSONResponse({"ok": True, "is_active": prize.is_active})


@router.post("/{prize_id}/delete")
async def admin_wheel_delete(prize_id: int, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    try:
        result = await session.execute(select(WheelPrize).where(WheelPrize.id == prize_id))
        prize = result.scalar_one_or_none()
        if not prize:
            return JSONResponse({"ok": False, "error": "Приз не найден"}, status_code=404)
        # wheel_spins.prize_id - ON DELETE SET NULL (см. миграцию): история кручений
        # с этим призом останется читаемой по снимку label/type/value, отдельно
        # подчищать WheelSpin здесь не нужно.
        await session.delete(prize)
        await session.commit()
        return JSONResponse({"ok": True})
    except Exception as e:
        await session.rollback()
        logger.error(f"admin_wheel_delete failed: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
