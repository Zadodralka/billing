"""
Восстановление подписок в биллинге по данным живой панели Remnawave -
для ситуации, когда сервер биллинга переустановили с нуля (пустая БД),
а сервер Remnawave (отдельная машина) пережил это и всё ещё хранит
реальные, действующие VPN-аккаунты пользователей.

Без этого скрипта такие аккаунты работают (VPN не обрывается), но
биллинг о них не знает: в личном кабинете/боте у пользователя "нет
подписки", уведомления (истекает/истекла/обнулился трафик) не приходят,
а автоочистка просроченных аккаунтов их не видит и не тронет.

Что делает:
  1. Забирает все аккаунты из Remnawave (GET /api/users).
  2. Для каждого активного (status=ACTIVE, срок не истёк) пытается
     найти telegram_id и/или email, с которыми аккаунт был создан.
  3. Находит существующего пользователя biллинга по telegram_id/email,
     либо создаёт нового (ровно так же, как это делает обычный вход
     через бота/почту - см. web/routers/auth.py).
  4. Создаёт Subscription, привязанную к уже существующему UUID в
     Remnawave (новый аккаунт НЕ создаётся) - с реальными expires_at
     и лимитом трафика, взятыми из самой Remnawave.

Безопасность:
  - По умолчанию - сухой прогон (ничего не пишет в БД), просто
    печатает, что бы сделал. Запись - только с флагом --apply.
  - Схема ответа Remnawave для полей telegramId/email нигде в проекте
    ещё не разбиралась и не проверена на реальных данных - сначала
    посмотрите на неё флагом --dump, прежде чем запускать --apply.
  - Повторный запуск безопасен: аккаунт с уже привязанным
    remnawave_sub_id не дублируется, только обновляется при отличиях.

Запуск (внутри контейнера web, где есть доступ к БД и Remnawave):
  docker compose exec web python3 scripts/restore_from_remnawave.py --dump 3
  docker compose exec web python3 scripts/restore_from_remnawave.py
  docker compose exec web python3 scripts/restore_from_remnawave.py --apply
"""
import asyncio
import argparse
import json
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

# Скрипт лежит в scripts/, а не в корне репозитория (как diagnose_remnawave.py) -
# при запуске `python3 scripts/restore_from_remnawave.py` в sys.path[0] попадает
# scripts/, а не корень с пакетом core/. Добавляем корень явно, иначе
# `ModuleNotFoundError: No module named 'core'` независимо от текущей директории.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from core.database import AsyncSessionLocal, init_db
from core.models import User, Subscription, SubscriptionStatus
from core.remnawave import remnawave
from core.config import PLANS

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("restore_from_remnawave")


