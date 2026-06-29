from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from core.config import settings
from core.database import init_db
from web.routers import auth, dashboard, admin, payments

app = FastAPI(title="VPN Cabinet", docs_url=None, redoc_url=None)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=settings.session_max_age,
    https_only=False,  # установи True в проде с HTTPS
)

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(payments.router)


@app.get("/")
async def root():
    return RedirectResponse("/dashboard")


@app.on_event("startup")
async def startup():
    await init_db()
