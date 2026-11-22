"""Small, side-effect-free helpers for the GUI status bar and local media views."""

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


TIMEZONES = {
    "Asia/Tokyo": "JST",
    "BRT": "BRT",
    "UTC": "UTC",
    "America/New_York": None,
    "Europe/London": None,
    "Asia/Seoul": "KST",
    "Asia/Shanghai": "CST",
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def format_clock(now=None, timezone_name="Asia/Tokyo"):
    """Format a Japanese-localized clock, preserving the special BRT label."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    zone_key = "UTC" if timezone_name == "BRT" else timezone_name
    try:
        local = now.astimezone(ZoneInfo(zone_key))
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        fallback_zones = {
            "Asia/Tokyo": timezone(timedelta(hours=9), "JST"),
            "UTC": timezone.utc,
            "America/New_York": timezone(timedelta(hours=-5), "EST"),
            "Europe/London": timezone.utc,
            "Asia/Seoul": timezone(timedelta(hours=9), "KST"),
            "Asia/Shanghai": timezone(timedelta(hours=8), "CST"),
        }
        zone = fallback_zones.get(zone_key, timezone(timedelta(hours=9), "JST"))
        local = now.astimezone(zone)
    label = "BRT" if timezone_name == "BRT" else (TIMEZONES.get(timezone_name) or local.tzname() or "UTC")
    weekdays = "月火水木金土日"
    return f"{local:%Y/%m/%d} ({weekdays[local.weekday()]}) {local:%H:%M:%S} {label}"


def storage_summary(base_dir):
    """Count generated local data without including protected application settings."""
    base = Path(base_dir)
    categories = {}

    def add_paths(label, paths):
        files = []
        for path in paths:
            if path.is_dir() and not path.is_symlink():
                files.extend(candidate for candidate in path.rglob("*") if candidate.is_file() and not candidate.is_symlink())
            elif path.is_file() and not path.is_symlink():
                files.append(path)
        # A path can be supplied by more than one legacy grouping; count it once.
        unique = list(dict.fromkeys(files))
        categories[label] = {
            "files": len(unique),
            "bytes": sum(_safe_size(path) for path in unique),
        }

    add_paths("記事HTML", [base / "downloaded_fandom_html"])
    add_paths("保存済み画像", [base / "downloaded_images"])
    add_paths("抽出データ", [
        base / "extracted_fandom_levels",
        base / "extracted_fandom_levels_all.txt",
        base / "extracted_fandom_levels_summary.csv",
    ])
    add_paths("一覧インデックス・Master DB", [
        base / "index_html",
        base / "parsed_index",
        base / "level_lists",
        base / "wiki_list.txt",
    ])
    add_paths("経路CSV", [
        base / "backrooms_nodes.csv",
        base / "backrooms_edges.csv",
        base / "backrooms_evidence.csv",
    ])

    ai_dir = base / "database" / "ai"
    ai_files = []
    if ai_dir.is_dir() and not ai_dir.is_symlink():
        for path in ai_dir.iterdir():
            if path.is_symlink() or not path.is_file():
                continue
            if (
                path.name == "assistant.sqlite3"
                or path.name in {"assistant.sqlite3-wal", "assistant.sqlite3-shm", "assistant.sqlite3-journal"}
                or re.fullmatch(
                    r"assistant-pre-migration-\d{8}T\d{6}Z(?:-\d+)?\.sqlite3",
                    path.name,
                )
            ):
                ai_files.append(path)
    add_paths("AI会話履歴・移行バックアップ", ai_files)

    total_files = sum(item["files"] for item in categories.values())
    total_bytes = sum(item["bytes"] for item in categories.values())
    try:
        usage = shutil.disk_usage(base if base.exists() else base.parent)
        drive = {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": round(usage.used * 100 / usage.total, 1) if usage.total else 0,
        }
    except OSError:
        drive = {"total": 0, "used": 0, "free": 0, "percent": 0}
    return {"categories": categories, "files": total_files, "bytes": total_bytes, "drive": drive}


def _safe_size(path):
    try:
        return path.stat().st_size
    except OSError:
        return 0


def normalize_media_identifier(value):
    """Normalize page titles and downloader filenames to the same comparison key."""
    text = str(value or "").strip()
    text = re.sub(r"\s+-\s+image\d+$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\.[^.\\/]+$", "", text)
    text = text.replace("\\", "/").replace("/", " ")
    return " ".join(text.replace("_", " ").split()).casefold()


def find_local_images(image_dir, level_id):
    """Find images whose normalized filename belongs to the requested article."""
    root = Path(image_dir)
    target = normalize_media_identifier(level_id)
    if not root.is_dir() or not target:
        return []
    matches = []
    for path in root.iterdir():
        if not path.is_file() or path.suffix.casefold() not in IMAGE_SUFFIXES:
            continue
        key = normalize_media_identifier(path.name)
        if key == target or key.startswith(target + " "):
            matches.append(path)
    return sorted(matches, key=lambda path: path.name.casefold())


def list_local_images(image_dir, known_titles=None, known_pages=None):
    """Return local image metadata, mapping filenames back to canonical page titles."""
    root = Path(image_dir)
    if not root.exists():
        return []
    title_keys = sorted(
        ((normalize_media_identifier(title), str(title).strip()) for title in (known_titles or []) if str(title).strip()),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    page_titles = {}
    for page in known_pages or []:
        if not isinstance(page, dict):
            continue
        page_url = str(page.get("url") or "").strip().rstrip("/")
        page_title = str(page.get("title") or "").strip()
        if page_url and page_title:
            page_key = hashlib.sha256(page_url.encode("utf-8")).hexdigest()[:16]
            page_titles[page_key] = page_title
    items = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.casefold() not in IMAGE_SUFFIXES:
            continue
        url_key_match = re.fullmatch(r"page_([0-9a-f]{16})_image\d+", path.stem, re.IGNORECASE)
        if url_key_match:
            identifier = path.stem
            key = ""
            level = page_titles.get(url_key_match.group(1).lower(), "")
        else:
            identifier = re.sub(r"\s+-\s+image\d+$", "", path.stem, flags=re.IGNORECASE)
            key = normalize_media_identifier(identifier)
            level = ""
        for title_key, title in title_keys:
            if key == title_key or key.startswith(title_key + " "):
                level = title
                break
        if not level:
            numeric = re.search(r"Level[_ ]+(-?[0-9]+(?:[.][0-9]+)?)(?:[_ ]+" + chr(0x03B7) + r")?", identifier, re.IGNORECASE)
            if numeric:
                level = "Level " + numeric.group(1)
                if chr(0x03B7) in identifier[numeric.end():] or re.search(r"[_ ]+" + chr(0x03B7) + r"(?:[_ ]|$)", identifier):
                    level += " " + chr(0x03B7)
            else:
                level = " ".join(identifier.replace("_", " ").split())
        items.append({"name": path.name, "path": str(path), "level_id": level, "bytes": _safe_size(path)})
    return items


def local_saved_level_list(candidates):
    """Return one row per locally saved article from the complete catalog."""
    unique = []
    seen = set()
    for item in candidates or []:
        if not isinstance(item, dict) or not item.get("saved"):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        key = normalize_media_identifier(title)
        if key not in seen:
            unique.append(dict(item))
            seen.add(key)
    return unique


def filter_local_images(images, query=""):
    """Filter image gallery records by their inferred Level or file name."""
    needle = str(query or "").strip().casefold()
    if not needle:
        return list(images or [])
    return [
        item for item in images or []
        if needle in f"{item.get('level_id', '')} {item.get('name', '')}".casefold()
    ]


def get_network_kind():
    """Best-effort network type detection; never raises into the UI."""
    try:
        # Route selection detects connectivity without sending application data.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("1.1.1.1", 53))
            local_ip = sock.getsockname()[0]
        finally:
            sock.close()
        if not local_ip or local_ip.startswith("127."):
            return "オフライン"
        try:
            import psutil
            for name, stats in psutil.net_if_stats().items():
                if stats.isup and any(token in name.casefold() for token in ("wi-fi", "wifi", "wlan", "wireless")):
                    return "Wi-Fi"
        except Exception:
            pass
        if sys.platform == "win32":
            try:
                # The GUI refreshes this status periodically. Suppress the child
                # console so netsh does not flash a terminal on every refresh.
                result = subprocess.run(
                    ["netsh", "wlan", "show", "interfaces"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=2,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                output = (result.stdout or "").casefold()
                connected = "connected" in output or "接続済み" in output
                has_ssid = "ssid" in output
                if result.returncode == 0 and connected and has_ssid:
                    return "Wi-Fi"
            except (OSError, subprocess.SubprocessError):
                pass
        return "有線LAN"
    except (OSError, socket.error):
        return "オフライン"


def get_power_status():
    """Windows battery status, with a safe AC-only fallback on desktops."""
    try:
        import ctypes
        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("Reserved1", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
        status = SYSTEM_POWER_STATUS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return "⚡ AC給電"
        percent = int(status.BatteryLifePercent)
        if percent > 100 or status.BatteryFlag & 128:
            return "⚡ AC給電"
        charging = bool(status.BatteryFlag & 8)
        return f"⚡ {percent}% (充電中)" if charging else f"🔋 {percent}%"
    except Exception:
        return "⚡ AC給電"
