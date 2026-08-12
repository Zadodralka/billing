from pydantic_settings import BaseSettings, NoDecode
from pydantic import field_validator
from typing import List, Annotated


class Settings(BaseSettings):
    # Telegram
    bot_token: str
    # NoDecode - без неё pydantic-settings считает List[int] "сложным" типом и
    # пытается сам распарсить значение env-переменной как JSON ДО того, как
    # добираются наши field_validator'ы ниже. "1" - валидный JSON (число), поэтому
    # с одним админом всё работало, а вот "1,2" (ровно то, что описано в
    # .env.example - "через запятую") - невалидный JSON, и старт приложения падал
    # с SettingsError на любом ADMIN_IDS с более чем одним ID. NoDecode отключает
    # эту предварительную JSON-попытку - строка доходит до parse_admin_ids как есть.
    admin_ids: Annotated[List[int], NoDecode] = []

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

    # Уведомления админам в Telegram-группу с топиками (опционально) - см.
    # README "Уведомления в группу с топиками". Если ADMIN_GROUP_CHAT_ID не
    # задан, всё работает как раньше: личными сообщениями каждому из ADMIN_IDS.
    # Топики per-категория опциональны и по отдельности - незаполненная
    # категория просто уходит в главный чат группы (без темы), а не падает.
    admin_group_chat_id: int | None = None
    admin_topic_payments: int | None = None
    admin_topic_support: int | None = None
    admin_topic_system: int | None = None
    admin_topic_backups: int | None = None

    @field_validator(
        "admin_group_chat_id", "admin_topic_payments", "admin_topic_support",
        "admin_topic_system", "admin_topic_backups",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v):
        # Пустая строка в .env для Optional[int] иначе падает при старте -
        # тот же класс бага, что уже был с SMTP_PORT/SESSION_MAX_AGE (см. git log).
        if isinstance(v, str) and not v.strip():
            return None
        return v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()

# traffic_gb=0 означает безлимит.
# traffic_reset_strategy - как Remnawave сбрасывает счётчик трафика: NO_RESET (не
# сбрасывать за весь срок подписки) / DAY / WEEK / MONTH. У месячного тарифа сброс
# не нужен - объём и так даётся на весь срок разом; у более длинных тарифов - раз
# в месяц, иначе многомесячный трафик пришлось бы сильно завышать.
PLANS = {
    "1m": {"name": "1 месяц", "days": 30, "price": settings.plan_1m_price, "traffic_gb": 50, "unlimited_extra": settings.plan_1m_unlimited_extra, "traffic_reset_strategy": "NO_RESET"},
    "3m": {"name": "3 месяца", "days": 90, "price": settings.plan_3m_price, "traffic_gb": 50, "unlimited_extra": settings.plan_3m_unlimited_extra, "traffic_reset_strategy": "MONTH"},
    "6m": {"name": "6 месяцев", "days": 180, "price": settings.plan_6m_price, "traffic_gb": 50, "unlimited_extra": settings.plan_6m_unlimited_extra, "traffic_reset_strategy": "MONTH"},
    "1y": {"name": "1 год", "days": 365, "price": settings.plan_1y_price, "traffic_gb": 50, "unlimited_extra": settings.plan_1y_unlimited_extra, "traffic_reset_strategy": "MONTH"},
}
