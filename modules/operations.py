"""Non-interactive application operations shared by the GUI and CLI."""

import re
from pathlib import Path
from urllib.parse import urlparse

from dataclasses import dataclass

from modules.downloader import download_page, find_existing_html
from modules.extractor import extract_levels
from modules.network import process_backrooms_data
from modules.operation_context import OperationContext
from modules.runtime_paths import get_application_dir
from modules.update_database import execute_update


LEVEL_LIST_DIR = get_application_dir() / "level_lists"
LEVEL_LIST_CATEGORY = re.compile(r"^\[(.*?)\]\s+\((.*?)\)\s*$")
LEVEL_LIST_TITLE = re.compile(r"^\[\d+\]\s+(.+?)\s*$")
FANDOM_WIKI_HOST = "backrooms.fandom.com"


@dataclass(frozen=True)
class OperationResult:
    success: bool
    message: str
    details: object = None


def get_level_list_languages():
    if not LEVEL_LIST_DIR.is_dir():
        return []
    return sorted(
        (
            path.stem
            for path in LEVEL_LIST_DIR.glob("*.txt")
            if not path.stem.startswith("_")
        ),
        key=str.casefold,
    )


def load_level_list_pages(language):
    language = str(language or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", language):
        raise ValueError("言語コードが不正です。")
    list_path = LEVEL_LIST_DIR / f"{language}.txt"
    if not list_path.is_file():
        raise FileNotFoundError(f"レベルリストがありません: {list_path.name}")

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
                    "title": title_match.group(1).strip(),
                    "url": "",
                    "category": category_code or category_label,
                    "category_label": category_label,
                    "language": language,
                    "source_type": "",
                    "indexes": "",
                    "translation_section": "",
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
                current_page["translation_section"] = stripped[
                    len("TRANSLATION_SECTION:"):
                ].strip()

    if current_page and current_page.get("url"):
        pages.append(current_page)

    return [
        page for page in pages
        if is_supported_level_list_url(page["url"])
    ]


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
        rows = extract_levels(interactive=False, context=context)
        success = rows is not None
        return OperationResult(
            success=success,
            message="情報データベースを更新しました。"
            if success else "情報データベースを更新できませんでした。",
            details={"extracted": len(rows)} if success else None,
        )

    if task_name == "update_network":
        success = process_backrooms_data(context=context)
        return OperationResult(
            success=bool(success),
            message="ネットワークを更新しました。"
            if success else "ネットワークを更新できませんでした。",
        )

    if task_name == "download_level_list_html":
        pages = options.get("pages", [])
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

    raise ValueError(f"未対応の保守タスクです: {task_name}")
