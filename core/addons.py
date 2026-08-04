"""
Сервис доп.опций подписки (см. core.models.PlanAddon) - платные тумблеры поверх
любого тарифа (например «Белые списки»), полностью независимые от тарифов и
редактируемые из /admin/addons.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.models import PlanAddon
from core.plans import parse_squad_uuids


def parse_addon_keys(raw: str | None) -> list[str]:
    """addon_keys хранится в Payment/Subscription/GiftCode одной строкой через
    запятую - тот же формат, что и squad_uuids в core.plans, поэтому та же логика."""
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def _serialize(row: PlanAddon) -> dict:
    return {
        "id": row.id,
        "key": row.key,
        "name": row.name,
        "description": row.description,
        "price": row.price,
        "squad_uuids": parse_squad_uuids(row.squad_uuids),
        "is_active": row.is_active,
    }


async def get_active_addons(session: AsyncSession) -> list[dict]:
    """Доп.опции для показа пользователю при покупке (только активные, по порядку)"""
    result = await session.execute(
        select(PlanAddon).where(PlanAddon.is_active == True).order_by(PlanAddon.sort_order)
    )
    return [_serialize(row) for row in result.scalars().all()]


async def get_all_addons(session: AsyncSession) -> list[PlanAddon]:
    """Все доп.опции включая скрытые (для админки) - модели, а не dict, т.к. нужны
    для рендера id/updated_at в шаблоне так же, как у PlanSetting в /admin/plans."""
    result = await session.execute(select(PlanAddon).order_by(PlanAddon.sort_order))
    return result.scalars().all()


async def get_addons_by_keys(session: AsyncSession, keys: list[str]) -> list[dict]:
    """Опции по списку ключей - для расчёта цены и сборки squad'ов при покупке/выдаче.
    Пропускает неактивные и несуществующие ключи (тариф мог измениться между тем,
    как пользователь открыл страницу, и тем, как нажал "Купить")."""
    if not keys:
        return []
    result = await session.execute(
        select(PlanAddon).where(PlanAddon.key.in_(keys), PlanAddon.is_active == True)
    )
    return [_serialize(row) for row in result.scalars().all()]


async def get_addon_name_map(session: AsyncSession) -> dict[str, str]:
    """{key: name} по ВСЕМ опциям, включая скрытые - для отображения уже купленных
    подписок, у которых опция могла быть скрыта или переименована с тех пор
    (см. dashboard.html/profile.html, аналог all_plans в web/routers/dashboard.py)."""
    result = await session.execute(select(PlanAddon))
    return {row.key: row.name for row in result.scalars().all()}


def addons_price(addons: list[dict]) -> int:
    return sum(a["price"] for a in addons)


def addons_squad_uuids(addons: list[dict]) -> list[str]:
    uuids: list[str] = []
    for addon in addons:
        for uuid in addon["squad_uuids"]:
            if uuid not in uuids:
                uuids.append(uuid)
    return uuids
