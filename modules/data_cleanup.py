"""Safely preview and remove explicitly allowlisted generated application data."""

import os
import re
import stat
import time
import uuid
from pathlib import Path


CONFIRMATION_KEYWORD = "DELETE"
PLAN_TTL_SECONDS = 300

CATEGORY_DEFINITIONS = {
    "articles": {
        "label": "記事HTML・抽出データ・画像",
        "directories": (
            "downloaded_fandom_html",
            "downloaded_images",
            "extracted_fandom_levels",
        ),
        "files": (
            "extracted_fandom_levels_all.txt",
            "extracted_fandom_levels_summary.csv",
        ),
        "patterns": (),
    },
    "index": {
        "label": "一覧インデックス・Master DB",
        "directories": ("index_html", "parsed_index", "level_lists"),
        "files": ("wiki_list.txt", "parsed_index/terms_index.json"),
        "patterns": (),
    },
    "routes": {
        "label": "経路CSV",
        "directories": (),
        "files": (
            "backrooms_nodes.csv",
            "backrooms_edges.csv",
            "backrooms_evidence.csv",
        ),
        "patterns": (),
    },
    "ai": {
        "label": "AI会話履歴・移行バックアップ",
        "directories": (),
        "files": (),
        "patterns": (
            re.compile(r"assistant\.sqlite3(?:-(?:wal|shm|journal))?\Z"),
            re.compile(
                r"assistant-pre-migration-\d{8}T\d{6}Z(?:-\d+)?\.sqlite3\Z"
            ),
        ),
    },
}


class CleanupSafetyError(RuntimeError):
    """Raised when a cleanup target cannot be proven safe."""


def _is_reparse_point(path, info=None):
    info = info or path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag) or bool(
        getattr(os.path, "isjunction", lambda value: False)(str(path))
    )


def _relative(base, path):
    try:
        return path.relative_to(base).as_posix()
    except ValueError as error:
        raise CleanupSafetyError("削除対象がアプリケーションルート外です。") from error


def _file_record(base, path):
    try:
        info = path.lstat()
    except FileNotFoundError as error:
        raise CleanupSafetyError(
            f"削除対象ファイルが存在しません: {_relative(base, path)}"
        ) from error
    if _is_reparse_point(path, info) or not stat.S_ISREG(info.st_mode):
        raise CleanupSafetyError(
            f"リンクまたは通常ファイル以外の対象を検出したため中止しました: {_relative(base, path)}"
        )
    return {
        "path": _relative(base, path),
        "kind": "file",
        "bytes": int(info.st_size),
        "mtime_ns": int(info.st_mtime_ns),
    }


def _directory_records(base, root):
    if root.is_symlink() or getattr(os.path, "isjunction", lambda value: False)(str(root)):
        raise CleanupSafetyError(f"対象ディレクトリがリンクです: {_relative(base, root)}")
    if not root.exists():
        return [], []
    info = root.lstat()
    if _is_reparse_point(root, info) or not stat.S_ISDIR(info.st_mode):
        raise CleanupSafetyError(f"対象ディレクトリが安全ではありません: {_relative(base, root)}")

    files = []
    directories = [{
        "path": _relative(base, root),
        "kind": "directory",
        "root": True,
        "inode": int(getattr(info, "st_ino", 0)),
    }]
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda item: item.name.casefold())
        except OSError as error:
            raise CleanupSafetyError(f"ディレクトリを確認できません: {_relative(base, current)}") from error
        for entry in entries:
            path = Path(entry.path)
            try:
                entry_info = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise CleanupSafetyError(f"対象を確認できません: {_relative(base, path)}") from error
            if _is_reparse_point(path, entry_info):
                raise CleanupSafetyError(
                    f"シンボリックリンク・ジャンクション等を検出したため中止しました: {_relative(base, path)}"
                )
            if stat.S_ISDIR(entry_info.st_mode):
                directories.append({
                    "path": _relative(base, path),
                    "kind": "directory",
                    "root": False,
                    "inode": int(getattr(entry_info, "st_ino", 0)),
                })
                stack.append(path)
            elif stat.S_ISREG(entry_info.st_mode):
                files.append({
                    "path": _relative(base, path),
                    "kind": "file",
                    "bytes": int(entry_info.st_size),
                    "mtime_ns": int(entry_info.st_mtime_ns),
                })
            else:
                raise CleanupSafetyError(
                    f"通常ファイル以外の対象を検出したため中止しました: {_relative(base, path)}"
                )
    return files, directories


def _scan_category(base, category_id):
    definition = CATEGORY_DEFINITIONS[category_id]
    files = []
    directories = []
    for name in definition["directories"]:
        root = base / name
        found_files, found_directories = _directory_records(base, root)
        files.extend(found_files)
        directories.extend(found_directories)

    known_file_paths = {item["path"] for item in files}
    for name in definition["files"]:
        path = base / name
        relative = path.relative_to(base).as_posix()
        if relative in known_file_paths:
            continue
        if path.exists() or path.is_symlink():
            files.append(_file_record(base, path))
            known_file_paths.add(relative)

    if category_id == "ai":
        database_dir = base / "database"
        if database_dir.is_symlink() or getattr(os.path, "isjunction", lambda value: False)(str(database_dir)):
            raise CleanupSafetyError("database保存先がリンクです。")
        if database_dir.exists():
            database_info = database_dir.lstat()
            if _is_reparse_point(database_dir, database_info) or not stat.S_ISDIR(database_info.st_mode):
                raise CleanupSafetyError("database保存先が安全なディレクトリではありません。")
        ai_dir = database_dir / "ai"
        if ai_dir.is_symlink() or getattr(os.path, "isjunction", lambda value: False)(str(ai_dir)):
            raise CleanupSafetyError("AIデータ保存先がリンクです。")
        if ai_dir.exists():
            info = ai_dir.lstat()
            if _is_reparse_point(ai_dir, info) or not stat.S_ISDIR(info.st_mode):
                raise CleanupSafetyError("AIデータ保存先が安全ではありません。")
            for entry in sorted(os.scandir(ai_dir), key=lambda item: item.name.casefold()):
                if not any(pattern.fullmatch(entry.name) for pattern in definition["patterns"]):
                    continue
                path = Path(entry.path)
                files.append(_file_record(base, path))

    files.sort(key=lambda item: item["path"].casefold())
    directories.sort(key=lambda item: (item["path"].count("/"), item["path"].casefold()), reverse=True)
    return files, directories


