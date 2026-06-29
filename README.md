# VPN Bot — Remnawave + YooMoney

Полноценный проект для продажи VPN-подписок через Telegram-бота с веб-кабинетом.

## Стек
- **Backend**: Python 3.11+, FastAPI, aiogram 3
- **БД**: PostgreSQL + SQLAlchemy (async) + Alembic
- **Оплата**: ЮМани (YooMoney)
- **VPN-панель**: Remnawave API
- **Веб**: Jinja2 + Bootstrap 5
- **Авторизация**: Email (magic link) + Telegram Login Widget

## Структура
```
vpn-bot/
├── bot/
│   ├── __init__.py
│   ├── main.py              # Запуск бота
│   ├── handlers/
│   │   ├── start.py
│   │   ├── subscriptions.py
│   │   └── payments.py
│   ├── keyboards/
│   │   └── main.py
│   └── middlewares/
│       └── auth.py
├── web/
│   ├── __init__.py
│   ├── main.py              # FastAPI приложение
│   ├── routers/
│   │   ├── auth.py          # Email + Telegram auth
│   │   ├── dashboard.py     # Кабинет пользователя
│   │   ├── admin.py         # Админ-панель
│   │   └── payments.py      # Вебхук ЮМани
│   └── templates/
│       ├── base.html
│       ├── login.html
│       ├── dashboard.html
│       └── admin/
│           ├── users.html
│           └── subscriptions.html
├── core/
│   ├── config.py            # Настройки (.env)
│   ├── database.py          # SQLAlchemy async
│   ├── models.py            # ORM модели
│   ├── remnawave.py         # Клиент Remnawave API
│   └── yoomoney.py          # Клиент ЮМани
├── migrations/              # Alembic
├── .env.example
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Быстрый старт

1. Скопируй `.env.example` → `.env` и заполни переменные
2. `docker-compose up -d`
3. `docker-compose exec app alembic upgrade head`

## Переменные окружения

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен Telegram-бота |
| `DATABASE_URL` | PostgreSQL DSN |
| `SECRET_KEY` | Секрет для JWT/сессий |
| `YOOMONEY_RECEIVER` | Номер кошелька ЮМани |
| `YOOMONEY_SECRET` | Секрет уведомлений ЮМани |
| `REMNAWAVE_URL` | URL панели Remnawave |
| `REMNAWAVE_TOKEN` | API токен Remnawave |
| `SMTP_HOST` | SMTP для email |
| `SMTP_USER` | Email отправителя |
| `SMTP_PASS` | Пароль SMTP |
| `WEBAPP_URL` | Публичный URL веб-кабинета |
| `ADMIN_IDS` | Telegram ID админов (через запятую) |
