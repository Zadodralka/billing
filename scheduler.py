"""
Планировщик: обрабатывает истёкшие подписки.

Логика:
1. Подписка истекла (expires_at <= now, статус ACTIVE) -> блокируем доступ в Remnawave
   немедленно, статус в нашей БД меняем на EXPIRED.
2. Подписка истекла более N дней назад (DELETE_AFTER_DAYS) -> полностью удаляем
   VPN-аккаунт из Remnawave (запись о подписке в нашей БД остаётся для истории).

Каждая подписка имеет свой независимый VPN-аккаунт (sub.remnawave_sub_id),
поэтому блокировка/удаление одной подписки не затрагивает остальные подписки пользователя.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import select
from core.database import AsyncSessionLocal, init_db
from core.models import Subscription, SubscriptionStatus, User
from core.remnawave import remnawave
from core.config import settings
from aiogram import Bot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DELETE_AFTER_DAYS = 7  # сколько дней хранить заблокированный аккаунт перед полным удалением


async def disable_expired_subscriptions():
    """Шаг 1: блокирует доступ для только что истёкших подписок"""
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

            if sub.remnawave_sub_id:
                try:
                    await remnawave.disable_user(sub.remnawave_sub_id)
                    logger.info(f"Disabled Remnawave access for expired subscription {sub.id}")
                except Exception as e:
                    logger.error(f"Failed to disable remnawave user for sub {sub.id} ({sub.remnawave_sub_id}): {e}")
            else:
                logger.warning(f"Subscription {sub.id} has no remnawave_sub_id, nothing to disable")

            # Уведомление в Telegram (если есть)
            result = await session.execute(select(User).where(User.id == sub.user_id))
            user = result.scalar_one_or_none()
            if user and user.telegram_id:
                try:
                    bot = Bot(token=settings.bot_token)
                    await bot.send_message(
                        user.telegram_id,
                        "⚠️ <b>Подписка истекла</b>\n\n"
                        "Доступ к VPN заблокирован.\n"
                        "Зайдите в личный кабинет, чтобы продлить подписку.",
                        parse_mode="HTML",
                    )
                    await bot.session.close()
                except Exception as e:
                    logger.warning(f"Failed to notify user {user.telegram_id}: {e}")

        if expired:
            await session.commit()
            logger.info(f"Disabled {len(expired)} expired subscription(s)")


async def delete_old_expired_accounts():
    """Шаг 2: полностью удаляет аккаунты, истёкшие более DELETE_AFTER_DAYS дней назад"""
    async with AsyncSessionLocal() as session:
        cutoff = datetime.utcnow() - timedelta(days=DELETE_AFTER_DAYS)
        result = await session.execute(
            select(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.EXPIRED,
                Subscription.expires_at <= cutoff,
                Subscription.remnawave_sub_id.is_not(None),
            )
        )
        to_delete = result.scalars().all()

        deleted_count = 0
        for sub in to_delete:
            try:
                await remnawave.delete_user(sub.remnawave_sub_id)
                logger.info(f"Deleted Remnawave account for long-expired subscription {sub.id}")
                sub.remnawave_sub_id = None  # отмечаем что аккаунт удалён, запись о подписке остаётся
                deleted_count += 1
            except Exception as e:
                logger.error(f"Failed to delete remnawave account for sub {sub.id}: {e}")

        if deleted_count:
            await session.commit()
            logger.info(f"Permanently deleted {deleted_count} Remnawave account(s) (expired >{DELETE_AFTER_DAYS}d ago)")


async def run_cycle():
    try:
        await disable_expired_subscriptions()
    except Exception as e:
        logger.error(f"disable_expired_subscriptions failed: {e}")

    try:
        await delete_old_expired_accounts()
    except Exception as e:
        logger.error(f"delete_old_expired_accounts failed: {e}")


async def main():
    await init_db()
    logger.info("Scheduler started")
    while True:
        await run_cycle()
        await asyncio.sleep(3600)  # каждый час


if __name__ == "__main__":
    asyncio.run(main())
