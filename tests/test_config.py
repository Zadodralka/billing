"""
core.config.settings инстанцируется один раз при импорте модуля - поэтому
поведение парсинга ADMIN_IDS (и связанное с ним поведение при разных env)
тестируется через отдельные подпроцессы, а не переимпортом в этом же
процессе (модуль всё равно останется закэширован).

Регрессия: ADMIN_IDS с несколькими ID через запятую (ровно то, что описано в
.env.example) валила старт приложения - pydantic-settings считает List[int]
"сложным" типом и пытается сам распарсить значение как JSON ДО вызова наших
field_validator'ов. "1" - валидный JSON (число), поэтому с одним админом
ошибка не проявлялась, а "1,2" - невалидный JSON, и Settings() падала с
SettingsError. Исправлено через Annotated[List[int], NoDecode] в core/config.py.
"""
import os
import subprocess
import sys

_BASE_ENV = {
    "BOT_TOKEN": "123:test",
    "SECRET_KEY": "test-secret-key-that-is-long-enough-0000",
    "YOOMONEY_RECEIVER": "1",
    "YOOMONEY_SECRET": "test",
    "REMNAWAVE_URL": "http://remnawave.invalid",
    "REMNAWAVE_TOKEN": "test",
    "WEBAPP_URL": "https://test.invalid",
    "DATABASE_URL": "postgresql+asyncpg://invalid:invalid@localhost/invalid",
}


def _run_with_env(extra_env: dict) -> subprocess.CompletedProcess:
    env = {**os.environ, **_BASE_ENV, **extra_env}
    return subprocess.run(
        [sys.executable, "-c", "from core.config import settings; print(settings.admin_ids)"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env, capture_output=True, text=True, timeout=30,
    )


def test_single_admin_id_still_works():
    result = _run_with_env({"ADMIN_IDS": "1"})
    assert result.returncode == 0, result.stderr
    assert "[1]" in result.stdout


def test_multiple_admin_ids_comma_separated_does_not_crash():
    """Главный сценарий регрессии - раньше падало с SettingsError."""
    result = _run_with_env({"ADMIN_IDS": "1,2,3"})
    assert result.returncode == 0, result.stderr
    assert "[1, 2, 3]" in result.stdout


def test_admin_ids_with_spaces_around_commas():
    result = _run_with_env({"ADMIN_IDS": "1, 2 ,3"})
    assert result.returncode == 0, result.stderr
    assert "[1, 2, 3]" in result.stdout


def test_empty_admin_group_settings_do_not_crash():
    """ADMIN_GROUP_CHAT_ID/ADMIN_TOPIC_* пустой строкой (как в .env.example) -
    тот же класс бага, что раньше был у SMTP_PORT/SESSION_MAX_AGE."""
    env = {**os.environ, **_BASE_ENV, "ADMIN_GROUP_CHAT_ID": "", "ADMIN_TOPIC_PAYMENTS": ""}
    result = subprocess.run(
        [sys.executable, "-c",
         "from core.config import settings; print(settings.admin_group_chat_id, settings.admin_topic_payments)"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "None None" in result.stdout
