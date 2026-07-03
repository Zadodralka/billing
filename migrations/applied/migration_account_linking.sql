-- Миграция: привязка Telegram/email к уже существующему аккаунту

ALTER TABLE email_tokens ADD COLUMN IF NOT EXISTS purpose VARCHAR(20) NOT NULL DEFAULT 'login';
ALTER TABLE email_tokens ADD COLUMN IF NOT EXISTS link_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_email_tokens_link_user ON email_tokens(link_user_id);
