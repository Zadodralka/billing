import markdown
import nh3

# Раньше контент статей (/docs/{slug}) рендерился в браузере через marked.js +
# DOMPurify, подключаемые с cdnjs.cloudflare.com уже после DOMContentLoaded.
# У этого было два минуса: при недоступном CDN или отключённом JS страница
# показывала пустой div вместо текста инструкции, и статья не попадала в
# HTML-ответ сервера как есть (что не важно для SEO конкретно тут, т.к. раздел
# закрыт логином, но лишний клиентский рендер-проход всё равно ни к чему).
# Теперь то же самое делается один раз на сервере при заходе на страницу.
#
# Список extensions подобран так, чтобы покрывать GitHub Flavored Markdown,
# который админ-редактор статей (admin/docs_edit.html) явно обещает
# авторам в подсказке под полем ("Поддерживается GitHub Flavored Markdown"):
#   - extra          - таблицы, fenced code, footnotes, def_list и т.п.
#   - nl2br          - одиночный перенос строки = <br>, как markedjs с breaks:true
#   - sane_lists     - более предсказуемые вложенные списки
#   - pymdownx.tilde - ~~зачёркнутый текст~~ (в GFM есть, в 'extra' - нет)
#   - pymdownx.magiclink - автоссылки на голые URL/email без markdown-синтаксиса
_MD_EXTENSIONS = ["extra", "nl2br", "sane_lists", "pymdownx.tilde", "pymdownx.magiclink"]

# Разрешённый набор тегов/атрибутов - осознанно шире, чем реально генерирует
# наш набор extensions выше, чтобы markdown-контент старых статей (если он
# когда-то содержал сырой HTML) не терял форматирование при повторном рендере.
# XSS-векторы (script, оnclick и т.п. события, style) исключены полностью -
# nh3 по умолчанию не пропускает неизвестные теги/атрибуты, а не наоборот.
_ALLOWED_TAGS = {
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "b", "i", "s", "del",
    "a", "img",
    "ul", "ol", "li",
    "code", "pre",
    "blockquote",
    "table", "thead", "tbody", "tr", "th", "td",
}
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
}


def render_article_markdown(source: str) -> str:
    """Markdown -> санитайзнутый HTML для публичной страницы статьи."""
    raw_html = markdown.markdown(source or "", extensions=_MD_EXTENSIONS)
    return nh3.clean(raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRIBUTES)
