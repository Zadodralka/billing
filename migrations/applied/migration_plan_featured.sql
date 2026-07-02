-- Миграция: поле «Популярный выбор» в тарифах

ALTER TABLE plan_settings ADD COLUMN IF NOT EXISTS is_featured BOOLEAN DEFAULT FALSE;
