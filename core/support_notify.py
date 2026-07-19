"""Утилиты для уведомлений о тикетах поддержки через Telegram и email"""
import logging
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from core.models import User
from core.notify import send_telegram, send_telegram_to_admins

logger = logging.getLogger("support.notify")


def _admin_ticket_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Ответить", callback_data=f"admin_ticket:reply:{ticket_id}")],
        [InlineKeyboardButton(text="👁 Открыть тикет", callback_data=f"admin_ticket:view:{ticket_id}")],
        [InlineKeyboardButton(text="✅ Закрыть", callback_data=f"admin_ticket:close:{ticket_id}")],
    ])


def _user_reply_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Ответить", callback_data=f"ticket:reply:{ticket_id}")],
        [InlineKeyboardButton(text="👁 Открыть тикет", callback_data=f"ticket:view:{ticket_id}")],
    ])


async def notify_admins_new_message(ticket_id: int, subject: str, user_display_name: str, text_preview: str):
    """Уведомляет всех админов о новом тикете/сообщении от пользователя"""
    await send_telegram_to_admins(
        f"🎫 <b>Новое сообщение — #{ticket_id}</b>\n\n"
        f"👤 От: {user_display_name}\n"
        f"📋 Тема: {subject}\n\n"
        f"«{text_preview[:300]}»",
        reply_markup=_admin_ticket_keyboard(ticket_id),
    )


async def notify_user_admin_replied(user: User, ticket_id: int, subject: str, text_preview: str):
    """Уведомляет пользователя об ответе администратора - в Telegram (если привязан)
    и на email (если есть), независимо друг от друга: у пользователя может быть
    привязан только один из способов входа, а уведомление важно не пропустить."""
    if user.telegram_id:
        await send_telegram(
            user.telegram_id,
            f"💬 <b>Ответ по тикету #{ticket_id}</b>\n\n"
            f"📋 {subject}\n\n"
            f"«{text_preview[:400]}»",
            reply_markup=_user_reply_keyboard(ticket_id),
        )

    if user.email:
        try:
            from core.email import send_ticket_reply_email
            await send_ticket_reply_email(user.email, ticket_id, subject, text_preview)
        except Exception as e:
            logger.warning(f"notify_user_admin_replied (email) failed for {user.email}: {e}")
