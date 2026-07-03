-- Миграция: системный проход по всем внешним ключам на users/payments/subscriptions/
-- promo_codes/support_tickets - у большинства из них не было ON DELETE поведения
-- (RESTRICT по умолчанию), из-за чего удаление пользователя/платежа/подписки в админке
-- падало с ForeignKeyViolationError, если на запись хоть где-то ссылались (например
-- support_tickets.user_id при попытке удалить пользователя с открытыми тикетами).
-- Тот же класс бага, что чинили для gift_codes - на этот раз проходим по всем таблицам.

-- users.referred_by_id: SET NULL, не CASCADE - удаление реферера не должно удалять
-- пользователей, которых он привёл
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_referred_by_id_fkey;
ALTER TABLE users ADD CONSTRAINT users_referred_by_id_fkey
    FOREIGN KEY (referred_by_id) REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscriptions_user_id_fkey;
ALTER TABLE subscriptions ADD CONSTRAINT subscriptions_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE payments DROP CONSTRAINT IF EXISTS payments_user_id_fkey;
ALTER TABLE payments ADD CONSTRAINT payments_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE payments DROP CONSTRAINT IF EXISTS payments_renew_subscription_id_fkey;
ALTER TABLE payments ADD CONSTRAINT payments_renew_subscription_id_fkey
    FOREIGN KEY (renew_subscription_id) REFERENCES subscriptions(id) ON DELETE SET NULL;

ALTER TABLE payments DROP CONSTRAINT IF EXISTS payments_promo_code_id_fkey;
ALTER TABLE payments ADD CONSTRAINT payments_promo_code_id_fkey
    FOREIGN KEY (promo_code_id) REFERENCES promo_codes(id) ON DELETE SET NULL;

ALTER TABLE support_tickets DROP CONSTRAINT IF EXISTS support_tickets_user_id_fkey;
ALTER TABLE support_tickets ADD CONSTRAINT support_tickets_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE support_messages DROP CONSTRAINT IF EXISTS support_messages_ticket_id_fkey;
ALTER TABLE support_messages ADD CONSTRAINT support_messages_ticket_id_fkey
    FOREIGN KEY (ticket_id) REFERENCES support_tickets(id) ON DELETE CASCADE;

ALTER TABLE balance_transactions DROP CONSTRAINT IF EXISTS balance_transactions_user_id_fkey;
ALTER TABLE balance_transactions ADD CONSTRAINT balance_transactions_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE promo_code_usages DROP CONSTRAINT IF EXISTS promo_code_usages_promo_code_id_fkey;
ALTER TABLE promo_code_usages ADD CONSTRAINT promo_code_usages_promo_code_id_fkey
    FOREIGN KEY (promo_code_id) REFERENCES promo_codes(id) ON DELETE CASCADE;

ALTER TABLE promo_code_usages DROP CONSTRAINT IF EXISTS promo_code_usages_user_id_fkey;
ALTER TABLE promo_code_usages ADD CONSTRAINT promo_code_usages_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE promo_code_usages DROP CONSTRAINT IF EXISTS promo_code_usages_payment_id_fkey;
ALTER TABLE promo_code_usages ADD CONSTRAINT promo_code_usages_payment_id_fkey
    FOREIGN KEY (payment_id) REFERENCES payments(id) ON DELETE SET NULL;
