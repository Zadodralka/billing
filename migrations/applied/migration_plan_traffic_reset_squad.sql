-- Миграция: настраиваемая стратегия сброса трафика и squad'ы Remnawave на уровне тарифа
-- Выполнить один раз после обновления кода

ALTER TABLE plan_settings ADD COLUMN IF NOT EXISTS traffic_reset_strategy VARCHAR(16) DEFAULT 'MONTH';
ALTER TABLE plan_settings ADD COLUMN IF NOT EXISTS squad_uuids TEXT;

-- Явное требование: у тарифа "1 месяц" трафик не должен сбрасываться за весь срок
-- (у остальных тарифов дефолт MONTH из ALTER выше уже подходит).
UPDATE plan_settings SET traffic_reset_strategy = 'NO_RESET' WHERE plan_key = '1m';
