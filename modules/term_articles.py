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
    "p", "div", "span", "section", "article", "aside", "nav",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td",
    "ol", "ul", "li", "a", "sup", "sub", "br", "hr", "blockquote",
    "dl", "dt", "dd", "strong", "b", "em", "i", "small", "code",
    "pre", "figure", "figcaption", "img", "details", "summary",
    "fieldset", "legend", "label",
}
_DROP_SELECTORS = (
    ".toc", ".mw-editsection", ".page-header", ".page-header__title",
    ".navbox", ".navigation-not-searchable",
    ".page-footer", ".ad-slot", ".ratebox", ".page-rating-widget",
    ".article-vote", ".article-voting", ".mw-customtoggle-voting",
    "script", "style", "noscript", "iframe",
)
_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,100}$")
_ALLOWED_CLASSES = {
    "reference", "references", "mw-references-wrap", "mw-cite-backlink",
    "reference-text", "mw-headline", "wikitable", "thumb", "thumbinner",
    "thumbcaption", "floatright", "floatleft", "center", "nowrap",
    "small", "plainlinks", "mw-collapsible", "mw-collapsed",
    "mw-collapsible-content", "mw-form-frame", "mw-form-button",
    "mw-form-control", "mw-frame-dashed", "mw-frame-dotted",
    "mw-frame-solid", "mw-frame-panel", "portable-infobox",
    "pi-data", "pi-data-value", "pi-data-label", "pi-item",
    "pi-image", "pi-image-thumbnail", "pi-image-caption",
    "pi-header", "pi-title", "pi-media",
    "gallery", "gallerybox", "gallerytext", "gallerycaption",
    "wikia-gallery", "wikia-gallery-item", "wikia-gallery-caption",
    "wds-tabber", "wds-tabs",
    "wds-tab", "wds-tab__title", "wds-tab__content",
    "wds-tab__content-inner", "wds-is-current",
    "tabber", "tabbernav", "tabbertab", "tabber__header",
    "tabber__tabs", "tabber__tab", "tabber__panel",
    "tabber__content", "tabber__section", "tabber__tab--active",
    "tabber__panel--active",
    "image-lightbox-link", "article-inline-icon",
    "article-infobox-main-image",
}


def _safe_id(value):
    value = str(value or "").strip()
    return value if _ID_PATTERN.fullmatch(value) else ""


def _is_article_credit_text(value):
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()
    return (
        text.startswith("\u3053\u306e\u8a18\u4e8b\u306f")
        and any(word in text for word in (
            "\u4f5c\u6210", "\u57f7\u7b46", "\u7ffb\u8a33", "\u6539\u5909", "\u7de8\u96c6"
        ))
    )


def _extract_article_credits(root):
    """Collect author/translator credits and remove their original DOM nodes."""
    credits = []
    for node in list(root.find_all(["p", "small", "div", "span"])):
        if node.parent is None:
            continue
        # Prefer the smallest block carrying the credit; do not remove an
        # enclosing article wrapper when a nested paragraph contains it.
        if node.find(["p", "div", "section", "article"]) is not None:
            continue
        text = re.sub(
            r"\s+", " ", unicodedata.normalize("NFKC", node.get_text(" ", strip=True))
        ).strip()
        if not _is_article_credit_text(text):
            continue
        if text not in credits:
            credits.append(text)
        node.decompose()
    return credits


def _is_fandom_widget_residue(value):
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).casefold()
    if "(+/-) vote" in text:
        return True
    if "\u8aad\u307f\u8fbc\u307f\u4e2d" in text and "\u0070\u0063\u7248\u3067\u8868\u793a" in text and (
        "\u30c7\u30b9\u30af\u30c8\u30c3\u30d7\u8868\u793a" in text
        or "\u30da\u30fc\u30b8\u306e\u4e00\u756a\u4e0b" in text
        or "\u3044\u3064\u307e\u3067\u7d4c\u3063\u3066\u3082" in text
    ):
        return True
    return (
        "\u0070\u0063\u7248\u3067\u8868\u793a" in text
        and "\u30c7\u30b9\u30af\u30c8\u30c3\u30d7\u8868\u793a\u3078\u5207\u308a\u66ff\u3048" in text
    )


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


