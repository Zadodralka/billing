import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from core.config import settings


async def send_email(to: str, subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_pass,
        use_tls=True,
    )


async def send_magic_link(email: str, token: str):
    link = f"{settings.webapp_url}/auth/verify?token={token}"
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto;">
        <h2 style="color: #2563eb;">🔐 Вход в личный кабинет VPN</h2>
        <p>Нажмите кнопку ниже для входа в личный кабинет:</p>
        <a href="{link}" style="
            display: inline-block;
            background: #2563eb;
            color: white;
            padding: 12px 24px;
            text-decoration: none;
            border-radius: 8px;
            font-size: 16px;
            margin: 16px 0;
        ">Войти в кабинет</a>
        <p style="color: #666; font-size: 13px;">
            Ссылка действительна 15 минут.<br>
            Если вы не запрашивали вход — просто проигнорируйте это письмо.
        </p>
    </div>
    """
    await send_email(email, "Вход в личный кабинет VPN", html)
