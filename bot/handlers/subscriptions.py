from aiogram import Router, F
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from core.models import User
from core.plans import get_all_plans
import qrcode
import io

router = Router()


@router.message(F.text == "📋 Мои подписки")
async def cmd_subscriptions(message: Message, user: User, session: AsyncSession):
    await session.refresh(user, ["subscriptions"])

    active = [s for s in user.subscriptions if s.is_active]
    if not active:
        await message.answer(
            "📋 <b>Ваши подписки</b>\n\n"
            "У вас нет активных подписок.\n"
            "Нажмите <b>💳 Купить подписку</b> чтобы начать.",
            parse_mode="HTML",
        )
        return

    plans = await get_all_plans(session)
    text = "📋 <b>Ваши активные подписки:</b>\n\n"
    for sub in active:
        plan = plans.get(sub.plan_key, {})
        expires = sub.expires_at.strftime("%d.%m.%Y") if sub.expires_at else "—"
        text += f"✅ {plan.get('name', sub.plan_key)} — до {expires}\n"

    await message.answer(text, parse_mode="HTML")


@router.message(F.text == "🔑 Мои конфиги")
async def cmd_configs(message: Message, user: User, session: AsyncSession):
    await session.refresh(user, ["subscriptions"])

    active = [s for s in user.subscriptions if s.is_active and s.config_link]
    if not active:
        await message.answer("❌ У вас нет активных конфигов. Купите подписку!")
        return

    plans = await get_all_plans(session)
    for sub in active:
        try:
            qr = qrcode.QRCode(box_size=10, border=2)
            qr.add_data(sub.config_link)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)

            from aiogram.types import BufferedInputFile
            plan = plans.get(sub.plan_key, {})
            expires = sub.expires_at.strftime("%d.%m.%Y") if sub.expires_at else "—"
            await message.answer_photo(
                BufferedInputFile(buf.read(), filename="vpn_qr.png"),
                caption=(
                    f"🔑 <b>Конфиг VPN ({plan.get('name', '')})</b>\n"
                    f"📅 Действует до: {expires}\n\n"
                    f"<code>{sub.config_link}</code>\n\n"
                    "Отсканируйте QR-код или скопируйте ссылку в приложение."
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            await message.answer(f"⚠️ Ошибка получения конфига: {e}")
