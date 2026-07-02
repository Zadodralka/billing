"""
Сервис тарифов: тарифы хранятся в БД (таблица plan_settings) и редактируются из админки.
При первом запуске таблица заполняется дефолтными значениями из core.config.PLANS.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.models import PlanSetting
from core.config import PLANS as DEFAULT_PLANS


async def seed_plans_if_empty(session: AsyncSession):
    """Заполняет таблицу тарифов дефолтными значениями, если она пуста (первый запуск)"""
    result = await session.execute(select(PlanSetting))
    existing = result.scalars().first()
    if existing:
        return

    for i, (key, plan) in enumerate(DEFAULT_PLANS.items()):
        session.add(PlanSetting(
            plan_key=key,
            name=plan["name"],
            days=plan["days"],
            price=plan["price"],
            traffic_gb=plan.get("traffic_gb", 50),
            unlimited_extra=plan.get("unlimited_extra", 0),
            is_active=True,
            sort_order=i,
        ))
    await session.commit()


async def get_active_plans(session: AsyncSession) -> dict:
    """Возвращает тарифы для отображения на сайте (только активные, в нужном порядке)"""
    result = await session.execute(
        select(PlanSetting).where(PlanSetting.is_active == True).order_by(PlanSetting.sort_order)
    )
    rows = result.scalars().all()
    if not rows:
        return DEFAULT_PLANS  # фолбэк, если таблица почему-то пуста

    return {
        row.plan_key: {
            "name": row.name,
            "days": row.days,
            "price": row.price,
            "traffic_gb": row.traffic_gb,
            "unlimited_extra": row.unlimited_extra,
            "is_featured": row.is_featured,
        }
        for row in rows
    }


async def get_all_plans(session: AsyncSession) -> dict:
    """Все тарифы включая неактивные (для админки)"""
    result = await session.execute(select(PlanSetting).order_by(PlanSetting.sort_order))
    rows = result.scalars().all()
    if not rows:
        return DEFAULT_PLANS

    return {
        row.plan_key: {
            "name": row.name,
            "days": row.days,
            "price": row.price,
            "traffic_gb": row.traffic_gb,
            "unlimited_extra": row.unlimited_extra,
            "is_active": row.is_active,
            "is_featured": row.is_featured,
        }
        for row in rows
    }


async def get_plan(session: AsyncSession, plan_key: str) -> dict | None:
    result = await session.execute(select(PlanSetting).where(PlanSetting.plan_key == plan_key))
    row = result.scalar_one_or_none()
    if not row:
        return DEFAULT_PLANS.get(plan_key)
    return {
        "name": row.name,
        "days": row.days,
        "price": row.price,
        "traffic_gb": row.traffic_gb,
        "unlimited_extra": row.unlimited_extra,
        "is_active": row.is_active,
    }
