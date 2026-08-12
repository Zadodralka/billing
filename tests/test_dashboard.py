"""
_split_subscriptions отвечает за то, какие подписки видны в личном кабинете -
см. web/routers/dashboard.py. Раньше истёкшая подписка была видна 30 дней по
дате, независимо от того, жив ли ещё её VPN-аккаунт в Remnawave (удаляется
планировщиком через DELETE_AFTER_DAYS=7) - карточка висела ещё три недели без
единой рабочей кнопки. Эти тесты фиксируют исправленное поведение: видимость
истёкшей подписки завязана на remnawave_sub_id, а не на дату истечения.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from core.models import SubscriptionStatus
from web.routers.dashboard import _split_subscriptions


@dataclass
class FakeSub:
    id: int
    status: SubscriptionStatus
    expires_at: datetime | None
    remnawave_sub_id: str | None = None


NOW = datetime(2026, 8, 12, 12, 0, 0)


def test_active_subscription_is_active():
    sub = FakeSub(1, SubscriptionStatus.ACTIVE, NOW + timedelta(days=10))
    active, paused = _split_subscriptions([sub], NOW)
    assert active == [sub]
    assert paused == []


def test_cancelled_subscription_is_paused():
    sub = FakeSub(1, SubscriptionStatus.CANCELLED, NOW + timedelta(days=10))
    active, paused = _split_subscriptions([sub], NOW)
    assert active == []
    assert paused == [sub]


def test_recently_expired_with_live_remnawave_account_is_visible():
    """Истекла час назад, аккаунт в Remnawave ещё не удалён - должна быть видна
    (можно нажать "Возобновить" и продлить тот же конфиг)."""
    sub = FakeSub(1, SubscriptionStatus.EXPIRED, NOW - timedelta(hours=1), remnawave_sub_id="uuid-1")
    active, paused = _split_subscriptions([sub], NOW)
    assert active == []
    assert paused == [sub]


def test_expired_month_ago_but_still_has_remnawave_account_is_visible():
    """Раньше ЭТОТ случай был главным багом: по старой логике (>30 дней) такая
    подписка уже пропала бы, а по факту (если планировщик почему-то не удалил
    аккаунт) "Возобновить" всё ещё работала бы. Видимость должна идти за
    реальным состоянием Remnawave, а не за произвольной датой."""
    sub = FakeSub(1, SubscriptionStatus.EXPIRED, NOW - timedelta(days=40), remnawave_sub_id="uuid-1")
    active, paused = _split_subscriptions([sub], NOW)
    assert paused == [sub]


def test_expired_with_deleted_remnawave_account_disappears_immediately():
    """Главный сценарий из жалобы пользователя: аккаунт в Remnawave уже удалён
    (remnawave_sub_id обнулён - планировщиком через 7 дней или самим юзером
    через "Отказаться") - карточка должна пропасть из кабинета сразу, а не
    висеть ещё недели без единой рабочей кнопки."""
    sub = FakeSub(1, SubscriptionStatus.EXPIRED, NOW - timedelta(days=8), remnawave_sub_id=None)
    active, paused = _split_subscriptions([sub], NOW)
    assert active == []
    assert paused == []


def test_active_subscription_past_expiry_without_scheduler_run_is_not_active():
    """Пограничный случай: expires_at уже в прошлом, но статус в БД ещё ACTIVE
    (планировщик пока не пробежался) - не должна попадать в active_subs."""
    sub = FakeSub(1, SubscriptionStatus.ACTIVE, NOW - timedelta(minutes=1))
    active, paused = _split_subscriptions([sub], NOW)
    assert active == []
