from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta
import logging
import traceback
import secrets
from core.database import get_db
from core.models import User, Subscription, Payment, SubscriptionStatus, PaymentStatus, PlanSetting
from core.remnawave import remnawave
from web.routers.auth import require_admin

logger = logging.getLogger("admin")
logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="web/templates")


@router.get("", response_class=HTMLResponse)
async def admin_index(request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    total_users = (await session.execute(select(func.count(User.id)))).scalar()
    active_subs = (await session.execute(
        select(func.count(Subscription.id)).where(Subscription.status == SubscriptionStatus.ACTIVE)
    )).scalar()
    total_revenue = (await session.execute(
        select(func.sum(Payment.amount)).where(Payment.status == PaymentStatus.SUCCESS)
    )).scalar() or 0

    from core.models import SupportTicket, TicketStatus
    open_tickets = (await session.execute(
        select(func.count(SupportTicket.id)).where(SupportTicket.status == TicketStatus.OPEN)
    )).scalar()

    rw_online = True
    try:
        await remnawave.get_all_users()
    except Exception:
        rw_online = False

    return templates.TemplateResponse(request, "admin/index.html", {
        "user": admin,
        "total_users": total_users,
        "active_subs": active_subs,
        "total_revenue": total_revenue,
        "rw_online": rw_online,
        "admin_open_tickets_count": open_tickets,
    })


@router.get("/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    page: int = 1,
):
    per_page = 20
    offset = (page - 1) * per_page
    result = await session.execute(
        select(User)
        .options(selectinload(User.subscriptions))
        .order_by(User.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    users = result.scalars().all()
    total = (await session.execute(select(func.count(User.id)))).scalar()

    from core.config import PLANS
    return templates.TemplateResponse(request, "admin/users.html", {
        "user": admin,
        "users": users,
        "plans": PLANS,
        "page": page,
        "total": total,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    })


@router.post("/users/{user_id}/ban")
async def ban_user(user_id: int, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    try:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return JSONResponse({"ok": False, "error": "Пользователь не найден"}, status_code=404)

        user.is_banned = not user.is_banned

        if user.remnawave_uuid:
            try:
                if user.is_banned:
                    await remnawave.disable_user(user.remnawave_uuid)
                else:
                    await remnawave.enable_user(user.remnawave_uuid)
            except Exception as e:
                logger.warning(f"Remnawave ban sync failed for {user.remnawave_uuid}: {e}")

        await session.commit()
        return JSONResponse({"ok": True, "is_banned": user.is_banned})
    except Exception as e:
        await session.rollback()
        logger.error(f"ban_user failed for {user_id}: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/users/{user_id}/edit")
async def edit_user(
    user_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    email: str = Form(""),
    is_admin: str = Form(None),
):
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "Пользователь не найден")
    user.email = email.strip() or None
    user.is_admin = is_admin == "true"
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(400, "Этот email уже используется другим пользователем")
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/{user_id}/delete")
async def delete_user(user_id: int, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    logger.info(f"Delete user requested: user_id={user_id} by admin={admin.id}")
    try:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            logger.warning(f"Delete user: user_id={user_id} not found")
            return JSONResponse({"ok": False, "error": "Пользователь не найден"}, status_code=404)

        remnawave_uuid = user.remnawave_uuid
        logger.info(f"Deleting user {user_id}, remnawave_uuid={remnawave_uuid}")

        payments_deleted = await session.execute(delete(Payment).where(Payment.user_id == user_id))
        logger.info(f"Deleted {payments_deleted.rowcount} payments for user {user_id}")

        subs_deleted = await session.execute(delete(Subscription).where(Subscription.user_id == user_id))
        logger.info(f"Deleted {subs_deleted.rowcount} subscriptions for user {user_id}")

        await session.flush()

        user_deleted = await session.execute(delete(User).where(User.id == user_id))
        logger.info(f"Deleted {user_deleted.rowcount} user rows for user {user_id}")

        await session.commit()
        logger.info(f"Successfully deleted user {user_id} from database")

        if remnawave_uuid:
            try:
                await remnawave.delete_user(remnawave_uuid)
                logger.info(f"Deleted remnawave user {remnawave_uuid}")
            except Exception as e:
                logger.warning(f"Remnawave delete failed for {remnawave_uuid}: {e}")

        return JSONResponse({"ok": True})

    except Exception as e:
        await session.rollback()
        logger.error(f"Failed to delete user {user_id}: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/users/{user_id}/grant-subscription")
async def grant_subscription(
    user_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    plan_key: str = Form(...),
    traffic_gb: int = Form(50),
):
    from core.config import PLANS
    from datetime import datetime, timedelta as td

    plan = PLANS.get(plan_key)
    if not plan:
        return JSONResponse({"ok": False, "error": "Неверный тариф"}, status_code=400)

    logger.info(f"grant_subscription: admin={admin.id} user_id={user_id} plan={plan_key} traffic_gb={traffic_gb}")

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return JSONResponse({"ok": False, "error": "Пользователь не найден"}, status_code=404)

    now = datetime.utcnow()

    # Каждая подписка - это отдельный, независимый аккаунт в Remnawave.
    # Управление (остановка/продление/удаление) каждой подписки происходит независимо от других.
    username = f"user_{user.id}_{secrets.token_hex(4)}"
    logger.info(f"grant_subscription: creating independent remnawave account '{username}'")

    try:
        rw_user = await remnawave.create_user(
            username,
            plan["days"],
            traffic_limit_gb=traffic_gb,
            telegram_id=user.telegram_id,
            email=user.email,
        )
        if not rw_user or "uuid" not in rw_user:
            logger.error(f"grant_subscription: remnawave create_user returned unexpected data: {rw_user}")
            return JSONResponse({"ok": False, "error": "Remnawave вернула некорректный ответ при создании пользователя"}, status_code=502)
        remnawave_uuid = rw_user["uuid"]
        config_link = rw_user.get("subscriptionUrl", "")
        logger.info(f"grant_subscription: created independent remnawave account uuid={remnawave_uuid}")
    except Exception as e:
        logger.error(f"grant_subscription: Remnawave step failed: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": f"Ошибка Remnawave: {e}"}, status_code=502)

    if not config_link:
        try:
            config_data = await remnawave.get_user_config(remnawave_uuid)
            config_link = config_data.get("subscriptionUrl") or config_data.get("link", "")
        except Exception as e:
            logger.warning(f"grant_subscription: could not fetch config link: {e}")

    try:
        subscription = Subscription(
            user_id=user.id,
            plan_key=plan_key,
            traffic_gb=traffic_gb,
            status=SubscriptionStatus.ACTIVE,
            starts_at=now,
            expires_at=now + td(days=plan["days"]),
            remnawave_sub_id=remnawave_uuid,
            config_link=config_link,
        )
        session.add(subscription)
        await session.commit()
        logger.info(f"grant_subscription: success, subscription id={subscription.id} (remnawave_uuid={remnawave_uuid}) created for user {user_id}")
        return JSONResponse({"ok": True})
    except Exception as e:
        await session.rollback()
        logger.error(f"grant_subscription: DB save step failed: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": f"Подписка создана в Remnawave, но не сохранена в базе: {e}. UUID для ручной проверки: {remnawave_uuid}"}, status_code=500)


@router.get("/subscriptions", response_class=HTMLResponse)
async def admin_subscriptions(
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    page: int = 1,
):
    per_page = 20
    offset = (page - 1) * per_page
    result = await session.execute(
        select(Subscription)
        .options(selectinload(Subscription.user))
        .order_by(Subscription.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    subs = result.scalars().all()
    total = (await session.execute(select(func.count(Subscription.id)))).scalar()

    from core.config import PLANS
    return templates.TemplateResponse(request, "admin/subscriptions.html", {
        "user": admin,
        "subscriptions": subs,
        "plans": PLANS,
        "page": page,
        "total": total,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    })


@router.post("/subscriptions/{sub_id}/pause")
async def pause_subscription(sub_id: int, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    try:
        result = await session.execute(select(Subscription).where(Subscription.id == sub_id))
        sub = result.scalar_one_or_none()
        if not sub:
            return JSONResponse({"ok": False, "error": "Подписка не найдена"}, status_code=404)

        logger.info(f"pause_subscription: sub_id={sub_id}, remnawave_sub_id={sub.remnawave_sub_id}")

        sub.status = SubscriptionStatus.CANCELLED
        remnawave_warning = None

        if not sub.remnawave_sub_id:
            logger.warning(f"pause_subscription: sub {sub_id} has no remnawave_sub_id")
            remnawave_warning = "В Remnawave не найден связанный аккаунт"
        else:
            try:
                rw_result = await remnawave.disable_user(sub.remnawave_sub_id)
                logger.info(f"pause_subscription: Remnawave disable_user response: {rw_result}")
            except Exception as e:
                logger.error(f"pause_subscription: Remnawave disable FAILED for {sub.remnawave_sub_id}: {traceback.format_exc()}")
                remnawave_warning = f"Статус изменён в магазине, но Remnawave вернула ошибку: {e}"

        await session.commit()
        return JSONResponse({"ok": True, "warning": remnawave_warning})
    except Exception as e:
        await session.rollback()
        logger.error(f"pause_subscription failed: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/subscriptions/{sub_id}/resume")
async def resume_subscription(sub_id: int, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    try:
        result = await session.execute(select(Subscription).where(Subscription.id == sub_id))
        sub = result.scalar_one_or_none()
        if not sub:
            return JSONResponse({"ok": False, "error": "Подписка не найдена"}, status_code=404)

        sub.status = SubscriptionStatus.ACTIVE
        remnawave_warning = None

        if sub.remnawave_sub_id:
            try:
                await remnawave.enable_user(sub.remnawave_sub_id)
                logger.info(f"resume_subscription: Remnawave access re-enabled for sub {sub_id}")
            except Exception as e:
                logger.warning(f"resume_subscription: Remnawave enable failed for sub {sub_id}: {e}")
                remnawave_warning = f"Статус изменён в магазине, но Remnawave вернула ошибку: {e}"
        else:
            remnawave_warning = "В Remnawave не найден связанный аккаунт"

        await session.commit()
        return JSONResponse({"ok": True, "warning": remnawave_warning})
    except Exception as e:
        await session.rollback()
        logger.error(f"resume_subscription failed: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/subscriptions/{sub_id}/extend")
async def extend_subscription(
    sub_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    days: int = Form(...),
):
    try:
        result = await session.execute(select(Subscription).where(Subscription.id == sub_id))
        sub = result.scalar_one_or_none()
        if not sub:
            return JSONResponse({"ok": False, "error": "Подписка не найдена"}, status_code=404)

        logger.info(f"extend_subscription: sub_id={sub_id} days={days} old_expires_at={sub.expires_at} remnawave_sub_id={sub.remnawave_sub_id}")

        base = sub.expires_at if sub.expires_at and sub.expires_at > datetime.utcnow() else datetime.utcnow()
        new_expires = base + timedelta(days=days)
        sub.expires_at = new_expires
        sub.status = SubscriptionStatus.ACTIVE

        remnawave_warning = None
        if sub.remnawave_sub_id:
            try:
                await remnawave.extend_user(sub.remnawave_sub_id, days)
                await remnawave.enable_user(sub.remnawave_sub_id)
                logger.info(f"extend_subscription: Remnawave extended successfully for sub {sub_id}")
            except Exception as e:
                logger.warning(f"extend_subscription: Remnawave extend failed for sub {sub_id} uuid={sub.remnawave_sub_id}: {e}")
                remnawave_warning = f"Срок продлён в магазине, но Remnawave вернула ошибку: {e}"
        else:
            remnawave_warning = "В Remnawave не найден связанный аккаунт"

        await session.commit()
        logger.info(f"extend_subscription: success, sub_id={sub_id} new_expires_at={new_expires}")
        return JSONResponse({"ok": True, "new_expires_at": new_expires.isoformat(), "warning": remnawave_warning})
    except Exception as e:
        await session.rollback()
        logger.error(f"extend_subscription failed: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/subscriptions/{sub_id}/delete")
async def delete_subscription(sub_id: int, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    try:
        result = await session.execute(select(Subscription).where(Subscription.id == sub_id))
        sub = result.scalar_one_or_none()
        if not sub:
            return JSONResponse({"ok": False, "error": "Подписка не найдена"}, status_code=404)

        remnawave_sub_id = sub.remnawave_sub_id
        logger.info(f"delete_subscription: sub_id={sub_id} remnawave_sub_id={remnawave_sub_id}")

        await session.execute(delete(Subscription).where(Subscription.id == sub_id))
        await session.commit()

        # У каждой подписки свой независимый аккаунт - удаляем его сразу, без проверки других подписок
        if remnawave_sub_id:
            try:
                await remnawave.delete_user(remnawave_sub_id)
                logger.info(f"delete_subscription: deleted remnawave account {remnawave_sub_id}")
            except Exception as e:
                logger.warning(f"delete_subscription: could not delete remnawave account {remnawave_sub_id}: {e}")

        return JSONResponse({"ok": True})
    except Exception as e:
        await session.rollback()
        logger.error(f"delete_subscription failed: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/payments", response_class=HTMLResponse)
async def admin_payments(
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    page: int = 1,
):
    per_page = 25
    offset = (page - 1) * per_page
    result = await session.execute(
        select(Payment)
        .options(selectinload(Payment.user))
        .order_by(Payment.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    payments = result.scalars().all()
    total = (await session.execute(select(func.count(Payment.id)))).scalar()
    pending_count = (await session.execute(
        select(func.count(Payment.id)).where(Payment.status == PaymentStatus.PENDING)
    )).scalar()

    from core.config import PLANS
    return templates.TemplateResponse(request, "admin/payments.html", {
        "user": admin,
        "payments": payments,
        "plans": PLANS,
        "page": page,
        "total": total,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "pending_count": pending_count,
    })


@router.post("/payments/{payment_id}/delete")
async def delete_payment(payment_id: int, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    try:
        result = await session.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()
        if not payment:
            return JSONResponse({"ok": False, "error": "Платёж не найден"}, status_code=404)
        if payment.status == PaymentStatus.SUCCESS:
            return JSONResponse({"ok": False, "error": "Нельзя удалить оплаченный платёж"}, status_code=400)

        await session.execute(delete(Payment).where(Payment.id == payment_id))
        await session.commit()
        return JSONResponse({"ok": True})
    except Exception as e:
        await session.rollback()
        logger.error(f"Failed to delete payment {payment_id}: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/payments/cleanup")
async def cleanup_pending_payments(admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    cutoff = datetime.utcnow() - timedelta(hours=1)
    await session.execute(
        delete(Payment).where(
            Payment.status == PaymentStatus.PENDING,
            Payment.created_at < cutoff,
        )
    )
    await session.commit()
    return JSONResponse({"ok": True})


@router.get("/remnawave", response_class=HTMLResponse)
async def admin_remnawave(request: Request, admin: User = Depends(require_admin)):
    from core.config import settings
    try:
        overview = await remnawave.get_panel_overview()
        traffic_map = {
            uid: bytes_val / 1024 ** 3
            for uid, bytes_val in overview["traffic_bytes_map"].items()
        }
        lifetime_traffic_map = {
            uid: bytes_val / 1024 ** 3
            for uid, bytes_val in overview["lifetime_traffic_map"].items()
        }

        return templates.TemplateResponse(request, "admin/remnawave.html", {
            "user": admin,
            "stats": overview,
            "rw_users": overview["users"],
            "traffic_map": traffic_map,
            "lifetime_traffic_map": lifetime_traffic_map,
            "online_at_map": overview["online_at_map"],
            "remnawave_url": settings.remnawave_url,
            "error": None,
        })
    except Exception as e:
        logger.error(f"Remnawave overview failed: {traceback.format_exc()}")
        return templates.TemplateResponse(request, "admin/remnawave.html", {
            "user": admin,
            "remnawave_url": settings.remnawave_url,
            "error": str(e),
        })


@router.get("/plans", response_class=HTMLResponse)
async def admin_plans(request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(PlanSetting).order_by(PlanSetting.sort_order))
    plans = result.scalars().all()

    return templates.TemplateResponse(request, "admin/plans.html", {
        "user": admin,
        "plans": plans,
    })


@router.post("/plans/{plan_id}/update")
async def update_plan(
    plan_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    name: str = Form(...),
    days: int = Form(...),
    price: int = Form(...),
    traffic_gb: int = Form(...),
    unlimited_extra: int = Form(0),
    is_active: str = Form(None),
    is_featured: str = Form(None),
):
    try:
        result = await session.execute(select(PlanSetting).where(PlanSetting.id == plan_id))
        plan = result.scalar_one_or_none()
        if not plan:
            return JSONResponse({"ok": False, "error": "Тариф не найден"}, status_code=404)

        if price < 0 or days < 1:
            return JSONResponse({"ok": False, "error": "Цена не может быть отрицательной, срок - минимум 1 день"}, status_code=400)

        plan.name = name.strip()
        plan.days = days
        plan.price = price
        plan.traffic_gb = traffic_gb
        plan.unlimited_extra = unlimited_extra
        plan.is_active = is_active == "true"
        plan.is_featured = is_featured == "true"

        await session.commit()
        logger.info(f"Admin {admin.id} updated plan {plan.plan_key}: price={price}, days={days}, active={plan.is_active}")
        return JSONResponse({"ok": True})
    except Exception as e:
        await session.rollback()
        logger.error(f"update_plan failed: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/plans/create")
async def create_plan(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    plan_key: str = Form(...),
    name: str = Form(...),
    days: int = Form(...),
    price: int = Form(...),
    traffic_gb: int = Form(50),
    unlimited_extra: int = Form(0),
    is_featured: str = Form(None),
):
    try:
        plan_key = plan_key.strip().lower().replace(" ", "_")
        if not plan_key:
            return JSONResponse({"ok": False, "error": "Укажите ключ тарифа"}, status_code=400)

        existing = await session.execute(select(PlanSetting).where(PlanSetting.plan_key == plan_key))
        if existing.scalar_one_or_none():
            return JSONResponse({"ok": False, "error": f"Тариф с ключом '{plan_key}' уже существует"}, status_code=400)

        max_order = await session.execute(select(func.max(PlanSetting.sort_order)))
        next_order = (max_order.scalar() or 0) + 1

        new_plan = PlanSetting(
            plan_key=plan_key,
            name=name.strip(),
            days=days,
            price=price,
            traffic_gb=traffic_gb,
            unlimited_extra=unlimited_extra,
            is_active=True,
            is_featured=is_featured == "true",
            sort_order=next_order,
        )
        session.add(new_plan)
        await session.commit()
        logger.info(f"Admin {admin.id} created new plan '{plan_key}'")
        return JSONResponse({"ok": True})
    except Exception as e:
        await session.rollback()
        logger.error(f"create_plan failed: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/plans/{plan_id}/delete")
async def delete_plan(plan_id: int, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    try:
        result = await session.execute(select(PlanSetting).where(PlanSetting.id == plan_id))
        plan = result.scalar_one_or_none()
        if not plan:
            return JSONResponse({"ok": False, "error": "Тариф не найден"}, status_code=404)

        await session.execute(delete(PlanSetting).where(PlanSetting.id == plan_id))
        await session.commit()
        return JSONResponse({"ok": True})
    except Exception as e:
        await session.rollback()
        logger.error(f"delete_plan failed: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
