from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.database import get_db
from core.models import Article
from core.markdown_render import render_article_markdown
from core.version import APP_VERSION
from core.timezone import to_local
from web.routers.auth import require_user, User

router = APIRouter(prefix="/docs")
templates = Jinja2Templates(directory="web/templates")
templates.env.globals["app_version"] = APP_VERSION
templates.env.filters["localtime"] = to_local


@router.get("", response_class=HTMLResponse)
async def docs_list(
    request: Request,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_db),
):
    result = await session.execute(
        select(Article)
        .where(Article.is_published == True)
        .order_by(Article.sort_order, Article.created_at)
    )
    articles = result.scalars().all()

    return templates.TemplateResponse(request, "docs_list.html", {
        "user": user,
        "articles": articles,
    })


@router.get("/{slug}", response_class=HTMLResponse)
async def docs_article(
    slug: str,
    request: Request,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_db),
):
    result = await session.execute(
        select(Article).where(Article.slug == slug, Article.is_published == True)
    )
    article = result.scalar_one_or_none()
    if not article:
        raise HTTPException(404, "Инструкция не найдена")

    return templates.TemplateResponse(request, "docs_article.html", {
        "user": user,
        "article": article,
        "article_html": render_article_markdown(article.content),
    })
