"""Конвертация дат/времени из UTC (как всё хранится в БД) в часовой пояс
для отображения пользователю, настраиваемый через TIMEZONE в .env."""
from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo
from core.config import settings

APP_TZ = ZoneInfo(settings.timezone)


def to_local(dt: datetime | None) -> datetime | None:
    """UTC (наивный или с tzinfo) -> локальное время в TIMEZONE. Используется
    только для вывода - сравнения и арифметика в остальном коде продолжают
    идти в UTC (datetime.utcnow())."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt.astimezone(APP_TZ)
