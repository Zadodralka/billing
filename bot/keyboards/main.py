from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from core.config import settings


def terms_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура принятия правил при первом запуске"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принимаю правила", callback_data="accept_terms")],
    ])


def main_menu() -> InlineKeyboardMarkup:
    """Главное меню бота"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💳 Купить подписку", callback_data="menu:buy"),
            InlineKeyboardButton(text="📋 Мои подписки", callback_data="menu:subs"),
        ],
        [
            InlineKeyboardButton(text="🌐 Личный кабинет", url=settings.webapp_url),
            InlineKeyboardButton(text="📖 Инструкции", url=f"{settings.webapp_url}/docs"),
        ],
        [
            InlineKeyboardButton(text="💬 Поддержка", callback_data="support:menu"),
        ],
    ])


def payment_keyboard(payment_url: str, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить через ЮМани", url=payment_url)],
        [InlineKeyboardButton(text="✅ Я оплатил(а)", callback_data=f"check_payment:{label}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Главное меню", callback_data="menu:main")],
    ])
