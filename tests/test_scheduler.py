"""Тесты логики планировщика (scheduler.py) - истечение подписок и
протухшие неоплаченные счета. Remnawave и Telegram замоканы: тестируем
переходы состояний в БД, а не внешние API."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from core.models import User, Subscription, SubscriptionStatus, Payment, PaymentStatus

pytestmark = pytest.mark.asyncio


async def _mk_user(session, telegram_id=1, **kw) -> User:
    u = User(telegram_id=telegram_id, **kw)
    session.add(u)
    await session.commit()
    await session.refresh(u)
    return u


def _patch_session_factory(db_session):
    """Функции планировщика открывают собственную сессию через AsyncSessionLocal -
    подменяем фабрику так, чтобы они работали в сессии теста (одна и та же БД,
    один и тот же снапшот данных)."""
    class _Ctx:
        async def __aenter__(self):
            return db_session
        async def __aexit__(self, *a):
            return False
    return patch("scheduler.AsyncSessionLocal", lambda: _Ctx())


async def test_expired_subscription_disabled_and_notified(db_session):
    import scheduler
    user = await _mk_user(db_session, email="u@example.com")
    sub = Subscription(
        user_id=user.id, plan_key="1m", traffic_gb=50,
        status=SubscriptionStatus.ACTIVE, remnawave_sub_id="uuid-1",
        starts_at=datetime.utcnow() - timedelta(days=31),
        expires_at=datetime.utcnow() - timedelta(hours=1),
    )
    db_session.add(sub)
    await db_session.commit()

    with _patch_session_factory(db_session), \
         patch("scheduler.remnawave.disable_user", new=AsyncMock()) as rw_disable, \
         patch("scheduler.send_telegram", new=AsyncMock()) as tg, \
         patch("core.email.send_subscription_expired_email", new=AsyncMock()) as em:
        await scheduler.disable_expired_subscriptions()

    await db_session.refresh(sub)
    assert sub.status == SubscriptionStatus.EXPIRED
    rw_disable.assert_awaited_once_with("uuid-1")
    tg.assert_awaited_once()      # у пользователя есть telegram_id
    em.assert_awaited_once()      # и есть email - уходят оба канала


async def test_expired_subscription_kept_active_if_remnawave_fails(db_session):
    """Ключевой инвариант: если Remnawave не ответила, статус НЕ меняется на
    EXPIRED - иначе подписка выпадет из следующего прохода и пользователь
    останется с рабочим VPN бесплатно навсегда."""
    import scheduler
    user = await _mk_user(db_session)
    sub = Subscription(
        user_id=user.id, plan_key="1m", traffic_gb=50,
        status=SubscriptionStatus.ACTIVE, remnawave_sub_id="uuid-1",
        expires_at=datetime.utcnow() - timedelta(hours=1),
    )
    db_session.add(sub)
    await db_session.commit()

    with _patch_session_factory(db_session), \
         patch("scheduler.remnawave.disable_user", new=AsyncMock(side_effect=Exception("api down"))), \
         patch("scheduler.send_telegram", new=AsyncMock()):
        await scheduler.disable_expired_subscriptions()

    await db_session.refresh(sub)
    assert sub.status == SubscriptionStatus.ACTIVE  # остаётся видимой для ретрая


async def test_notify_failure_does_not_roll_back_expired_status(db_session):
    """Регрессия: раньше весь проход коммитился одним общим commit() в конце -
    необработанное исключение при уведомлении по ОДНОЙ подписке откатывало
    несохранённый EXPIRED статус у ВСЕХ подписок этого прохода, хотя доступ
    в Remnawave для них уже был реально отключён (внешний вызов не откатить -
    получался рассинхрон "в Remnawave expired, в биллинге active")."""
    import scheduler
    user1 = await _mk_user(db_session, telegram_id=101)
    user2 = await _mk_user(db_session, telegram_id=102)
    sub1 = Subscription(
        user_id=user1.id, plan_key="1m", traffic_gb=50,
        status=SubscriptionStatus.ACTIVE, remnawave_sub_id="uuid-1",
        expires_at=datetime.utcnow() - timedelta(hours=2),
    )
    sub2 = Subscription(
        user_id=user2.id, plan_key="1m", traffic_gb=50,
        status=SubscriptionStatus.ACTIVE, remnawave_sub_id="uuid-2",
        expires_at=datetime.utcnow() - timedelta(hours=1),
    )
    db_session.add_all([sub1, sub2])
    await db_session.commit()

    async def tg_side_effect(chat_id, *a, **kw):
        if chat_id == 101:
            raise Exception("boom")
        return True

    with _patch_session_factory(db_session), \
         patch("scheduler.remnawave.disable_user", new=AsyncMock()), \
         patch("scheduler.send_telegram", new=AsyncMock(side_effect=tg_side_effect)):
        await scheduler.disable_expired_subscriptions()

    await db_session.refresh(sub1)
    await db_session.refresh(sub2)
    # Сбой уведомления по sub1 не должен откатывать даже её собственный статус
    # (он коммитится раньше, до попытки уведомления) - и тем более не должен
    # задевать sub2, обработанную в том же проходе.
    assert sub1.status == SubscriptionStatus.EXPIRED
    assert sub2.status == SubscriptionStatus.EXPIRED


async def test_expiry_reminder_sent_once(db_session):
    import scheduler
    user = await _mk_user(db_session, email="u@example.com")
    sub = Subscription(
        user_id=user.id, plan_key="1m", traffic_gb=50,
        status=SubscriptionStatus.ACTIVE,
        expires_at=datetime.utcnow() + timedelta(days=2),  # внутри 3-дневного окна
    )
    db_session.add(sub)
    await db_session.commit()

    with _patch_session_factory(db_session), \
         patch("scheduler.send_telegram", new=AsyncMock()) as tg, \
         patch("core.email.send_expiry_reminder_email", new=AsyncMock()) as em:
        await scheduler.notify_expiring_soon()
        # Второй проход не должен слать повторно - флаг уже стоит
        await scheduler.notify_expiring_soon()

    await db_session.refresh(sub)
    assert sub.expiry_reminder_sent is True
    assert tg.await_count == 1
    assert em.await_count == 1


async def test_reminder_not_sent_for_far_future_subscription(db_session):
    import scheduler
    user = await _mk_user(db_session)
    sub = Subscription(
        user_id=user.id, plan_key="1m", traffic_gb=50,
        status=SubscriptionStatus.ACTIVE,
        expires_at=datetime.utcnow() + timedelta(days=20),  # далеко за окном
    )
    db_session.add(sub)
    await db_session.commit()

    with _patch_session_factory(db_session), \
         patch("scheduler.send_telegram", new=AsyncMock()) as tg:
        await scheduler.notify_expiring_soon()

    await db_session.refresh(sub)
    assert sub.expiry_reminder_sent is False
    tg.assert_not_awaited()


async def test_stale_pending_payment_failed_and_balance_refunded(db_session):
    import scheduler
    user = await _mk_user(db_session, balance=0)
    payment = Payment(
        user_id=user.id, plan_key="1m", traffic_gb=50, amount=100,
        status=PaymentStatus.PENDING, label="stale-1", balance_spent=30,
        created_at=datetime.utcnow() - timedelta(hours=25),
    )
    db_session.add(payment)
    await db_session.commit()

    with _patch_session_factory(db_session):
        await scheduler.expire_stale_pending_payments()

    await db_session.refresh(payment)
    await db_session.refresh(user)
    assert payment.status == PaymentStatus.FAILED
    assert payment.balance_spent == 0
    assert user.balance == 30  # списанный на счёт баланс вернулся


async def test_fresh_pending_payment_untouched(db_session):
    import scheduler
    user = await _mk_user(db_session)
    payment = Payment(
        user_id=user.id, plan_key="1m", traffic_gb=50, amount=100,
        status=PaymentStatus.PENDING, label="fresh-1",
        created_at=datetime.utcnow() - timedelta(hours=1),
    )
    db_session.add(payment)
    await db_session.commit()

    with _patch_session_factory(db_session):
        await scheduler.expire_stale_pending_payments()

    await db_session.refresh(payment)
    assert payment.status == PaymentStatus.PENDING
