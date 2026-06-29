from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from core.config import PLANS, settings


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💳 Купить подписку"), KeyboardButton(text="📋 Мои подписки")],
            [KeyboardButton(text="🔑 Мои конфиги"), KeyboardButton(text="🌐 Веб-кабинет")],
            [KeyboardButton(text="💬 Поддержка")],
        ],
        resize_keyboard=True,
    )


def plans_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for key, plan in PLANS.items():
        buttons.append([
            InlineKeyboardButton(
                text=f"{plan['name']} — {plan['price']} ₽",
                callback_data=f"buy:{key}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payment_keyboard(payment_url: str, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить через ЮМани", url=payment_url)],
        [InlineKeyboardButton(text="✅ Я оплатил(а)", callback_data=f"check_payment:{label}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


def webapp_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Открыть личный кабинет", url=settings.webapp_url)],
    ])
