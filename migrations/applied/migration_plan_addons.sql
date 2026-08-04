-- Миграция: платные доп.опции подписки (например «Белые списки»), выбираемые
-- пользователем тумблером поверх любого тарифа при покупке - см. core/addons.py
-- Выполнить один раз после обновления кода

CREATE TABLE IF NOT EXISTS plan_addons (
    id SERIAL PRIMARY KEY,
    key VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(64) NOT NULL,
    description VARCHAR(255),
    price INTEGER DEFAULT 0,
    squad_uuids TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    sort_order INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_plan_addons_key ON plan_addons (key);

ALTER TABLE payments ADD COLUMN IF NOT EXISTS addon_keys TEXT;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS addon_keys TEXT;
ALTER TABLE gift_codes ADD COLUMN IF NOT EXISTS addon_keys TEXT;
