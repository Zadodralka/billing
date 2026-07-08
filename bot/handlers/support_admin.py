from html import escape
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from core.models import User, SupportTicket, SupportMessage, TicketStatus
from core.support_notify import notify_user_admin_replied
from core.config import settings
from core.timezone import to_local
from bot.states import AdminReplyToTicket
from bot.keyboards.support import (
    admin_ticket_keyboard, admin_reply_sent_keyboard, cancel_keyboard
)

router = Router()


def _parse_id(callback_data: str, index: int) -> int | None:
    try:
        return int(callback_data.split(":")[index])
    except (IndexError, ValueError):
        return None


def _is_admin(user: User) -> bool:
    return user.telegram_id in settings.admin_ids


def _format_ticket_for_admin(ticket: SupportTicket) -> str:
    status_map = {"open": "🔴 Ожидает ответа", "answered": "🟢 Отвечено", "closed": "⚫ Закрыт"}
    lines = [
        f"🎫 <b>Тикет #{ticket.id}</b>",
        f"👤 {escape(ticket.user.display_name)}",
        f"📋 {escape(ticket.subject)}",
        f"Статус: {status_map.get(ticket.status.value, '?')}",
        "",
    ]
    for msg in (ticket.messages or [])[-5:]:
        who = "🛡 Поддержка" if msg.is_from_admin else "👤 Пользователь"
        lines.append(f"<b>{who}</b> <i>{to_local(msg.created_at).strftime('%d.%m %H:%M')}</i>")
        lines.append(escape(msg.text[:400]) + ("…" if len(msg.text) > 400 else ""))
        lines.append("")
    return "\n".join(lines).strip()


# ===== /tickets — список открытых тикетов =====
@router.message(Command("tickets"))
async def cmd_tickets(message: Message, user: User, session: AsyncSession):
    if not _is_admin(user):
        return

    result = await session.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.user))
        .where(SupportTicket.status == TicketStatus.OPEN)
        .order_by(SupportTicket.updated_at.desc())
        .limit(15)
    )
    tickets = result.scalars().all()

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    if not tickets:
        await message.answer(
            "✅ Нет открытых тикетов.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="← Админ-панель", callback_data="admin_menu:root")],
            ]),
        )
        return

    buttons = []
    for t in tickets:
        name = t.user.display_name if t.user else f"User#{t.user_id}"
        label = t.subject[:25] + ("…" if len(t.subject) > 25 else "")
        buttons.append([InlineKeyboardButton(
            text=f"🔴 #{t.id} {name} — {label}",
            callback_data=f"admin_ticket:view:{t.id}",
        )])
    buttons.append([InlineKeyboardButton(text="← Админ-панель", callback_data="admin_menu:root")])

    await message.answer(
        f"📋 <b>Открытые тикеты ({len(tickets)})</b>:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ===== Список открытых тикетов (inline) =====
@router.callback_query(F.data == "admin_ticket:list")
async def cb_admin_ticket_list(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()

    result = await session.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.user))
        .where(SupportTicket.status == TicketStatus.OPEN)
        .order_by(SupportTicket.updated_at.desc())
        .limit(15)
    )
    tickets = result.scalars().all()

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    if not tickets:
        await callback.message.edit_text(
            "✅ Нет открытых тикетов.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="← Админ-панель", callback_data="admin_menu:root")],
            ]),
        )
        await callback.answer()
        return

    buttons = []
    for t in tickets:
        name = t.user.display_name if t.user else f"User#{t.user_id}"
        label = t.subject[:25] + ("…" if len(t.subject) > 25 else "")
        buttons.append([InlineKeyboardButton(
            text=f"🔴 #{t.id} {name} — {label}",
            callback_data=f"admin_ticket:view:{t.id}",
        )])
    buttons.append([InlineKeyboardButton(text="← Админ-панель", callback_data="admin_menu:root")])

    await callback.message.edit_text(
        f"📋 <b>Открытые тикеты ({len(tickets)})</b>:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


# ===== Просмотр тикета (для админа) =====
@router.callback_query(F.data.startswith("admin_ticket:view:"))
async def cb_admin_ticket_view(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()

    ticket_id = _parse_id(callback.data, 2)
    if ticket_id is None:
        await callback.answer("Некорректный запрос", show_alert=True)
        return
    result = await session.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.messages), selectinload(SupportTicket.user))
        .where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        await callback.answer("Тикет не найден", show_alert=True)
        return

    await callback.message.edit_text(
        _format_ticket_for_admin(ticket),
        parse_mode="HTML",
        reply_markup=admin_ticket_keyboard(ticket.id),
    )
    await callback.answer()


# ===== Закрыть тикет (для админа) =====
@router.callback_query(F.data.startswith("admin_ticket:close:"))
async def cb_admin_close_ticket(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    ticket_id = _parse_id(callback.data, 2)
    if ticket_id is None:
        await callback.answer("Некорректный запрос", show_alert=True)
        return
    result = await session.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        await callback.answer("Тикет не найден", show_alert=True)
        return

    ticket.status = TicketStatus.CLOSED
    await session.commit()
    await callback.answer("✅ Тикет закрыт")

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    await callback.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Все открытые", callback_data="admin_ticket:list")],
        ])
    )


# ===== Ответ на тикет — шаг 1 =====
@router.callback_query(F.data.startswith("admin_ticket:reply:"))
async def cb_admin_reply_start(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    ticket_id = _parse_id(callback.data, 2)
    if ticket_id is None:
        await callback.answer("Некорректный запрос", show_alert=True)
        return
    await state.set_state(AdminReplyToTicket.waiting_reply)
    await state.update_data(ticket_id=ticket_id, original_message_id=callback.message.message_id)

    await callback.message.answer(
        f"✉️ Введите ответ на тикет <b>#{ticket_id}</b>:\n\n"
        "(или нажмите Отмена чтобы вернуться)",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(f"admin_ticket:view:{ticket_id}"),
    )
    await callback.answer()


# ===== Ответ на тикет — шаг 2: обработка текста =====
@router.message(AdminReplyToTicket.waiting_reply)
async def process_admin_reply(message: Message, user: User, state: FSMContext, session: AsyncSession):
    if not _is_admin(user):
        await state.clear()
        return

    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    await state.clear()

    if not message.text:
        await message.answer("Пожалуйста, введите ответ текстом.")
        return

    result = await session.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.user))
        .where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        await message.answer("Тикет не найден.")
        return

    author_name = user.telegram_username or user.email or "Поддержка"
    reply_text = message.text.strip()

    msg = SupportMessage(
        ticket_id=ticket.id,
        is_from_admin=True,
        author_name=author_name,
        text=reply_text,
    )
    session.add(msg)
    ticket.status = TicketStatus.ANSWERED
    await session.commit()

    # Уведомление пользователю
    if ticket.user:
        await notify_user_admin_replied(
            ticket.user,
            ticket.id,
            ticket.subject,
            reply_text,
        )

    display_name = escape(ticket.user.display_name) if ticket.user else "?"
    await message.answer(
        f"✅ <b>Ответ отправлен</b> на тикет #{ticket.id}\n"
        f"👤 Пользователь: {display_name}\n\n"
        f"<b>Ваш ответ:</b>\n{escape(reply_text[:300])}{'…' if len(reply_text) > 300 else ''}",
        parse_mode="HTML",
        reply_markup=admin_reply_sent_keyboard(ticket.id),
    )
