"""
Единая точка отправки Telegram-уведомлений из веба/планировщика/сервисов.

До этого паттерн `Bot(token=...) -> send_message -> session.close()` был
скопирован в шести местах (scheduler, promo_referral, support_notify,
admin_notify, web/routers/payments) - каждое со своим try/except и своим
логированием. Здесь он собран в два хелпера:

  send_telegram(chat_id, text, ...)     - одно сообщение одному получателю
  send_telegram_to_admins(text, ...)    - то же всем админам из settings.admin_ids
                                           (или одним сообщением в группу с
                                           топиками, если она настроена - см.
                                           ниже и README)

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


# Категория уведомления -> в каком поле settings искать ID топика группы.
# Список категорий, которые реально используются в проекте - см. вызовы
# send_telegram_to_admins(..., topic=...) в core/admin_notify.py и
# core/support_notify.py.
_TOPIC_SETTING_MAP = {
    "payments": "admin_topic_payments",
    "support": "admin_topic_support",
    "system": "admin_topic_system",
    "backups": "admin_topic_backups",
}


async def send_telegram_to_admins(text: str, reply_markup=None, topic: str | None = None) -> int:
    """Шлёт сообщение админам. Возвращает число успешных доставок.

    Если задан settings.admin_group_chat_id (см. README "Уведомления в
    группу с топиками") - шлёт ОДНИМ сообщением в эту группу, в топик по
    `topic` (payments/support/system/backups, см. _TOPIC_SETTING_MAP). Топик
    для конкретной категории можно не настраивать - тогда сообщение придёт в
    общий чат группы (без темы), а не потеряется.

    Если группа не настроена вовсе - прежнее поведение: личным сообщением
    каждому из settings.admin_ids, одна Bot-сессия на всю рассылку, ошибка
    доставки одному админу не прерывает рассылку остальным."""
    if settings.admin_group_chat_id:
        thread_id = getattr(settings, _TOPIC_SETTING_MAP.get(topic, ""), None) if topic else None
        try:
            bot = Bot(token=settings.bot_token)
            try:
                await bot.send_message(
                    settings.admin_group_chat_id, text, parse_mode="HTML",
                    reply_markup=reply_markup, message_thread_id=thread_id,
                )
                return 1
            except Exception as e:
                logger.warning(f"send_telegram_to_admins: could not send to admin group (topic={topic}): {e}")
                return 0
            finally:
                await bot.session.close()
        except Exception as e:
            logger.error(f"send_telegram_to_admins (group) failed entirely: {e}")
            return 0

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
