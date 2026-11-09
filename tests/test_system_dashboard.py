import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from modules.app_settings import AppSettingsStore
from modules.system_dashboard import format_clock, get_network_kind, get_power_status, list_local_images, storage_summary


class SystemDashboardTests(unittest.TestCase):
    def test_clock_formats_tokyo_and_backrooms_standard_time(self):
        instant = datetime(2026, 11, 9, 16, 56, 0, tzinfo=timezone.utc)
        self.assertEqual(format_clock(instant, "Asia/Tokyo"), "2026/11/10 (火) 01:56:00 JST")
        self.assertEqual(format_clock(instant, "BRT"), "2026/11/09 (月) 16:56:00 BRT")

    def test_settings_persist_theme_and_timezone(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = AppSettingsStore(Path(temporary) / "settings.json")
            saved = store.update({"theme": "dark", "timezone": "BRT"})
            self.assertEqual(saved["theme"], "dark")
            self.assertEqual(saved["timezone"], "BRT")
            self.assertEqual(store.get()["theme"], "dark")
            store.update({"theme": "invalid", "timezone": "invalid"})
            self.assertEqual(store.get()["theme"], "dark")
            self.assertEqual(store.get()["timezone"], "BRT")
            store.update({"theme": {}, "timezone": []})
            self.assertEqual(store.get()["theme"], "dark")
            self.assertEqual(store.get()["timezone"], "BRT")

    def test_storage_summary_counts_downloaded_files_and_drive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "downloaded_fandom_html").mkdir()
            (root / "downloaded_fandom_html" / "Level_0.html").write_text("abc", encoding="utf-8")
            (root / "downloaded_images").mkdir()
            (root / "downloaded_images" / "Level_0_a.png").write_bytes(b"12345")
            (root / "backrooms_edges.csv").write_text("x", encoding="utf-8")
            summary = storage_summary(root)
            self.assertEqual(summary["categories"]["取得済みHTML"], {"files": 1, "bytes": 3})
            self.assertEqual(summary["categories"]["保存済み画像"], {"files": 1, "bytes": 5})
            self.assertEqual(summary["categories"]["インデックス・DB・CSV"]["files"], 1)
            self.assertGreater(summary["drive"]["total"], 0)

    def test_image_gallery_infers_level_and_ignores_other_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Level_12_η_photo.jpg").write_bytes(b"image")
            (root / "readme.txt").write_text("skip", encoding="utf-8")
            images = list_local_images(root)
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0]["level_id"], "Level 12 η")
            self.assertEqual(images[0]["name"], "Level_12_η_photo.jpg")

    def test_status_helpers_return_safe_ui_values(self):
        self.assertIn(get_network_kind(), {"有線LAN", "Wi-Fi", "オフライン"})
        self.assertIsInstance(get_power_status(), str)


if __name__ == "__main__":
    unittest.main()
