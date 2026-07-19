"""
Фикстуры тестов. Тесты гоняются на НАСТОЯЩЕМ Postgres (не sqlite): в проде
asyncpg+Postgres, и подмена диалекта в тестах регулярно прячет реальные баги
(поведение enum, timestamp, ON DELETE). Перед запуском нужен доступный сервер:

  TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/billing_test pytest

Если TEST_DATABASE_URL не задан, тесты со свежей схемой пропускаются целиком,
а не падают - чтобы pytest можно было запускать и без поднятой БД (например,
на будущие чисто-юнитовые тесты).

Обязательные env-переменные приложения (BOT_TOKEN и т.п.) подставляются
фейками до первого импорта core.config - тестам не нужны реальные секреты.
"""
import os

# Все обязательные настройки - до любых импортов приложения (core.config
# инстанцирует Settings() на уровне модуля).
os.environ.setdefault("BOT_TOKEN", "123:test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-0000")
os.environ.setdefault("YOOMONEY_RECEIVER", "1")
os.environ.setdefault("YOOMONEY_SECRET", "test")
os.environ.setdefault("REMNAWAVE_URL", "http://remnawave.invalid")
os.environ.setdefault("REMNAWAVE_TOKEN", "test")
os.environ.setdefault("WEBAPP_URL", "https://test.invalid")

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "")
os.environ.setdefault("DATABASE_URL", TEST_DB_URL or "postgresql+asyncpg://invalid:invalid@localhost/invalid")

import pytest
import pytest_asyncio

if TEST_DB_URL:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


def pytest_collection_modifyitems(config, items):
    if TEST_DB_URL:
        return
    skip_db = pytest.mark.skip(reason="TEST_DATABASE_URL не задан - тесты с БД пропущены")
    for item in items:
        if "db_session" in getattr(item, "fixturenames", []):
            item.add_marker(skip_db)


@pytest_asyncio.fixture()
async def db_engine():
    """Свой engine на каждый тест: разные тесты - разные event loop'ы у
    pytest-asyncio, а engine с пулом соединений привязывается к loop'у."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    from core.database import Base
    import core.models  # noqa: F401 - регистрирует таблицы в metadata
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture()
async def db_session(db_engine):
    """Чистая БД + сессия. TRUNCATE перед тестом, а не после - упавший тест
    оставляет данные для разбора, а следующий всё равно начинает с чистого."""
    async with db_engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE support_messages, support_tickets, balance_transactions, "
            "promo_code_usages, promo_codes, gift_codes, payments, subscriptions, "
            "email_tokens, users RESTART IDENTITY CASCADE"
        ))
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        yield session
