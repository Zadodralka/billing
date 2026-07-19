"""
Единая точка отправки Telegram-уведомлений из веба/планировщика/сервисов.

До этого паттерн `Bot(token=...) -> send_message -> session.close()` был
скопирован в шести местах (scheduler, promo_referral, support_notify,
admin_notify, web/routers/payments) - каждое со своим try/except и своим
логированием. Здесь он собран в два хелпера:

  send_telegram(chat_id, text, ...)     - одно сообщение одному получателю
  send_telegram_to_admins(text, ...)    - то же всем админам из settings.admin_ids

Оба - fire-and-forget по контракту: любые ошибки Telegram (пользователь
заблокировал бота, невалидный chat_id, сеть) логируются и подавляются,
потому что ни одно уведомление в этой системе не критично настолько, чтобы
ронять вызвавшую его бизнес-операцию (активацию оплаты, цикл планировщика).
Возвращают True/False, если вызывающему коду всё же важно знать результат.

Заметка про производительность: на каждый вызов создаётся новый Bot и
закрывается его сессия. Это осознанно - уведомления здесь редкие (единицы
в минуту максимум), а держать глобальную aiohttp-сессию через три разных
entrypoint'а (web, scheduler, bot) сложнее, чем платить ~50мс на соединение.
"""
import logging
from aiogram import Bot
from core.config import settings

logger = logging.getLogger("notify")


async def send_telegram(chat_id: int, text: str, reply_markup=None) -> bool:
    """Шлёт одно HTML-сообщение в Telegram. False - если не удалось (уже залогировано)."""
    try:
        bot = Bot(token=settings.bot_token)
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)
            return True
        finally:
            await bot.session.close()
    except Exception as e:
        logger.warning(f"send_telegram to {chat_id} failed: {e}")
        return False


async def send_telegram_to_admins(text: str, reply_markup=None) -> int:
    """Шлёт сообщение всем админам. Возвращает число успешных доставок.
    Одна Bot-сессия на всю рассылку, ошибка доставки одному админу не
    прерывает рассылку остальным."""
    if not settings.admin_ids:
        return 0
    delivered = 0
    try:
        bot = Bot(token=settings.bot_token)
        try:
            for admin_id in settings.admin_ids:
                try:
                    await bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=reply_markup)
                    delivered += 1
                except Exception as e:
                    logger.warning(f"send_telegram_to_admins: could not reach admin {admin_id}: {e}")
        finally:
            await bot.session.close()
    except Exception as e:
        logger.error(f"send_telegram_to_admins failed entirely: {e}")
    return delivered
