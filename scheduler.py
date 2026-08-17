"""
Планировщик: обрабатывает истёкшие подписки.

Логика:
1. Подписка истекла (expires_at <= now, статус ACTIVE) -> блокируем доступ в Remnawave
   немедленно, статус в нашей БД меняем на EXPIRED.
2. Подписка истекла более N дней назад (DELETE_AFTER_DAYS) -> полностью удаляем
   VPN-аккаунт из Remnawave (запись о подписке в нашей БД остаётся для истории).

Каждая подписка имеет свой независимый VPN-аккаунт (sub.remnawave_sub_id),
поэтому блокировка/удаление одной подписки не затрагивает остальные подписки пользователя.
"""
import asyncio
import logging
import pathlib
from datetime import datetime, timedelta
from sqlalchemy import select
from core.database import AsyncSessionLocal, init_db
from core.models import Subscription, SubscriptionStatus, User, Payment, PaymentStatus
from core.remnawave import remnawave
from core.config import settings
from core.timezone import to_local
from core.notify import send_telegram

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Docker healthcheck читает mtime этого файла - если цикл планировщика завис,
# файл перестаёт обновляться и контейнер помечается unhealthy.
HEARTBEAT_FILE = pathlib.Path("/tmp/scheduler_heartbeat")
HEARTBEAT_INTERVAL_SECONDS = 30

DELETE_AFTER_DAYS = 7  # сколько дней хранить заблокированный аккаунт перед полным удалением
PENDING_PAYMENT_TIMEOUT_HOURS = 24  # через сколько часов гасить неоплаченный счёт и вернуть баланс
EXPIRY_REMINDER_DAYS_BEFORE = 3  # за сколько дней до истечения напомнить о продлении
ZERO_TRAFFIC_CHECK_AFTER_HOURS = 24  # через сколько часов после начала подписки проверять расход трафика


async def disable_expired_subscriptions():
    """Шаг 1: блокирует доступ для только что истёкших подписок"""
    async with AsyncSessionLocal() as session:
        now = datetime.utcnow()
        result = await session.execute(
            select(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.expires_at <= now,
            )
        )
        expired = result.scalars().all()

        disabled_count = 0
        for sub in expired:
            # Статус EXPIRED выставляем только если реально отключили доступ в Remnawave
            # (или отключать нечего) - иначе временный сбой API навсегда "потеряет" эту
            # подписку из вида следующего прохода (он фильтрует только status == ACTIVE),
            # и пользователь останется с рабочим VPN бесплатно.
            if sub.remnawave_sub_id:
                try:
                    await remnawave.disable_user(sub.remnawave_sub_id)
                    logger.info(f"Disabled Remnawave access for expired subscription {sub.id}")
                except Exception as e:
                    logger.error(f"Failed to disable remnawave user for sub {sub.id} ({sub.remnawave_sub_id}): {e}, will retry next cycle")
                    continue

            sub.status = SubscriptionStatus.EXPIRED
            disabled_count += 1

            # Коммитим смену статуса СРАЗУ, отдельно от уведомлений ниже - раньше это
            # делалось одним коммитом на весь цикл в конце функции, и необработанное
            # исключение (например в блоке уведомлений) для ОДНОЙ подписки откатывало
            # несохранённые изменения статуса у ВСЕХ подписок этого прохода, хотя
            # доступ в Remnawave для них уже реально отключён - внешний вызов не
            # откатить, и получался рассинхрон "в Remnawave expired, в биллинге active".
            try:
                await session.commit()
            except Exception as e:
                logger.error(f"Failed to commit EXPIRED status for sub {sub.id}: {e}")
                continue

            # Уведомляем и в Telegram, и на email независимо - у пользователя может
            # быть привязан только один из способов входа, а пропущенное уведомление
            # об истечении почти наверняка означает непродлённую подписку. Статус уже
            # сохранён выше, поэтому сбой здесь не мешает обработке остальных подписок.
            try:
                result = await session.execute(select(User).where(User.id == sub.user_id))
                user = result.scalar_one_or_none()
                if user and user.telegram_id:
                    await send_telegram(
                        user.telegram_id,
                        "⚠️ <b>Подписка истекла</b>\n\n"
                        "Доступ к VPN заблокирован.\n"
                        "Зайдите в личный кабинет, чтобы продлить подписку.",
                    )
                if user and user.email:
                    try:
                        from core.plans import get_plan
                        from core.email import send_subscription_expired_email
                        plan = await get_plan(session, sub.plan_key)
                        await send_subscription_expired_email(user.email, plan["name"] if plan else sub.plan_key)
                    except Exception as e:
                        logger.warning(f"Failed to send expiry email to {user.email}: {e}")
            except Exception as e:
                logger.error(f"Failed to notify user for expired sub {sub.id}: {e}")

        if disabled_count:
            logger.info(f"Disabled {disabled_count} expired subscription(s)")


