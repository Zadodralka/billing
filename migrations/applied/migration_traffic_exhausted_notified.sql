-- Миграция: флаг "уведомление об исчерпании лимита трафика уже отправлено"

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS traffic_exhausted_notified BOOLEAN DEFAULT FALSE;
