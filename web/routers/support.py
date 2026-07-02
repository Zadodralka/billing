from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
import logging
from core.database import get_db
from core.models import User, SupportTicket, SupportMessage, TicketStatus
from core.support_notify import notify_admins_new_message
from web.routers.auth import require_user

logger = logging.getLogger("support")

router = APIRouter(prefix="/dashboard/support")
templates = Jinja2Templates(directory="web/templates")


@router.get("", response_class=HTMLResponse)
async def support_list(request: Request, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(SupportTicket).where(SupportTicket.user_id == user.id).order_by(SupportTicket.updated_at.desc())
    )
    tickets = result.scalars().all()

    return templates.TemplateResponse(request, "support_list.html", {
        "user": user,
        "tickets": tickets,
    })


@router.post("/create")
async def create_ticket(
    request: Request,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_db),
    subject: str = Form(...),
    message: str = Form(...),
):
    subject = subject.strip()[:200]
    message = message.strip()
    if not subject or not message:
        return JSONResponse({"ok": False, "error": "Заполните тему и сообщение"}, status_code=400)

    try:
        ticket = SupportTicket(user_id=user.id, subject=subject, status=TicketStatus.OPEN)
        session.add(ticket)
        await session.flush()

        msg = SupportMessage(ticket_id=ticket.id, is_from_admin=False, text=message)
        session.add(msg)
        await session.commit()

        await notify_admins_new_message(ticket.id, subject, user.display_name, message)

        return JSONResponse({"ok": True, "ticket_id": ticket.id})
    except Exception as e:
        await session.rollback()
        logger.error(f"create_ticket failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/{ticket_id}", response_class=HTMLResponse)
async def ticket_thread(ticket_id: int, request: Request, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.messages))
        .where(SupportTicket.id == ticket_id, SupportTicket.user_id == user.id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(404, "Тикет не найден")

    return templates.TemplateResponse(request, "support_thread.html", {
        "user": user,
        "ticket": ticket,
        "is_admin_view": False,
    })


@router.post("/{ticket_id}/reply")
async def reply_to_ticket(
    ticket_id: int,
    request: Request,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_db),
    message: str = Form(...),
):
    message = message.strip()
    if not message:
        return JSONResponse({"ok": False, "error": "Сообщение не может быть пустым"}, status_code=400)

    try:
        result = await session.execute(
            select(SupportTicket).where(SupportTicket.id == ticket_id, SupportTicket.user_id == user.id)
        )
        ticket = result.scalar_one_or_none()
        if not ticket:
            return JSONResponse({"ok": False, "error": "Тикет не найден"}, status_code=404)

        if ticket.status == TicketStatus.CLOSED:
            ticket.status = TicketStatus.OPEN  # реопен при ответе в закрытый тикет

        msg = SupportMessage(ticket_id=ticket.id, is_from_admin=False, text=message)
        session.add(msg)
        ticket.status = TicketStatus.OPEN
        await session.commit()

        await notify_admins_new_message(ticket.id, ticket.subject, user.display_name, message)

        return JSONResponse({"ok": True})
    except Exception as e:
        await session.rollback()
        logger.error(f"reply_to_ticket failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/{ticket_id}/close")
async def close_ticket(ticket_id: int, user: User = Depends(require_user), session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(SupportTicket).where(SupportTicket.id == ticket_id, SupportTicket.user_id == user.id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        return JSONResponse({"ok": False, "error": "Тикет не найден"}, status_code=404)

    ticket.status = TicketStatus.CLOSED
    await session.commit()
    return JSONResponse({"ok": True})