def _canonical_image_key(raw_url, base_url=""):
    raw_url = str(raw_url or "").strip()
    if not raw_url:
        return ""
    if raw_url.casefold().startswith("data:image/"):
        return "data:" + hashlib.sha256(raw_url.encode("utf-8")).hexdigest()
    absolute_url = urljoin(base_url, raw_url)
    try:
        from modules.image_downloader import parse_image_info
        download_url, _filename = parse_image_info(absolute_url)
    except Exception:
        download_url = None
    if download_url:
        parsed_download = urlsplit(str(download_url))
        return f"{parsed_download.scheme.casefold()}://{parsed_download.netloc.casefold()}{parsed_download.path}".casefold()
    parsed = urlsplit(absolute_url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}{parsed.path}".casefold()
    return absolute_url.split("#", 1)[0].casefold()


def _article_image_caption_node(image):
    """Find a semantic caption attached to a figure, thumbnail, or gallery item."""
    figure = image.find_parent("figure")
    if figure is not None:
        caption = figure.find("figcaption")
        if caption is not None:
            return caption

    thumb = image.find_parent(class_="thumbinner")
    if thumb is not None:
        caption = thumb.select_one(".thumbcaption")
        if caption is not None:
            return caption

    caption_selectors = (
        ".gallerytext, .gallerycaption, .wikia-gallery-caption, "
        ".pi-image-caption, .thumbcaption, figcaption"
    )
    for ancestor in image.parents:
        if not isinstance(ancestor, Tag):
            continue
        classes = set(str(value) for value in ancestor.get("class", []))
        if classes.intersection({"gallerybox", "wikia-gallery-item", "pi-image"}):
            caption = ancestor.select_one(caption_selectors)
            if caption is not None:
                return caption
    return None


def _deduplicate_article_images(root, base_url="", local_image_map=None):
    """Keep one representative per local/source image, preferring its captioned figure."""
    groups = {}
    for image in root.find_all("img"):
        raw_url = image.get("data-src") or image.get("src") or ""
        key = _canonical_image_key(raw_url, base_url)
        if not key:
            continue
        caption_node = _article_image_caption_node(image)
        has_caption = bool(caption_node and caption_node.get_text(" ", strip=True))
        figure = image.find_parent("figure")
        thumb_container = image.find_parent(class_="thumbinner")
        score = 4 if has_caption else 0
        score += 2 if figure or thumb_container else 0
        score += 1 if str(image.get("alt") or "").strip() else 0
        groups.setdefault(key, []).append((score, image))

    removed_wrappers = []
    for candidates in groups.values():
        if len(candidates) < 2:
            continue
        candidates.sort(key=lambda item: item[0], reverse=True)
        for _score, duplicate in candidates[1:]:
            wrapper = duplicate.find_parent("figure") or duplicate.find_parent(class_="thumbinner")
            duplicate.decompose()
            if wrapper and not wrapper.find("img"):
                removed_wrappers.append(wrapper)

    seen_wrapper_ids = set()
    for wrapper in removed_wrappers:
        if id(wrapper) in seen_wrapper_ids:
            continue
        seen_wrapper_ids.add(id(wrapper))
        if wrapper.parent is not None:
            wrapper.decompose()

    for anchor in root.find_all("a"):
        if anchor.find("img") is None and not anchor.get_text(" ", strip=True):
            anchor.decompose()


