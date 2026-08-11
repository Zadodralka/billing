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
# Второй временный каталог - для архива, отправляемого в Telegram (см. ниже,
# TELEGRAM_BACKUP_NOTIFY). Обязательно ОТДЕЛЬНЫЙ от WORKDIR: класть его файл
# внутрь WORKDIR нельзя - именно так и запаковывался WORKDIR, из-за чего tar
# видел, что читаемая директория меняется прямо во время чтения, и падал.
TG_WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR" "$TG_WORKDIR"' EXIT

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

if [ "${TELEGRAM_BACKUP_NOTIFY:-}" = "1" ]; then
    BOT_TOKEN="$(grep -E '^BOT_TOKEN=' .env | cut -d= -f2- || true)"
    ADMIN_IDS="$(grep -E '^ADMIN_IDS=' .env | cut -d= -f2- || true)"
    if [ -z "$BOT_TOKEN" ] || [ -z "$ADMIN_IDS" ]; then
        echo "ПРЕДУПРЕЖДЕНИЕ: TELEGRAM_BACKUP_NOTIFY=1, но BOT_TOKEN/ADMIN_IDS не найдены в .env - отправка пропущена." >&2
    else
        # Отдельный архив БЕЗ .env специально для Telegram - боты не шлют файлы через
        # E2E-шифрование (в отличие от Secret Chats), документы хранятся на серверах
        # Telegram, и гонять туда пароли/токены из .env не стоит даже в приватном чате
        # с самим собой. БД и загруженные файлы для восстановления данных достаточно -
        # секреты для .env предполагаются сохранёнными отдельно (см. README).
        echo "==> Отправка бэкапа (без .env) админам в Telegram..."
        # Архив пишется в TG_WORKDIR, а не в WORKDIR (см. объявление выше) -
        # раньше он писался прямо в $WORKDIR, который же и паковался (-C
        # "$WORKDIR" .), tar видел, что читаемая им директория меняется
        # прямо во время чтения (в неё дописывается новый файл), выводил
        # "file changed as we read it" и завершался с кодом 1. Из-за set -e
        # это тихо убивало скрипт ДО отправки в Telegram, хотя сам бэкап
        # (дамп БД и т.д.) в $BACKUP_DIR уже успешно создавался - именно
        # так и выглядел баг: архив в backups/ есть, а в Telegram ничего.
        TG_ARCHIVE="$TG_WORKDIR/${ARCHIVE_NAME}"
        tar -czf "$TG_ARCHIVE" -C "$WORKDIR" --exclude='.env' .
        TG_SIZE="$(du -h "$TG_ARCHIVE" | cut -f1)"
        TG_SIZE_BYTES="$(stat -c%s "$TG_ARCHIVE" 2>/dev/null || stat -f%z "$TG_ARCHIVE")"

        # Bot API принимает документы до 50 МБ - пока БД маленькая, это не проблема,
        # но однажды может стать актуальным (тогда используйте REMOTE_BACKUP_PATH).
        if [ "$TG_SIZE_BYTES" -gt $((50 * 1024 * 1024)) ]; then
            echo "ПРЕДУПРЕЖДЕНИЕ: архив для Telegram больше 50 МБ (${TG_SIZE}) - Bot API его не примет, пропускаю отправку. Используйте REMOTE_BACKUP_PATH." >&2
        else
            IFS=',' read -ra IDS <<< "$ADMIN_IDS"
            for chat_id in "${IDS[@]}"; do
                chat_id="$(echo "$chat_id" | xargs)"
                [ -z "$chat_id" ] && continue
                if curl -sf -F chat_id="$chat_id" \
                     -F document=@"$TG_ARCHIVE;filename=${ARCHIVE_NAME}" \
                     -F caption="🗄 Бэкап Unlockless VPN — $(date '+%d.%m.%Y %H:%M') (${TG_SIZE}, без .env)" \
                     "https://api.telegram.org/bot${BOT_TOKEN}/sendDocument" > /dev/null; then
                    echo "    -> $chat_id: отправлено"
                else
                    echo "    -> $chat_id: ОШИБКА отправки" >&2
                fi
            done
        fi
    fi
fi

echo "==> Удаление локальных бэкапов старше ${KEEP_DAYS} дней..."
find "$BACKUP_DIR" -maxdepth 1 -name 'backup_*.tar.gz' -mtime "+${KEEP_DAYS}" -print -delete

echo
echo "Готово. Архив содержит секреты (.env) - не храните его только на этом же"
echo "сервере и не заливайте в публичные места. Восстановление: scripts/restore.sh"
