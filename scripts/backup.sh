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

if [ "${TELEGRAM_BACKUP_NOTIFY:-}" = "1" ]; then
    BOT_TOKEN="$(grep -E '^BOT_TOKEN=' .env | cut -d= -f2- || true)"
    ADMIN_IDS="$(grep -E '^ADMIN_IDS=' .env | cut -d= -f2- || true)"
    # Группа с топиками (см. README "Уведомления в группу с топиками") -
    # опционально, если задана ADMIN_GROUP_CHAT_ID, бэкап уходит одним
    # сообщением в группу (в топик ADMIN_TOPIC_BACKUPS, если он тоже указан),
    # а не личным сообщением каждому из ADMIN_IDS по отдельности.
    ADMIN_GROUP_CHAT_ID="$(grep -E '^ADMIN_GROUP_CHAT_ID=' .env | cut -d= -f2- || true)"
    ADMIN_TOPIC_BACKUPS="$(grep -E '^ADMIN_TOPIC_BACKUPS=' .env | cut -d= -f2- || true)"
    if [ -z "$BOT_TOKEN" ] || { [ -z "$ADMIN_IDS" ] && [ -z "$ADMIN_GROUP_CHAT_ID" ]; }; then
        echo "ПРЕДУПРЕЖДЕНИЕ: TELEGRAM_BACKUP_NOTIFY=1, но BOT_TOKEN и ни ADMIN_IDS, ни ADMIN_GROUP_CHAT_ID не найдены в .env - отправка пропущена." >&2
    else
        # Отправляем тот же архив, что уже лежит в $BACKUP_DIR - он содержит .env
        # с секретами (это осознанный выбор, см. README): архив специально для
        # Telegram отдельно от него больше не собираем, раньше это был отдельный
        # tar БЕЗ .env, но теперь содержимое совпадает 1-в-1, так что нет смысла
        # паковать его дважды.
        echo "==> Отправка бэкапа админам в Telegram..."
        TG_ARCHIVE="$BACKUP_DIR/$ARCHIVE_NAME"
        TG_SIZE_BYTES="$(stat -c%s "$TG_ARCHIVE" 2>/dev/null || stat -f%z "$TG_ARCHIVE")"

        # Bot API принимает документы до 50 МБ - пока БД маленькая, это не проблема,
        # но однажды может стать актуальным (тогда используйте REMOTE_BACKUP_PATH).
        if [ "$TG_SIZE_BYTES" -gt $((50 * 1024 * 1024)) ]; then
            echo "ПРЕДУПРЕЖДЕНИЕ: архив для Telegram больше 50 МБ (${SIZE}) - Bot API его не примет, пропускаю отправку. Используйте REMOTE_BACKUP_PATH." >&2
        elif [ -n "$ADMIN_GROUP_CHAT_ID" ]; then
            CAPTION="🗄 Бэкап Unlockless VPN — $(date '+%d.%m.%Y %H:%M') (${SIZE}, содержит .env - храните бережно)"
            if [ -n "$ADMIN_TOPIC_BACKUPS" ]; then
                SEND_OK=$(curl -sf -F chat_id="$ADMIN_GROUP_CHAT_ID" -F message_thread_id="$ADMIN_TOPIC_BACKUPS" \
                     -F document=@"$TG_ARCHIVE;filename=${ARCHIVE_NAME}" -F caption="$CAPTION" \
                     "https://api.telegram.org/bot${BOT_TOKEN}/sendDocument" > /dev/null && echo 1 || echo 0)
            else
                SEND_OK=$(curl -sf -F chat_id="$ADMIN_GROUP_CHAT_ID" \
                     -F document=@"$TG_ARCHIVE;filename=${ARCHIVE_NAME}" -F caption="$CAPTION" \
                     "https://api.telegram.org/bot${BOT_TOKEN}/sendDocument" > /dev/null && echo 1 || echo 0)
            fi
            if [ "$SEND_OK" = "1" ]; then
                echo "    -> группа: отправлено"
            else
                echo "    -> группа: ОШИБКА отправки" >&2
            fi
        else
            IFS=',' read -ra IDS <<< "$ADMIN_IDS"
            for chat_id in "${IDS[@]}"; do
                chat_id="$(echo "$chat_id" | xargs)"
                [ -z "$chat_id" ] && continue
                if curl -sf -F chat_id="$chat_id" \
                     -F document=@"$TG_ARCHIVE;filename=${ARCHIVE_NAME}" \
                     -F caption="🗄 Бэкап Unlockless VPN — $(date '+%d.%m.%Y %H:%M') (${SIZE}, содержит .env - храните бережно)" \
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
