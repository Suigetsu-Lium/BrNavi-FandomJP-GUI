"""Safe, structure-preserving renderer for locally saved Backrooms Wiki articles."""

from __future__ import annotations

import base64
import hashlib
import html
import mimetypes
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag


_ALLOWED_TAGS = {
    "p", "div", "span", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td",
    "ol", "ul", "li", "a", "sup", "sub", "br", "hr", "blockquote",
    "dl", "dt", "dd", "strong", "b", "em", "i", "small", "code",
    "pre", "figure", "figcaption", "img", "details", "summary",
    "fieldset", "legend", "label",
}
_DROP_SELECTORS = (
    ".toc", ".mw-editsection", ".page-header", ".page-header__title",
    ".portable-infobox", ".navbox", ".navigation-not-searchable",
    ".wds-tabs", ".page-footer", ".ad-slot", "script", "style",
    "noscript", "iframe",
)
_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,100}$")
_ALLOWED_CLASSES = {
    "reference", "references", "mw-references-wrap", "mw-cite-backlink",
    "reference-text", "mw-headline", "wikitable", "thumb", "thumbinner",
    "thumbcaption", "floatright", "floatleft", "center", "nowrap",
    "small", "plainlinks", "mw-collapsible", "mw-collapsed",
    "mw-collapsible-content", "mw-form-frame", "mw-form-button",
    "mw-form-control", "mw-frame-dashed", "mw-frame-dotted",
    "mw-frame-solid", "mw-frame-panel",
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
    return text[:80] or fallback


def _image_data_uri(path):
    path = Path(path)
    try:
        data = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{data}"


def _normalize_image_title(value):
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = text.replace("\\", "/").replace("/", " ").replace("_", " ")
    return " ".join(text.split()).casefold()


def _build_local_image_map(soup, base_url, source_path, image_dir, article_title=""):
    """Map source image URLs to URL-scoped, legacy-named, or saved HTML assets."""
    mapping = {}
    source_path = Path(source_path) if source_path else None
    image_root = Path(image_dir) if image_dir else None
    page_key = hashlib.sha256(
        str(base_url or "").strip().rstrip("/").encode("utf-8")
    ).hexdigest()[:16] if base_url else ""
    indexed_files = {}
    if image_root and page_key and image_root.is_dir():
        pattern = re.compile(rf"page_{re.escape(page_key)}_image(\d+)$", re.IGNORECASE)
        for path in image_root.glob(f"page_{page_key}_image*"):
            match = pattern.fullmatch(path.stem)
            if match and path.is_file():
                indexed_files[int(match.group(1))] = path

    legacy_files = {}
    target_title_key = _normalize_image_title(article_title)
    if image_root and target_title_key and image_root.is_dir():
        legacy_pattern = re.compile(r"(?:\s+-\s+|\s+)image(\d+)$", re.IGNORECASE)
        for path in image_root.iterdir():
            if not path.is_file() or path.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                continue
            match = legacy_pattern.search(path.stem)
            if (
                match
                and _normalize_image_title(path.stem[:match.start()]) == target_title_key
            ):
                legacy_files[int(match.group(1))] = path

    try:
        from modules.image_downloader import parse_image_info
    except Exception:
        parse_image_info = lambda _value: (None, None)

    seen_urls = set()
    image_index = 0
    for image in soup.select(".portable-infobox img, .mw-parser-output img"):
        raw_url = str(image.get("data-src") or image.get("src") or "").strip()
        download_url, _filename = parse_image_info(raw_url)
        if not download_url:
            continue
        absolute_url = urljoin(base_url, raw_url)
        if download_url in seen_urls:
            if mapping.get(download_url):
                mapping[absolute_url] = mapping[download_url]
            continue
        seen_urls.add(download_url)
        image_index += 1
        local_path = indexed_files.get(image_index) or legacy_files.get(image_index)
        if local_path:
            data_uri = _image_data_uri(local_path)
            if data_uri:
                mapping[download_url] = data_uri
                mapping[absolute_url] = data_uri

    if source_path:
        for image in soup.select(".mw-parser-output img"):
            raw_url = str(image.get("data-src") or image.get("src") or "").strip()
            if not raw_url or raw_url.startswith(("http://", "https://", "data:")):
                continue
            relative = unquote(urlsplit(raw_url).path)
            if not relative:
                continue
            try:
                candidate = (source_path.parent / relative).resolve()
                candidate.relative_to(source_path.parent.resolve())
            except (OSError, ValueError):
                continue
            if candidate.is_file():
                data_uri = _image_data_uri(candidate)
                if data_uri:
                    mapping[raw_url] = data_uri
                    mapping[urljoin(base_url, raw_url)] = data_uri
    return mapping


def _prepare_collapsible_blocks(root, soup):
    """Convert MediaWiki collapsible containers into native local details widgets."""
    for node in reversed(root.select(".mw-collapsible")):
        if node.name == "details":
            continue
        classes = [str(value) for value in node.get("class", [])]
        collapsed = "mw-collapsed" in classes
        heading = next(
            (child for child in node.find_all(re.compile(r"^h[1-6]$"), recursive=False)),
            None,
        )
        toggle = node.select_one(".mw-collapsible-toggle")
        details = soup.new_tag("details")
        details["class"] = ["mw-collapsible"]
        if not collapsed:
            details["open"] = ""

        summary = soup.new_tag("summary")
        if heading is not None:
            if toggle is not None and heading in toggle.parents:
                toggle.decompose()
            summary.append(heading.extract())
        else:
            label = toggle.get_text(" ", strip=True) if toggle is not None else ""
            if not label:
                for candidate in list(node.contents):
                    if isinstance(candidate, NavigableString) and str(candidate).strip():
                        label = str(candidate).strip()
                        candidate.extract()
                        break
                    if isinstance(candidate, Tag) and "mw-collapsible-content" not in candidate.get("class", []):
                        candidate_label = candidate.get_text(" ", strip=True)
                        if candidate_label and len(candidate_label) <= 120:
                            label = candidate_label
                            if candidate.name in {"span", "strong", "b", "p"}:
                                candidate.decompose()
                            break
            if not label or label.casefold() in {"show", "hide", "表示", "非表示"}:
                label = "詳細を表示"
            summary.string = label
        details.append(summary)

        for child in list(node.contents):
            if child is toggle:
                child.decompose()
            else:
                details.append(child.extract())
        node.replace_with(details)


def _prepare_static_forms(root, soup):
    """Keep form presentation while removing all submit and input behavior."""
    for form in root.find_all("form"):
        form.name = "div"
        form.attrs = {"class": ["mw-form-frame"]}
    for control in root.find_all(["input", "textarea", "select"]):
        placeholder = str(
            control.get("placeholder") or control.get("value") or ""
        ).strip()
        replacement = soup.new_tag("span")
        replacement["class"] = ["mw-form-control"]
        replacement.string = placeholder or " "
        control.replace_with(replacement)
    for button in root.find_all("button"):
        button.name = "span"
        button.attrs = {"class": ["mw-form-button"]}


def _visual_classes(node):
    classes = []
    style = str(node.get("style") or "").casefold()
    border_style = re.search(r"border(?:-[a-z-]+)?\s*:[^;]*(dashed|dotted|solid)", style)
    if border_style:
        classes.append("mw-frame-" + border_style.group(1))
    elif re.search(r"border-style\s*:\s*(dashed|dotted|solid)", style):
        match = re.search(r"border-style\s*:\s*(dashed|dotted|solid)", style)
        classes.append("mw-frame-" + match.group(1))
    if "background" in style or "background-color" in style:
        classes.append("mw-frame-panel")
    return classes


def _serialize(node, base_url, local_image_map=None, source_path=None):
    if isinstance(node, Comment):
        return ""
    if isinstance(node, NavigableString):
        return html.escape(str(node), quote=False)
    if not isinstance(node, Tag):
        return ""
    name = str(node.name or "").casefold()
    if name not in _ALLOWED_TAGS:
        return "".join(
            _serialize(child, base_url, local_image_map, source_path)
            for child in node.children
        )

    attrs = []
    element_id = _safe_id(node.get("id"))
    if element_id:
        attrs.append(f'id="{html.escape(element_id, quote=True)}"')
    classes = [
        str(value) for value in node.get("class", [])
        if str(value) in _ALLOWED_CLASSES
    ]
    classes.extend(_visual_classes(node))
    if name == "table":
        classes.append("term-table")
    elif name in {"ol", "ul"}:
        classes.append("term-list")
    elif name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        classes.append("term-heading")
    if classes:
        attrs.append(
            f'class="{html.escape(" ".join(dict.fromkeys(classes)), quote=True)}"'
        )
    if name == "details" and node.has_attr("open"):
        attrs.append("open")

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
            attrs.append(
                f'data-wiki-target="{html.escape(wiki_target, quote=True)}"'
            )
        if node.get("title"):
            attrs.append(
                f'title="{html.escape(str(node.get("title")), quote=True)}"'
            )
    elif name == "img":
        raw_src = str(node.get("data-src") or node.get("src") or "").strip()
        absolute_src = urljoin(base_url, raw_src)
        src = (local_image_map or {}).get(raw_src) or (local_image_map or {}).get(absolute_src)
        if not src:
            src, _wiki_target = _safe_url(raw_src, base_url)
        if not src or src.startswith("#"):
            return ""
        attrs.append(f'src="{html.escape(src, quote=True)}"')
        if node.get("alt") is not None:
            attrs.append(f'alt="{html.escape(str(node.get("alt")), quote=True)}"')
        return "<img " + " ".join(attrs) + ">"

    attr_text = (" " + " ".join(attrs)) if attrs else ""
    if name in {"br", "hr"}:
        return f"<{name}{attr_text}>"
    inner = "".join(
        _serialize(child, base_url, local_image_map, source_path)
        for child in node.children
    )
    if name == "table":
        return '<div class="term-table-wrap">' + f"<{name}{attr_text}>{inner}</{name}>" + "</div>"
    return f"<{name}{attr_text}>{inner}</{name}>"


def has_portable_infobox(html_text):
    """Return whether the original saved Wiki HTML contains a portable infobox."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    return bool(soup.select_one(".portable-infobox"))


def render_article_html(html_text, base_url="", source_path=None, image_dir=None, article_title=""):
    """Return sanitized article DOM, TOC, and source-infobox presence."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    has_source_infobox = bool(soup.select_one(".portable-infobox"))
    root = (
        soup.select_one("#mw-content-text .mw-parser-output")
        or soup.select_one(".mw-parser-output")
        or soup.select_one("#mw-content-text")
        or soup.select_one("article")
    )
    if root is None:
        return {
            "html": "", "toc": [], "title": "",
            "has_source_infobox": has_source_infobox,
        }

    _prepare_collapsible_blocks(root, soup)
    _prepare_static_forms(root, soup)
    local_image_map = _build_local_image_map(
        soup, base_url, source_path, image_dir, article_title
    )

    for selector in _DROP_SELECTORS:
        for element in root.select(selector):
            element.decompose()

    title = ""
    heading_nodes = root.select("h2, h3, h4, h5, h6")
    toc = []
    used_ids = set()
    for index, heading in enumerate(heading_nodes, 1):
        heading_text = heading.get_text(" ", strip=True)
        if not title and heading.name == "h2":
            title = heading_text
        element_id = _safe_id(heading.get("id"))
        if not element_id:
            element_id = _slug(heading_text, f"article-section-{index}")
        base_id = element_id
        suffix = 2
        while element_id in used_ids:
            element_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(element_id)
        heading["id"] = element_id
        toc.append({
            "id": element_id,
            "title": heading_text,
            "level": int(heading.name[1]),
        })

    body = "".join(
        _serialize(child, base_url, local_image_map, source_path)
        for child in root.children
    )
    toc_html = ""
    if toc:
        items = "".join(
            '<li class="term-toc-level-' + str(item["level"]) + '">'
            '<a href="#' + html.escape(item["id"], quote=True) + '">'
            + html.escape(item["title"]) + "</a></li>"
            for item in toc
        )
        toc_html = (
            '<nav class="term-toc" aria-label="記事目次">'
            "<strong>目次</strong><ol>" + items + "</ol></nav>"
        )
    return {
        "html": toc_html + '<div class="term-article">' + body + "</div>",
        "toc": toc,
        "title": title,
        "has_source_infobox": has_source_infobox,
    }


def render_article_file(path, base_url="", image_dir=None, article_title=""):
    """Read a saved HTML page and render its article body with local assets."""
    source_path = Path(path)
    try:
        source = source_path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {
            "html": "", "toc": [], "title": "",
            "has_source_infobox": False,
        }
    return render_article_html(
        source,
        base_url=base_url,
        source_path=source_path,
        image_dir=image_dir,
        article_title=article_title,
    )


def render_term_article(html_text, base_url=""):
    """Backward-compatible term article renderer."""
    return render_article_html(html_text, base_url=base_url)


def render_term_article_file(path, base_url=""):
    """Backward-compatible term article file renderer."""
    return render_article_file(path, base_url=base_url)
