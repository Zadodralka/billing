"""Уведомления администраторам о событиях бизнес-значимости (не связанные с поддержкой -
для тикетов см. core.support_notify)."""
import logging
from aiogram import Bot
from core.config import settings

logger = logging.getLogger("admin.notify")


async def notify_admins_new_payment(user_display_name: str, plan_name: str, amount: int, is_gift: bool, is_renew: bool):
    if not settings.admin_ids:
        return

    kind = "🎁 Подарок" if is_gift else ("🔁 Продление" if is_renew else "🆕 Новая подписка")
    message = (
        f"💵 <b>Оплата получена</b>\n\n"
        f"{kind}\n"
        f"👤 {user_display_name}\n"
        f"📦 {plan_name}\n"
        f"💰 {amount} ₽"
    )

    try:
        bot = Bot(token=settings.bot_token)
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(admin_id, message, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"Could not notify admin {admin_id} about payment: {e}")
        await bot.session.close()
    except Exception as e:
        logger.error(f"notify_admins_new_payment failed: {e}")
