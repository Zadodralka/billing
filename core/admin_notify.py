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


async def notify_admins_scheduler_step_failed(step_name: str, error: Exception):
    """Шаг планировщика (scheduler.run_cycle) упал ЦЕЛИКОМ - в отличие от точечных
    ошибок внутри цикла по подпискам (те уже логируются построчно и не мешают
    обработке остальных), это значит, что весь шаг пропущен в этом часовом
    проходе и будет повторён только следующим - без явного алерта такое легко
    не заметить неделями (именно так и не поймали рассинхрон Remnawave/биллинга)."""
    await send_telegram_to_admins(
        f"🚨 <b>Сбой планировщика</b>\n\n"
        f"Шаг: <code>{step_name}</code>\n"
        f"Ошибка: {error}\n\n"
        f"Будет повторён на следующем часовом цикле."
    )
