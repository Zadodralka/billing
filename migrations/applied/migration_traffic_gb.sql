-- Миграция: добавление поддержки выбора объёма трафика (50GB / Безлимит)
-- Выполнить один раз после обновления кода

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS traffic_gb INTEGER DEFAULT 50;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS traffic_gb INTEGER DEFAULT 50;
