"""Non-interactive application operations shared by the GUI and CLI."""

import csv
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from dataclasses import dataclass

from modules.downloader import download_page, find_existing_html
from modules.extractor import extract_levels, is_level_article_category
from modules.image_downloader import download_images, rename_legacy_images
from modules.network import process_backrooms_data
from modules.operation_context import OperationContext
from modules.runtime_paths import get_application_dir
from modules.update_database import execute_update


LEVEL_LIST_DIR = get_application_dir() / "level_lists"
MASTER_CATALOG_FILE = get_application_dir() / "parsed_index" / "pages_master.csv"
DOWNLOADED_HTML_DIR = get_application_dir() / "downloaded_fandom_html"
LEVEL_LIST_CATEGORY = re.compile(r"^\[(.*?)\]\s+\((.*?)\)\s*$")
LEVEL_LIST_TITLE = re.compile(r"^\[\d+\]\s+(.+?)\s*$")
FANDOM_WIKI_HOST = "backrooms.fandom.com"


@dataclass(frozen=True)
class OperationResult:
    success: bool
    message: str
    details: object = None


def _alias_redirect_urls(rows):
    """Return alias redirect targets, excluding URLs that are canonical articles."""
    alias_urls = set()
    canonical_urls = set()
    for row in rows:
        try:
            groups = json.loads(row.get("alias_groups") or "[]")
        except (TypeError, ValueError):
            groups = []
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            canonical = group.get("canonical") or {}
            canonical_url = str(canonical.get("url") or "").strip()
            if canonical_url:
                canonical_urls.add(canonical_url)
            for alias in group.get("aliases", []):
                if isinstance(alias, dict):
                    alias_url = str(alias.get("url") or "").strip()
                    if alias_url:
                        alias_urls.add(alias_url)
    for row in rows:
        row_url = str(row.get("url") or "").strip()
        if row_url and _has_non_alias_membership(row):
            canonical_urls.add(row_url)
    return alias_urls - canonical_urls


def _has_non_alias_membership(row):
    try:
        memberships = json.loads(row.get("index_memberships") or "[]")
    except (TypeError, ValueError):
        memberships = []
    if isinstance(memberships, list) and memberships:
        return any(
            isinstance(item, dict)
            and str(item.get("index_name") or "").strip().casefold() != "alias"
            for item in memberships
        )
    indexes = [
        value.strip().casefold()
        for value in str(row.get("indexes") or "").split("|")
        if value.strip()
    ]
    return any(value != "alias" for value in indexes)


def _saved_html_url(path):
    """Read the original Wiki URL from a saved HTML metadata header."""
    try:
        with Path(path).open("r", encoding="utf-8-sig", errors="replace") as stream:
            header = stream.read(8192)
    except OSError:
        return ""
    match = re.search(r"BRNAVI_META_INFO:.*?\bURL=([^|\s>]+)", header)
    return match.group(1).strip() if match else ""


