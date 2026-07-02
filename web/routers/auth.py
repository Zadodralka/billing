import secrets
import hashlib
import hmac
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.database import get_db
from core.models import User, EmailToken
from core.email import send_magic_link
from core.config import settings

logger = logging.getLogger("auth")

router = APIRouter(prefix="/auth")
templates = Jinja2Templates(directory="web/templates")

_bot_username_cache = {"value": None}
_redis_client = None
LOGIN_EMAIL_RATE_LIMIT = 3
LOGIN_EMAIL_RATE_WINDOW_SECONDS = 900  # 15 минут


def get_redis():
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


async def _check_login_email_rate_limit(email: str) -> bool:
    """True если лимит не превышен. При недоступности Redis - не блокируем (fail-open)."""
    try:
        r = get_redis()
        key = f"ratelimit:login_email:{email}"
        attempts = await r.incr(key)
        if attempts == 1:
            await r.expire(key, LOGIN_EMAIL_RATE_WINDOW_SECONDS)
        return attempts <= LOGIN_EMAIL_RATE_LIMIT
    except Exception as e:
        logger.warning(f"Rate limit check failed (allowing request): {e}")
        return True


async def get_bot_username() -> str | None:
    if _bot_username_cache["value"]:
        return _bot_username_cache["value"]
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"https://api.telegram.org/bot{settings.bot_token}/getMe")
            data = resp.json()
            if data.get("ok"):
                username = data["result"]["username"]
                _bot_username_cache["value"] = username
                return username
    except Exception:
        pass
    return None


def get_session_user_id(request: Request) -> int | None:
    return request.session.get("user_id")


async def get_current_user(request: Request, session: AsyncSession = Depends(get_db)) -> User | None:
    user_id = get_session_user_id(request)
    if not user_id:
        return None
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def require_user(request: Request, session: AsyncSession = Depends(get_db)) -> User:
    user = await get_current_user(request, session)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/auth/login"})
    if user.is_banned:
        request.session.clear()
        raise HTTPException(status_code=403, detail="Доступ заблокирован")
    return user


async def require_admin(request: Request, session: AsyncSession = Depends(get_db)) -> User:
    user = await require_user(request, session)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    return user


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    bot_username = await get_bot_username()
    # Сохраняем реферальный код в сессии для применения при регистрации
    ref_code = request.query_params.get("ref")
    if ref_code:
        request.session["pending_ref"] = ref_code.strip().upper()
    return templates.TemplateResponse(request, "login.html", {
        "bot_username": bot_username,
        "webapp_url": settings.webapp_url,
    })


@router.post("/login/email")
async def login_email(
    request: Request,
    email: str = Form(...),
    session: AsyncSession = Depends(get_db),
):
    email = email.strip().lower()
    bot_username = await get_bot_username()

    if not await _check_login_email_rate_limit(email):
        return templates.TemplateResponse(request, "login.html", {
            "error": "Слишком много запросов для этого email. Попробуйте позже.",
            "bot_username": bot_username,
            "webapp_url": settings.webapp_url,
        }, status_code=429)

    token = secrets.token_urlsafe(32)

    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        user = User(email=email)
        session.add(user)
        await session.commit()

    email_token = EmailToken(email=email, token=token)
    session.add(email_token)
    await session.commit()

    try:
        await send_magic_link(email, token)
    except Exception as e:
        return templates.TemplateResponse(request, "login.html", {
            "error": f"Ошибка отправки email: {e}",
            "bot_username": bot_username,
            "webapp_url": settings.webapp_url,
        })

    return templates.TemplateResponse(request, "login.html", {
        "message": f"Письмо с ссылкой для входа отправлено на {email}",
        "bot_username": bot_username,
        "webapp_url": settings.webapp_url,
    })


@router.get("/verify")
async def verify_email(token: str, request: Request, session: AsyncSession = Depends(get_db)):
    bot_username = await get_bot_username()
    result = await session.execute(
        select(EmailToken).where(EmailToken.token == token, EmailToken.used == False)
    )
    email_token = result.scalar_one_or_none()

    if not email_token:
        return templates.TemplateResponse(request, "login.html", {
            "error": "Ссылка недействительна или уже использована.",
            "bot_username": bot_username,
            "webapp_url": settings.webapp_url,
        })

    if datetime.utcnow() - email_token.created_at > timedelta(minutes=15):
        return templates.TemplateResponse(request, "login.html", {
            "error": "Ссылка истекла. Запросите новую.",
            "bot_username": bot_username,
            "webapp_url": settings.webapp_url,
        })

    email_token.used = True
    result = await session.execute(select(User).where(User.email == email_token.email))
    user = result.scalar_one_or_none()
    if not user:
        return RedirectResponse("/auth/login")

    user.last_seen = datetime.utcnow()
    await session.commit()

    request.session["user_id"] = user.id

    pending_gift_code = request.session.pop("pending_gift_code", None)
    if pending_gift_code:
        return RedirectResponse(f"/gift/redeem/{pending_gift_code}")
    return RedirectResponse("/dashboard")


@router.get("/telegram")
async def telegram_auth(request: Request, session: AsyncSession = Depends(get_db)):
    params = dict(request.query_params)
    received_hash = params.pop("hash", "")

    data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hashlib.sha256(settings.bot_token.encode()).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        raise HTTPException(403, "Invalid Telegram signature")

    if abs(datetime.utcnow().timestamp() - int(params.get("auth_date", 0))) > 300:
        raise HTTPException(403, "Auth data expired")

    tg_id = int(params["id"])
    result = await session.execute(select(User).where(User.telegram_id == tg_id))
    user = result.scalar_one_or_none()

    if not user:
        referred_by_id = None
        pending_ref = request.session.get("pending_ref")
        if pending_ref:
            ref_result = await session.execute(select(User).where(User.referral_code == pending_ref))
            ref_user = ref_result.scalar_one_or_none()
            if ref_user and ref_user.telegram_id != tg_id:
                referred_by_id = ref_user.id
            request.session.pop("pending_ref", None)

        user = User(
            telegram_id=tg_id,
            telegram_username=params.get("username"),
            is_admin=tg_id in settings.admin_ids,
            referred_by_id=referred_by_id,
        )
        session.add(user)
    else:
        user.telegram_username = params.get("username")
        user.last_seen = datetime.utcnow()
        user.is_admin = tg_id in settings.admin_ids

    await session.commit()
    await session.refresh(user)

    request.session["user_id"] = user.id
    return RedirectResponse("/dashboard")


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/auth/login")
