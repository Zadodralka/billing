-- Миграция: подарочные подписки

ALTER TABLE payments ADD COLUMN IF NOT EXISTS is_gift BOOLEAN DEFAULT FALSE;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS gift_recipient_email VARCHAR(255);

CREATE TABLE IF NOT EXISTS gift_codes (
    id SERIAL PRIMARY KEY,
    code VARCHAR(32) UNIQUE NOT NULL,
    payment_id INTEGER NOT NULL REFERENCES payments(id),
    buyer_user_id INTEGER NOT NULL REFERENCES users(id),
    recipient_email VARCHAR(255) NOT NULL,
    plan_key VARCHAR(10) NOT NULL,
    plan_name VARCHAR(64) NOT NULL,
    days INTEGER NOT NULL,
    traffic_gb INTEGER DEFAULT 50,
    status VARCHAR(20) NOT NULL DEFAULT 'issued',
    created_at TIMESTAMP DEFAULT NOW(),
    redeemed_at TIMESTAMP,
    redeemed_by_user_id INTEGER REFERENCES users(id),
    subscription_id INTEGER REFERENCES subscriptions(id)
);

CREATE INDEX IF NOT EXISTS idx_gift_codes_code ON gift_codes(code);
CREATE INDEX IF NOT EXISTS idx_gift_codes_recipient ON gift_codes(recipient_email);
CREATE INDEX IF NOT EXISTS idx_gift_codes_status ON gift_codes(status);
