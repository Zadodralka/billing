"""
send_telegram_to_admins должна слать: (а) одним сообщением в группу с нужным
message_thread_id, если settings.admin_group_chat_id настроен; (б) иначе -
как раньше, личным сообщением каждому из settings.admin_ids.
"""
from unittest.mock import AsyncMock, patch

import pytest

from core.config import settings
import core.notify as notify_module


@pytest.fixture(autouse=True)
def _reset_admin_settings():
    """settings - модульный синглтон, мутируем поля прямо на нём и
    возвращаем как было, чтобы тесты не подтекали друг в друга."""
    saved = {
        "admin_group_chat_id": settings.admin_group_chat_id,
        "admin_topic_payments": settings.admin_topic_payments,
        "admin_topic_support": settings.admin_topic_support,
        "admin_ids": settings.admin_ids,
    }
    yield
    for k, v in saved.items():
        setattr(settings, k, v)


@pytest.mark.asyncio
async def test_group_configured_sends_one_message_with_topic_thread_id():
    settings.admin_group_chat_id = -1001234567890
    settings.admin_topic_payments = 45
    settings.admin_ids = [111, 222]  # не должны использоваться, раз группа настроена

    mock_bot = AsyncMock()
    mock_bot.session.close = AsyncMock()
    with patch.object(notify_module, "Bot", return_value=mock_bot):
        delivered = await notify_module.send_telegram_to_admins("тест", topic="payments")

    assert delivered == 1
    mock_bot.send_message.assert_awaited_once()
    _, kwargs = mock_bot.send_message.call_args
    assert kwargs["message_thread_id"] == 45
    assert mock_bot.send_message.call_args[0][0] == -1001234567890


@pytest.mark.asyncio
async def test_group_configured_but_topic_not_set_sends_without_thread_id():
    settings.admin_group_chat_id = -1001234567890
    settings.admin_topic_support = None  # тема для этой категории не заведена

    mock_bot = AsyncMock()
    mock_bot.session.close = AsyncMock()
    with patch.object(notify_module, "Bot", return_value=mock_bot):
        delivered = await notify_module.send_telegram_to_admins("тест", topic="support")

    assert delivered == 1
    _, kwargs = mock_bot.send_message.call_args
    assert kwargs["message_thread_id"] is None


@pytest.mark.asyncio
async def test_no_group_falls_back_to_per_admin_dm():
    settings.admin_group_chat_id = None
    settings.admin_ids = [111, 222, 333]

    mock_bot = AsyncMock()
    mock_bot.session.close = AsyncMock()
    with patch.object(notify_module, "Bot", return_value=mock_bot):
        delivered = await notify_module.send_telegram_to_admins("тест", topic="payments")

    assert delivered == 3
    assert mock_bot.send_message.await_count == 3
    called_chat_ids = [c.args[0] for c in mock_bot.send_message.call_args_list]
    assert called_chat_ids == [111, 222, 333]


@pytest.mark.asyncio
async def test_no_group_and_no_admins_delivers_nothing():
    settings.admin_group_chat_id = None
    settings.admin_ids = []

    mock_bot = AsyncMock()
    with patch.object(notify_module, "Bot", return_value=mock_bot):
        delivered = await notify_module.send_telegram_to_admins("тест")

    assert delivered == 0
    mock_bot.send_message.assert_not_awaited()
