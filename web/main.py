from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from core.config import settings
from core.database import init_db, AsyncSessionLocal
from core.plans import seed_plans_if_empty
from web.routers import auth, dashboard, admin, payments, support, admin_support, admin_promo, referral, docs, admin_docs

app = FastAPI(title="Unlock VPN", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory="web/static"), name="static")

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
app.include_router(support.router)
app.include_router(admin_support.router)
app.include_router(admin_promo.router)
app.include_router(referral.router)
app.include_router(docs.router)
app.include_router(admin_docs.router)


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
