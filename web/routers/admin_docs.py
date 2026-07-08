import re
import uuid
import os
from fastapi import APIRouter, Request, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
import logging
import traceback
from core.database import get_db
from core.models import Article
from core.version import APP_VERSION
from core.timezone import to_local
from web.routers.auth import require_admin, User

logger = logging.getLogger("admin.docs")
router = APIRouter(prefix="/admin/docs")
templates = Jinja2Templates(directory="web/templates")
templates.env.globals["app_version"] = APP_VERSION
templates.env.filters["localtime"] = to_local

UPLOAD_DIR = "web/static/uploads/docs"
ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_FILE_SIZE = 8 * 1024 * 1024  # 8 MB


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[а-яёА-ЯЁ]", lambda m: {
        "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"yo","ж":"zh","з":"z",
        "и":"i","й":"j","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r",
        "с":"s","т":"t","у":"u","ф":"f","х":"h","ц":"ts","ч":"ch","ш":"sh","щ":"sch",
        "ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
    }.get(m.group().lower(), ""), text)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-") or "article"


@router.get("", response_class=HTMLResponse)
async def admin_docs_list(
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
):
    result = await session.execute(
        select(Article).order_by(Article.sort_order, Article.created_at)
    )
    articles = result.scalars().all()
    return templates.TemplateResponse(request, "admin/docs_list.html", {
        "user": admin, "articles": articles,
    })


@router.get("/new", response_class=HTMLResponse)
async def admin_docs_new(request: Request, admin: User = Depends(require_admin)):
    return templates.TemplateResponse(request, "admin/docs_edit.html", {
        "user": admin, "article": None, "mode": "create",
    })


@router.post("/create")
async def admin_docs_create(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    title: str = Form(...),
    excerpt: str = Form(""),
    content: str = Form(""),
    is_published: str = Form(None),
    sort_order: int = Form(0),
):
    try:
        base_slug = _slugify(title)
        slug = base_slug
        i = 1
        while True:
            exists = await session.execute(select(Article).where(Article.slug == slug))
            if not exists.scalar_one_or_none():
                break
            slug = f"{base_slug}-{i}"
            i += 1

        article = Article(
            title=title.strip(),
            slug=slug,
            excerpt=excerpt.strip() or None,
            content=content,
            is_published=is_published == "true",
            sort_order=sort_order,
        )
        session.add(article)
        await session.commit()
        return JSONResponse({"ok": True, "id": article.id})
    except Exception as e:
        await session.rollback()
        logger.error(f"create article failed: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/{article_id}/edit", response_class=HTMLResponse)
async def admin_docs_edit(
    article_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
):
    result = await session.execute(select(Article).where(Article.id == article_id))
    article = result.scalar_one_or_none()
    if not article:
        raise HTTPException(404, "Статья не найдена")
    return templates.TemplateResponse(request, "admin/docs_edit.html", {
        "user": admin, "article": article, "mode": "edit",
    })


@router.post("/{article_id}/update")
async def admin_docs_update(
    article_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    title: str = Form(...),
    excerpt: str = Form(""),
    content: str = Form(""),
    is_published: str = Form(None),
    sort_order: int = Form(0),
):
    try:
        result = await session.execute(select(Article).where(Article.id == article_id))
        article = result.scalar_one_or_none()
        if not article:
            return JSONResponse({"ok": False, "error": "Статья не найдена"}, status_code=404)

        article.title = title.strip()
        article.excerpt = excerpt.strip() or None
        article.content = content
        article.is_published = is_published == "true"
        article.sort_order = sort_order
        await session.commit()
        return JSONResponse({"ok": True})
    except Exception as e:
        await session.rollback()
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/{article_id}/delete")
async def admin_docs_delete(
    article_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
):
    try:
        await session.execute(delete(Article).where(Article.id == article_id))
        await session.commit()
        return JSONResponse({"ok": True})
    except Exception as e:
        await session.rollback()
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/{article_id}/toggle-publish")
async def admin_docs_toggle(
    article_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
):
    result = await session.execute(select(Article).where(Article.id == article_id))
    article = result.scalar_one_or_none()
    if not article:
        return JSONResponse({"ok": False, "error": "Не найдено"}, status_code=404)
    article.is_published = not article.is_published
    await session.commit()
    return JSONResponse({"ok": True, "is_published": article.is_published})


@router.post("/upload-image")
async def upload_image(
    admin: User = Depends(require_admin),
    file: UploadFile = File(...),
):
    """Загрузка скриншота — возвращает URL для вставки в Markdown"""
    try:
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in ALLOWED_EXTS:
            return JSONResponse({"ok": False, "error": f"Формат не поддерживается. Используйте: {', '.join(ALLOWED_EXTS)}"}, status_code=400)

        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            return JSONResponse({"ok": False, "error": "Файл слишком большой (максимум 8 MB)"}, status_code=400)

        filename = f"{uuid.uuid4().hex}{ext}"
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        filepath = os.path.join(UPLOAD_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(content)

        url = f"/static/uploads/docs/{filename}"
        return JSONResponse({"ok": True, "url": url})
    except Exception as e:
        logger.error(f"upload_image failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
