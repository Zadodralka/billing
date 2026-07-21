from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from core.config import settings


def terms_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура принятия правил при первом запуске"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принимаю правила", callback_data="accept_terms")],
    ])


def terms_keyboard_for_login(token: str) -> InlineKeyboardMarkup:
    """Та же клавиатура принятия правил, но для случая входа/привязки через бот-диплинк -
    после принятия нужно ещё и подтвердить сам токен входа, поэтому callback_data другой."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принимаю правила", callback_data=f"terms_login:{token}")],
    ])


def main_menu(is_admin: bool = False, has_active_sub: bool = False) -> InlineKeyboardMarkup:
    """Главное меню бота. is_admin добавляет отдельную кнопку входа в админ-меню -
    админские команды раньше были доступны только через слэш-команды (/stats,
    /find, /broadcast), теперь то же самое доступно кнопками.

    "Мои подписки" и "QR-код подключения" показываются только при наличии активной
    подписки - без неё там нечего смотреть, а кнопки вели на пустой экран."""
    row1 = [InlineKeyboardButton(text="💳 Купить подписку", callback_data="menu:buy")]
    row2 = []
    if has_active_sub:
        row1.append(InlineKeyboardButton(text="📋 Мои подписки", callback_data="menu:subs"))
        row2.append(InlineKeyboardButton(text="🔑 QR-код подключения", callback_data="menu:configs"))
    row2.append(InlineKeyboardButton(text="💰 Баланс и бонусы", callback_data="menu:balance"))

    buttons = [
        row1,
        row2,
        [
            # web_app, а не url: обычная URL-кнопка открывает сайт во встроенном
            # браузере Telegram БЕЗ initData - автовход не срабатывает, человек
            # упирается в экран логина и не понимает, что делать. Mini App-кнопка
            # открывает кабинет с мгновенной авторизацией через initData
            # (см. /auth/telegram-webapp) - ноль лишних шагов.
            InlineKeyboardButton(text="🌐 Личный кабинет", web_app=WebAppInfo(url=settings.webapp_url)),
            InlineKeyboardButton(text="📖 Инструкции", web_app=WebAppInfo(url=f"{settings.webapp_url}/docs")),
        ],
        [
            InlineKeyboardButton(text="💬 Поддержка", callback_data="support:menu"),
        ],
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton(text="🛡 Админ-панель", callback_data="admin_menu:root")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_menu() -> InlineKeyboardMarkup:
    """Меню админ-функций бота кнопками вместо слэш-команд."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_menu:stats")],
        [InlineKeyboardButton(text="🎫 Тикеты поддержки", callback_data="admin_ticket:list")],
        [InlineKeyboardButton(text="🔍 Найти пользователя", callback_data="admin_menu:find")],
        [InlineKeyboardButton(text="📣 Рассылка", callback_data="admin_menu:broadcast")],
        [InlineKeyboardButton(text="← Главное меню", callback_data="menu:main")],
    ])


def subscription_actions_row(sub_id: int) -> list[InlineKeyboardButton]:
    """Ряд кнопок действий для одной подписки в списке — какая подписка имеется в виду,
    видно по нумерации в тексте сообщения над кнопками (sub_id зашит в callback_data)."""
    return [
        InlineKeyboardButton(text="🔑 Конфиг", callback_data=f"sub:config:{sub_id}"),
        InlineKeyboardButton(text="🔁 Продлить", callback_data=f"sub:renew:{sub_id}"),
    ]


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


def open_webapp_keyboard() -> InlineKeyboardMarkup:
    """Одна Mini App-кнопка "открыть кабинет" - для сообщений подтверждения входа:
    если человек начинал вход из браузера, а вкладка уже умерла (частый случай на
    телефоне - встроенный браузер закрывается при переходе в чат бота), ему не
    нужно ничего искать: тап по кнопке открывает mini app с автовходом."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Открыть личный кабинет", web_app=WebAppInfo(url=settings.webapp_url))],
    ])
