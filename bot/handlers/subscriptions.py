import asyncio
import io
from datetime import datetime
from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, BufferedInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
import qrcode
from core.models import User, Subscription, SubscriptionStatus
from core.plans import get_active_plans
from core.remnawave import remnawave
from core.config import settings
from bot.keyboards.main import back_to_menu, subscription_actions_row

router = Router()


async def _none():
    return None


async def _load_active_subscriptions(user_id: int, session: AsyncSession) -> list[Subscription]:
    result = await session.execute(
        select(User).where(User.id == user_id).options(selectinload(User.subscriptions))
    )
    user = result.scalar_one()
    now = datetime.utcnow()
    return [
        s for s in user.subscriptions
        if s.status == SubscriptionStatus.ACTIVE and (not s.expires_at or s.expires_at > now)
    ]


async def _send_subscription_qr(target: Message, sub: Subscription, plan_name: str):
    """Отправляет QR-код + ссылку конфига для одной подписки. target.answer_photo используется
    и для обычных сообщений, и для callback.message - у обоих есть этот метод.

    Обёрнуто в try/except: при показе конфигов сразу для НЕСКОЛЬКИХ подписок
    (menu:configs) ошибка на одной из них не должна прерывать отправку остальных."""
    if not sub.config_link:
        await target.answer(f"⚠️ Для подписки «{plan_name}» конфиг ещё не готов, обратитесь в поддержку.")
        return

    try:
        qr = qrcode.QRCode(box_size=10, border=2)
        qr.add_data(sub.config_link)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        expires = sub.expires_at.strftime("%d.%m.%Y") if sub.expires_at else "—"
        await target.answer_photo(
            BufferedInputFile(buf.read(), filename="vpn_qr.png"),
            caption=(
                f"🔑 <b>Конфиг VPN ({plan_name})</b>\n"
                f"📅 Действует до: {expires}\n\n"
                f"<code>{sub.config_link}</code>\n\n"
                "Отсканируйте QR-код или скопируйте ссылку в приложение."
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        await target.answer(f"⚠️ Не удалось получить конфиг для «{plan_name}»: {e}")


# ===== Мои подписки (список + действия) =====
@router.callback_query(F.data == "menu:subs")
async def cb_my_subs(callback: CallbackQuery, user: User, session: AsyncSession):
    active = await _load_active_subscriptions(user.id, session)

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

    plans = await get_active_plans(session)

    # Запросы расхода трафика идут параллельно, а не по очереди - иначе при
    # недоступности/медленности Remnawave открытие "Мои подписки" с несколькими
    # активными подписками ждало бы каждый запрос (до 30с) последовательно.
    usage_results = await asyncio.gather(*(
        remnawave.get_traffic_usage_gb(sub.remnawave_sub_id) if sub.remnawave_sub_id else _none()
        for sub in active
    ))

    lines = ["📋 <b>Мои активные подписки:</b>"]
    buttons = []
    for i, (sub, used_gb) in enumerate(zip(active, usage_results), start=1):
        plan_name = plans.get(sub.plan_key, {}).get("name", sub.plan_key)
        expires = sub.expires_at.strftime("%d.%m.%Y") if sub.expires_at else "—"
        traffic = "Безлимит" if sub.traffic_gb == 0 else f"{sub.traffic_gb} GB"

        usage_line = ""
        if used_gb is not None:
            limit_part = "" if sub.traffic_gb == 0 else f" из {sub.traffic_gb} GB"
            usage_line = f"\n📊 Использовано: {used_gb} GB{limit_part}"

        lines.append(f"\n<b>{i}. {plan_name}</b> · {traffic}\n📅 До {expires}{usage_line}")
        buttons.append(subscription_actions_row(sub.id, i))

    lines.append(f"\n🌐 Полное управление: <a href='{settings.webapp_url}/dashboard'>Личный кабинет</a>")
    buttons.append([InlineKeyboardButton(text="← Главное меню", callback_data="menu:main")])

    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        disable_web_page_preview=True,
    )
    await callback.answer()


# ===== Мои конфиги — QR всех активных подписок сразу =====
@router.callback_query(F.data == "menu:configs")
async def cb_menu_configs(callback: CallbackQuery, user: User, session: AsyncSession):
    active = await _load_active_subscriptions(user.id, session)
    if not active:
        await callback.message.edit_text(
            "❌ У вас нет активных подписок с конфигом.\nКупите подписку, чтобы получить доступ к VPN.",
            reply_markup=back_to_menu(),
        )
        await callback.answer()
        return

    plans = await get_active_plans(session)
    await callback.answer()
    for sub in active:
        plan_name = plans.get(sub.plan_key, {}).get("name", sub.plan_key)
        await _send_subscription_qr(callback.message, sub, plan_name)

    await callback.message.answer("👆 Ваши конфиги выше.", reply_markup=back_to_menu())


# ===== Конфиг одной конкретной подписки (кнопка в списке "Мои подписки") =====
@router.callback_query(F.data.startswith("sub:config:"))
async def cb_sub_config(callback: CallbackQuery, user: User, session: AsyncSession):
    sub_id = int(callback.data.split(":")[2])
    result = await session.execute(
        select(Subscription).where(Subscription.id == sub_id, Subscription.user_id == user.id)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        await callback.answer("Подписка не найдена", show_alert=True)
        return

    plans = await get_active_plans(session)
    plan_name = plans.get(sub.plan_key, {}).get("name", sub.plan_key)
    await callback.answer()
    await _send_subscription_qr(callback.message, sub, plan_name)
