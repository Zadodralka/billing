import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from core.config import settings
from core.database import init_db
from bot.middlewares.db import DbSessionMiddleware
from bot.middlewares.auth import AuthMiddleware
from bot.handlers import start, subscriptions, payments

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    await init_db()

    storage = RedisStorage.from_url(settings.redis_url)
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=storage)

    # Middlewares (порядок важен)
    dp.update.middleware(DbSessionMiddleware())
    dp.update.middleware(AuthMiddleware())

    # Routers
    dp.include_router(start.router)
    dp.include_router(subscriptions.router)
    dp.include_router(payments.router)

    logger.info("Bot started")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
