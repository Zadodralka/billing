from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def support_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Создать обращение", callback_data="support:create")],
        [InlineKeyboardButton(text="📋 Мои обращения", callback_data="support:list")],
        [InlineKeyboardButton(text="← Главное меню", callback_data="menu:main")],
    ])


def ticket_list_keyboard(tickets: list) -> InlineKeyboardMarkup:
    buttons = []
    for t in tickets:
        status_icon = {"open": "🔴", "answered": "🟢", "closed": "⚫"}.get(t.status.value, "⚪")
        label = t.subject[:30] + ("…" if len(t.subject) > 30 else "")
        buttons.append([InlineKeyboardButton(
            text=f"{status_icon} {label}",
            callback_data=f"ticket:view:{t.id}",
        )])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="support:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def ticket_view_keyboard(ticket_id: int, status: str) -> InlineKeyboardMarkup:
    buttons = []
    if status != "closed":
        buttons.append([InlineKeyboardButton(text="✉️ Ответить", callback_data=f"ticket:reply:{ticket_id}")])
        buttons.append([InlineKeyboardButton(text="✅ Закрыть тикет", callback_data=f"ticket:close:{ticket_id}")])
    else:
        buttons.append([InlineKeyboardButton(text="🔄 Открыть снова", callback_data=f"ticket:reply:{ticket_id}")])
    buttons.append([InlineKeyboardButton(text="← К списку", callback_data="support:list")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def cancel_keyboard(back_cb: str = "support:menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=back_cb)],
    ])


def admin_ticket_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Ответить", callback_data=f"admin_ticket:reply:{ticket_id}")],
        [InlineKeyboardButton(text="✅ Закрыть", callback_data=f"admin_ticket:close:{ticket_id}")],
        [InlineKeyboardButton(text="📋 Все открытые", callback_data="admin_ticket:list")],
    ])


def admin_reply_sent_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Ответить ещё", callback_data=f"admin_ticket:reply:{ticket_id}")],
        [InlineKeyboardButton(text="✅ Закрыть тикет", callback_data=f"admin_ticket:close:{ticket_id}")],
        [InlineKeyboardButton(text="📋 Все открытые", callback_data="admin_ticket:list")],
    ])
