from html import escape
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from core.models import User
from core.config import settings
from core.telegram_login import confirm_token
from bot.keyboards.main import terms_keyboard, terms_keyboard_for_login, main_menu, back_to_menu

router = Router()


def _is_admin(user: User) -> bool:
    return user.telegram_id in settings.admin_ids

_bot_username_cache: str | None = None


async def _get_bot_username(bot: Bot) -> str:
    global _bot_username_cache
    if _bot_username_cache is None:
        me = await bot.get_me()
        _bot_username_cache = me.username
    return _bot_username_cache

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

Быстрый и стабильный VPN по подписке: оплата картой прямо в этом чате, конфиг для
подключения приходит сразу после оплаты — никаких дополнительных ссылок и писем.

Выберите действие в меню ниже:
""".strip()


# ===== Вход/привязка через бота (диплинк t.me/<bot>?start=tglogin_XXXX) =====
async def _confirm_login_token(token: str, tg_user, reply_target: Message):
    """Подтверждает токен входа/привязки в Redis и отвечает пользователю. Вызывается либо сразу
    (если правила уже приняты раньше), либо после нажатия "Принимаю правила" для этого токена.

    tg_user - именно aiogram-объект человека, которого подтверждаем (message.from_user для
    обычного /start, но callback.from_user для колбэка с кнопки!). Если бы функция сама брала
    reply_target.from_user - для колбэков это было бы сообщение, которое ОТПРАВИЛ БОТ, и
    from_user там - сам бот, а не человек, нажавший кнопку. Именно так был баг: после принятия
    правил в кабинет входил аккаунт бота вместо аккаунта пользователя.
    reply_target - Message, на который отвечаем через .answer() (не обязательно от tg_user)."""
    data = await confirm_token(token, tg_user.id, tg_user.username, tg_user.first_name)

    if not data:
        await reply_target.answer(
            "⚠️ Эта ссылка для входа уже недействительна (устарела или уже использована).\n"
            "Вернитесь на сайт и запросите новую."
        )
        return

    if data.get("purpose") == "link":
        await reply_target.answer(
            "✅ <b>Telegram подтверждён!</b>\n\nВернитесь в браузер — привязка завершится автоматически.",
            parse_mode="HTML",
        )
    else:
        await reply_target.answer(
            "✅ <b>Вход подтверждён!</b>\n\nВернитесь в браузер — вы будете авторизованы автоматически.",
            parse_mode="HTML",
        )


async def _handle_login_deeplink(payload: str, message: Message, user: User, session: AsyncSession) -> bool:
    """Возвращает True, если payload был токеном входа/привязки и уже обработан (или отложен
    до принятия правил). Правила обязательны и для входа через бота - без этого через диплинк
    можно было бы попасть в кабинет, ни разу их не увидев."""
    if not payload.startswith("tglogin_"):
        return False

    token = payload[len("tglogin_"):]

    if not user.terms_accepted:
        await message.answer(
            TERMS_TEXT + "\n\n<i>Примите правила, чтобы завершить вход/привязку.</i>",
            parse_mode="HTML",
            reply_markup=terms_keyboard_for_login(token),
        )
        return True

    await _confirm_login_token(token, message.from_user, message)
    return True


# ===== Реферальная ссылка через бота (диплинк t.me/<bot>?start=ref_XXXX) =====
async def _apply_referral_deeplink(payload: str, user: User, session: AsyncSession) -> None:
    """Привязывает пользователя к пригласившему по реферальному коду из /start ref_<code>.
    Бонус начисляется позже, при первой успешной оплате (см. core.promo_referral.process_referral_bonus).
    Не трогаем уже привязанных пользователей - повторный переход по чужой ссылке ничего не меняет."""
    if not payload.startswith("ref_") or user.referred_by_id:
        return

    ref_code = payload[len("ref_"):].upper()
    result = await session.execute(select(User).where(User.referral_code == ref_code))
    referrer = result.scalar_one_or_none()
    if referrer and referrer.id != user.id:
        user.referred_by_id = referrer.id
        await session.commit()


# ===== /start =====
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, user: User, session: AsyncSession):
    if command.args:
        if await _handle_login_deeplink(command.args, message, user, session):
            return
        await _apply_referral_deeplink(command.args, user, session)

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
        reply_markup=main_menu(is_admin=_is_admin(user)),
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
        reply_markup=main_menu(is_admin=_is_admin(user)),
    )
    await callback.answer()


# ===== Принятие правил в контексте входа/привязки через бота =====
@router.callback_query(F.data.startswith("terms_login:"))
async def cb_accept_terms_login(callback: CallbackQuery, user: User, session: AsyncSession):
    token = callback.data.split(":", 1)[1]
    user.terms_accepted = True
    await session.commit()

    await callback.message.edit_text("✅ <b>Правила приняты!</b>", parse_mode="HTML")
    await _confirm_login_token(token, callback.from_user, callback.message)
    await callback.answer()


# ===== Навигация главного меню =====
@router.callback_query(F.data == "menu:main")
async def cb_main_menu(callback: CallbackQuery, user: User):
    await callback.message.edit_text(
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=main_menu(is_admin=_is_admin(user)),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:buy")
async def cb_menu_buy(callback: CallbackQuery, user: User, session: AsyncSession):
    from core.plans import get_active_plans
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    plans = await get_active_plans(session)
    buttons = []
    for key, plan in plans.items():
        # Цена тут "от" - точная сумма зависит от объёма трафика, который
        # выбирается следующим шагом (см. cb_buy_plan_traffic в payments.py)
        buttons.append([InlineKeyboardButton(
            text=f"{plan['name']} — от {plan['price']} ₽",
            callback_data=f"buy_plan:{key}",
        )])
    buttons.append([InlineKeyboardButton(text="← Главное меню", callback_data="menu:main")])

    await callback.message.edit_text(
        "💳 <b>Выберите тарифный план:</b>\n\n"
        "💡 После оплаты конфиг для подключения появится в личном кабинете автоматически.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


# ===== Баланс и реферальная программа =====
@router.callback_query(F.data == "menu:balance")
async def cb_menu_balance(callback: CallbackQuery, user: User, session: AsyncSession, bot: Bot):
    from core.promo_referral import ensure_referral_code

    ref_code = await ensure_referral_code(user, session)
    bot_username = await _get_bot_username(bot)
    ref_link = f"https://t.me/{bot_username}?start=ref_{ref_code}"

    referrals_count = (await session.execute(
        select(func.count(User.id)).where(User.referred_by_id == user.id)
    )).scalar() or 0
    paid_referrals = (await session.execute(
        select(func.count(User.id)).where(User.referred_by_id == user.id, User.referral_bonus_paid == True)
    )).scalar() or 0

    await callback.message.edit_text(
        f"💰 <b>Баланс: {user.balance} ₽</b>\n"
        "Баланс можно использовать при оплате подписки в личном кабинете на сайте.\n\n"
        "🎁 <b>Реферальная программа</b>\n"
        f"Приглашайте друзей — получайте {settings.referral_bonus_referrer} ₽ на баланс "
        f"за каждого, кто оформит подписку. Друг тоже получит {settings.referral_bonus_referred} ₽.\n\n"
        f"👥 Приглашено: {referrals_count} (оплатили: {paid_referrals})\n\n"
        f"🔗 Ваша реферальная ссылка:\n<code>{ref_link}</code>",
        parse_mode="HTML",
        reply_markup=back_to_menu(),
        disable_web_page_preview=True,
    )
    await callback.answer()


# ===== /help =====
HELP_TEXT = """
ℹ️ <b>Что умеет этот бот</b>

/start — открыть главное меню
/help — показать эту справку

💳 <b>Купить подписку</b> — выбрать тариф, объём трафика и оплатить через ЮMoney
📋 <b>Мои подписки</b> — даты, расход трафика, продление и конфиг каждой подписки
🔑 <b>QR-код подключения</b> — быстрый доступ к QR-коду и ссылке без лишних деталей
💰 <b>Баланс и бонусы</b> — баланс и реферальная ссылка
💬 <b>Поддержка</b> — задать вопрос, ответим в чате

Полное управление подпиской (включая оплату балансом и промокоды) — в личном кабинете на сайте.
""".strip()


@router.message(Command("help"))
async def cmd_help(message: Message, user: User):
    await message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=main_menu(is_admin=_is_admin(user)))
