#!/usr/bin/env bash
# Установка на чистый сервер одной командой:
#   git clone git@github.com:Zadodralka/billing.git vpn_shop_bot
#   cd vpn_shop_bot
#   sudo ./scripts/install.sh
#
# Что делает:
#   1. Ставит Docker + Compose plugin, если их ещё нет (apt-based дистрибутивы -
#      Ubuntu/Debian; для других систем ставьте Docker вручную и пропустите шаг)
#   2. Создаёт .env из .env.example, сам генерирует SECRET_KEY и POSTGRES_PASSWORD -
#      бизнес-секреты (токен бота, Remnawave, ЮMoney, SMTP) знает только оператор,
#      их сгенерировать нельзя - если хоть один не заполнен, скрипт остановится
#      и попросит дозаполнить .env
#   3. Поднимает docker compose up -d --build
#   4. По желанию (интерактивный вопрос) настраивает nginx + HTTPS через certbot
#
# Безопасно запускать повторно - уже выполненные шаги и уже заполненные вручную
# переменные в .env не трогает.

set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$(id -u)" -ne 0 ]; then
    echo "ОШИБКА: запустите от root (sudo ./scripts/install.sh) - нужен доступ к apt/nginx." >&2
    exit 1
fi

# ── 1. Docker ──
if ! command -v docker &>/dev/null; then
    echo "==> Docker не найден, устанавливаю..."
    if [ -f /etc/debian_version ]; then
        apt-get update -qq
        apt-get install -y -qq ca-certificates curl gnupg
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
        chmod a+r /etc/apt/keyrings/docker.asc
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
            > /etc/apt/sources.list.d/docker.list
        apt-get update -qq
        apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    else
        echo "ОШИБКА: автоустановка Docker поддержана только для Debian/Ubuntu (apt)." >&2
        echo "Поставьте Docker Engine + Compose plugin вручную и запустите скрипт снова:" >&2
        echo "  https://docs.docker.com/engine/install/" >&2
        exit 1
    fi
else
    echo "==> Docker уже установлен, пропускаю"
fi

if ! docker compose version &>/dev/null; then
    echo "ОШИБКА: плагин 'docker compose' не найден (docker-compose-plugin)." >&2
    exit 1
fi

# ── 2. .env ──
if [ ! -f .env ]; then
    echo "==> Создаю .env из .env.example"
    cp .env.example .env
fi

# Подставляет значение в .env, только если переменная там ещё пустая - безопасно
# перезапускать скрипт, уже заполненные (вручную или этим же скриптом) значения не тронет.
_env_set() {
    local key="$1" value="$2"
    if grep -q "^${key}=$" .env 2>/dev/null; then
        sed -i "s|^${key}=\$|${key}=${value}|" .env
    fi
}

if grep -q "^SECRET_KEY=$" .env 2>/dev/null; then
    echo "==> Генерирую SECRET_KEY"
    _env_set SECRET_KEY "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
fi

if grep -q "^POSTGRES_PASSWORD=$" .env 2>/dev/null; then
    echo "==> Генерирую POSTGRES_PASSWORD и DATABASE_URL"
    PG_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
    _env_set POSTGRES_PASSWORD "$PG_PASS"
    _env_set DATABASE_URL "postgresql+asyncpg://vpnbot:${PG_PASS}@db:5432/vpnbot"
fi

_env_set REDIS_URL "redis://redis:6379/0"
# Пустая строка здесь роняет старт приложения (не парсится как число) -
# подставляем те же дефолты, что и так использовались бы, будь переменная
# просто не задана (core/config.py: smtp_port=465, session_max_age=86400).
_env_set SMTP_PORT "465"
_env_set SESSION_MAX_AGE "86400"

# Список того, что знает только оператор и сгенерировать нельзя.
REQUIRED_VARS="BOT_TOKEN ADMIN_IDS YOOMONEY_RECEIVER YOOMONEY_SECRET REMNAWAVE_URL REMNAWAVE_TOKEN SMTP_HOST SMTP_USER SMTP_PASS SMTP_FROM WEBAPP_URL"
MISSING=""
for var in $REQUIRED_VARS; do
    if grep -q "^${var}=$" .env 2>/dev/null; then
        MISSING="$MISSING $var"
    fi
done

if [ -n "$MISSING" ]; then
    echo ""
    echo "==> Заполните в .env перед продолжением:"
    for var in $MISSING; do echo "    - $var"; done
    echo ""
    echo "    nano .env"
    echo "    Затем запустите скрипт ещё раз - уже заполненные переменные он не тронет."
    exit 0
fi

# ── 3. Запуск ──
echo "==> Собираю и запускаю контейнеры (может занять несколько минут)..."
docker compose up -d --build

echo ""
echo "==> Контейнеры запущены. Проверить статус: docker compose ps"
echo "    Логи: docker compose logs -f web bot scheduler"

# ── 4. nginx + TLS (по желанию, только в интерактивном режиме) ──
if [ -t 0 ]; then
    echo ""
    read -rp "Настроить nginx + HTTPS (certbot) сейчас? [y/N] " SETUP_NGINX
    if [[ "$SETUP_NGINX" =~ ^[Yy]$ ]]; then
        read -rp "Домен (например shop.example.com): " DOMAIN
        if [ -z "$DOMAIN" ]; then
            echo "Домен не указан, пропускаю настройку nginx." >&2
        else
            if ! command -v nginx &>/dev/null; then
                apt-get install -y -qq nginx
            fi
            if ! command -v certbot &>/dev/null; then
                apt-get install -y -qq certbot python3-certbot-nginx
            fi
            cat > "/etc/nginx/sites-available/$DOMAIN" <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
            ln -sf "/etc/nginx/sites-available/$DOMAIN" "/etc/nginx/sites-enabled/$DOMAIN"
            nginx -t && systemctl reload nginx
            certbot --nginx -d "$DOMAIN"
            echo "==> nginx + HTTPS настроены для $DOMAIN"
        fi
    fi
fi

echo ""
echo "==> Готово. Не забудьте:"
echo "    - вебхук ЮMoney: https://ВАШ_ДОМЕН/payment/webhook/yoomoney"
echo "    - cron для регулярных бэкапов, см. README.md -> 'Бэкап и восстановление на другом сервере'"
