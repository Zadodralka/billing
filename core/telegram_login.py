"""
Вход/привязка Telegram через бота вместо JS-виджета.

Идея: сайт создаёт разовый токен и отдаёт диплинк вида t.me/<bot>?start=tglogin_<token>.
Пользователь открывает Telegram (он там уже авторизован - вводить номер телефона негде и незачем)
и жмёт Start. Бот получает /start tglogin_<token> - Telegram сам сообщает боту telegram_id
отправителя, это авторитетные данные от самого Telegram API, подделать нельзя. Бот помечает
токен подтверждённым в Redis, а страница на сайте (которая всё это время опрашивает статус)
подхватывает подтверждение и логинит пользователя - без номера телефона и без виджета.

Тот же механизм используется для привязки Telegram к уже существующему (email-) аккаунту:
purpose="link" + user_id текущей сессии вместо purpose="login".
"""
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
import redis.asyncio as aioredis
from core.config import settings

_redis_client = None

KEY_PREFIX = "tglogin:"
TOKEN_TTL_SECONDS = 600          # 10 минут на то, чтобы открыть Telegram и нажать Start
CONFIRMED_TTL_SECONDS = 120      # после подтверждения - недолгий запас, чтобы веб успел забрать статус


def get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


async def create_token(purpose: str, user_id: int | None = None) -> str:
    """purpose: 'login' - свежий вход, 'link' - привязка Telegram к текущему аккаунту (user_id обязателен)."""
    token = secrets.token_urlsafe(24)
    data = {"status": "pending", "purpose": purpose}
    if user_id is not None:
        data["user_id"] = user_id
    await get_redis().set(KEY_PREFIX + token, json.dumps(data), ex=TOKEN_TTL_SECONDS)
    return token


async def get_token_data(token: str) -> dict | None:
    raw = await get_redis().get(KEY_PREFIX + token)
    return json.loads(raw) if raw else None


async def confirm_token(token: str, telegram_id: int, username: str | None, first_name: str | None) -> dict | None:
    """Вызывается ботом при получении /start <token>. None - токен не найден/истёк/уже подтверждён."""
    r = get_redis()
    key = KEY_PREFIX + token
    raw = await r.get(key)
    if not raw:
        return None
    data = json.loads(raw)
    if data.get("status") != "pending":
        return None  # уже подтверждён ранее - не даём повторно подтвердить другим telegram_id
    data.update(status="confirmed", telegram_id=telegram_id, username=username, first_name=first_name)
    await r.set(key, json.dumps(data), ex=CONFIRMED_TTL_SECONDS)
    return data


async def consume_token(token: str) -> None:
    """Удаляет токен сразу после того, как веб забрал подтверждённый статус - защита от повторного использования."""
    await get_redis().delete(KEY_PREFIX + token)


# ───────────── Нативный вход из Telegram Mini App (initData) ─────────────

WEBAPP_INITDATA_MAX_AGE_SECONDS = 6 * 3600  # initData генерируется при каждом открытии mini app


def validate_webapp_init_data(init_data: str, max_age_seconds: int = WEBAPP_INITDATA_MAX_AGE_SECONDS) -> dict | None:
    """
    Проверяет initData из Telegram Mini App (Telegram.WebApp.initData) по
    официальному алгоритму: HMAC-SHA256 всех полей ключом, производным от
    токена бота. Подделать подпись, не зная BOT_TOKEN, нельзя - поэтому
    прошедшим проверку данным можно доверять как самому Telegram.

    Возвращает {"telegram_id": int, "username": str|None} или None, если
    подпись не сошлась / данные протухли / формат неожиданный.

    Диплинк-вход через бота (create_token/confirm_token выше) остаётся для
    обычных браузеров; этот путь - для mini app, где переход в чат бота
    закрывает само приложение и старый флоу физически не может завершиться.
    """
    if not init_data:
        return None
    try:
        pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    except ValueError:
        return None

    received_hash = None
    data_pairs = []
    for key, value in pairs:
        if key == "hash":
            received_hash = value
        else:
            data_pairs.append((key, value))
    if not received_hash:
        return None

    # data_check_string: пары key=value, отсортированные по ключу, через \n
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data_pairs))
    secret_key = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    fields = dict(data_pairs)
    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError:
        return None
    if auth_date <= 0 or time.time() - auth_date > max_age_seconds:
        return None  # защита от replay старой скопированной initData

    try:
        user = json.loads(fields.get("user", ""))
        telegram_id = int(user["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None

    return {"telegram_id": telegram_id, "username": user.get("username")}
