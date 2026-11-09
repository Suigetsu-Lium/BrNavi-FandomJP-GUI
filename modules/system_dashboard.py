"""Small, side-effect-free helpers for the GUI status bar and local media views."""

from datetime import datetime, timedelta, timezone
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
    """Count local app data and report the containing drive usage."""
    base = Path(base_dir)
    folders = {
        "取得済みHTML": base / "downloaded_fandom_html",
        "保存済み画像": base / "downloaded_images",
        "抽出テキスト": base / "extracted_fandom_levels",
        "インデックス・DB・CSV": base / "database",
        "パーサー索引": base / "parsed_index",
    }
    categories = {}
    total_files = total_bytes = 0
    for label, root in folders.items():
        files = [path for path in root.rglob("*") if path.is_file()] if root.exists() else []
        size = sum(_safe_size(path) for path in files)
        categories[label] = {"files": len(files), "bytes": size}
        total_files += len(files)
        total_bytes += size
    # CSVs may be intentionally written at the application root for legacy tools.
    root_csvs = [path for path in base.glob("*.csv") if path.is_file()] if base.exists() else []
    if root_csvs:
        categories["インデックス・DB・CSV"]["files"] += len(root_csvs)
        categories["インデックス・DB・CSV"]["bytes"] += sum(_safe_size(path) for path in root_csvs)
        total_files += len(root_csvs)
        total_bytes += sum(_safe_size(path) for path in root_csvs)
    try:
        usage = shutil.disk_usage(base if base.exists() else base.parent)
        drive = {"total": usage.total, "used": usage.used, "free": usage.free,
                 "percent": round(usage.used * 100 / usage.total, 1) if usage.total else 0}
    except OSError:
        drive = {"total": 0, "used": 0, "free": 0, "percent": 0}
    return {"categories": categories, "files": total_files, "bytes": total_bytes, "drive": drive}


def _safe_size(path):
    try:
        return path.stat().st_size
    except OSError:
        return 0


def list_local_images(image_dir):
    """Return image metadata with Level names inferred from downloader filenames."""
    root = Path(image_dir)
    if not root.exists():
        return []
    items = []
    for path in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
        if not path.is_file() or path.suffix.casefold() not in IMAGE_SUFFIXES:
            continue
        stem = path.stem
        match = re.match(r"(Level[_\s]+-?\d+(?:\.\d+)?(?:[_\s]+η)?(?:[_\s]+\([^)]*\))?)", stem, re.I)
        level = re.sub(r"[_\s]+", " ", match.group(1)).replace("( ", "(").strip() if match else ""
        level = re.sub(r"\s+η", " η", level)
        items.append({"name": path.name, "path": str(path), "level_id": level, "bytes": _safe_size(path)})
    return items


def local_saved_level_list(candidates):
    """Return one row per locally saved Level article from a candidate catalog."""
    unique = []
    seen = set()
    for item in candidates or []:
        if not isinstance(item, dict) or not item.get("saved"):
            continue
        title = str(item.get("title") or "").strip()
        if not re.match(r"^(?:Level|レベル)\s+-?\d", title, re.IGNORECASE):
            continue
        key = re.sub(r"[_\s]+", " ", title).casefold()
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
                result = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True,
                                        text=True, encoding="utf-8", errors="replace", timeout=2, check=False)
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
