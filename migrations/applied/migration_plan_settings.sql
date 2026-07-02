-- Миграция: редактируемые тарифы из админ-панели

CREATE TABLE IF NOT EXISTS plan_settings (
    id SERIAL PRIMARY KEY,
    plan_key VARCHAR(10) UNIQUE NOT NULL,
    name VARCHAR(64) NOT NULL,
    days INTEGER NOT NULL,
    price INTEGER NOT NULL,
    traffic_gb INTEGER DEFAULT 50,
    unlimited_extra INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    sort_order INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plan_settings_key ON plan_settings(plan_key);
