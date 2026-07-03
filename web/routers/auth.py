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
from core.version import APP_VERSION
from core.telegram_login import create_token as create_tg_login_token, get_token_data as get_tg_login_data, consume_token as consume_tg_login_token

logger = logging.getLogger("auth")

router = APIRouter(prefix="/auth")
templates = Jinja2Templates(directory="web/templates")
templates.env.globals["app_version"] = APP_VERSION

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

    if email_token.purpose == "link":
        # Привязка email к уже существующему (обычно Telegram-) аккаунту, а не обычный вход
        target_result = await session.execute(select(User).where(User.id == email_token.link_user_id))
        target_user = target_result.scalar_one_or_none()
        if not target_user:
            await session.commit()
            return templates.TemplateResponse(request, "login.html", {
                "error": "Аккаунт для привязки email не найден.",
                "bot_username": bot_username,
                "webapp_url": settings.webapp_url,
            })

        conflict = await session.execute(
            select(User).where(User.email == email_token.email, User.id != target_user.id)
        )
        if conflict.scalar_one_or_none():
            await session.commit()
            return templates.TemplateResponse(request, "login.html", {
                "error": "Этот email уже привязан к другому аккаунту.",
                "bot_username": bot_username,
                "webapp_url": settings.webapp_url,
            })

        target_user.email = email_token.email
        target_user.last_seen = datetime.utcnow()
        await session.commit()

        request.session["user_id"] = target_user.id
        return RedirectResponse("/dashboard?linked=email")

    result = await session.execute(select(User).where(User.email == email_token.email))
    user = result.scalar_one_or_none()
    if not user:
        return RedirectResponse("/auth/login")

    user.last_seen = datetime.utcnow()
    await session.commit()

    request.session["user_id"] = user.id
    return RedirectResponse("/dashboard")


async def _find_or_create_telegram_user(
    tg_id: int, username: str | None, request: Request, session: AsyncSession
) -> User:
    """Общая логика для входа через JS-виджет и через диплинк в бота: находит пользователя
    по telegram_id, применяет отложенный реферальный код (pending_ref) при первой регистрации,
    синхронизирует is_admin с ADMIN_IDS.

    Реферальный код применяется не только при создании новой записи, но и если она уже
    существует, но ещё без referred_by_id: при входе через бот-диплинк пользователь сначала
    пишет боту /start, и AuthMiddleware бота успевает молча создать голую запись User ещё
    до того, как веб дойдёт до этой функции - иначе pending_ref из сессии браузера потерялся бы."""
    result = await session.execute(select(User).where(User.telegram_id == tg_id))
    user = result.scalar_one_or_none()

    if not user:
        user = User(telegram_id=tg_id, telegram_username=username, is_admin=tg_id in settings.admin_ids)
        session.add(user)
    else:
        user.telegram_username = username
        user.last_seen = datetime.utcnow()
        user.is_admin = tg_id in settings.admin_ids

    pending_ref = request.session.pop("pending_ref", None)
    if pending_ref and not user.referred_by_id:
        ref_result = await session.execute(select(User).where(User.referral_code == pending_ref))
        ref_user = ref_result.scalar_one_or_none()
        if ref_user and ref_user.telegram_id != tg_id:
            user.referred_by_id = ref_user.id

    await session.commit()
    await session.refresh(user)
    return user


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
    user = await _find_or_create_telegram_user(tg_id, params.get("username"), request, session)

    request.session["user_id"] = user.id
    return RedirectResponse("/dashboard")


@router.post("/telegram-login/start")
async def telegram_login_start():
    """Вход через бота вместо JS-виджета: не спрашивает номер телефона, т.к. авторизация
    целиком проходит через уже залогиненный Telegram-клиент пользователя (см. core/telegram_login.py)."""
    bot_username = await get_bot_username()
    if not bot_username:
        raise HTTPException(503, "Бот временно недоступен, попробуйте позже")
    token = await create_tg_login_token(purpose="login")
    return {"deep_link": f"https://t.me/{bot_username}?start=tglogin_{token}"}


@router.get("/telegram-login/status/{token}")
async def telegram_login_status(token: str, request: Request, session: AsyncSession = Depends(get_db)):
    data = await get_tg_login_data(token)
    if not data:
        return {"status": "expired"}
    if data.get("status") != "confirmed":
        return {"status": "pending"}

    user = await _find_or_create_telegram_user(data["telegram_id"], data.get("username"), request, session)
    await consume_tg_login_token(token)
    request.session["user_id"] = user.id
    return {"status": "confirmed", "redirect": "/dashboard"}


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/auth/login")
