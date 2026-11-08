"""Live Fandom page search, reading, and local archival."""

import re
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests

from modules.downloader import download_url


API_URL = "https://backrooms.fandom.com/ja/api.php"
REQUEST_TIMEOUT = 15
MAX_SEARCH_RESULTS = 30
MAX_IMAGES_PER_PAGE = 40
MAX_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_EXTENSIONS = {
    "image/avif": ".avif",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
ALLOWED_IMAGE_HOSTS = {
    "static.wikia.nocookie.net",
    "static.wikia.com",
    "images.wikia.nocookie.net",
}
ARTICLE_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "center", "cite", "code",
    "dd", "del", "div", "dl", "dt", "em", "figcaption", "figure", "h1", "h2",
    "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "ol", "p", "pre", "s",
    "small", "span", "strong", "sub", "sup", "table", "tbody", "td", "th",
    "thead", "tr", "u", "ul",
}
ARTICLE_ATTRIBUTES = {
    "*": ["class"],
    "a": ["href", "title"],
    "img": ["src", "alt", "width", "height"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan", "scope"],
}


def _api_request(params):
    response = requests.get(
        API_URL,
        params={**params, "format": "json", "formatversion": 2},
        impersonate="chrome",
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": "BrNaviFandomJP/1.0 (desktop Wiki reader)"},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        error = payload["error"]
        raise RuntimeError(
            f"Fandom API error {error.get('code', 'unknown')}: "
            f"{error.get('info', 'no details')}"
        )
    return payload


def is_supported_wiki_url(url):
    parsed = urlparse(str(url or ""))
    return (
        parsed.scheme == "https"
        and parsed.hostname == "backrooms.fandom.com"
        and parsed.path.startswith("/ja/wiki/")
    )


def _page_url(page):
    for candidate in (page.get("canonicalurl"), page.get("fullurl")):
        if is_supported_wiki_url(candidate):
            return candidate
    return ""


def search_pages(query, limit=MAX_SEARCH_RESULTS):
    query = str(query or "").strip()
    if not query:
        return []
    limit = max(1, min(int(limit), MAX_SEARCH_RESULTS))
    payload = _api_request(
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srnamespace": 0,
            "srlimit": limit,
        }
    )
    hits = payload.get("query", {}).get("search", [])
    pageids = [str(hit["pageid"]) for hit in hits if hit.get("pageid")]
    if not pageids:
        return []
    details = _api_request(
        {
            "action": "query",
            "prop": "info|categories",
            "inprop": "url",
            "cllimit": "max",
            "pageids": "|".join(pageids),
        }
    )
    by_id = {
        str(page.get("pageid")): page
        for page in details.get("query", {}).get("pages", [])
    }
    pages = []
    for hit in hits:
        page = by_id.get(str(hit.get("pageid")), {})
        url = _page_url(page)
        if not url:
            continue
        categories = []
        for category in page.get("categories", []):
            category_title = str(category.get("title", ""))
            category_title = re.sub(r"^(?:Category:|カテゴリ:)", "", category_title, flags=re.I)
            if category_title:
                categories.append(category_title)
        pages.append(
            {
                "title": page.get("title") or hit.get("title", ""),
                "url": url,
                "categories": categories,
                "snippet": BeautifulSoup(hit.get("snippet", ""), "html.parser").get_text(" ", strip=True),
            }
        )
    return pages


def fetch_page(url):
    if not is_supported_wiki_url(url):
        raise ValueError("日本語版Backrooms WikiのページURLを指定してください。")
    title = unquote(url.rstrip("/").rsplit("/", 1)[-1]).replace("_", " ")
    payload = _api_request(
        {
            "action": "parse",
            "page": title,
            "prop": "text|displaytitle",
            "disableeditsection": 1,
            "disabletoc": 1,
        }
    )
    page = payload.get("parse", {})
    article_html = str(page.get("text", ""))
    if not article_html.strip():
        raise RuntimeError("ページ本文を取得できませんでした。")
    soup = BeautifulSoup(article_html, "html.parser")
    for image in soup.find_all("img"):
        image_url = ""
        for attribute in ("src", "data-src", "data-original"):
            candidate = urljoin(url, image.get(attribute, ""))
            parsed_candidate = urlparse(candidate)
            if (
                parsed_candidate.scheme == "https"
                and parsed_candidate.hostname in ALLOWED_IMAGE_HOSTS
            ):
                image_url = candidate
                break
        if not image_url:
            image.decompose()
            continue
        image["src"] = image_url
        image.attrs.pop("data-src", None)
        image.attrs.pop("srcset", None)
    for anchor in soup.find_all("a", href=True):
        target_url = urljoin(url, anchor["href"])
        if is_supported_wiki_url(target_url):
            anchor["href"] = target_url
        else:
            anchor.attrs.pop("href", None)
    import bleach

    clean_html = bleach.clean(
        str(soup),
        tags=ARTICLE_TAGS,
        attributes=ARTICLE_ATTRIBUTES,
        protocols={"http", "https"},
        strip=True,
    )
    return {
        "title": page.get("displaytitle") or page.get("title") or title,
        "url": url,
        "html": clean_html,
    }


def _localize_images(html, html_path):
    soup = BeautifulSoup(html, "html.parser")
    for base in soup.find_all("base"):
        base.decompose()
    assets_dir = html_path.with_name(f"{html_path.stem}_assets")
    image_count = 0
    failures = 0
    seen = {}
    for image in soup.find_all("img")[:MAX_IMAGES_PER_PAGE]:
        image_url = ""
        for attribute in ("src", "data-src", "data-original"):
            candidate = urljoin(
                "https://backrooms.fandom.com/",
                image.get(attribute, ""),
            )
            parsed_candidate = urlparse(candidate)
            if (
                parsed_candidate.scheme == "https"
                and parsed_candidate.hostname in ALLOWED_IMAGE_HOSTS
            ):
                image_url = candidate
                break
        if not image_url:
            continue
        parsed = urlparse(image_url)
        if image_url in seen:
            image["src"] = seen[image_url]
            continue
        try:
            response = requests.get(
                image_url,
                impersonate="chrome",
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "BrNaviFandomJP/1.0 (desktop Wiki reader)"},
            )
            response.raise_for_status()
            content = response.content
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            suffix = IMAGE_EXTENSIONS.get(content_type)
            if not suffix or len(content) > MAX_IMAGE_BYTES:
                failures += 1
                continue
            assets_dir.mkdir(parents=True, exist_ok=True)
            image_path = assets_dir / f"image_{image_count + 1:02d}{suffix}"
            image_path.write_bytes(content)
            relative_path = f"{assets_dir.name}/{image_path.name}"
            seen[image_url] = relative_path
            image["src"] = relative_path
            image.attrs.pop("data-src", None)
            image.attrs.pop("data-original", None)
            image.attrs.pop("srcset", None)
            image_count += 1
        except Exception as error:
            print(f"画像を保存できませんでした ({image_url}): {error}")
            failures += 1
    html_path.write_text(str(soup), encoding="utf-8")
    return {"saved": image_count, "failed": failures}


def save_page(page):
    url = str(page.get("url", "")).strip()
    if not is_supported_wiki_url(url):
        raise ValueError("日本語版Backrooms Wiki以外のURLは保存できません。")
    title = str(page.get("title", "")).strip()
    if not title:
        raise ValueError("ページタイトルがありません。")
    metadata = {
        "title": title,
        "url": url,
        "category": "|".join(page.get("categories", [])),
        "language": "JP",
        "source_type": "wiki",
        "indexes": "",
    }
    print(f"オンラインWikiページを保存します: {title} ({url})")
    saved_path = download_url(
        url,
        page_info=metadata,
        delay=0,
        overwrite=False,
        show_detail=True,
    )
    if saved_path is None:
        raise RuntimeError("Wiki本文をHTMLとして保存できませんでした。")
    image_result = _localize_images(saved_path.read_text(encoding="utf-8"), saved_path)
    print(
        f"保存完了: {saved_path.name} / "
        f"画像 {image_result['saved']} 件"
        + (f" / 画像失敗 {image_result['failed']} 件" if image_result["failed"] else "")
    )
    return {
        "path": str(saved_path),
        "title": title,
        "images_saved": image_result["saved"],
        "images_failed": image_result["failed"],
    }
