from html import escape
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from core.models import User, SupportTicket, SupportMessage, TicketStatus
from core.support_notify import notify_admins_new_message
from core.config import settings
from bot.states import CreateTicket, ReplyToTicket
from bot.keyboards.support import (
    support_menu_keyboard, ticket_list_keyboard,
    ticket_view_keyboard, cancel_keyboard,
)

router = Router()

MAX_MESSAGES_PREVIEW = 5  # сколько последних сообщений показываем в просмотре тикета


def _parse_id(callback_data: str, index: int) -> int | None:
    try:
        return int(callback_data.split(":")[index])
    except (IndexError, ValueError):
        return None


def _format_ticket_thread(ticket: SupportTicket) -> str:
    """Форматирует последние сообщения тикета для отображения в боте"""
    status_map = {"open": "🔴 Ожидает ответа", "answered": "🟢 Отвечено", "closed": "⚫ Закрыт"}
    status_text = status_map.get(ticket.status.value, ticket.status.value)

    lines = [
        f"📋 <b>{escape(ticket.subject)}</b>",
        f"Статус: {status_text}",
        "",
    ]

    messages = ticket.messages[-MAX_MESSAGES_PREVIEW:] if ticket.messages else []
    if not messages:
        lines.append("<i>Сообщений пока нет</i>")
    else:
        if len(ticket.messages) > MAX_MESSAGES_PREVIEW:
            lines.append(f"<i>... показаны последние {MAX_MESSAGES_PREVIEW} сообщений ...</i>")
            lines.append("")
        for msg in messages:
            who = "🛡 <b>Поддержка</b>" if msg.is_from_admin else "👤 <b>Вы</b>"
            time_str = msg.created_at.strftime("%d.%m %H:%M")
            lines.append(f"{who} <i>{time_str}</i>")
            text = escape(msg.text[:500]) + ("…" if len(msg.text) > 500 else "")
            lines.append(text)
            lines.append("")

    return "\n".join(lines).strip()


# ===== Поддержка — главное меню =====
@router.callback_query(F.data == "support:menu")
@router.callback_query(F.data == "menu:support")
async def cb_support_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "💬 <b>Поддержка</b>\n\n"
        "Создайте обращение — мы ответим в течение 1-2 часов.\n"
        "При ответе вы получите уведомление в этом чате.",
        parse_mode="HTML",
        reply_markup=support_menu_keyboard(),
    )
    await callback.answer()


