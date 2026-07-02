-- Миграция: поддержка продления существующей подписки (вместо создания новой)

ALTER TABLE payments ADD COLUMN IF NOT EXISTS renew_subscription_id INTEGER REFERENCES subscriptions(id);
