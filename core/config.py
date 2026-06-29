from pydantic_settings import BaseSettings
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

    # Plans (default prices)
    plan_1m_price: int = 149
    plan_3m_price: int = 399
    plan_6m_price: int = 699
    plan_1y_price: int = 1199

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

PLANS = {
    "1m": {"name": "1 месяц", "days": 30, "price": settings.plan_1m_price},
    "3m": {"name": "3 месяца", "days": 90, "price": settings.plan_3m_price},
    "6m": {"name": "6 месяцев", "days": 180, "price": settings.plan_6m_price},
    "1y": {"name": "1 год", "days": 365, "price": settings.plan_1y_price},
}
