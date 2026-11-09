"""Persistent general application settings and calendar-month update schedule."""

import calendar
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path


class AppSettingsStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    @staticmethod
    def _now():
        return datetime.now().astimezone()

    @staticmethod
    def _parse(value):
        try:
            parsed = datetime.fromisoformat(str(value))
            return parsed.astimezone() if parsed.tzinfo else parsed.astimezone()
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _add_calendar_month(value):
        month_index = value.year * 12 + value.month
        year, month_zero = divmod(month_index, 12)
        month = month_zero + 1
        day = min(value.day, calendar.monthrange(year, month)[1])
        return value.replace(year=year, month=month, day=day)

    def get(self):
        data = {
            "theme": "light",
            "timezone": "Asia/Tokyo",
            "auto_update_catalog": True,
            "last_catalog_update_attempt_at": None,
            "last_catalog_update_success_at": None,
            "last_catalog_update_status": "never",
        }
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                data.update({key: saved[key] for key in data if key in saved})
        except (OSError, ValueError, TypeError):
            pass
        return data

    def update(self, values):
        with self._lock:
            data = self.get()
            if isinstance(values.get("theme"), str) and values["theme"] in {"light", "dark"}:
                data["theme"] = values["theme"]
            allowed_timezones = {"Asia/Tokyo", "BRT", "UTC", "America/New_York", "Europe/London", "Asia/Seoul", "Asia/Shanghai"}
            if isinstance(values.get("timezone"), str) and values["timezone"] in allowed_timezones:
                data["timezone"] = values["timezone"]
            if "auto_update_catalog" in values:
                data["auto_update_catalog"] = bool(values["auto_update_catalog"])
            self._write(data)
            return data

    def is_due(self, now=None):
        data = self.get()
        if not data["auto_update_catalog"]:
            return False, None
        now = now or self._now()
        last = self._parse(data.get("last_catalog_update_attempt_at"))
        if last is None:
            return True, now
        if data.get("last_catalog_update_status") == "running":
            retry_at = last + timedelta(minutes=30)
            return now >= retry_at, retry_at
        due_at = self._add_calendar_month(last)
        return now >= due_at, due_at

    def record_attempt(self, when=None):
        with self._lock:
            data = self.get()
            data["last_catalog_update_attempt_at"] = (when or self._now()).isoformat(timespec="seconds")
            data["last_catalog_update_status"] = "running"
            self._write(data)
            return data

    def record_result(self, success, cancelled=False):
        with self._lock:
            data = self.get()
            now = self._now().isoformat(timespec="seconds")
            data["last_catalog_update_attempt_at"] = now
            data["last_catalog_update_status"] = (
                "success" if success else "cancelled" if cancelled else "failed"
            )
            if success:
                data["last_catalog_update_success_at"] = now
            self._write(data)
            return data

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(self.path.name + ".tmp")
        temp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp_path.replace(self.path)
