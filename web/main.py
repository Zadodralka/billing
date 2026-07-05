from urllib.parse import urlparse
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.gzip import GZipMiddleware
from core.config import settings
from core.database import init_db, AsyncSessionLocal
from core.plans import seed_plans_if_empty
from web.routers import auth, dashboard, admin, payments, support, admin_support, admin_promo, referral, docs, admin_docs, gift

app = FastAPI(title="Unlock VPN", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory="web/static"), name="static")

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
async def root(request: Request, ref: str = None):
    if ref:
        request.session["pending_ref"] = ref.strip().upper()
    return RedirectResponse("/dashboard")


@app.on_event("startup")
async def startup():
    await init_db()
    async with AsyncSessionLocal() as session:
        await seed_plans_if_empty(session)
