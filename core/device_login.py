"""
Вход на другом устройстве по одноразовому коду.

Сценарий: пользователь уже залогинен на устройстве A (телефон/основной
компьютер). Там он генерирует короткий код и вводит его на устройстве B на
странице входа - устройство B получает сессию того же аккаунта. Ни Telegram,
ни почта на устройстве B не нужны - удобно для рабочих/чужих машин, где не
хочется логиниться в личные сервисы.

Безопасность:
- код создаётся ТОЛЬКО из уже аутентифицированной сессии;
- живёт DEVICE_CODE_TTL_SECONDS и сгорает при первом использовании
  (Redis GETDEL - атомарно, два устройства не могут погасить один код);
- алфавит без неоднозначных символов (0/O, 1/I/L), 8 знаков из 30 - ~39 бит
  энтропии, при 5-минутном TTL и rate-limit на ввод перебор нереален;
- ввод кода лимитируется по IP на стороне роута (см. web/routers/auth.py).
"""
import secrets
import redis.asyncio as aioredis
from core.config import settings

_redis_client = None

KEY_PREFIX = "devicelogin:"
DEVICE_CODE_TTL_SECONDS = 300  # 5 минут - успеть перепечатать код на второй машине

# Без 0/O/1/I/L - код диктуют вслух и перепечатывают вручную, похожие символы = опечатки
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ2345679"
CODE_LENGTH = 8


def get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def normalize_code(raw: str) -> str:
    """Пользовательский ввод -> канонический вид: верхний регистр, без пробелов
    и дефисов (код показывается как XXXX-XXXX для читаемости)."""
    return raw.strip().upper().replace("-", "").replace(" ", "")


def format_code(code: str) -> str:
    """Канонический код -> вид для показа человеку (XXXX-XXXX)."""
    return f"{code[:4]}-{code[4:]}"


async def create_device_code(user_id: int) -> str:
    """Создаёт одноразовый код входа для user_id. Возвращает канонический код.
    Повторная генерация просто создаёт ещё один код - старый доживёт свой TTL
    или сгорит при использовании; хранить "один активный код на пользователя"
    незачем, TTL короткий."""
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    await get_redis().set(KEY_PREFIX + code, str(user_id), ex=DEVICE_CODE_TTL_SECONDS)
    return code


async def consume_device_code(raw_code: str) -> int | None:
    """Гасит код и возвращает user_id, либо None (нет такого / истёк / уже
    использован). GETDEL - атомарно: одновременный ввод одного кода с двух
    устройств выдаст сессию только одному."""
    code = normalize_code(raw_code)
    if len(code) != CODE_LENGTH or any(c not in CODE_ALPHABET for c in code):
        return None
    value = await get_redis().getdel(KEY_PREFIX + code)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
