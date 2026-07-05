import asyncio
import logging
from datetime import datetime
from html import escape
from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload
from core.models import User, Subscription, SubscriptionStatus, Payment, PaymentStatus, SupportTicket, TicketStatus
from core.config import settings
from bot.states import AdminBroadcast

logger = logging.getLogger("bot.admin")

router = Router()

MAX_BROADCAST_RECIPIENTS_PER_SECOND = 25  # запас от лимита Telegram (~30 msg/sec)


def _is_admin(user: User) -> bool:
    return user.telegram_id in settings.admin_ids


# ===== /stats — быстрая сводка =====
@router.message(Command("stats"))
async def cmd_stats(message: Message, user: User, session: AsyncSession):
    if not _is_admin(user):
        return

    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    month_start = datetime(now.year, now.month, 1)

    total_users = (await session.execute(select(func.count(User.id)))).scalar() or 0

    active_subs = (await session.execute(
        select(func.count(Subscription.id)).where(
            Subscription.status == SubscriptionStatus.ACTIVE,
            or_(Subscription.expires_at.is_(None), Subscription.expires_at > now),
        )
    )).scalar() or 0

    revenue_today = (await session.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == PaymentStatus.SUCCESS, Payment.paid_at >= today_start,
        )
    )).scalar() or 0

    revenue_month = (await session.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == PaymentStatus.SUCCESS, Payment.paid_at >= month_start,
        )
    )).scalar() or 0

    payments_today = (await session.execute(
        select(func.count(Payment.id)).where(
            Payment.status == PaymentStatus.SUCCESS, Payment.paid_at >= today_start,
        )
    )).scalar() or 0

    open_tickets = (await session.execute(
        select(func.count(SupportTicket.id)).where(SupportTicket.status == TicketStatus.OPEN)
    )).scalar() or 0

    await message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей всего: {total_users}\n"
        f"✅ Активных подписок: {active_subs}\n\n"
        f"💰 Выручка сегодня: {revenue_today} ₽ ({payments_today} оплат)\n"
        f"💰 Выручка за месяц: {revenue_month} ₽\n\n"
        f"🎫 Открытых тикетов: {open_tickets}",
        parse_mode="HTML",
    )


# ===== /find <email или username> — поиск пользователя =====
@router.message(Command("find"))
async def cmd_find(message: Message, user: User, session: AsyncSession, command: CommandObject):
    if not _is_admin(user):
        return

    query = (command.args or "").strip()
    if not query:
        await message.answer("Использование: <code>/find email_или_username</code>", parse_mode="HTML")
        return

    result = await session.execute(
        select(User)
        .options(selectinload(User.subscriptions))
        .where(or_(
            User.email.ilike(f"%{query}%"),
            User.telegram_username.ilike(f"%{query}%"),
        ))
        .limit(10)
    )
    users = result.scalars().all()

    if not users:
        await message.answer("Никого не найдено.")
        return

    now = datetime.utcnow()
    lines = [f"🔎 <b>Найдено: {len(users)}</b>\n"]
    for u in users:
        active = [s for s in u.subscriptions if s.status == SubscriptionStatus.ACTIVE and (not s.expires_at or s.expires_at > now)]
        status = "🚫 забанен" if u.is_banned else ("🛡 админ" if u.is_admin else "")
        lines.append(
            f"👤 <b>{escape(u.display_name)}</b> (id {u.id}) {status}\n"
            f"   email: {u.email or '—'} · tg: @{u.telegram_username or '—'}\n"
            f"   баланс: {u.balance} ₽ · активных подписок: {len(active)}"
        )

    await message.answer("\n\n".join(lines), parse_mode="HTML")


# ===== /broadcast — рассылка всем пользователям =====
@router.message(Command("broadcast"))
async def cmd_broadcast_start(message: Message, user: User, state: FSMContext):
    if not _is_admin(user):
        return

    await state.set_state(AdminBroadcast.waiting_text)
    await message.answer(
        "📣 Введите текст рассылки (уйдёт всем пользователям с привязанным Telegram).\n"
        "Поддерживается HTML-разметка. Для отмены — /cancel."
    )


@router.message(Command("cancel"), AdminBroadcast.waiting_text)
@router.message(Command("cancel"), AdminBroadcast.waiting_confirm)
async def cmd_broadcast_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Рассылка отменена.")


@router.message(AdminBroadcast.waiting_text)
async def process_broadcast_text(message: Message, user: User, state: FSMContext, session: AsyncSession):
    if not _is_admin(user):
        await state.clear()
        return
    if not message.text:
        await message.answer("Пожалуйста, отправьте текст рассылки.")
        return

    total = (await session.execute(
        select(func.count(User.id)).where(User.telegram_id.is_not(None))
    )).scalar() or 0

    await state.update_data(text=message.text)
    await state.set_state(AdminBroadcast.waiting_confirm)
    await message.answer(
        f"📣 <b>Предпросмотр рассылки</b> (получателей: {total}):\n\n{message.text}\n\n"
        "Отправить?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить", callback_data="broadcast:send")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast:cancel")],
        ]),
    )


@router.callback_query(F.data == "broadcast:cancel", AdminBroadcast.waiting_confirm)
async def cb_broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Рассылка отменена.")
    await callback.answer()


@router.callback_query(F.data == "broadcast:send", AdminBroadcast.waiting_confirm)
async def cb_broadcast_send(callback: CallbackQuery, user: User, state: FSMContext, session: AsyncSession, bot: Bot):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    text = data.get("text")
    await state.clear()
    await callback.answer()

    if not text:
        await callback.message.edit_text("Текст рассылки потерян, начните заново через /broadcast.")
        return

    result = await session.execute(select(User.telegram_id).where(User.telegram_id.is_not(None)))
    telegram_ids = result.scalars().all()
    # Рассылка может идти минуты (пауза раз в 25 сообщений) - не держим соединение с БД
    # выписанным из пула всё это время, оно тут больше не нужно.
    await session.close()

    await callback.message.edit_text(f"📣 Рассылка запущена ({len(telegram_ids)} получателей)...")

    sent, failed = 0, 0
    for i, tg_id in enumerate(telegram_ids, start=1):
        try:
            await bot.send_message(tg_id, text, parse_mode="HTML")
            sent += 1
        except Exception as e:
            failed += 1
            logger.info(f"Broadcast: failed to send to {tg_id}: {e}")
        if i % MAX_BROADCAST_RECIPIENTS_PER_SECOND == 0:
            await asyncio.sleep(1)

    await callback.message.answer(f"✅ Рассылка завершена. Доставлено: {sent}, ошибок: {failed}.")