def get_article_image_roles(html_text, base_url=""):
    """Return URL-keyed download image indexes, excluding infobox data icons."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    try:
        from modules.image_downloader import parse_image_info
    except Exception:
        parse_image_info = lambda _value: (None, None)

    seen = set()
    photo_indices = []
    image_keys = {}
    main_image_index = None
    main_infobox = soup.select_one(".portable-infobox")
    for image in soup.select(".portable-infobox img, .mw-parser-output img"):
        raw_url = str(image.get("data-src") or image.get("src") or "").strip()
        download_url, _filename = parse_image_info(raw_url)
        if not download_url:
            continue
        key = str(download_url).casefold()
        if key in seen:
            continue
        seen.add(key)
        image_index = len(seen)
        image_keys[image_index] = _canonical_image_key(raw_url, base_url)

        ancestors = list(image.parents)
        in_pi_image = any(
            ancestor.name == "figure" and "pi-image" in ancestor.get("class", [])
            or "pi-image" in ancestor.get("class", [])
            for ancestor in ancestors if isinstance(ancestor, Tag)
        )
        in_pi_data = any(
            "pi-data" in ancestor.get("class", [])
            for ancestor in ancestors if isinstance(ancestor, Tag)
        )
        icon_class = any(
            any(token in str(class_name).casefold() for token in ("icon", "badge", "status-symbol"))
            for class_name in image.get("class", [])
        )
        small_dimension = any(
            str(image.get(dimension) or "").isdigit()
            and 1 <= int(image.get(dimension)) <= 88
            for dimension in ("width", "height")
        )
        small_scaled_asset = bool(re.search(
            r"/scale-to-width-down/(?:[1-9]\d?)\b", raw_url, re.IGNORECASE
        ))
        image_infobox = next(
            (
                ancestor for ancestor in ancestors
                if isinstance(ancestor, Tag)
                and "portable-infobox" in ancestor.get("class", [])
            ),
            None,
        )
        if in_pi_image and image_infobox is main_infobox and main_image_index is None:
            main_image_index = image_index
        if (in_pi_data or icon_class or small_dimension or small_scaled_asset) and not in_pi_image:
            continue
        photo_indices.append(image_index)

    if main_image_index is not None and main_image_index not in photo_indices:
        photo_indices.insert(0, main_image_index)
    if main_image_index is None and photo_indices and main_infobox is None:
        main_image_index = photo_indices[0]
    return {
        "main_image_index": main_image_index,
        "main_image_key": image_keys.get(main_image_index, ""),
        "photo_indices": photo_indices,
        "image_keys": image_keys,
    }


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


def _is_compact_table_candidate(table):
    """Identify long, narrow, regular tables suitable for timetable-style layout."""
    if table.find("table") is not None or table.find("tfoot", recursive=False) is not None:
        return False

    rows = [
        row for row in table.find_all("tr")
        if row.find_parent("table") is table
    ]
    if not rows:
        return False

    header_rows = []
    thead = table.find("thead", recursive=False)
    if thead is not None:
        header_rows = list(thead.find_all("tr", recursive=False))
    elif rows[0].find(["th"], recursive=False) is not None:
        header_rows = [rows[0]]
    if not header_rows:
        return False

    data_rows = [row for row in rows if row not in header_rows]
    if len(data_rows) < 14:
        return False

    first_cells = header_rows[-1].find_all(["th", "td"], recursive=False)
    column_count = len(first_cells)
    if not 2 <= column_count <= 5:
        return False

    for row in rows:
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) != column_count:
            return False
        for cell in cells:
            if cell.has_attr("rowspan") or cell.has_attr("colspan"):
                try:
                    if int(cell.get("rowspan", 1)) != 1 or int(cell.get("colspan", 1)) != 1:
                        return False
                except (TypeError, ValueError):
                    return False
            if cell.find(["p", "ul", "ol", "div", "table", "img", "figure"]) is not None:
                return False
            text = re.sub(r"\\s+", " ", cell.get_text(" ", strip=True)).strip()
            if len(text) > 42:
                return False
        row_text = " ".join(cell.get_text(" ", strip=True) for cell in cells)
        if len(row_text) > 100:
            return False

    return True


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
    if name == "a" and node.find("img") is not None:
        classes.append("image-lightbox-link")
    if name == "img":
        raw_image_url = str(node.get("data-src") or node.get("src") or "").strip()
        image_ancestors = [parent for parent in node.parents if isinstance(parent, Tag)]
        in_pi_data = any("pi-data" in parent.get("class", []) for parent in image_ancestors)
        in_pi_image = any("pi-image" in parent.get("class", []) for parent in image_ancestors)
        small_dimension = any(
            str(node.get(dimension) or "").isdigit()
            and 1 <= int(node.get(dimension)) <= 88
            for dimension in ("width", "height")
        )
        small_scaled_asset = bool(re.search(
            r"/scale-to-width-down/(?:[1-9]\d?)\b", raw_image_url, re.IGNORECASE
        ))
        if in_pi_data or (small_dimension or small_scaled_asset) and not in_pi_image:
            classes.append("article-inline-icon")
        if in_pi_image:
            classes.append("article-infobox-main-image")
    classes.extend(_visual_classes(node))
    if name == "table":
        classes.append("term-table")
        if _is_compact_table_candidate(node):
            attrs.append('data-compact-table="true"')
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
        raw_href = str(node.get("href") or "").strip()
        parsed_href = urlsplit(urljoin(base_url, raw_href)) if raw_href else None
        path_lower = unquote(parsed_href.path).casefold() if parsed_href else ""
        namespace_match = re.search(r"/(?:ja/)?wiki/([^/:]+):", path_lower)
        namespace = namespace_match.group(1) if namespace_match else ""
        is_file_page = namespace in {"file", "image", "ファイル", "画像"}
        is_image_target = bool(
            parsed_href
            and (
                parsed_href.scheme.casefold() == "data"
                or (parsed_href.hostname or "").casefold().endswith("wikia.nocookie.net")
                and bool(re.search(r"\.(?:png|jpe?g|gif|webp|bmp|avif|svg)(?:/|$)", parsed_href.path, re.IGNORECASE))
            )
        )
        contains_image = node.find("img") is not None
        if contains_image and is_image_target:
            image_href, _image_wiki_target = _safe_url(raw_href, base_url)
            if image_href.startswith(("http://", "https://")):
                attrs.append(f'data-lightbox-src="{html.escape(image_href, quote=True)}"')
        if not (is_file_page or is_image_target or contains_image):
            href, wiki_target = _safe_url(raw_href, base_url)
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
        if not src and raw_src.casefold().startswith("data:image/"):
            if re.match(
                r"^data:image/(?:png|jpe?g|gif|webp|avif);base64,[a-z0-9+/=\s]+$",
                raw_src,
                re.IGNORECASE,
            ):
                src = raw_src
        if not src:
            src, _wiki_target = _safe_url(raw_src, base_url)
        if not src or src.startswith("#"):
            return ""
        attrs.append(f'src="{html.escape(src, quote=True)}"')
        if node.get("alt") is not None:
            attrs.append(f'alt="{html.escape(str(node.get("alt")), quote=True)}"')
        for dimension in ("width", "height"):
            value = str(node.get(dimension) or "").strip()
            if value.isdigit() and 1 <= int(value) <= 5000:
                attrs.append(f'{dimension}="{value}"')
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
            "inline_image_keys": [], "image_captions": {},
            "main_image_key": "", "main_image_caption": "",
            "source_infobox_fields": [],
            "article_credits": [],
        }

    local_image_map = _build_local_image_map(
        soup, base_url, source_path, image_dir, article_title
    )
    main_source_infobox = soup.select_one(".portable-infobox") if has_source_infobox else None
    main_image_key = (
        get_article_image_roles(html_text, base_url).get("main_image_key", "")
        if main_source_infobox is not None else ""
    )
    main_image_keys = {main_image_key} if main_image_key else set()
    main_image_container = (
        main_source_infobox.select_one("figure.pi-image, .pi-image")
        if main_source_infobox is not None else None
    )
    caption_root = main_image_container or main_source_infobox
    main_image_caption_node = None
    if caption_root is not None:
        # Fandom's portable infobox commonly stores the visible image caption
        # as a pi-data-value in a data-source="caption" row, not a figcaption.
        # Prefer that semantic value and never substitute image title/alt text.
        for selector in (
            '[data-source="caption"] .pi-data-value',
            "figcaption.pi-caption",
            ".pi-item.pi-caption",
            ".pi-image-caption",
            "figcaption",
        ):
            main_image_caption_node = caption_root.select_one(selector)
            if main_image_caption_node is not None:
                break
    if main_image_caption_node is None and main_source_infobox is not None:
        main_image_caption_node = main_source_infobox.select_one(
            '[data-source="caption"] .pi-data-value'
        )
    main_image_caption = (
        main_image_caption_node.get_text(" ", strip=True)
        if main_image_caption_node else ""
    )
    source_infobox_fields = []
    if main_source_infobox is not None:
        for data_node in main_source_infobox.select(".pi-data"):
            label_node = data_node.select_one("h3.pi-data-label, .pi-data-label")
            value_node = data_node.select_one(".pi-data-value")
            label_text = label_node.get_text(" ", strip=True) if label_node else ""
            value_html = (
                "".join(
                    _serialize(child, base_url, local_image_map, source_path)
                    for child in value_node.children
                )
                if value_node is not None else ""
            )
            value_text = value_node.get_text(" ", strip=True) if value_node else ""
            if label_text and (value_html.strip() or value_text):
                source_infobox_fields.append({
                    "label": label_text,
                    "value_html": value_html,
                    "value_text": value_text,
                })

    # The first source infobox is represented by the GUI's status card.
    # Later infoboxes are article content and must remain at their DOM position.
    if main_source_infobox is not None and (
        main_source_infobox is root or main_source_infobox in root.descendants
    ):
        main_source_infobox.decompose()

    for image in list(root.find_all("img")):
        image_key = _canonical_image_key(
            image.get("data-src") or image.get("src") or "", base_url
        )
        if image_key not in main_image_keys:
            continue
        wrapper = image.find_parent("figure") or image.find_parent(class_="thumbinner")
        if wrapper:
            wrapper.decompose()
        else:
            image.decompose()
    article_credits = _extract_article_credits(root)

    # Remove Fandom-only rating controls and lazy-comment/mobile fallback UI.
    for element in list(root.select(
        ".ratebox, .page-rating-widget, .article-vote, .article-voting, "
        ".mw-customtoggle-voting"
    )):
        element.decompose()

    # Lazy-comment placeholders often live inside a details/mw-collapsible
    # wrapper. Remove that outer widget as a unit rather than leaving an empty
    # "▶ 詳細を表示" shell after deleting only its placeholder paragraph.
    residue_candidates = list(root.find_all(
        ["p", "small", "span", "div", "section", "aside", "details"]
    ))
    for element in residue_candidates:
        if element.parent is None:
            continue
        if not _is_fandom_widget_residue(element.get_text(" ", strip=True)):
            continue
        wrapper = element if (
            element.name == "details"
            or "mw-collapsible" in element.get("class", [])
        ) else element.find_parent("details")
        if wrapper is None:
            wrapper = element.find_parent(class_="mw-collapsible")
        (wrapper or element).decompose()

    for noscript in root.select("noscript"):
        noscript.decompose()
    _deduplicate_article_images(root, base_url, local_image_map)
    _prepare_collapsible_blocks(root, soup)
    _prepare_static_forms(root, soup)

    for selector in _DROP_SELECTORS:
        for element in root.select(selector):
            element.decompose()

    # A converted details block is useful only when its body contains visible
    # text or an image. Ignore the summary label itself ("詳細を表示").
    for details in reversed(list(root.find_all("details"))):
        if details.parent is None:
            continue
        body_nodes = [
            child for child in details.contents
            if not (
                isinstance(child, Comment)
                or isinstance(child, Tag) and child.name == "summary"
            )
        ]
        body_text = " ".join(
            child.get_text(" ", strip=True) if isinstance(child, Tag)
            else str(child).strip()
            for child in body_nodes
        ).strip()
        if not body_text and not details.find("img"):
            details.decompose()

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

    inline_image_keys = []
    image_captions = {}
    for image in root.find_all("img"):
        raw_image_url = str(image.get("data-src") or image.get("src") or "").strip()
        image_key = _canonical_image_key(raw_image_url, base_url)
        if not image_key:
            continue
        inline_image_keys.append(image_key)
        caption_node = _article_image_caption_node(image)
        caption_text = caption_node.get_text(" ", strip=True) if caption_node else ""
        if not caption_text:
            caption_text = str(image.get("alt") or "").strip()
        if caption_text:
            image_captions.setdefault(image_key, caption_text)

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
        "inline_image_keys": list(dict.fromkeys(inline_image_keys)),
        "image_captions": image_captions,
        "main_image_key": next(iter(main_image_keys), ""),
        "main_image_caption": main_image_caption,
        "source_infobox_fields": source_infobox_fields,
        "article_credits": article_credits,
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
            "inline_image_keys": [], "image_captions": {},
            "main_image_key": "", "main_image_caption": "",
            "source_infobox_fields": [],
            "article_credits": [],
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
