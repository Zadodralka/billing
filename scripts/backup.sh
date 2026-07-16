#!/usr/bin/env bash
# Бэкап всего, что нужно для быстрого восстановления системы на другом сервере:
# дамп БД Postgres, .env с секретами, загруженные файлы (web/static/uploads),
# конфиг nginx. Код и миграции уже в git - на новом сервере они приходят
# через git clone, в бэкап их класть незачем.
#
# Использование:
#   ./scripts/backup.sh
#   BACKUP_DIR=/mnt/backups BACKUP_KEEP_DAYS=30 ./scripts/backup.sh
#
# Результат - один архив backups/backup_YYYYMMDD_HHMMSS.tar.gz с правами 600
# (внутри секреты из .env - хранить архив нужно вне сервера, в защищённом месте).

set -euo pipefail

cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
ARCHIVE_NAME="backup_${TIMESTAMP}.tar.gz"
NGINX_CONF="${NGINX_CONF:-/etc/nginx/sites-available/vpnbot}"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

mkdir -p "$BACKUP_DIR"

if [ ! -f .env ]; then
    echo "ОШИБКА: .env не найден в $(pwd) - похоже, скрипт запущен не из корня проекта." >&2
    exit 1
fi

# Имя пользователя/БД читаем из .env, а не хардкодим - на случай если их меняли
POSTGRES_USER="$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2- || true)"
POSTGRES_DB="$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2- || true)"
POSTGRES_USER="${POSTGRES_USER:-vpnbot}"
POSTGRES_DB="${POSTGRES_DB:-vpnbot}"

echo "==> Дамп базы данных (${POSTGRES_DB})..."
# --clean --if-exists - дамп сам сносит старые таблицы перед восстановлением,
# поэтому restore.sh безопасно накатывать даже поверх уже проинициализированной
# (например автосозданной SQLAlchemy при первом старте web) базы.
if ! docker compose exec -T db pg_dump --clean --if-exists --no-owner -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$WORKDIR/db.sql.gz"; then
    echo "ОШИБКА: не удалось снять дамп БД - контейнер db запущен? (docker compose ps)" >&2
    exit 1
fi
if [ ! -s "$WORKDIR/db.sql.gz" ]; then
    echo "ОШИБКА: дамп БД получился пустым, прерываю бэкап." >&2
    exit 1
fi

echo "==> Копирование .env..."
cp .env "$WORKDIR/.env"

echo "==> Загруженные файлы (web/static/uploads)..."
if [ -d web/static/uploads ]; then
    tar -czf "$WORKDIR/uploads.tar.gz" -C web/static uploads
else
    echo "    web/static/uploads отсутствует - пропускаю (пока ничего не загружали)"
fi

echo "==> Конфиг nginx (${NGINX_CONF})..."
if [ -f "$NGINX_CONF" ]; then
    cp "$NGINX_CONF" "$WORKDIR/nginx_vpnbot.conf"
else
    echo "    ПРЕДУПРЕЖДЕНИЕ: $NGINX_CONF не найден - пропускаю (перенесите nginx-конфиг вручную)" >&2
fi

echo "==> Версия и коммит..."
[ -f VERSION ] && cp VERSION "$WORKDIR/VERSION"
git rev-parse HEAD > "$WORKDIR/git_commit.txt" 2>/dev/null || echo "unknown" > "$WORKDIR/git_commit.txt"

echo "==> Упаковка архива..."
tar -czf "$BACKUP_DIR/$ARCHIVE_NAME" -C "$WORKDIR" .
chmod 600 "$BACKUP_DIR/$ARCHIVE_NAME"

SIZE="$(du -h "$BACKUP_DIR/$ARCHIVE_NAME" | cut -f1)"
echo "==> Готово: $BACKUP_DIR/$ARCHIVE_NAME ($SIZE)"

if [ -n "${REMOTE_BACKUP_PATH:-}" ]; then
    echo "==> Копирование на удалённое хранилище (${REMOTE_BACKUP_PATH})..."
    if command -v rclone >/dev/null 2>&1; then
        rclone copy "$BACKUP_DIR/$ARCHIVE_NAME" "$REMOTE_BACKUP_PATH"
    else
        scp "$BACKUP_DIR/$ARCHIVE_NAME" "$REMOTE_BACKUP_PATH"
    fi
fi

echo "==> Удаление локальных бэкапов старше ${KEEP_DAYS} дней..."
find "$BACKUP_DIR" -maxdepth 1 -name 'backup_*.tar.gz' -mtime "+${KEEP_DAYS}" -print -delete

echo
echo "Готово. Архив содержит секреты (.env) - не храните его только на этом же"
echo "сервере и не заливайте в публичные места. Восстановление: scripts/restore.sh"
