from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.models import User
from core.config import settings


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        if tg_user:
            session: AsyncSession = data["session"]
            result = await session.execute(
                select(User).where(User.telegram_id == tg_user.id)
            )
            user = result.scalar_one_or_none()

            if not user:
                user = User(
                    telegram_id=tg_user.id,
                    telegram_username=tg_user.username,
                    is_admin=tg_user.id in settings.admin_ids,
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
            else:
                user.telegram_username = tg_user.username
                if tg_user.id in settings.admin_ids:
                    user.is_admin = True
                await session.commit()

            data["user"] = user

        return await handler(event, data)
