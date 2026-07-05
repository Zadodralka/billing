-- Миграция: флаг отправленного напоминания об истечении подписки

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS expiry_reminder_sent BOOLEAN DEFAULT FALSE;
