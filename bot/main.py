import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import ErrorEvent
from core.config import settings
from core.database import init_db
from bot.middlewares.db import DbSessionMiddleware
from bot.middlewares.auth import AuthMiddleware
from bot.handlers import start, subscriptions, payments
from bot.handlers import support_user, support_admin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def on_error(event: ErrorEvent):
    """
    Без глобального обработчика любое необработанное исключение в хендлере
    (падение парсинга HTML, гонка в БД и т.п.) просто логировалось библиотекой
    молча для пользователя - он видел зависший спиннер и тишину. Здесь логируем
    подробно и мягко уведомляем пользователя, чтобы диалог не "зависал".
    """
    logger.exception(
        f"Unhandled exception while processing update {event.update.update_id}: {event.exception}"
    )
    try:
        update = event.update
        text = "⚠️ Произошла ошибка. Попробуйте ещё раз или напишите в поддержку."
        if update.message:
            await update.message.answer(text)
        elif update.callback_query:
            await update.callback_query.answer(text, show_alert=True)
    except Exception as notify_err:
        logger.warning(f"Failed to notify user about error: {notify_err}")
    return True


async def main():
    await init_db()

    storage = RedisStorage.from_url(settings.redis_url)
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=storage)

    dp.errors.register(on_error)

    # Middlewares (порядок важен)
    dp.update.middleware(DbSessionMiddleware())
    dp.update.middleware(AuthMiddleware())

    # Routers — порядок важен: admin раньше user,
    # чтобы admin_ticket:* коллбэки не перехватывались ticket:* обработчиком
    dp.include_router(start.router)
    dp.include_router(support_admin.router)
    dp.include_router(support_user.router)
    dp.include_router(subscriptions.router)
    dp.include_router(payments.router)

    logger.info("Bot started")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
