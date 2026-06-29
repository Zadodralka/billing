from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from core.models import User
from bot.keyboards.main import main_menu, webapp_keyboard

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, user: User):
    name = message.from_user.first_name or "друг"
    await message.answer(
        f"👋 Привет, {name}!\n\n"
        "🔒 Я помогу тебе купить и настроить VPN-подписку.\n\n"
        "Используй кнопки меню ниже для навигации:",
        reply_markup=main_menu(),
    )


@router.message(F.text == "🌐 Веб-кабинет")
async def cmd_webapp(message: Message):
    await message.answer(
        "🌐 <b>Личный кабинет</b>\n\n"
        "В кабинете вы можете:\n"
        "• Управлять подписками\n"
        "• Скачать конфиги\n"
        "• История платежей\n",
        reply_markup=webapp_keyboard(),
        parse_mode="HTML",
    )


@router.message(F.text == "💬 Поддержка")
async def cmd_support(message: Message):
    await message.answer(
        "💬 <b>Поддержка</b>\n\n"
        "Если у вас возникли вопросы, напишите нам:\n"
        "👉 @your_support_username\n\n"
        "Обычно отвечаем в течение 1-2 часов.",
        parse_mode="HTML",
    )
