-- Миграция: промокоды и реферальная программа

-- Баланс и реферальные поля в таблице пользователей
ALTER TABLE users ADD COLUMN IF NOT EXISTS balance INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code VARCHAR(20) UNIQUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by_id INTEGER REFERENCES users(id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_bonus_paid BOOLEAN DEFAULT FALSE;

-- История транзакций баланса
CREATE TABLE IF NOT EXISTS balance_transactions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    amount INTEGER NOT NULL,
    type VARCHAR(30) NOT NULL,
    description VARCHAR(300) DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_balance_tx_user ON balance_transactions(user_id);

-- Промокоды
CREATE TABLE IF NOT EXISTS promo_codes (
    id SERIAL PRIMARY KEY,
    code VARCHAR(50) UNIQUE NOT NULL,
    discount_percent INTEGER NOT NULL,
    max_uses INTEGER,
    uses_count INTEGER DEFAULT 0,
    expires_at TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    description VARCHAR(200),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_promo_codes_code ON promo_codes(code);

-- Использования промокодов
CREATE TABLE IF NOT EXISTS promo_code_usages (
    id SERIAL PRIMARY KEY,
    promo_code_id INTEGER NOT NULL REFERENCES promo_codes(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    payment_id INTEGER REFERENCES payments(id),
    discount_amount INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Дополнительные поля в платежах для скидок
ALTER TABLE payments ADD COLUMN IF NOT EXISTS original_amount INTEGER DEFAULT 0;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS promo_discount INTEGER DEFAULT 0;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS balance_spent INTEGER DEFAULT 0;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS promo_code_id INTEGER REFERENCES promo_codes(id);
