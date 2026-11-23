"""Safe, structure-preserving renderer for locally saved Backrooms term articles."""

from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag


_ALLOWED_TAGS = {
    "p", "div", "span", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td",
    "ol", "ul", "li", "a", "sup", "sub", "br", "hr", "blockquote",
    "dl", "dt", "dd", "strong", "b", "em", "i", "small", "code",
    "pre", "figure", "figcaption", "img",
}
_DROP_SELECTORS = (
    ".toc", ".mw-editsection", ".page-header", ".page-header__title",
    ".portable-infobox", ".navbox", ".navigation-not-searchable",
    ".wds-tabs", ".page-footer", ".ad-slot", "script", "style",
    "noscript", "iframe", "form", "button",
)
_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,100}$")
_ALLOWED_CLASSES = {
    "reference", "references", "mw-references-wrap", "mw-cite-backlink",
    "reference-text", "mw-headline", "wikitable", "thumb", "thumbinner",
    "thumbcaption", "floatright", "floatleft", "center", "nowrap",
    "small", "plainlinks",
}


def _safe_id(value):
    value = str(value or "").strip()
    return value if _ID_PATTERN.fullmatch(value) else ""


def _safe_url(value, base_url):
    value = str(value or "").strip()
    if not value:
        return "", ""
    if value.startswith("#"):
        fragment = value[1:]
        return ("#" + fragment, "") if _safe_id(fragment) else ("", "")
    absolute = urljoin(base_url, value)
    parsed = urlsplit(absolute)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return "", ""
    if parsed.hostname and parsed.hostname.casefold() == "backrooms.fandom.com":
        prefix = "/ja/wiki/"
        if parsed.path.startswith(prefix):
            target = unquote(parsed.path[len(prefix):]).replace("_", " ").strip()
            if target:
                return "#", target
    return absolute, ""


def _slug(value, fallback):
    text = re.sub(r"[^\w.-]+", "-", str(value or "").strip(), flags=re.UNICODE).strip("-")
    return (text[:80] or fallback)


def _serialize(node, base_url):
    # BeautifulSoup Comment inherits NavigableString, so it must be
    # discarded before the generic text-node serialization branch.
    if isinstance(node, Comment):
        return ""
    if isinstance(node, NavigableString):
        return html.escape(str(node), quote=False)
    if not isinstance(node, Tag):
        return ""
    name = str(node.name or "").casefold()
    if name not in _ALLOWED_TAGS:
        return "".join(_serialize(child, base_url) for child in node.children)

    attrs = []
    element_id = _safe_id(node.get("id"))
    if element_id:
        attrs.append(f'id="{html.escape(element_id, quote=True)}"')
    classes = [
        str(value) for value in node.get("class", [])
        if str(value) in _ALLOWED_CLASSES
    ]
    if name == "table":
        classes.extend(["term-table"])
    elif name in {"ol", "ul"}:
        classes.append("term-list")
    elif name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        classes.append("term-heading")
    if classes:
        attrs.append(f'class="{html.escape(" ".join(dict.fromkeys(classes)), quote=True)}"')

    if name in {"td", "th"}:
        for attr in ("colspan", "rowspan"):
            try:
                number = int(node.get(attr, 1))
            except (TypeError, ValueError):
                number = 1
            if 1 <= number <= 50:
                attrs.append(f'{attr}="{number}"')
        scope = str(node.get("scope") or "").casefold()
        if name == "th" and scope in {"row", "col", "rowgroup", "colgroup"}:
            attrs.append(f'scope="{scope}"')
    if name == "ol":
        try:
            start = int(node.get("start", 1))
        except (TypeError, ValueError):
            start = 1
        if start != 1:
            attrs.append(f'start="{start}"')

    if name == "a":
        href, wiki_target = _safe_url(node.get("href"), base_url)
        if href:
            attrs.append(f'href="{html.escape(href, quote=True)}"')
        if wiki_target:
            attrs.append(f'data-wiki-target="{html.escape(wiki_target, quote=True)}"')
        if node.get("title"):
            attrs.append(f'title="{html.escape(str(node.get("title")), quote=True)}"')
    elif name == "img":
        src, _ = _safe_url(node.get("src"), base_url)
        if not src or src.startswith("#"):
            return ""
        attrs.append(f'src="{html.escape(src, quote=True)}"')
        if node.get("alt") is not None:
            attrs.append(f'alt="{html.escape(str(node.get("alt")), quote=True)}')
        return "<img " + " ".join(attrs) + ">"

    attr_text = (" " + " ".join(attrs)) if attrs else ""
    if name in {"br", "hr"}:
        return f"<{name}{attr_text}>"
    inner = "".join(_serialize(child, base_url) for child in node.children)
    if name == "table":
        return '<div class="term-table-wrap">' + f"<{name}{attr_text}>{inner}</{name}>" + "</div>"
    return f"<{name}{attr_text}>{inner}</{name}>"


def render_term_article(html_text, base_url=""):
    """Return sanitized article HTML and an automatically generated heading TOC."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    root = (
        soup.select_one("#mw-content-text .mw-parser-output")
        or soup.select_one(".mw-parser-output")
        or soup.select_one("#mw-content-text")
        or soup.select_one("article")
    )
    if root is None:
        return {"html": "", "toc": [], "title": ""}

    for selector in _DROP_SELECTORS:
        for element in root.select(selector):
            element.decompose()

    title = ""
    heading_nodes = root.select("h2, h3, h4, h5, h6")
    toc = []
    used_ids = set()
    for index, heading in enumerate(heading_nodes, 1):
        text = heading.get_text(" ", strip=True)
        if not title and heading.name == "h2":
            title = text
        element_id = _safe_id(heading.get("id"))
        if not element_id:
            element_id = _slug(text, f"term-section-{index}")
        base_id = element_id
        suffix = 2
        while element_id in used_ids:
            element_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(element_id)
        heading["id"] = element_id
        toc.append({
            "id": element_id,
            "title": text,
            "level": int(heading.name[1]),
        })

    body = "".join(_serialize(child, base_url) for child in root.children)
    toc_html = ""
    if toc:
        items = "".join(
            '<li class="term-toc-level-' + str(item["level"]) + '">'
            '<a href="#' + html.escape(item["id"], quote=True) + '">'
            + html.escape(item["title"]) + "</a></li>"
            for item in toc
        )
        toc_html = '<nav class="term-toc" aria-label="記事目次"><strong>目次</strong><ol>' + items + "</ol></nav>"
    return {
        "html": toc_html + '<div class="term-article">' + body + "</div>",
        "toc": toc,
        "title": title,
    }


def render_term_article_file(path, base_url=""):
    """Read a saved HTML page and render its article body safely."""
    try:
        source = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {"html": "", "toc": [], "title": ""}
    return render_term_article(source, base_url=base_url)
