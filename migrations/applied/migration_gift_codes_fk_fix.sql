-- Миграция: чинит foreign key на gift_codes, созданные без ON DELETE поведения
-- (миграция migration_gift_codes.sql создала их как RESTRICT по умолчанию, из-за чего
-- удаление подписки/пользователя/платежа, на которые ссылается gift_codes, падало с
-- ForeignKeyViolationError). GiftCode - историческая запись о подарке, поэтому вместо
-- блокировки удаления просто обнуляем/каскадно чистим ссылки.

ALTER TABLE gift_codes DROP CONSTRAINT IF EXISTS gift_codes_subscription_id_fkey;
ALTER TABLE gift_codes ADD CONSTRAINT gift_codes_subscription_id_fkey
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE SET NULL;

ALTER TABLE gift_codes DROP CONSTRAINT IF EXISTS gift_codes_redeemed_by_user_id_fkey;
ALTER TABLE gift_codes ADD CONSTRAINT gift_codes_redeemed_by_user_id_fkey
    FOREIGN KEY (redeemed_by_user_id) REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE gift_codes ALTER COLUMN buyer_user_id DROP NOT NULL;
ALTER TABLE gift_codes DROP CONSTRAINT IF EXISTS gift_codes_buyer_user_id_fkey;
ALTER TABLE gift_codes ADD CONSTRAINT gift_codes_buyer_user_id_fkey
    FOREIGN KEY (buyer_user_id) REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE gift_codes DROP CONSTRAINT IF EXISTS gift_codes_payment_id_fkey;
ALTER TABLE gift_codes ADD CONSTRAINT gift_codes_payment_id_fkey
    FOREIGN KEY (payment_id) REFERENCES payments(id) ON DELETE CASCADE;
