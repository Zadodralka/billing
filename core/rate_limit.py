"""Простой rate-limit поверх Redis: N попыток за период по произвольному ключу.
Используется там, где действие пользователя рассылает уведомления (тикеты
поддержки шлют сообщение всем админам в Telegram) или создаёт записи в БД
(подарки) - без лимита это легко заспамить с одного аккаунта."""
import logging
import redis.asyncio as aioredis
from core.config import settings

logger = logging.getLogger("rate_limit")

_redis_client = None


def get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


async def check_rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    """True - лимит не превышен, действие можно продолжать.
    При недоступности Redis - fail-open (не блокируем), чтобы сбой инфраструктуры
    не превращался в отказ всем пользователям сразу."""
    try:
        r = get_redis()
        full_key = f"ratelimit:{key}"
        attempts = await r.incr(full_key)
        if attempts == 1:
            await r.expire(full_key, window_seconds)
        return attempts <= limit
    except Exception as e:
        logger.warning(f"Rate limit check failed for '{key}' (allowing request): {e}")
        return True
