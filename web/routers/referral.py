from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from core.database import get_db
from core.models import User, BalanceTransaction
from core.promo_referral import ensure_referral_code
from core.config import settings
from core.version import APP_VERSION
from core.timezone import to_local
from web.routers.auth import require_user

router = APIRouter(prefix="/dashboard/referral")
templates = Jinja2Templates(directory="web/templates")
templates.env.globals["app_version"] = APP_VERSION
templates.env.filters["localtime"] = to_local


@router.get("", response_class=HTMLResponse)
async def referral_page(request: Request, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    from sqlalchemy import select as sa_select
    result = await session.execute(sa_select(User).where(User.id == user.id))
    user = result.scalar_one()

    ref_code = await ensure_referral_code(user, session)

    # Считаем рефералов
    referrals_count = (await session.execute(
        select(func.count(User.id)).where(User.referred_by_id == user.id)
    )).scalar() or 0

    paid_referrals = (await session.execute(
        select(func.count(User.id)).where(User.referred_by_id == user.id, User.referral_bonus_paid == True)
    )).scalar() or 0

    # История транзакций
    txs_result = await session.execute(
        select(BalanceTransaction).where(BalanceTransaction.user_id == user.id).order_by(BalanceTransaction.created_at.desc()).limit(20)
    )
    transactions = txs_result.scalars().all()

    ref_link_web = f"{settings.webapp_url}?ref={ref_code}"
    ref_link_bot = f"https://t.me/{await _get_bot_username()}?start=ref_{ref_code}"
    bonus_referrer = getattr(settings, "referral_bonus_referrer", 100)
    bonus_referred = getattr(settings, "referral_bonus_referred", 50)

    return templates.TemplateResponse(request, "referral.html", {
        "user": user,
        "ref_code": ref_code,
        "ref_link_web": ref_link_web,
        "ref_link_bot": ref_link_bot,
        "referrals_count": referrals_count,
        "paid_referrals": paid_referrals,
        "transactions": transactions,
        "bonus_referrer": bonus_referrer,
        "bonus_referred": bonus_referred,
    })


async def _get_bot_username() -> str:
    try:
        import httpx
        from core.config import settings as s
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"https://api.telegram.org/bot{s.bot_token}/getMe")
            return r.json()["result"]["username"]
    except Exception:
        return "your_bot"
