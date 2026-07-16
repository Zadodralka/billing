from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from core.database import get_db
from core.version import APP_VERSION
from core.timezone import to_local
from core import wheel as wheel_service
from web.routers.auth import require_user, User

router = APIRouter(prefix="/wheel")
templates = Jinja2Templates(directory="web/templates")
templates.env.globals["app_version"] = APP_VERSION
templates.env.filters["localtime"] = to_local


@router.get("", response_class=HTMLResponse)
async def wheel_page(request: Request, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    prizes = await wheel_service.get_active_prizes(session)
    spin_state = await wheel_service.can_spin(user, session)
    return templates.TemplateResponse(request, "wheel.html", {
        "user": user,
        "prizes": prizes,
        "can_spin": spin_state["can_spin"],
        "retry_after_seconds": spin_state["retry_after_seconds"],
    })


@router.post("/spin")
async def wheel_spin(user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    result = await wheel_service.spin(user, session)
    if not result["ok"]:
        return JSONResponse(result, status_code=429 if result.get("retry_after_seconds") else 400)
    return JSONResponse(result)
