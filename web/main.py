from datetime import datetime, timezone
from urllib.parse import urlparse
from fastapi import FastAPI, Request, Depends
from fastapi.responses import RedirectResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.gzip import GZipMiddleware
from core.config import settings
from core.database import init_db, AsyncSessionLocal, get_db
from core.plans import seed_plans_if_empty
from web.routers import auth, dashboard, admin, payments, support, admin_support, admin_promo, referral, docs, admin_docs, gift

app = FastAPI(title="Unlock VPN", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory="web/templates")

app.mount("/static", StaticFiles(directory="web/static"), name="static")

# Публичный лендинг на "/" - разрешаем ботам обходить только его, всё остальное
# (кабинет, админка, оплата) требует авторизации и индексировать/сканировать
# незачем. См. также web.routers.auth.get_current_user / get_bot_username.
_ROBOTS_TXT = f"User-agent: *\nAllow: /$\nDisallow: /\n\nSitemap: {settings.webapp_url}/sitemap.xml\n"
_SITEMAP_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>{settings.webapp_url}/</loc><changefreq>monthly</changefreq><priority>1.0</priority></url>
</urlset>
"""

# Пути, куда легитимно приходят запросы без Origin/Referer (сервер-сервер вебхуки)
_CSRF_EXEMPT_PATHS = {"/payment/webhook/yoomoney"}


@app.middleware("http")
async def same_origin_check(request: Request, call_next):
    """
    Базовая защита от CSRF: авторизация в приложении полностью на cookie-сессии,
    поэтому для любых изменяющих состояние запросов (не GET/HEAD/OPTIONS) проверяем,
    что Origin/Referer совпадает с хостом приложения. Работает как defense-in-depth
    поверх SameSite=lax на cookie сессии (см. SessionMiddleware ниже).
    """
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path not in _CSRF_EXEMPT_PATHS:
        origin = request.headers.get("origin") or request.headers.get("referer")
        if origin:
            origin_host = urlparse(origin).netloc
            # Сверяем и с Host запроса, и с публичным WEBAPP_URL - за обратным прокси
            # request.url.netloc не всегда совпадает с публичным доменом.
            allowed_hosts = {request.url.netloc, urlparse(settings.webapp_url).netloc}
            if origin_host and origin_host not in allowed_hosts:
                return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
    return await call_next(request)


app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=settings.session_max_age,
    https_only=settings.session_https_only,
    same_site="lax",
)

# Страницы кабинета несут по 5-10 KB инлайнового CSS/JS в каждом ответе -
# сжатие ощутимо уменьшает трафик и время загрузки без какого-либо риска.
app.add_middleware(GZipMiddleware, minimum_size=500)

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(payments.router)
app.include_router(support.router)
app.include_router(admin_support.router)
app.include_router(admin_promo.router)
app.include_router(referral.router)
app.include_router(docs.router)
app.include_router(admin_docs.router)
app.include_router(gift.router)


@app.get("/")
async def root(request: Request, ref: str = None, session: AsyncSession = Depends(get_db)):
    if ref:
        request.session["pending_ref"] = ref.strip().upper()

    # Уже вошедших сразу отправляем в кабинет - лендинг нужен только тем, кто
    # ещё не авторизован (в т.ч. поисковым ботам, для которых это единственная
    # публичная страница сайта, см. _ROBOTS_TXT выше).
    from web.routers.auth import get_current_user, get_bot_username
    user = await get_current_user(request, session)
    if user:
        return RedirectResponse("/dashboard")

    bot_username = await get_bot_username()
    contact_url = f"https://t.me/{bot_username}" if bot_username else "https://t.me/"
    return templates.TemplateResponse(request, "landing.html", {
        "contact_url": contact_url,
        "current_year": datetime.now(timezone.utc).year,
    })


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return _ROBOTS_TXT


@app.get("/sitemap.xml")
async def sitemap_xml():
    return Response(_SITEMAP_XML, media_type="application/xml")


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


@app.on_event("startup")
async def startup():
    await init_db()
    async with AsyncSessionLocal() as session:
        await seed_plans_if_empty(session)