async def notify_expiring_soon():
    """Напоминает пользователю о скором истечении подписки, чтобы он успел продлить
    без разрыва доступа. Шлётся один раз на подписку (expiry_reminder_sent), флаг
    сбрасывается при продлении (см. bot.handlers.payments.activate_subscription)."""
    async with AsyncSessionLocal() as session:
        now = datetime.utcnow()
        cutoff = now + timedelta(days=EXPIRY_REMINDER_DAYS_BEFORE)
        result = await session.execute(
            select(Subscription).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.expires_at.is_not(None),
                Subscription.expires_at > now,
                Subscription.expires_at <= cutoff,
                Subscription.expiry_reminder_sent == False,
            )
        )
        expiring = result.scalars().all()

        count = 0
        for sub in expiring:
            sub.expiry_reminder_sent = True

            # Коммитим флаг СРАЗУ, до попытки уведомления - иначе сбой при отправке
            # одной подписке (или в запросе пользователя ниже) откатывает флаг у всех
            # подписок этого прохода, и уже отправленные напоминания задваиваются на
            # следующем часовом цикле (тот же класс бага, что и в
            # disable_expired_subscriptions, только с задвоением вместо потери).
            try:
                await session.commit()
            except Exception as e:
                logger.error(f"Failed to commit expiry_reminder_sent for sub {sub.id}: {e}")
                continue
            count += 1

            try:
                user_result = await session.execute(select(User).where(User.id == sub.user_id))
                user = user_result.scalar_one_or_none()

                days_left = (sub.expires_at - now).days
                expires_str = to_local(sub.expires_at).strftime('%d.%m.%Y')
                # Оба канала независимо - см. комментарий в disable_expired_subscriptions
                if user and user.telegram_id:
                    await send_telegram(
                        user.telegram_id,
                        f"⏳ <b>Подписка скоро истекает</b>\n\n"
                        f"Осталось {days_left} дн. (до {expires_str}).\n"
                        "Продлите заранее, чтобы доступ к VPN не прерывался — "
                        "кнопка «🔁 Продлить» в разделе «Мои подписки» в боте.",
                    )
                if user and user.email:
                    try:
                        from core.plans import get_plan
                        from core.email import send_expiry_reminder_email
                        plan = await get_plan(session, sub.plan_key)
                        await send_expiry_reminder_email(
                            user.email, plan["name"] if plan else sub.plan_key, days_left, expires_str,
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send expiry reminder email to {user.email}: {e}")
            except Exception as e:
                logger.error(f"Failed to notify user for expiring sub {sub.id}: {e}")

        if count:
            logger.info(f"Sent expiry reminder for {count} subscription(s)")


async def notify_zero_traffic_subscriptions():
    """Предупреждает пользователя, если подписка активна уже сутки, а трафик по ней
    так и не расходовался - похоже на проблему с подключением, о которой лучше
    сказать заранее, а не ждать, пока человек сам напишет в поддержку (или молча
    не продлит подписку). Проверяется один раз на подписку (zero_traffic_checked)."""
    async with AsyncSessionLocal() as session:
        now = datetime.utcnow()
        cutoff = now - timedelta(hours=ZERO_TRAFFIC_CHECK_AFTER_HOURS)
        result = await session.execute(
            select(Subscription).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.remnawave_sub_id.is_not(None),
                Subscription.starts_at.is_not(None),
                Subscription.starts_at <= cutoff,
                Subscription.zero_traffic_checked == False,
            )
        )
        candidates = result.scalars().all()

        checked_count = 0
        notified_count = 0
        for sub in candidates:
            used_gb = await remnawave.get_traffic_usage_gb(sub.remnawave_sub_id)
            if used_gb is None:
                # Remnawave недоступна/не ответила - пробуем ещё раз в следующем цикле,
                # флаг не трогаем, иначе можем ошибочно "простить" реально проблемную подписку
                continue

            sub.zero_traffic_checked = True

            # Коммитим флаг СРАЗУ, до попытки уведомления - иначе сбой на одной
            # подписке откатывает флаг у всех подписок этого прохода, и уже
            # проверенные задваиваются на следующем часовом цикле (тот же класс
            # бага, что и в disable_expired_subscriptions/notify_expiring_soon).
            try:
                await session.commit()
            except Exception as e:
                logger.error(f"Failed to commit zero_traffic_checked for sub {sub.id}: {e}")
                continue
            checked_count += 1

            if used_gb > 0:
                continue

            try:
                user_result = await session.execute(select(User).where(User.id == sub.user_id))
                user = user_result.scalar_one_or_none()
                if not user:
                    continue

                from core.plans import get_plan
                plan = await get_plan(session, sub.plan_key)
                plan_name = plan["name"] if plan else sub.plan_key

                if user.telegram_id:
                    await send_telegram(
                        user.telegram_id,
                        f"📡 <b>Не видим активности по подписке «{plan_name}»</b>\n\n"
                        "Похоже, конфиг ещё не подключён — трафик по подписке пока нулевой.\n"
                        "Если не получается подключиться — напишите в поддержку, поможем.\n"
                        "Если ещё не приступали — конфиг ждёт в разделе «🔑 QR-код подключения».",
                    )

                if user.email:
                    try:
                        from core.email import send_zero_traffic_email
                        await send_zero_traffic_email(user.email, plan_name)
                    except Exception as e:
                        logger.warning(f"Failed to send zero-traffic email to {user.email}: {e}")

                notified_count += 1
            except Exception as e:
                logger.error(f"Failed to notify user for zero-traffic sub {sub.id}: {e}")

        if checked_count:
            logger.info(f"Zero-traffic check: checked {checked_count} subscription(s), notified {notified_count}")


