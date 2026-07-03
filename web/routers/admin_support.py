from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
import logging
from core.database import get_db
from core.models import User, SupportTicket, SupportMessage, TicketStatus
from core.support_notify import notify_user_admin_replied
from core.version import APP_VERSION
from web.routers.auth import require_admin

logger = logging.getLogger("admin.support")

router = APIRouter(prefix="/admin/support")
templates = Jinja2Templates(directory="web/templates")
templates.env.globals["app_version"] = APP_VERSION


@router.get("", response_class=HTMLResponse)
async def admin_support_list(
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    status: str = "all",
):
    query = select(SupportTicket).options(selectinload(SupportTicket.user)).order_by(SupportTicket.updated_at.desc())
    if status != "all":
        query = query.where(SupportTicket.status == status)

    result = await session.execute(query)
    tickets = result.scalars().all()

    open_count = (await session.execute(
        select(func.count(SupportTicket.id)).where(SupportTicket.status == TicketStatus.OPEN)
    )).scalar()

    return templates.TemplateResponse(request, "admin/support_list.html", {
        "user": admin,
        "tickets": tickets,
        "current_status": status,
        "open_count": open_count,
        "admin_open_tickets_count": open_count,
    })


@router.get("/{ticket_id}", response_class=HTMLResponse)
async def admin_ticket_thread(ticket_id: int, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.messages), selectinload(SupportTicket.user))
        .where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(404, "Тикет не найден")

    return templates.TemplateResponse(request, "support_thread.html", {
        "user": admin,
        "ticket": ticket,
        "is_admin_view": True,
    })


@router.post("/{ticket_id}/reply")
async def admin_reply_to_ticket(
    ticket_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    message: str = Form(...),
):
    message = message.strip()
    if not message:
        return JSONResponse({"ok": False, "error": "Сообщение не может быть пустым"}, status_code=400)

    try:
        result = await session.execute(
            select(SupportTicket).options(selectinload(SupportTicket.user)).where(SupportTicket.id == ticket_id)
        )
        ticket = result.scalar_one_or_none()
        if not ticket:
            return JSONResponse({"ok": False, "error": "Тикет не найден"}, status_code=404)

        author_name = admin.telegram_username or admin.email or "Поддержка"
        msg = SupportMessage(ticket_id=ticket.id, is_from_admin=True, author_name=author_name, text=message)
        session.add(msg)
        ticket.status = TicketStatus.ANSWERED
        await session.commit()

        if ticket.user:
            await notify_user_admin_replied(ticket.user.telegram_id, ticket.id, ticket.subject, message)

        return JSONResponse({"ok": True})
    except Exception as e:
        await session.rollback()
        logger.error(f"admin_reply_to_ticket failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/{ticket_id}/close")
async def admin_close_ticket(ticket_id: int, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        return JSONResponse({"ok": False, "error": "Тикет не найден"}, status_code=404)

    ticket.status = TicketStatus.CLOSED
    await session.commit()
    return JSONResponse({"ok": True})


@router.post("/{ticket_id}/reopen")
async def admin_reopen_ticket(ticket_id: int, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        return JSONResponse({"ok": False, "error": "Тикет не найден"}, status_code=404)

    ticket.status = TicketStatus.OPEN
    await session.commit()
    return JSONResponse({"ok": True})
