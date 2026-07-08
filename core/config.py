from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import List


class Settings(BaseSettings):
    # Telegram
    bot_token: str
    admin_ids: List[int] = []

    # Database
    database_url: str

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Security
    secret_key: str
    session_max_age: int = 86400
    session_https_only: bool = True  # False только для локальной разработки без HTTPS

    # YooMoney
    yoomoney_receiver: str
    yoomoney_secret: str

    # Remnawave
    remnawave_url: str
    remnawave_token: str

    # SMTP
    smtp_host: str = "smtp.yandex.ru"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_from: str = ""

    # Web
    webapp_url: str = "http://localhost:8000"

    # Часовой пояс для отображения дат/времени пользователю (в БД всё хранится в UTC,
    # конвертация только на вывод - см. core.timezone). Название - из базы IANA,
    # например "Europe/Moscow", "Asia/Yekaterinburg", "Asia/Novosibirsk".
    timezone: str = "UTC"

    # Plans (default prices)
    plan_1m_price: int = 149
    plan_3m_price: int = 399
    plan_6m_price: int = 699
    plan_1y_price: int = 1199

    # Unlimited traffic upgrade price (added on top of base plan price)
    plan_1m_unlimited_extra: int = 100
    plan_3m_unlimited_extra: int = 250
    plan_6m_unlimited_extra: int = 450
    plan_1y_unlimited_extra: int = 800

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError:
            raise ValueError(
                f"Неизвестный часовой пояс TIMEZONE='{v}'. Используйте имя из базы IANA, "
                "например Europe/Moscow или Asia/Yekaterinburg."
            )
        return v

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, v):
        if not v or len(v) < 32:
            raise ValueError(
                "SECRET_KEY должен быть не короче 32 символов (используется для подписи "
                "веб-сессий). Сгенерируйте его командой: "
                "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        return v

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, v):
        if isinstance(v, list):
            return v
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return []

    # Referral bonuses (RUB)
    referral_bonus_referrer: int = 100
    referral_bonus_referred: int = 50

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()

# traffic_gb=0 означает безлимит
PLANS = {
    "1m": {"name": "1 месяц", "days": 30, "price": settings.plan_1m_price, "traffic_gb": 50, "unlimited_extra": settings.plan_1m_unlimited_extra},
    "3m": {"name": "3 месяца", "days": 90, "price": settings.plan_3m_price, "traffic_gb": 50, "unlimited_extra": settings.plan_3m_unlimited_extra},
    "6m": {"name": "6 месяцев", "days": 180, "price": settings.plan_6m_price, "traffic_gb": 50, "unlimited_extra": settings.plan_6m_unlimited_extra},
    "1y": {"name": "1 год", "days": 365, "price": settings.plan_1y_price, "traffic_gb": 50, "unlimited_extra": settings.plan_1y_unlimited_extra},
}