def _first(d: dict, *keys):
    """Пробует несколько вариантов названия поля - формат ответа Remnawave
    в этой части (telegramId/email на GET, а не на POST) в проекте раньше
    не проверялся, поэтому не полагаемся на единственное точное имя."""
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _parse_dt(raw) -> datetime | None:
    """БД хранит время как TIMESTAMP WITHOUT TIME ZONE (везде в проекте - см.
    datetime.utcnow() в core/models.py, core/scheduler.py) - возвращаем
    наивный datetime в UTC, а не timezone-aware, иначе asyncpg отказывается
    писать его в поле (can't subtract offset-naive and offset-aware datetimes)."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _guess_plan_key(days_left: int) -> str:
    """Косметический подбор ближайшего тарифа по оставшимся дням - только
    для отображаемого названия. На реальный срок действия и лимит трафика
    (берутся напрямую из Remnawave) это не влияет."""
    best_key, best_diff = "1m", None
    for key, plan in PLANS.items():
        diff = abs(plan["days"] - days_left)
        if best_diff is None or diff < best_diff:
            best_key, best_diff = key, diff
    return best_key


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Реально писать в БД (по умолчанию - сухой прогон)")
    parser.add_argument("--dump", type=int, nargs="?", const=3, default=None,
                         help="Напечатать сырой JSON первых N аккаунтов (по умолчанию 3) и выйти - для проверки полей перед запуском")
    parser.add_argument("--include-inactive", action="store_true",
                         help="Обрабатывать и уже отключённые/просроченные аккаунты (по умолчанию - только ACTIVE с непросроченным сроком)")
    args = parser.parse_args()

    users = await remnawave.get_all_users()
    logger.info(f"Remnawave вернула {len(users)} аккаунтов")

    if args.dump is not None:
        for u in users[:args.dump]:
            print(json.dumps(u, ensure_ascii=False, indent=2))
        return

    if not args.apply:
        logger.info("=== СУХОЙ ПРОГОН - в БД ничего не пишется. Добавьте --apply, чтобы применить. ===\n")

    now = datetime.utcnow()  # наивный, как и всё остальное время в БД (см. _parse_dt)
    created_users = 0
    created_subs = 0
    updated_subs = 0
    skipped_inactive = 0
    skipped_no_identity = 0
    skipped_conflict = 0

    await init_db()
    async with AsyncSessionLocal() as session:
        for u in users:
            uuid = u.get("uuid")
            username = u.get("username", "?")
            status = u.get("status")
            expire_at = _parse_dt(u.get("expireAt"))

            if not args.include_inactive:
                if status != "ACTIVE" or not expire_at or expire_at <= now:
                    skipped_inactive += 1
                    continue

            telegram_id_raw = _first(u, "telegramId", "telegram_id", "tgId")
            email_raw = _first(u, "email", "userEmail")
            telegram_id = int(telegram_id_raw) if telegram_id_raw else None
            email = email_raw.strip().lower() if email_raw else None

            if not telegram_id and not email:
                logger.warning(f"[ПРОПУСК] {username} (uuid={uuid}): нет ни telegramId, ни email в Remnawave - некому привязать вручную сверьте в панели")
                skipped_no_identity += 1
                continue

            # Уже восстанавливали этот аккаунт раньше - обновляем, не дублируем
            existing_sub = (await session.execute(
                select(Subscription).where(Subscription.remnawave_sub_id == uuid)
            )).scalar_one_or_none()

            # Поиск пользователя: telegram_id в приоритете (основная идентичность
            # бота), email - фолбэк. Если оба поля указывают на РАЗНЫХ уже
            # существующих пользователей биллинга - руками разбираться безопаснее,
            # чем гадать, кого с кем сливать.
            user_by_tg = None
            user_by_email = None
            if telegram_id:
                user_by_tg = (await session.execute(
                    select(User).where(User.telegram_id == telegram_id)
                )).scalar_one_or_none()
            if email:
                user_by_email = (await session.execute(
                    select(User).where(User.email == email)
                )).scalar_one_or_none()

            if user_by_tg and user_by_email and user_by_tg.id != user_by_email.id:
                logger.warning(
                    f"[ПРОПУСК] {username} (uuid={uuid}): telegram_id={telegram_id} и email={email} "
                    f"принадлежат РАЗНЫМ пользователям биллинга (id={user_by_tg.id} и id={user_by_email.id}) - разберите вручную"
                )
                skipped_conflict += 1
                continue

            user = user_by_tg or user_by_email

            if not user:
                user = User(telegram_id=telegram_id, email=email)
                logger.info(f"[НОВЫЙ ПОЛЬЗОВАТЕЛЬ] telegram_id={telegram_id} email={email} (для {username})")
                if args.apply:
                    session.add(user)
                    await session.flush()  # получить user.id для Subscription ниже
                created_users += 1
            else:
                # Дозаполняем недостающее поле у уже существующего пользователя,
                # если оно не занято кем-то другим (иначе - молча пропускаем поле,
                # это не повод срывать восстановление подписки).
                if email and not user.email:
                    conflict = (await session.execute(
                        select(User).where(User.email == email, User.id != user.id)
                    )).scalar_one_or_none()
                    if not conflict:
                        user.email = email
                if telegram_id and not user.telegram_id:
                    conflict = (await session.execute(
                        select(User).where(User.telegram_id == telegram_id, User.id != user.id)
                    )).scalar_one_or_none()
                    if not conflict:
                        user.telegram_id = telegram_id

            traffic_limit_bytes = u.get("trafficLimitBytes") or 0
            traffic_gb = round(traffic_limit_bytes / 1024 ** 3) if traffic_limit_bytes else 0
            days_left = max((expire_at - now).days, 1) if expire_at else 30
            plan_key = _guess_plan_key(days_left)
            config_link = u.get("subscriptionUrl") or None
            created_at = _parse_dt(u.get("createdAt")) or now

            if existing_sub:
                logger.info(f"[ОБНОВЛЕНИЕ] sub#{existing_sub.id} <- {username} (uuid={uuid}): expires_at={expire_at}, traffic_gb={traffic_gb}")
                if args.apply:
                    existing_sub.status = SubscriptionStatus.ACTIVE
                    existing_sub.expires_at = expire_at
                    existing_sub.traffic_gb = traffic_gb
                    if config_link:
                        existing_sub.config_link = config_link
                updated_subs += 1
            else:
                logger.info(
                    f"[НОВАЯ ПОДПИСКА] user_id={getattr(user, 'id', '?')} <- {username} (uuid={uuid}): "
                    f"план~{plan_key}, expires_at={expire_at}, traffic_gb={traffic_gb or 'безлимит'}"
                )
                if args.apply:
                    session.add(Subscription(
                        user_id=user.id,
                        plan_key=plan_key,
                        traffic_gb=traffic_gb,
                        status=SubscriptionStatus.ACTIVE,
                        starts_at=created_at,
                        expires_at=expire_at,
                        remnawave_sub_id=uuid,
                        config_link=config_link,
                    ))
                created_subs += 1

            if args.apply:
                try:
                    await session.commit()
                except Exception as e:
                    logger.error(f"Не удалось сохранить {username} (uuid={uuid}): {e}")
                    await session.rollback()

    logger.info("\n=== Итого ===")
    logger.info(f"Новых пользователей:      {created_users}")
    logger.info(f"Новых подписок:           {created_subs}")
    logger.info(f"Обновлено подписок:       {updated_subs}")
    logger.info(f"Пропущено (неактивны):    {skipped_inactive}")
    logger.info(f"Пропущено (нет identity): {skipped_no_identity}")
    logger.info(f"Пропущено (конфликт):     {skipped_conflict}")
    if not args.apply:
        logger.info("\nЭто был сухой прогон. Проверьте вывод выше и запустите с --apply, чтобы применить.")


if __name__ == "__main__":
    asyncio.run(main())
