-- Исправление: status в support_tickets должен быть нативным enum-типом PostgreSQL,
-- а не VARCHAR, как ожидает SQLAlchemy-модель TicketStatus.

DO $$ BEGIN
    CREATE TYPE ticketstatus AS ENUM ('OPEN', 'ANSWERED', 'CLOSED');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Приводим уже сохранённые значения к верхнему регистру (на случай если что-то успело записаться)
UPDATE support_tickets SET status = UPPER(status) WHERE status IS NOT NULL;

ALTER TABLE support_tickets ALTER COLUMN status DROP DEFAULT;
ALTER TABLE support_tickets
    ALTER COLUMN status TYPE ticketstatus USING status::ticketstatus;
ALTER TABLE support_tickets ALTER COLUMN status SET DEFAULT 'OPEN';
