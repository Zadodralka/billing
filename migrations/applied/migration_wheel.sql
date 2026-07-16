-- Миграция: колесо фортуны (ежедневный бонус для пользователей)

-- Персональные промокоды-призы (владелец = единственный, кто может применить)
ALTER TABLE promo_codes ADD COLUMN IF NOT EXISTS owner_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_promo_codes_owner ON promo_codes(owner_user_id);

-- Каталог призов, редактируется из админки (/admin/wheel)
CREATE TABLE IF NOT EXISTS wheel_prizes (
    id SERIAL PRIMARY KEY,
    label VARCHAR(100) NOT NULL,
    prize_type VARCHAR(20) NOT NULL DEFAULT 'empty',
    value INTEGER NOT NULL DEFAULT 0,
    weight INTEGER NOT NULL DEFAULT 1,
    color VARCHAR(9) NOT NULL DEFAULT '#3ddc84',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Лог кручений - источник правды для кулдауна и лимита бесплатных призов (см. core/wheel.py)
CREATE TABLE IF NOT EXISTS wheel_spins (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    prize_id INTEGER REFERENCES wheel_prizes(id) ON DELETE SET NULL,
    prize_label VARCHAR(100) NOT NULL,
    prize_type VARCHAR(20) NOT NULL,
    prize_value INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wheel_spins_user ON wheel_spins(user_id);
CREATE INDEX IF NOT EXISTS idx_wheel_spins_created ON wheel_spins(created_at);

-- Сид дефолтных призов - только если таблица ещё пуста (не перетираем то, что
-- админ уже мог отредактировать при повторном запуске этого файла).
INSERT INTO wheel_prizes (label, prize_type, value, weight, color, sort_order)
SELECT * FROM (VALUES
    ('Пусто, повезёт в другой раз', 'empty', 0, 35, '#4b5566', 0),
    ('+1 день подписки', 'days', 1, 20, '#3ddc84', 1),
    ('Промокод -10% на продление', 'promo', 10, 15, '#5b8def', 2),
    ('+3 дня подписки', 'days', 3, 12, '#3ddc84', 3),
    ('+5 GB трафика', 'traffic_gb', 5, 8, '#f5bf42', 4),
    ('+30₽ на баланс', 'balance', 30, 5, '#5b8def', 5),
    ('+7 дней подписки', 'days', 7, 3, '#2fbd6a', 6),
    ('Джекпот: +30 дней подписки', 'days', 30, 2, '#e6b800', 7)
) AS seed(label, prize_type, value, weight, color, sort_order)
WHERE NOT EXISTS (SELECT 1 FROM wheel_prizes);
