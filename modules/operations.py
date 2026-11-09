"""Non-interactive application operations shared by the GUI and CLI."""

import re
from pathlib import Path
from urllib.parse import urlparse

from dataclasses import dataclass

from modules.downloader import download_page, find_existing_html
from modules.extractor import extract_levels
from modules.image_downloader import download_images
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

    if task_name == "refresh_saved_wiki_page":
        html_path = Path(str(options.get("html_path") or "")).resolve()
        html_root = (get_application_dir() / "downloaded_fandom_html").resolve()
        if html_path.parent != html_root or html_path.suffix.lower() not in {".html", ".htm"}:
            raise ValueError("保存済みWiki HTMLのパスが不正です。")
        if not html_path.is_file():
            raise FileNotFoundError("抽出対象の保存済みWiki HTMLがありません。")

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

        context.check_cancelled()
        context.log("最新の保存済みHTMLと抽出メタデータから経路CSVを再構築します。")
        network_updated = bool(process_backrooms_data(context=context))
        return OperationResult(
            success=network_updated,
            message=(
                "本文・メタデータを抽出し、図鑑用データと経路CSVを更新しました。"
                if network_updated else
                "本文は抽出しましたが、経路CSVを更新できませんでした。"
            ),
            details={"extraction_completed": True, "network_updated": network_updated},
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

    if task_name == "save_level_list_offline":
        pages = options.get("pages", [])
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

        context.log(f"選択したHTML {len(html_paths)}件からLevel情報を抽出します。")
        extracted_rows = extract_levels(
            html_files=html_paths,
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