def create_cleanup_plan(base_dir, categories):
    """Create a read-only preview plan from fixed category identifiers."""
    base = Path(base_dir).resolve()
    if not isinstance(categories, (list, tuple, set)):
        raise ValueError("削除カテゴリの指定が不正です。")
    selected = list(dict.fromkeys(str(value) for value in categories))
    unknown = [value for value in selected if value not in CATEGORY_DEFINITIONS]
    if unknown:
        raise ValueError("未定義の削除カテゴリが指定されました。")
    if not selected:
        raise ValueError("削除カテゴリを1つ以上選択してください。")

    result_categories = []
    all_files = []
    all_directories = []
    display_items = []
    for category_id in selected:
        definition = CATEGORY_DEFINITIONS[category_id]
        files, directories = _scan_category(base, category_id)
        bytes_total = sum(item["bytes"] for item in files)
        all_files.extend(files)
        all_directories.extend(directories)
        for item in directories:
            display_items.append({
                "category": category_id,
                "category_label": definition["label"],
                **item,
                "bytes": 0,
            })
        for item in files:
            display_items.append({
                "category": category_id,
                "category_label": definition["label"],
                **item,
            })
        result_categories.append({
            "id": category_id,
            "label": definition["label"],
            "files": len(files),
            "bytes": bytes_total,
            "exists": bool(files or directories),
        })

    display_items.sort(key=lambda item: (item["category"], item["path"].casefold()))
    return {
        "plan_id": uuid.uuid4().hex,
        "created_at": time.monotonic(),
        "categories": selected,
        "category_summaries": result_categories,
        "items": display_items,
        "files": all_files,
        "directories": all_directories,
        "total_files": len(all_files),
        "total_bytes": sum(item["bytes"] for item in all_files),
    }


def _plan_signature(plan):
    return (
        tuple(sorted(
            (item["path"], item["bytes"], item["mtime_ns"])
            for item in plan["files"]
        )),
        tuple(sorted((item["path"], item["inode"]) for item in plan["directories"])),
    )


def _safe_target(base, relative):
    candidate = base / Path(relative)
    try:
        resolved_parent = candidate.parent.resolve()
        resolved_parent.relative_to(base)
    except (OSError, ValueError) as error:
        raise CleanupSafetyError(f"削除対象のパスがアプリケーションルート外です: {relative}") from error
    current = base
    for part in Path(relative).parts[:-1]:
        current = current / part
        if current.exists():
            info = current.lstat()
            if _is_reparse_point(current, info) or not stat.S_ISDIR(info.st_mode):
                raise CleanupSafetyError(f"親ディレクトリが安全ではありません: {relative}")
    return candidate


def delete_cleanup_plan(base_dir, plan, confirmation):
    """Revalidate a plan and delete only its recorded files and child directories."""
    if confirmation != CONFIRMATION_KEYWORD:
        raise ValueError("確認キーワードが一致しません。")
    if time.monotonic() - plan.get("created_at", 0) > PLAN_TTL_SECONDS:
        raise CleanupSafetyError("削除プレビューの有効期限が切れました。再計測してください。")

    base = Path(base_dir).resolve()
    fresh = create_cleanup_plan(base, plan["categories"])
    if _plan_signature(fresh) != _plan_signature(plan):
        raise CleanupSafetyError("プレビュー後に保存データが変更されました。削除を中止しました。再計測してください。")

    removed_files = []
    removed_directories = []
    for item in plan["files"]:
        path = _safe_target(base, item["path"])
        current = _file_record(base, path)
        if current != item:
            raise CleanupSafetyError(f"削除直前にファイルが変更されました: {item['path']}")
        path.unlink()
        removed_files.append(item["path"])

    for item in plan["directories"]:
        if item.get("root"):
            continue
        path = _safe_target(base, item["path"])
        if not path.exists():
            continue
        info = path.lstat()
        if (
            _is_reparse_point(path, info)
            or not stat.S_ISDIR(info.st_mode)
            or int(getattr(info, "st_ino", 0)) != item["inode"]
        ):
            raise CleanupSafetyError(f"削除直前にディレクトリが変更されました: {item['path']}")
        try:
            path.rmdir()
            removed_directories.append(item["path"])
        except OSError as error:
            raise CleanupSafetyError(
                f"新しいファイル等が残っているためディレクトリを削除できません: {item['path']}"
            ) from error

    return {
        "removed_files": removed_files,
        "removed_directories": removed_directories,
        "files": len(removed_files),
        "bytes": sum(item["bytes"] for item in plan["files"]),
        "categories": list(plan["categories"]),
    }
