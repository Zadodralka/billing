-- Миграция: флаг разовой проверки "подписка активна, а трафик нулевой"

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS zero_traffic_checked BOOLEAN DEFAULT FALSE;
