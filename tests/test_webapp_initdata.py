"""Тесты валидации initData из Telegram Mini App (core/telegram_login.py).
Чисто криптографические - БД не нужна, работают и без TEST_DATABASE_URL."""
import hashlib
import hmac
import json
import time
import urllib.parse

from core.config import settings
from core.telegram_login import validate_webapp_init_data


def _sign_init_data(fields: dict, bot_token: str) -> str:
    """Собирает initData ровно так, как это делает Telegram: подписывает пары
    ключ=значение HMAC-ом от токена бота и добавляет hash."""
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode({**fields, "hash": calc_hash})


def _valid_fields(auth_date: int | None = None) -> dict:
    return {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAtest",
        "user": json.dumps({"id": 42, "first_name": "Иван", "username": "ivan_petrov"}),
    }


def test_valid_init_data_accepted():
    init_data = _sign_init_data(_valid_fields(), settings.bot_token)
    result = validate_webapp_init_data(init_data)
    assert result is not None
    assert result["telegram_id"] == 42
    assert result["username"] == "ivan_petrov"


def test_tampered_user_rejected():
    """Подмена telegram_id после подписания должна ломать подпись."""
    init_data = _sign_init_data(_valid_fields(), settings.bot_token)
    # Разбираем подписанную строку, меняем поле user, собираем обратно
    # с тем же (теперь уже неправильным для новых данных) hash
    pairs = dict(urllib.parse.parse_qsl(init_data))
    pairs["user"] = json.dumps({"id": 999, "first_name": "Хакер"})
    tampered = urllib.parse.urlencode(pairs)
    assert dict(urllib.parse.parse_qsl(tampered))["user"] != _valid_fields()["user"]
    assert validate_webapp_init_data(tampered) is None


def test_wrong_bot_token_rejected():
    """initData, подписанный ЧУЖИМ ботом, не должен приниматься."""
    init_data = _sign_init_data(_valid_fields(), "999:another-bot-token")
    assert validate_webapp_init_data(init_data) is None


def test_stale_auth_date_rejected():
    """Replay-защита: скопированный старый initData отклоняется по возрасту."""
    init_data = _sign_init_data(_valid_fields(auth_date=int(time.time()) - 7 * 3600), settings.bot_token)
    assert validate_webapp_init_data(init_data) is None


def test_missing_hash_rejected():
    fields = _valid_fields()
    assert validate_webapp_init_data(urllib.parse.urlencode(fields)) is None


def test_empty_and_garbage_rejected():
    assert validate_webapp_init_data("") is None
    assert validate_webapp_init_data("not=really&init=data&hash=deadbeef") is None


def test_user_without_id_rejected():
    fields = _valid_fields()
    fields["user"] = json.dumps({"first_name": "Без ID"})
    init_data = _sign_init_data(fields, settings.bot_token)
    assert validate_webapp_init_data(init_data) is None
