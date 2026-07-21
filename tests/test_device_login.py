"""Тесты одноразовых кодов входа на другом устройстве (core/device_login.py).
Redis подменён минимальным фейком - тестам не нужен настоящий сервер."""
import pytest
from unittest.mock import patch

from core import device_login
from core.device_login import (
    normalize_code, format_code, create_device_code, consume_device_code,
    CODE_ALPHABET, CODE_LENGTH,
)

pytestmark = pytest.mark.asyncio


class FakeRedis:
    """Ровно те два вызова, что использует device_login: set(ex=) и getdel."""
    def __init__(self):
        self.store = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def getdel(self, key):
        return self.store.pop(key, None)


@pytest.fixture()
def fake_redis():
    fr = FakeRedis()
    with patch.object(device_login, "get_redis", lambda: fr):
        yield fr


def test_normalize_strips_spaces_dashes_and_uppercases():
    assert normalize_code("  ab2f-k9p7 ") == "AB2FK9P7"
    assert normalize_code("AB2F K9P7") == "AB2FK9P7"


def test_format_code_readable():
    assert format_code("AB2FK9P7") == "AB2F-K9P7"


async def test_create_and_consume_roundtrip(fake_redis):
    code = await create_device_code(123)
    assert len(code) == CODE_LENGTH
    assert all(c in CODE_ALPHABET for c in code)
    assert await consume_device_code(code) == 123


async def test_code_is_single_use(fake_redis):
    code = await create_device_code(123)
    assert await consume_device_code(code) == 123
    assert await consume_device_code(code) is None


async def test_dirty_input_accepted(fake_redis):
    code = await create_device_code(55)
    dirty = " " + format_code(code).lower() + " "
    assert await consume_device_code(dirty) == 55


async def test_unknown_and_malformed_rejected(fake_redis):
    assert await consume_device_code("AAAAAAAA") is None       # валидный формат, нет в хранилище
    assert await consume_device_code("short") is None          # не та длина
    assert await consume_device_code("AAAA-AA0O") is None      # символы вне алфавита (0/O исключены)
    assert await consume_device_code("") is None


async def test_malformed_code_does_not_touch_redis(fake_redis):
    """Кривой ввод отсекается ещё до похода в Redis - мусор не генерирует запросов."""
    calls = []
    original = fake_redis.getdel
    async def counting_getdel(key):
        calls.append(key)
        return await original(key)
    fake_redis.getdel = counting_getdel
    await consume_device_code("явно не код!")
    assert calls == []
