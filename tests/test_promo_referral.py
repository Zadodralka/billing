"""Тесты промо-кодов и баланса (core/promo_referral.py)."""
import pytest
from datetime import datetime, timedelta

from core.models import User, PromoCode, PromoCodeUsage
from core.promo_referral import validate_promo_code, apply_promo_code, add_balance, spend_balance

pytestmark = pytest.mark.asyncio


async def _mk_user(session, telegram_id=1, **kw) -> User:
    u = User(telegram_id=telegram_id, **kw)
    session.add(u)
    await session.commit()
    await session.refresh(u)
    return u


async def _mk_promo(session, code="TEST20", **kw) -> PromoCode:
    p = PromoCode(code=code, discount_percent=kw.pop("discount_percent", 20), **kw)
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return p


async def test_valid_promo_accepted(db_session):
    user = await _mk_user(db_session)
    await _mk_promo(db_session)
    res = await validate_promo_code("TEST20", user.id, db_session)
    assert res["valid"] is True
    assert res["discount_percent"] == 20


async def test_promo_code_is_case_insensitive_and_trimmed(db_session):
    user = await _mk_user(db_session)
    await _mk_promo(db_session)
    res = await validate_promo_code("  test20  ", user.id, db_session)
    assert res["valid"] is True


async def test_unknown_promo_rejected(db_session):
    user = await _mk_user(db_session)
    res = await validate_promo_code("NOPE", user.id, db_session)
    assert res["valid"] is False


async def test_inactive_promo_rejected(db_session):
    user = await _mk_user(db_session)
    await _mk_promo(db_session, is_active=False)
    res = await validate_promo_code("TEST20", user.id, db_session)
    assert res["valid"] is False


async def test_expired_promo_rejected(db_session):
    user = await _mk_user(db_session)
    await _mk_promo(db_session, expires_at=datetime.utcnow() - timedelta(days=1))
    res = await validate_promo_code("TEST20", user.id, db_session)
    assert res["valid"] is False
    assert "истёк" in res["error"]


async def test_promo_max_uses_enforced(db_session):
    user = await _mk_user(db_session)
    await _mk_promo(db_session, max_uses=3, uses_count=3)
    res = await validate_promo_code("TEST20", user.id, db_session)
    assert res["valid"] is False


async def test_promo_single_use_per_user(db_session):
    user = await _mk_user(db_session)
    promo = await _mk_promo(db_session)
    db_session.add(PromoCodeUsage(promo_code_id=promo.id, user_id=user.id, discount_amount=30))
    await db_session.commit()
    res = await validate_promo_code("TEST20", user.id, db_session)
    assert res["valid"] is False
    assert "уже использовали" in res["error"]


async def test_apply_promo_increments_uses_and_records_usage(db_session):
    from sqlalchemy import select
    user = await _mk_user(db_session)
    promo = await _mk_promo(db_session)
    await apply_promo_code(promo, user.id, payment_id=None, discount_amount=30, session=db_session)
    await db_session.commit()

    assert promo.uses_count == 1
    usage = (await db_session.execute(select(PromoCodeUsage))).scalar_one()
    assert usage.user_id == user.id
    assert usage.discount_amount == 30


async def test_add_balance_credits_and_logs_transaction(db_session):
    from sqlalchemy import select
    from core.models import BalanceTransaction
    user = await _mk_user(db_session)
    await add_balance(user, 100, "referral_bonus", "тест", db_session)
    await db_session.commit()

    assert user.balance == 100
    tx = (await db_session.execute(select(BalanceTransaction))).scalar_one()
    assert tx.amount == 100
    assert tx.type == "referral_bonus"


async def test_spend_balance_capped_at_available(db_session):
    """Списание не может увести баланс в минус - списывается не больше остатка."""
    user = await _mk_user(db_session, balance=50)
    spent = await spend_balance(user, 80, "оплата", db_session)
    await db_session.commit()
    assert spent == 50
    assert user.balance == 0


async def test_spend_balance_zero_when_empty(db_session):
    from sqlalchemy import select, func
    from core.models import BalanceTransaction
    user = await _mk_user(db_session, balance=0)
    spent = await spend_balance(user, 100, "оплата", db_session)
    await db_session.commit()
    assert spent == 0
    # Пустое списание не должно оставлять мусорную нулевую транзакцию
    cnt = (await db_session.execute(select(func.count(BalanceTransaction.id)))).scalar()
    assert cnt == 0