async def notify_traffic_exhausted_subscriptions():
    """Предупреждает пользователя, когда расход трафика по подписке достиг лимита
    тарифа - Remnawave в этот момент сама блокирует дальнейший трафик, но никого
    об этом не уведомляет (ни нас, ни пользователя), и раньше это молча оставалось
    незамеченным. traffic_gb == 0 (безлимит) не проверяется - там лимита нет.

    В отличие от zero_traffic_checked (проверяется один раз за всю жизнь подписки),
    traffic_exhausted_notified планировщик сам сбрасывает обратно в False, как
    только расход падает ниже лимита - это происходит при периодическом сбросе
    счётчика Remnawave (см. traffic_reset_strategy в core/plans.py) или при
    продлении подписки. Без сброса исчерпание лимита в следующем периоде
    (например на 12-месячном тарифе с ежемесячным сбросом) осталось бы без
    уведомления - флаг был бы уже выставлен с первого раза."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Subscription).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.remnawave_sub_id.is_not(None),
                Subscription.traffic_gb > 0,
            )
        )
        candidates = result.scalars().all()

        checked_count = 0
        notified_count = 0
        for sub in candidates:
            used_gb = await remnawave.get_traffic_usage_gb(sub.remnawave_sub_id)
            if used_gb is None:
                # Remnawave недоступна/не ответила - пробуем в следующем цикле,
                # флаг не трогаем (см. аналогичную логику в других функциях этого файла)
                continue
            checked_count += 1

            exhausted = used_gb >= sub.traffic_gb

            if not exhausted:
                if sub.traffic_exhausted_notified:
                    sub.traffic_exhausted_notified = False
                    try:
                        await session.commit()
                    except Exception as e:
                        logger.error(f"Failed to reset traffic_exhausted_notified for sub {sub.id}: {e}")
                continue

            if sub.traffic_exhausted_notified:
                continue  # уже уведомляли об исчерпании в этом периоде

            sub.traffic_exhausted_notified = True

            # Коммитим флаг СРАЗУ, до попытки уведомления - см. комментарий в
            # disable_expired_subscriptions про тот же класс бага (частичный
            # откат при сбое уведомления задваивает рассылку в следующем цикле).
            try:
                await session.commit()
            except Exception as e:
                logger.error(f"Failed to commit traffic_exhausted_notified for sub {sub.id}: {e}")
                continue

            try:
                user_result = await session.execute(select(User).where(User.id == sub.user_id))
                user = user_result.scalar_one_or_none()
                if not user:
                    continue

                from core.plans import get_plan
                plan = await get_plan(session, sub.plan_key)
                plan_name = plan["name"] if plan else sub.plan_key

                if user.telegram_id:
                    await send_telegram(
                        user.telegram_id,
                        f"📶 <b>Лимит трафика по подписке «{plan_name}» исчерпан</b>\n\n"
                        f"Использовано {round(used_gb, 1)} из {sub.traffic_gb} ГБ — VPN временно не работает.\n"
                        "Доступ восстановится при обновлении лимита в новом периоде, либо "
                        "оформите тариф с бОльшим объёмом или безлимитным трафиком.",
                    )
                if user.email:
                    try:
                        from core.email import send_traffic_exhausted_email
                        await send_traffic_exhausted_email(user.email, plan_name, sub.traffic_gb)
                    except Exception as e:
                        logger.warning(f"Failed to send traffic-exhausted email to {user.email}: {e}")

                notified_count += 1
            except Exception as e:
                logger.error(f"Failed to notify user for traffic-exhausted sub {sub.id}: {e}")

        if checked_count:
            logger.info(f"Traffic-exhausted check: checked {checked_count} subscription(s), notified {notified_count}")


async def delete_old_expired_accounts():
    """Шаг 2: полностью удаляет аккаунты, истёкшие более DELETE_AFTER_DAYS дней назад"""
    async with AsyncSessionLocal() as session:
        cutoff = datetime.utcnow() - timedelta(days=DELETE_AFTER_DAYS)
        result = await session.execute(
            select(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.EXPIRED,
                Subscription.expires_at <= cutoff,
                Subscription.remnawave_sub_id.is_not(None),
            )
        )
        to_delete = result.scalars().all()

        deleted_count = 0
        for sub in to_delete:
            try:
                await remnawave.delete_user(sub.remnawave_sub_id)
                logger.info(f"Deleted Remnawave account for long-expired subscription {sub.id}")
                sub.remnawave_sub_id = None  # отмечаем что аккаунт удалён, запись о подписке остаётся
                deleted_count += 1
            except Exception as e:
                logger.error(f"Failed to delete remnawave account for sub {sub.id}: {e}")

        if deleted_count:
            await session.commit()
            logger.info(f"Permanently deleted {deleted_count} Remnawave account(s) (expired >{DELETE_AFTER_DAYS}d ago)")


async def expire_stale_pending_payments():
    """Гасит зависшие неоплаченные счета и возвращает списанный на них баланс"""
    async with AsyncSessionLocal() as session:
        cutoff = datetime.utcnow() - timedelta(hours=PENDING_PAYMENT_TIMEOUT_HOURS)
        result = await session.execute(
            select(Payment).where(
                Payment.status == PaymentStatus.PENDING,
                Payment.created_at <= cutoff,
            )
        )
        stale = result.scalars().all()

        from core.promo_referral import add_balance

        count = 0
        for payment in stale:
            payment.status = PaymentStatus.FAILED
            if payment.balance_spent > 0:
                user_result = await session.execute(select(User).where(User.id == payment.user_id))
                user = user_result.scalar_one_or_none()
                if user:
                    await add_balance(
                        user, payment.balance_spent, "payment_refund",
                        f"Возврат за неоплаченный счёт #{payment.id}", session,
                    )
                payment.balance_spent = 0
            count += 1

        if count:
            await session.commit()
            logger.info(f"Expired {count} stale pending payment(s), refunded balances")


async def _report_step_failure(step_name: str, error: Exception):
    """Шаг цикла упал целиком - лог уже есть в вызывающем коде, здесь только
    дублируем это админу в Telegram, чтобы не узнавать о таком постфактум от
    пользователя (см. историю с рассинхроном disable_expired_subscriptions -
    без алерта такой сбой был виден только в docker logs)."""
    try:
        from core.admin_notify import notify_admins_scheduler_step_failed
        await notify_admins_scheduler_step_failed(step_name, error)
    except Exception as notify_error:
        logger.error(f"Failed to notify admins about {step_name} failure: {notify_error}")


async def run_cycle():
    try:
        await disable_expired_subscriptions()
    except Exception as e:
        logger.error(f"disable_expired_subscriptions failed: {e}")
        await _report_step_failure("disable_expired_subscriptions", e)

    try:
        await notify_expiring_soon()
    except Exception as e:
        logger.error(f"notify_expiring_soon failed: {e}")
        await _report_step_failure("notify_expiring_soon", e)

    try:
        await notify_zero_traffic_subscriptions()
    except Exception as e:
        logger.error(f"notify_zero_traffic_subscriptions failed: {e}")
        await _report_step_failure("notify_zero_traffic_subscriptions", e)

    try:
        await notify_traffic_exhausted_subscriptions()
    except Exception as e:
        logger.error(f"notify_traffic_exhausted_subscriptions failed: {e}")
        await _report_step_failure("notify_traffic_exhausted_subscriptions", e)

    try:
        await delete_old_expired_accounts()
    except Exception as e:
        logger.error(f"delete_old_expired_accounts failed: {e}")
        await _report_step_failure("delete_old_expired_accounts", e)

    try:
        await expire_stale_pending_payments()
    except Exception as e:
        logger.error(f"expire_stale_pending_payments failed: {e}")
        await _report_step_failure("expire_stale_pending_payments", e)


async def _heartbeat_loop():
    while True:
        try:
            HEARTBEAT_FILE.touch()
        except Exception as e:
            logger.warning(f"Heartbeat touch failed: {e}")
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


async def main():
    await init_db()
    logger.info("Scheduler started")
    asyncio.create_task(_heartbeat_loop())
    while True:
        await run_cycle()
        await asyncio.sleep(3600)  # каждый час


if __name__ == "__main__":
    asyncio.run(main())
