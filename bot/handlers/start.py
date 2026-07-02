from html import escape
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.models import User
from core.config import settings
from bot.keyboards.main import terms_keyboard, main_menu, back_to_menu

router = Router()

# ===== Текст правил использования =====
TERMS_TEXT = """
🔒 <b>Unlock VPN — Правила использования</b>

Перед началом работы, пожалуйста, ознакомьтесь с правилами:

<b>1. Законность использования</b>
Сервис предназначен исключительно для законных целей. Использование VPN для противоправной деятельности запрещено.

<b>2. Персональный доступ</b>
Подписка оформляется на одного пользователя. Передача конфигурационных файлов третьим лицам не допускается.

<b>3. Трафик</b>
Запрещено использование для автоматизированного парсинга, DDoS-атак, массовых рассылок и других нарушений.

<b>4. Конфиденциальность</b>
Мы не храним логи вашей активности и не передаём данные третьим лицам.

<b>5. Возврат средств</b>
Возврат возможен в течение 24 часов с момента покупки при наличии технических неполадок на нашей стороне.

<b>6. Доступность сервиса</b>
Мы стремимся к работе 24/7, однако не гарантируем бесперебойную работу в форс-мажорных обстоятельствах.

Нажимая кнопку <b>«Принимаю правила»</b>, вы подтверждаете, что ознакомились с условиями и согласны с ними.
""".strip()


WELCOME_TEXT = """
👋 <b>Добро пожаловать в Unlock VPN!</b>

Выберите действие в меню ниже:
""".strip()


# ===== /start =====
@router.message(CommandStart())
async def cmd_start(message: Message, user: User, session: AsyncSession):
    name = escape(message.from_user.first_name or "друг")

    # Новый пользователь или не принял правила — показываем правила
    if not user.terms_accepted:
        await message.answer(
            TERMS_TEXT,
            parse_mode="HTML",
            reply_markup=terms_keyboard(),
        )
        return

    # Уже принял — сразу главное меню
    await message.answer(
        f"👋 С возвращением, {name}!\n\n" + WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


# ===== Принятие правил =====
@router.callback_query(F.data == "accept_terms")
async def cb_accept_terms(callback: CallbackQuery, user: User, session: AsyncSession):
    user.terms_accepted = True
    await session.commit()

    name = escape(callback.from_user.first_name or "друг")
    await callback.message.edit_text(
        f"✅ <b>Правила приняты!</b>\n\n"
        f"👋 Добро пожаловать, {name}!\n\n"
        + WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=main_menu(),
    )
    await callback.answer()


# ===== Навигация главного меню =====
@router.callback_query(F.data == "menu:main")
async def cb_main_menu(callback: CallbackQuery, user: User):
    await callback.message.edit_text(
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=main_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:subs")
async def cb_my_subs(callback: CallbackQuery, user: User, session: AsyncSession):
    from datetime import datetime
    from sqlalchemy.orm import selectinload
    from core.models import SubscriptionStatus

    result = await session.execute(
        select(User).where(User.id == user.id).options(selectinload(User.subscriptions))
    )
    user = result.scalar_one()
    now = datetime.utcnow()

    active = [s for s in user.subscriptions
              if s.status == SubscriptionStatus.ACTIVE
              and (not s.expires_at or s.expires_at > now)]

    if not active:
        await callback.message.edit_text(
            "📋 <b>Мои подписки</b>\n\n"
            "У вас нет активных подписок.\n"
            "Нажмите «Купить подписку» чтобы начать.",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
        await callback.answer()
        return

    from core.plans import get_active_plans
    plans = await get_active_plans(session)

    lines = ["📋 <b>Мои активные подписки:</b>\n"]
    for sub in active:
        plan_name = plans.get(sub.plan_key, {}).get("name", sub.plan_key)
        expires = sub.expires_at.strftime("%d.%m.%Y") if sub.expires_at else "—"
        traffic = "Безлимит" if sub.traffic_gb == 0 else f"{sub.traffic_gb} GB"
        lines.append(f"✅ <b>{plan_name}</b> · {traffic}\n📅 До {expires}")

    lines.append(f"\n🌐 Управление подписками: <a href='{settings.webapp_url}/dashboard'>Личный кабинет</a>")

    await callback.message.edit_text(
        "\n\n".join(lines),
        parse_mode="HTML",
        reply_markup=back_to_menu(),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == "menu:buy")
async def cb_menu_buy(callback: CallbackQuery, user: User, session: AsyncSession):
    from core.plans import get_active_plans
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    plans = await get_active_plans(session)
    buttons = []
    for key, plan in plans.items():
        buttons.append([InlineKeyboardButton(
            text=f"{plan['name']} — {plan['price']} ₽",
            callback_data=f"buy:{key}",
        )])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="menu:main")])

    await callback.message.edit_text(
        "💳 <b>Выберите тарифный план:</b>\n\n"
        "💡 После оплаты конфиг для подключения появится в личном кабинете автоматически.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()