def _saved_html_metadata(path):
    """Read category and canonical URL from the downloader metadata comment."""
    try:
        with Path(path).open("r", encoding="utf-8-sig", errors="replace") as stream:
            header = stream.read(8192)
    except OSError:
        return {}
    match = re.search(r"BRNAVI_META_INFO:\s*(.*?)-->", header, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return {}
    metadata = {}
    for part in match.group(1).split("|"):
        if "=" in part:
            key, value = part.split("=", 1)
            metadata[key.strip().casefold()] = value.strip()
    return metadata


def _terms_article_urls(rows):
    return {
        str(row.get("url") or "").strip()
        for row in rows
        if str(row.get("category") or "").strip().casefold() == "terms"
        and str(row.get("url") or "").strip()
    }


def _is_terms_html(path, term_urls=()):
    metadata = _saved_html_metadata(path)
    metadata_indexes = metadata.get("indexes", "").casefold().split("|")
    return (
        metadata.get("category", "").casefold() == "terms"
        or "terms" in metadata_indexes
        or metadata.get("url", "").strip() in term_urls
    )


def _load_master_catalog():
    """Read only canonical article records from the Master catalog."""
    master_path = MASTER_CATALOG_FILE
    if not master_path.is_file():
        return []
    try:
        with master_path.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = [dict(row) for row in csv.DictReader(stream) if row.get("url")]
    except (OSError, csv.Error, UnicodeError):
        return []
    redirect_urls = _alias_redirect_urls(rows)
    return [
        row for row in rows
        if not (
            str(row.get("url") or "").strip() in redirect_urls
            and not _has_non_alias_membership(row)
        )
    ]


def get_level_list_languages():
    rows = _load_master_catalog()
    languages = {
        str(row.get("language") or "").strip()
        for row in rows
        if str(row.get("language") or "").strip()
        and str(row.get("language") or "").strip().casefold() != "unknown"
    }
    if languages:
        return sorted(languages, key=str.casefold)
    if not LEVEL_LIST_DIR.is_dir():
        return []
    return sorted(
        (path.stem for path in LEVEL_LIST_DIR.glob("*.txt") if not path.stem.startswith("_")),
        key=str.casefold,
    )


def load_level_list_pages(language):
    language = str(language or "").strip()
    all_languages = language.casefold() == "all"
    if not all_languages and not re.fullmatch(r"[A-Za-z0-9_-]+", language):
        raise ValueError("\u8a00\u8a9e\u30b3\u30fc\u30c9\u304c\u4e0d\u6b63\u3067\u3059\u3002")
    rows = _load_master_catalog()
    if rows:
        pages = []
        for row in rows:
            row_language = str(row.get("language") or "").strip()
            if not all_languages and row_language.casefold() != language.casefold():
                continue
            category = str(row.get("category") or "").strip()
            category_labels = {
                "normal": "無印通常階層",
                "normal_eta": "通常階層 η",
                "sublevel": "無印通常亜階層",
                "sublevel_eta": "通常亜階層 η",
                "enigmatic": "無印番外階層",
                "enigmatic_eta": "番外階層 η",
                "enigmatic_sublevel_eta": "番外亜階層 η",
                "alias": "別名",
                "terms": "用語",
                "contest": "コンテスト",
                "foreign": "国外階層",
                "dimension": "次元",
                "entity": "エンティティ",
                "item": "物品",
                "phenomenon": "現象",
                "canon": "カノン",
                "joke_level": "ジョーク階層",
                "joke_article": "ジョーク記事",
                "foreign_joke": "国外ジョーク記事",
                "person": "人物",
            }
            page = {
                "title": str(row.get("title") or "").strip(),
                "meta_title": str(row.get("meta_title") or "").strip(),
                "meta_title_ja": str(row.get("meta_title_ja") or "").strip(),
                "has_strikethrough": str(row.get("has_strikethrough") or "").strip().lower() in {"1", "true", "yes"},
                "strikethrough_spans": str(row.get("strikethrough_spans") or "[]"),
                "url": str(row.get("url") or "").strip(),
                "category": category,
                "category_label": category_labels.get(category, category),
                "language": row_language,
                "source_type": str(row.get("source_type") or "").strip(),
                "indexes": str(row.get("indexes") or "").strip(),
                "index_memberships": str(row.get("index_memberships") or "[]"),
                "alias_groups": str(row.get("alias_groups") or "[]"),
                "section_path": str(row.get("section_path") or "[]"),
                "section_order": str(row.get("section_order") or "0"),
                "page_order": str(row.get("page_order") or "0"),
                "translation_section": str(row.get("translation_section") or "").strip(),
            }
            if page["title"] and is_supported_level_list_url(page["url"]):
                pages.append(page)
        return pages

    # Compatibility fallback for installations predating pages_master.csv.
    if all_languages:
        languages = get_level_list_languages()
        return [page for code in languages for page in load_level_list_pages(code)]
    list_path = LEVEL_LIST_DIR / f"{language}.txt"
    if not list_path.is_file():
        raise FileNotFoundError(f"\u30ec\u30d9\u30eb\u30ea\u30b9\u30c8\u304c\u3042\u308a\u307e\u305b\u3093: {list_path.name}")
    pages = []
    category_label = ""
    category_code = ""
    current_page = None
    with list_path.open("r", encoding="utf-8-sig") as file:
        for line in file:
            category_match = LEVEL_LIST_CATEGORY.match(line.strip())
            if category_match:
                category_label, category_code = category_match.groups()
                continue
            title_match = LEVEL_LIST_TITLE.match(line.strip())
            if title_match:
                if current_page and current_page.get("url"):
                    pages.append(current_page)
                current_page = {
                    "title": title_match.group(1).strip(), "url": "",
                    "meta_title": "", "meta_title_ja": "",
                    "has_strikethrough": False, "strikethrough_spans": "[]",
                    "category": category_code or category_label,
                    "category_label": category_label, "language": language,
                    "source_type": "", "indexes": "", "translation_section": "",
                    "index_memberships": "[]", "alias_groups": "[]", "section_path": "[]",
                    "section_order": "0", "page_order": "0",
                }
                continue
            if current_page is None:
                continue
            stripped = line.strip()
            if stripped.startswith("URL:"):
                current_page["url"] = stripped[4:].strip()
            elif stripped.startswith("SOURCE:"):
                current_page["source_type"] = stripped[7:].strip()
            elif stripped.startswith("INDEX:"):
                current_page["indexes"] = stripped[6:].strip()
            elif stripped.startswith("TRANSLATION_SECTION:"):
                current_page["translation_section"] = stripped[len("TRANSLATION_SECTION:"):].strip()
            elif stripped.startswith("INDEX_MEMBERSHIPS:"):
                current_page["index_memberships"] = stripped[len("INDEX_MEMBERSHIPS:"):].strip()
            elif stripped.startswith("ALIAS_GROUPS:"):
                current_page["alias_groups"] = stripped[len("ALIAS_GROUPS:"):].strip()
            elif stripped.startswith("SECTION_PATH:"):
                current_page["section_path"] = stripped[len("SECTION_PATH:"):].strip()
            elif stripped.startswith("SECTION_ORDER:"):
                current_page["section_order"] = stripped[len("SECTION_ORDER:"):].strip()
            elif stripped.startswith("PAGE_ORDER:"):
                current_page["page_order"] = stripped[len("PAGE_ORDER:"):].strip()
    if current_page and current_page.get("url"):
        pages.append(current_page)
    return [page for page in pages if is_supported_level_list_url(page["url"])]


def _normalize_level_list_category(value):
    """Mirror gui_app.py normalizeExplorerCategory() for backend validation."""
    category = str(value or "").strip().casefold()
    if re.search(r"番外.*亜階層|enigmatic_sublevel", category):
        return "enigmatic_sublevel_eta"
    if re.search(r"通常.*亜階層|sublevel", category):
        return "sublevel_eta" if re.search(r"η|eta", category) else "sublevel"
    if re.search(r"番外階層|enigmatic", category):
        return "enigmatic_eta" if re.search(r"η|eta", category) else "enigmatic"
    if re.search(r"通常階層|normal", category):
        return "normal_eta" if re.search(r"η|eta", category) else "normal"
    if re.search(r"別名|alias", category):
        return "alias"
    if re.search(r"翻訳|translation", category):
        return "translation"
    if re.search(r"コンテスト|contest", category):
        return "contest"
    return category


def _row_belongs_to_level_list_category(row, category_id):
    """Check the selected canonical row against one of the fixed nine indexes."""
    category = _normalize_level_list_category(row.get("category"))
    language = str(row.get("language") or "unknown").strip().upper()
    source_type = str(row.get("source_type") or "").strip().casefold()
    source_types = {
        value.strip().casefold()
        for value in source_type.split("|")
        if value.strip()
    }
    indexes = {
        value.strip().casefold()
        for value in str(row.get("indexes") or "").split("|")
        if value.strip()
    }
    try:
        memberships = json.loads(row.get("index_memberships") or "[]")
    except (TypeError, ValueError):
        memberships = []
    membership_names = {
        str(item.get("index_name") or "").strip().casefold()
        for item in memberships
        if isinstance(item, dict)
    } if isinstance(memberships, list) else set()
    has_translation = "translation" in source_types
    is_branch = language in {"RU", "PL", "IT", "PT", "UA", "TR", "ZH"}
    if not is_branch and isinstance(memberships, list):
        for membership in memberships:
            if not isinstance(membership, dict) or membership.get("index_name") != "hierarchy":
                continue
            for section in membership.get("section_path") or []:
                if not isinstance(section, dict):
                    continue
                heading = re.sub(r"\s*\[[ xX✓✔]?\]\s*$", "", str(section.get("heading") or "")).strip().upper()
                if heading in {"A", "PL", "IT", "PT", "UA", "TR", "ZH"}:
                    is_branch = True
                    break
    if not is_branch:
        title = str(row.get("title") or "").strip()
        is_branch = bool(
            re.match(r"^Level\s+(?:A|PL|UA|ZH)\s*-?\s*\(?-?\d", title, re.IGNORECASE)
            or re.match(r"^Level\s+-?\d+(?:\.\d+)*(?:\s*-\s*|\s+)(?:IT|PT|TR)\b", title, re.IGNORECASE)
        )
    default_language = language in {"EN", "JP", "UNKNOWN"}
    if category_id == "all":
        return "hierarchy" in membership_names or "hierarchy" in indexes
    if category_id == "normal":
        return (
            category == "normal" and not is_branch and not has_translation
            and (language == "EN" or "normal" in membership_names or "normal" in indexes)
        )
    if category_id == "normal_eta":
        return category == "normal_eta" and default_language and not is_branch and not has_translation
    if category_id == "enigmatic":
        return category in {"enigmatic", "enigmatic_eta"} and default_language and not is_branch and not has_translation
    if category_id == "sublevel":
        return category in {"sublevel", "sublevel_eta", "enigmatic_sublevel_eta"} and default_language and not is_branch and not has_translation
    if category_id == "alias":
        try:
            groups = json.loads(row.get("alias_groups") or "[]")
        except (TypeError, ValueError):
            groups = []
        return isinstance(groups, list) and bool(groups)
    if category_id == "terms":
        return category == "terms" or source_type == "terms" or "terms" in indexes
    if category_id == "translation":
        return has_translation or category == "translation"
    if category_id == "contest":
        return category in {"contest", "contest_level"} or "contest" in indexes
    return False


def validate_level_list_pages(pages, category_id=None):
    """Resolve selected GUI pages to canonical Master DB records in one fixed list.

    Alias-only redirect URLs are rejected. If the Master catalog is available,
    each selected URL must be a canonical record belonging to the active list.
    Older installations without a Master catalog retain the validated TXT
    compatibility path.
    """
    if not isinstance(pages, list) or not pages:
        raise ValueError("オフライン保存する記事を選択してください。")
    if category_id not in {
        "all", "normal", "normal_eta", "enigmatic", "sublevel",
        "alias", "terms", "translation", "contest",
    }:
        raise ValueError("保存対象の一覧カテゴリが不正です。")

    catalog_rows = _load_master_catalog()
    catalog_by_url = {
        str(row.get("url") or "").strip(): row
        for row in catalog_rows
        if str(row.get("url") or "").strip()
    }
    redirect_urls = _alias_redirect_urls(catalog_rows)
    validated = []
    seen_urls = set()

    for page in pages:
        if (
            not isinstance(page, dict)
            or not str(page.get("title") or "").strip()
            or not is_supported_level_list_url(page.get("url"))
        ):
            raise ValueError("選択リストに不正なページが含まれています。")

        url = str(page.get("url") or "").strip()
        if url in redirect_urls:
            raise ValueError("別名リダイレクトURLは保存対象にできません。")
        if url in seen_urls:
            continue

        if catalog_rows:
            canonical = catalog_by_url.get(url)
            if canonical is None:
                raise ValueError("選択した記事は現在のMaster DBに存在しない正規記事です。")
            if not _row_belongs_to_level_list_category(canonical, category_id):
                raise ValueError("選択した記事は現在の一覧カテゴリに所属していません。")
            resolved = dict(canonical)
        else:
            resolved = dict(page)

        resolved["title"] = str(resolved.get("title") or page.get("title") or "").strip()
        resolved["url"] = url
        validated.append(resolved)
        seen_urls.add(url)

    if not validated:
        raise ValueError("保存可能な正規記事がありません。")
    return validated


def is_supported_level_list_url(url):
    parsed = urlparse(str(url or ""))
    path_segments = parsed.path.strip("/").split("/")
    return (
        parsed.scheme == "https"
        and parsed.hostname == FANDOM_WIKI_HOST
        and "wiki" in path_segments
        and path_segments.index("wiki") < len(path_segments) - 1
    )


def run_maintenance_operation(task_name, options=None, context=None):
    options = options or {}
    context = context or OperationContext()

    if task_name == "update_master":
        mode = options.get("mode")
        if mode not in (1, 2):
            raise ValueError("データベース更新モードは1または2を指定してください。")
        success = execute_update(mode, context=context)
        return OperationResult(
            success=bool(success),
            message="既存データベースを更新しました。"
            if success else "既存データベースの更新に失敗しました。",
        )

    if task_name == "extract_info":
        html_files = sorted(
            path for path in DOWNLOADED_HTML_DIR.glob("*")
            if path.is_file() and path.suffix.casefold() in {".html", ".htm"}
        ) if DOWNLOADED_HTML_DIR.is_dir() else []
        catalog_rows = _load_master_catalog()
        redirect_urls = _alias_redirect_urls(catalog_rows)
        term_urls = _terms_article_urls(catalog_rows)
        if html_files:
            canonical_html_files = [
                path for path in html_files
                if _saved_html_url(path) not in redirect_urls
                and not _is_terms_html(path, term_urls)
            ]
            skipped_redirects = sum(1 for path in html_files if _saved_html_url(path) in redirect_urls)
            skipped_terms = len(html_files) - skipped_redirects - len(canonical_html_files)
            if not canonical_html_files and skipped_terms:
                return OperationResult(
                    success=True,
                    message="用語記事はLevel用Summary・TXT抽出の対象外として保持しました。",
                    details={"extracted": 0, "skipped_terms": skipped_terms, "skipped_redirects": skipped_redirects},
                )
            if not canonical_html_files:
                return OperationResult(
                    success=False,
                    message="\u4fdd\u5b58HTML\u304c\u3059\u3079\u3066\u5225\u540d\u30ea\u30c0\u30a4\u30ec\u30af\u30c8\u30da\u30fc\u30b8\u306e\u305f\u3081\u3001\u62bd\u51fa\u3092\u4e2d\u6b62\u3057\u307e\u3057\u305f\u3002",
                    details={"skipped_redirects": skipped_redirects},
                )
            rows = extract_levels(
                html_files=canonical_html_files,
                interactive=False,
                context=context,
            )
            context.log(f"\u5225\u540d\u30ea\u30c0\u30a4\u30ec\u30af\u30c8HTML\u3092\u62bd\u51fa\u5bfe\u8c61\u304b\u3089\u9664\u5916: {skipped_redirects}")
        else:
            rows = extract_levels(interactive=False, context=context)
        success = rows is not None
        return OperationResult(
            success=success,
            message="情報データベースを更新しました。"
            if success else "情報データベースを更新できませんでした。",
            details={"extracted": len(rows)} if success else None,
        )

    if task_name == "rename_legacy_images":
        context.log("保存済みHTMLと照合し、旧命名方式の画像を安全にリネームします。")
        result = rename_legacy_images(context=context)
        return OperationResult(
            success=result["failed"] == 0,
            message=(
                f"画像名の移行完了: {result['renamed']}件、"
                f"判定保留 {result['skipped']}件、失敗 {result['failed']}件"
            ),
            details=result,
        )

    if task_name == "update_network":
        success = process_backrooms_data(context=context)
        return OperationResult(
            success=bool(success),
            message="Nodes / Edges / Evidenceの正規3ファイルを更新しました。"
            if success else "経路CSV3ファイルを更新できませんでした。既存データとログを確認してください。",
        )

    if task_name == "refresh_saved_wiki_page":
        html_path = Path(str(options.get("html_path") or "")).resolve()
        html_root = (get_application_dir() / "downloaded_fandom_html").resolve()
        if html_path.parent != html_root or html_path.suffix.lower() not in {".html", ".htm"}:
            raise ValueError("保存済みWiki HTMLのパスが不正です。")
        if not html_path.is_file():
            raise FileNotFoundError("抽出対象の保存済みWiki HTMLがありません。")

        saved_url = _saved_html_url(html_path)
        if saved_url and saved_url in _alias_redirect_urls(_load_master_catalog()):
            return OperationResult(
                success=False,
                message="\u5225\u540d\u30ea\u30c0\u30a4\u30ec\u30af\u30c8\u5c02\u7528\u30da\u30fc\u30b8\u306f\u672c\u6587\u62bd\u51fa\u5bfe\u8c61\u3067\u306f\u3042\u308a\u307e\u305b\u3093\u3002\u6b63\u898f\u8a18\u4e8b\u3092\u6307\u5b9a\u3057\u3066\u304f\u3060\u3055\u3044\u3002",
                details={"redirect_url": saved_url},
            )

        catalog_rows = _load_master_catalog()
        term_urls = _terms_article_urls(catalog_rows)
        if _is_terms_html(html_path, term_urls):
            context.log(f"用語記事として保存しました。Level用Summary・経路解析はスキップします: {html_path.name}")
            return OperationResult(
                success=True,
                message="用語記事を保存しました。図鑑では用語専用レイアウトで表示します。",
                details={"extraction_completed": False, "network_updated": False, "term_article": True},
            )

        saved_metadata = _saved_html_metadata(html_path)
        saved_category = str(saved_metadata.get("category") or "normal").strip().casefold()
        saved_source_type = str(saved_metadata.get("source_type") or "").strip().casefold()
        if saved_source_type != "translation" and not is_level_article_category(
            saved_category,
            saved_source_type,
        ):
            context.log("非Level記事を保存しました。Level用Summary/TXT・経路CSVの再構築は対象外としてスキップします。")
            return OperationResult(
                success=True,
                message="非Level記事を保存しました。Level用Summary/TXT・経路CSVの再構築は行っていません。",
                details={
                    "extraction_completed": False,
                    "network_updated": False,
                    "non_level_article": True,
                },
            )

        context.log(f"保存した記事から本文・メタデータを抽出します: {html_path.name}")
        extracted_rows = extract_levels(
            html_files=[html_path],
            merge_existing=True,
            interactive=False,
            context=context,
        )
        if extracted_rows is None:
            return OperationResult(
                success=False,
                message="HTMLは保存されましたが、本文・メタデータを抽出できませんでした。",
                details={"extraction_completed": False, "network_updated": False},
            )

        if saved_source_type == "translation":
            context.log("翻訳記事のTXT・Summaryを更新しました。Level経路CSVの再構築は対象外としてスキップします。")
            return OperationResult(
                success=True,
                message="翻訳記事を保存し、正規URLに対応するTXT・Summaryを更新しました。経路CSVは変更していません。",
                details={
                    "extraction_completed": True,
                    "network_updated": False,
                    "translation_article": True,
                },
            )

        context.check_cancelled()
        context.log("最新の保存済みHTMLと抽出メタデータからNodes / Edges / Evidenceを再構築します。")
        network_updated = bool(process_backrooms_data(context=context))
        return OperationResult(
            success=network_updated,
            message=(
                "本文・メタデータを抽出し、図鑑用データと経路CSV3ファイルを更新しました。"
                if network_updated else
                "本文は抽出しましたが、経路CSV3ファイルを更新できませんでした。"
            ),
            details={"extraction_completed": True, "network_updated": network_updated},
        )

    if task_name == "download_level_list_html":
        pages = options.get("pages", [])
        catalog_rows = _load_master_catalog()
        redirect_urls = _alias_redirect_urls(catalog_rows)
        pages = [
            page for page in pages
            if isinstance(page, dict)
            and str(page.get("url") or "").strip() not in redirect_urls
        ]
        overwrite = bool(options.get("overwrite", False))
        counts = {"downloaded": 0, "skipped": 0, "failed": 0}
        total = len(pages)
        if total == 0:
            return OperationResult(
                success=False,
                message="ダウンロード対象がありません。",
                details=counts,
            )
        for index, page in enumerate(pages, 1):
            context.check_cancelled()
            existing = find_existing_html(page)
            context.progress(index - 1, total, page["title"])
            context.log(f"[{index}/{total}] HTML取得: {page['title']}")
            path = download_page(
                page,
                overwrite=overwrite,
                delay=0,
                show_detail=False,
                context=context,
            )
            if path is None:
                counts["failed"] += 1
                context.log(f"取得失敗: {page['title']}", "error")
            elif existing is not None and not overwrite:
                counts["skipped"] += 1
                context.log(f"取得済みのためスキップ: {path.name}")
            else:
                counts["downloaded"] += 1
                context.log(f"保存完了: {path.name}")
            context.progress(index, total, page["title"])
        context.check_cancelled()
        return OperationResult(
            success=counts["failed"] == 0,
            message=(
                f"HTML取得完了: 新規 {counts['downloaded']}件、"
                f"取得済み {counts['skipped']}件、失敗 {counts['failed']}件"
            ),
            details=counts,
        )

    if task_name == "save_level_list_offline":
        pages = options.get("pages", [])
        catalog_rows = _load_master_catalog()
        redirect_urls = _alias_redirect_urls(catalog_rows)
        if options.get("require_catalog"):
            pages = validate_level_list_pages(pages, options.get("category_id"))
        else:
            pages = [
                page for page in pages
                if isinstance(page, dict)
                and str(page.get("url") or "").strip() not in redirect_urls
            ]
        overwrite = bool(options.get("overwrite", False))
        counts = {"downloaded": 0, "skipped": 0, "failed": 0}
        total = len(pages)
        if total == 0:
            return OperationResult(
                success=False,
                message="オフライン保存するレベルを選択してください。",
                details={"html": counts, "extracted": 0, "images": None},
            )

        html_paths = []
        seen_paths = set()
        for index, page in enumerate(pages, 1):
            context.check_cancelled()
            if (
                not isinstance(page, dict)
                or not page.get("title")
                or not is_supported_level_list_url(page.get("url"))
            ):
                raise ValueError("レベルリストに不正なページが含まれています。")
            existing = find_existing_html(page)
            context.progress(index - 1, total, f"HTML取得: {page['title']}")
            path = download_page(
                page,
                overwrite=overwrite,
                delay=0,
                show_detail=False,
                context=context,
            )
            if path is None:
                counts["failed"] += 1
                context.log(f"HTML取得失敗: {page['title']}", "error")
                continue
            if existing is not None and not overwrite:
                counts["skipped"] += 1
                context.log(f"取得済みHTMLを使用: {path.name}")
            else:
                counts["downloaded"] += 1
                context.log(f"HTML保存完了: {path.name}")
            resolved_path = Path(path).resolve()
            if resolved_path not in seen_paths:
                seen_paths.add(resolved_path)
                html_paths.append(resolved_path)
            context.progress(index, total, f"HTML取得: {page['title']}")

        context.check_cancelled()
        if not html_paths:
            return OperationResult(
                success=False,
                message="利用できるHTMLがありません。HTML取得に成功したページがないため抽出を行いませんでした。",
                details={"html": counts, "extracted": 0, "images": None},
            )

        term_urls = _terms_article_urls(catalog_rows)
        level_html_paths = [
            path for path in html_paths
            if not _is_terms_html(path, term_urls)
        ]
        if level_html_paths:
            context.log(f"選択したHTML {len(level_html_paths)}件からLevel情報を抽出します。")
            extracted_rows = extract_levels(
                html_files=level_html_paths,
                merge_existing=True,
                interactive=False,
                context=context,
            )
            if extracted_rows is None:
                return OperationResult(
                    success=False,
                    message="HTMLは保存されましたが、Level情報をTXTへ抽出できませんでした。",
                    details={"html": counts, "extracted": 0, "images": None},
                )
        else:
            extracted_rows = []
            context.log("選択した記事は用語記事のみのため、Level用Summary・TXT抽出をスキップします。")

        context.check_cancelled()
        context.log("同じHTMLだけを対象に画像を抽出します。")
        image_counts = download_images(
            html_files=html_paths,
            context=context,
            return_details=True,
        )
        success = counts["failed"] == 0 and image_counts["failed"] == 0
        message = (
            f"Level情報のオフライン保存完了: HTML新規 {counts['downloaded']}件、"
            f"取得済み {counts['skipped']}件、TXT抽出 {len(extracted_rows)}件、"
            f"画像保存 {image_counts['downloaded']}件"
        )
        if counts["failed"] or image_counts["failed"]:
            message += (
                f"、HTML取得失敗 {counts['failed']}件"
                f"、画像取得失敗 {image_counts['failed']}件"
            )
        return OperationResult(
            success=success,
            message=message,
            details={
                "html": counts,
                "extracted": len(extracted_rows),
                "images": image_counts,
            },
        )

    raise ValueError(f"未対応の保守タスクです: {task_name}")
