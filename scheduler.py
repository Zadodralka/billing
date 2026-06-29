"""
Планировщик: проверяет истёкшие подписки и отключает пользователей в Remnawave.
Запускается как отдельный процесс.
"""
import asyncio
import logging
from datetime import datetime
from sqlalchemy import select
from core.database import AsyncSessionLocal, init_db
from core.models import Subscription, SubscriptionStatus, User
from core.remnawave import remnawave
from core.config import settings
from aiogram import Bot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def check_expired_subscriptions():
    async with AsyncSessionLocal() as session:
        now = datetime.utcnow()
        result = await session.execute(
            select(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.expires_at <= now,
            )
        )
        expired = result.scalars().all()

        for sub in expired:
            sub.status = SubscriptionStatus.EXPIRED
            result = await session.execute(select(User).where(User.id == sub.user_id))
            user = result.scalar_one_or_none()
            if user and user.remnawave_uuid:
                try:
                    await remnawave.disable_user(user.remnawave_uuid)
                except Exception as e:
                    logger.error(f"Failed to disable remnawave user {user.remnawave_uuid}: {e}")

            # Уведомление в Telegram
            if user and user.telegram_id:
                try:
                    bot = Bot(token=settings.bot_token)
                    await bot.send_message(
                        user.telegram_id,
                        "⚠️ <b>Подписка истекла</b>\n\n"
                        "Ваша VPN-подписка закончилась.\n"
                        "Нажмите /start чтобы продлить её.",
                        parse_mode="HTML",
                    )
                    await bot.session.close()
                except Exception as e:
                    logger.error(f"Failed to notify user {user.telegram_id}: {e}")

        if expired:
            await session.commit()
            logger.info(f"Expired {len(expired)} subscriptions")


async def main():
    await init_db()
    logger.info("Scheduler started")
    while True:
        try:
            await check_expired_subscriptions()
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
        await asyncio.sleep(3600)  # каждый час


if __name__ == "__main__":
    asyncio.run(main())
