#!/usr/bin/env bash
# Восстановление системы из архива, созданного scripts/backup.sh - на новом
# сервере после git clone этого репозитория.
#
# Использование:
#   git clone https://github.com/Zadodralka/billing.git
#   cd billing
#   ./scripts/restore.sh /путь/к/backup_20260716_120000.tar.gz
#
# Что делает: поднимает БД, накатывает в неё дамп, возвращает .env и
# загруженные файлы на место. web/bot/scheduler НЕ запускает - последний
# шаг (docker compose up -d --build) оставлен на оператора осознанно,
# чтобы сначала можно было проверить .env/nginx.

set -euo pipefail

cd "$(dirname "$0")/.."

ARCHIVE="${1:-}"
if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
    echo "Использование: $0 /путь/к/backup_ВРЕМЯ.tar.gz" >&2
    exit 1
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "==> Распаковка архива..."
tar -xzf "$ARCHIVE" -C "$WORKDIR"

if [ ! -f "$WORKDIR/db.sql.gz" ]; then
    echo "ОШИБКА: в архиве нет db.sql.gz - это точно бэкап от scripts/backup.sh?" >&2
    exit 1
fi

if [ -f "$WORKDIR/git_commit.txt" ]; then
    echo "    Бэкап снят с коммита: $(cat "$WORKDIR/git_commit.txt")"
fi
if [ -f "$WORKDIR/VERSION" ]; then
    echo "    Версия на момент бэкапа: $(cat "$WORKDIR/VERSION")"
fi

if [ -f .env ]; then
    BACKUP_ENV=".env.bak.$(date +%Y%m%d_%H%M%S)"
    echo "==> .env уже существует - сохраняю текущий как $BACKUP_ENV и заменяю на бэкапный"
    cp .env "$BACKUP_ENV"
fi
cp "$WORKDIR/.env" .env
echo "==> .env восстановлен"

echo "==> Поднимаю только БД (без web/bot/scheduler)..."
docker compose up -d db

echo "==> Жду, пока Postgres станет готов принимать соединения..."
for _ in $(seq 1 30); do
    if docker compose exec -T db pg_isready -U "$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2- || echo vpnbot)" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

POSTGRES_USER="$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2- || true)"
POSTGRES_DB="$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2- || true)"
POSTGRES_USER="${POSTGRES_USER:-vpnbot}"
POSTGRES_DB="${POSTGRES_DB:-vpnbot}"

echo "==> Восстанавливаю базу данных (${POSTGRES_DB})..."
gunzip -c "$WORKDIR/db.sql.gz" | docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
echo "==> База восстановлена"

if [ -f "$WORKDIR/uploads.tar.gz" ]; then
    echo "==> Восстанавливаю загруженные файлы..."
    mkdir -p web/static/uploads
    tar -xzf "$WORKDIR/uploads.tar.gz" -C web/static
fi

if [ -f "$WORKDIR/nginx_vpnbot.conf" ]; then
    echo
    echo "==> В бэкапе есть конфиг nginx - он НЕ установлен автоматически (нужны"
    echo "    права root и решение, куда именно на новом сервере его класть)."
    echo "    Файл лежит здесь: $WORKDIR/nginx_vpnbot.conf"
    echo "    Обычно: sudo cp \"$WORKDIR/nginx_vpnbot.conf\" /etc/nginx/sites-available/vpnbot"
    echo "            sudo ln -s /etc/nginx/sites-available/vpnbot /etc/nginx/sites-enabled/"
    echo "            sudo nginx -t && sudo systemctl reload nginx"
    cp "$WORKDIR/nginx_vpnbot.conf" ./nginx_vpnbot.conf.restored
    echo "    Также скопирован в ./nginx_vpnbot.conf.restored для удобства."
fi

echo
echo "==> Готово. Осталось:"
echo "    1. Проверить .env (адреса/токены могли поменяться на новом сервере)"
echo "    2. Установить nginx-конфиг (см. выше) и выпустить TLS-сертификат заново:"
echo "       sudo certbot --nginx -d ВАШ_ДОМЕН"
echo "    3. Запустить всё приложение:"
echo "       docker compose up -d --build"
