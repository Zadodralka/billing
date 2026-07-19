"""Уведомления администраторам о событиях бизнес-значимости (не связанные с поддержкой -
для тикетов см. core.support_notify)."""
from core.notify import send_telegram_to_admins


async def notify_admins_new_payment(user_display_name: str, plan_name: str, amount: int, is_gift: bool, is_renew: bool):
    kind = "🎁 Подарок" if is_gift else ("🔁 Продление" if is_renew else "🆕 Новая подписка")
    await send_telegram_to_admins(
        f"💵 <b>Оплата получена</b>\n\n"
        f"{kind}\n"
        f"👤 {user_display_name}\n"
        f"📦 {plan_name}\n"
        f"💰 {amount} ₽"
    )