# ===== Список тикетов пользователя =====
@router.callback_query(F.data == "support:list")
async def cb_support_list(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    await state.clear()
    result = await session.execute(
        select(SupportTicket)
        .where(SupportTicket.user_id == user.id)
        .order_by(SupportTicket.updated_at.desc())
        .limit(10)
    )
    tickets = result.scalars().all()

    if not tickets:
        await callback.message.edit_text(
            "📋 <b>Мои обращения</b>\n\nУ вас пока нет обращений.",
            parse_mode="HTML",
            reply_markup=support_menu_keyboard(),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "📋 <b>Мои обращения</b>\n\nВыберите тикет:",
        parse_mode="HTML",
        reply_markup=ticket_list_keyboard(tickets),
    )
    await callback.answer()


# ===== Просмотр тикета =====
@router.callback_query(F.data.startswith("ticket:view:"))
async def cb_ticket_view(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    await state.clear()
    ticket_id = _parse_id(callback.data, 2)
    if ticket_id is None:
        await callback.answer("Некорректный запрос", show_alert=True)
        return

    result = await session.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.messages))
        .where(SupportTicket.id == ticket_id, SupportTicket.user_id == user.id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        await callback.answer("Тикет не найден", show_alert=True)
        return

    await callback.message.edit_text(
        _format_ticket_thread(ticket),
        parse_mode="HTML",
        reply_markup=ticket_view_keyboard(ticket.id, ticket.status.value),
    )
    await callback.answer()


# ===== Закрыть тикет =====
@router.callback_query(F.data.startswith("ticket:close:"))
async def cb_ticket_close(callback: CallbackQuery, user: User, session: AsyncSession):
    ticket_id = _parse_id(callback.data, 2)
    if ticket_id is None:
        await callback.answer("Некорректный запрос", show_alert=True)
        return
    result = await session.execute(
        select(SupportTicket).where(SupportTicket.id == ticket_id, SupportTicket.user_id == user.id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        await callback.answer("Тикет не найден", show_alert=True)
        return

    ticket.status = TicketStatus.CLOSED
    await session.commit()
    await callback.answer("✅ Тикет закрыт")
    await callback.message.edit_reply_markup(
        reply_markup=ticket_view_keyboard(ticket.id, "closed")
    )


# ===== Создание тикета — шаг 1: тема =====
@router.callback_query(F.data == "support:create")
async def cb_support_create(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CreateTicket.waiting_subject)
    await callback.message.edit_text(
        "✏️ <b>Новое обращение</b>\n\n"
        "Введите тему обращения (кратко, одной строкой):",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("support:menu"),
    )
    await callback.answer()


@router.message(CreateTicket.waiting_subject)
async def process_ticket_subject(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, введите тему текстом.", reply_markup=cancel_keyboard("support:menu"))
        return

    subject = message.text.strip()[:200]
    if not subject:
        await message.answer("Пожалуйста, введите тему.", reply_markup=cancel_keyboard("support:menu"))
        return

    await state.update_data(subject=subject)
    await state.set_state(CreateTicket.waiting_message)
    await message.answer(
        f"📝 Тема: <b>{escape(subject)}</b>\n\nТеперь опишите вашу проблему подробнее:",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("support:menu"),
    )


# ===== Создание тикета — шаг 2: сообщение =====
@router.message(CreateTicket.waiting_message)
async def process_ticket_message(message: Message, user: User, state: FSMContext, session: AsyncSession):
    if not message.text:
        await message.answer("Пожалуйста, введите сообщение текстом.", reply_markup=cancel_keyboard("support:menu"))
        return

    text = message.text.strip()
    if not text:
        await message.answer("Пожалуйста, введите сообщение.", reply_markup=cancel_keyboard("support:menu"))
        return

    data = await state.get_data()
    subject = data.get("subject", "Без темы")
    await state.clear()

    ticket = SupportTicket(user_id=user.id, subject=subject, status=TicketStatus.OPEN)
    session.add(ticket)
    await session.flush()

    msg = SupportMessage(ticket_id=ticket.id, is_from_admin=False, text=text)
    session.add(msg)
    await session.commit()

    await notify_admins_new_message(ticket.id, subject, user.display_name, text)

    await message.answer(
        f"✅ <b>Обращение создано!</b>\n\n"
        f"📋 Тема: {escape(subject)}\n"
        f"🆔 Номер: #{ticket.id}\n\n"
        "Мы ответим в течение 1-2 часов. Вы получите уведомление здесь.",
        parse_mode="HTML",
        reply_markup=ticket_view_keyboard(ticket.id, "open"),
    )


# ===== Ответ на тикет — шаг 1 =====
@router.callback_query(F.data.startswith("ticket:reply:"))
async def cb_ticket_reply(callback: CallbackQuery, state: FSMContext):
    ticket_id = _parse_id(callback.data, 2)
    if ticket_id is None:
        await callback.answer("Некорректный запрос", show_alert=True)
        return
    await state.set_state(ReplyToTicket.waiting_reply)
    await state.update_data(ticket_id=ticket_id)
    await callback.message.edit_text(
        "✉️ Введите ваш ответ:",
        reply_markup=cancel_keyboard(f"ticket:view:{ticket_id}"),
    )
    await callback.answer()


@router.message(ReplyToTicket.waiting_reply)
async def process_user_reply(message: Message, user: User, state: FSMContext, session: AsyncSession):
    if not message.text:
        await message.answer("Пожалуйста, введите ответ текстом.")
        return

    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    await state.clear()

    result = await session.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.messages))
        .where(SupportTicket.id == ticket_id, SupportTicket.user_id == user.id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        await message.answer("Тикет не найден.")
        return

    ticket.status = TicketStatus.OPEN
    msg = SupportMessage(ticket_id=ticket.id, is_from_admin=False, text=message.text.strip())
    session.add(msg)
    await session.commit()

    await notify_admins_new_message(ticket.id, ticket.subject, user.display_name, message.text)

    await message.answer(
        "✅ Ответ отправлен! Вы получите уведомление, когда поддержка ответит.",
        reply_markup=ticket_view_keyboard(ticket.id, ticket.status.value),
    )
