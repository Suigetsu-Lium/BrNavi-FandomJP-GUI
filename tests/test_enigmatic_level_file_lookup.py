import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gui_app
from modules import database


ETA = chr(0x03B7)
ROOT = "Level "


class EnigmaticLevelFileLookupTests(unittest.TestCase):
    def setUp(self):
        self.api = gui_app.BackroomsAPI.__new__(gui_app.BackroomsAPI)

    def test_normal_eta_category_does_not_filter_to_numeric_titles(self):
        start = gui_app.HTML_TEMPLATE.index('if (categoryId === "normal_eta")')
        end = gui_app.HTML_TEMPLATE.index('if (categoryId === "anomalous")', start)
        eta_filter = gui_app.HTML_TEMPLATE[start:end]
        self.assertIn('return category === "normal_eta" && source !== "translation";', eta_filter)
        self.assertNotIn('test(title)', eta_filter)

    def test_html_prefix_supports_numeric_and_non_numeric_eta_ids(self):
        cases = {
            f"Level 000 {ETA}": f"Level_000_{ETA}",
            f"Level -0 {ETA}": f"Level_-0_{ETA}",
            f"Level {chr(0x221A)}-7 {ETA}": f"Level_{chr(0x221A)}-7_{ETA}",
            f"Level Snow Globe H {ETA}": f"Level_Snow_Globe_H_{ETA}",
            f"Level ! {ETA}": f"Level_!_{ETA}",
            f"Level 7/8 {ETA}": f"Level_7_8_{ETA}",
        }
        for level_id, expected in cases.items():
            with self.subTest(level_id=level_id):
                self.assertEqual(database.get_html_filename_prefix(level_id), expected)

    def test_html_and_text_database_find_non_numeric_eta_files(self):
        level_id = f"Level {chr(0x221A)}-7 {ETA}"
        html_name = f"Level_{chr(0x221A)}-7_{ETA}.html"
        text_name = f"Level {chr(0x221A)}-7 {ETA}__caption.txt"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / html_name).write_text("html", encoding="utf-8")
            (root / text_name).write_text("text", encoding="utf-8")
            with patch.object(database, "HTML_DB_DIR", root), patch.object(database, "TEXT_DB_DIR", root):
                self.assertEqual(database.find_html_database(level_id), root / html_name)
                self.assertEqual(database.find_text_database(level_id), root / text_name)

    def test_gui_filename_parser_reads_arbitrary_eta_identifiers(self):
        cases = [
            (f"Level 000 {ETA}.html", f"Level 000 {ETA}"),
            (f"Level -0 {ETA}.html", f"Level -0 {ETA}"),
            (f"Level_{chr(0x221A)}-7_{ETA}.html", f"Level {chr(0x221A)}-7 {ETA}"),
            (f"Level_Snow_Globe_H_{ETA}.html", f"Level Snow Globe H {ETA}"),
            (f"Level_!_{ETA}.html", f"Level ! {ETA}"),
        ]
        for filename, expected in cases:
            with self.subTest(filename=filename):
                self.assertEqual(self.api._extract_level_id_from_filename(Path(filename)), expected)

    def test_gui_filename_matching_never_accepts_a_title_prefix(self):
        cases = [
            ("Level 0 eta.html", "Level 0"),
            ("Level 0.1.html", "Level 0"),
            ("Level 0 eta__caption.txt", "Level 0"),
        ]
        for filename, level_id in cases:
            with self.subTest(filename=filename):
                self.assertFalse(
                    self.api._filename_matches_level_id(Path(filename), level_id)
                )

    def test_gui_matches_sanitized_filenames_including_slash_ids(self):
        cases = [
            (f"Level_{chr(0x221A)}-7_{ETA}.html", f"Level {chr(0x221A)}-7 {ETA}"),
            (f"Level_Snow_Globe_H_{ETA}.html", f"Level Snow Globe H {ETA}"),
            (f"Level_7_8_{ETA}.html", f"Level 7/8 {ETA}"),
            (f"Level 7_8 {ETA}__caption.txt", f"Level 7/8 {ETA}"),
        ]
        for filename, level_id in cases:
            with self.subTest(filename=filename):
                self.assertTrue(self.api._filename_matches_level_id(Path(filename), level_id))

    def test_level_detail_uses_the_saved_special_eta_html_path(self):
        level_id = f"Level {chr(0x221A)}-7 {ETA}"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html_dir = root / "html"
            text_dir = root / "text"
            html_dir.mkdir()
            text_dir.mkdir()
            html_path = html_dir / f"{database.get_html_filename_prefix(level_id)}.html"
            html_path.write_text("<html>saved</html>", encoding="utf-8")
            text_name = database.sanitize_filename(level_id) + "__caption.txt"
            (text_dir / text_name).write_text("[saved body]", encoding="utf-8")
            with patch.object(gui_app, "HTML_DIR", html_dir), patch.object(gui_app, "TEXT_DIR", text_dir), patch.object(
                gui_app, "IMAGE_DIR", root / "images"
            ), patch.object(gui_app, "BASE_DIR", root), patch.object(
                gui_app, "load_master_database",
                return_value={"url": {"title": level_id, "url": "https://example.test"}},
            ):
                self.api._find_images_flexible = lambda _level_id: []
                result = self.api.get_level_detail(level_id)
                self.assertEqual(self.api.current_info["html_file"], html_path)
                self.assertTrue(self.api.current_info["html_file"].exists())
                self.assertEqual(result["level_id"], level_id)

    def test_negative_level_does_not_borrow_positive_level_text_file(self):
        negative_id = f"Level -20 {ETA}"
        positive_id = f"Level 20 {ETA}"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html_dir = root / "html"
            text_dir = root / "text"
            image_dir = root / "images"
            html_dir.mkdir()
            text_dir.mkdir()
            image_dir.mkdir()
            page_url = "https://example.test/level-minus-20"
            html_path = html_dir / "Level_-20_eta.html"
            html_path.write_text(f"URL={page_url}\n<html>negative level</html>", encoding="utf-8")
            (text_dir / f"Level 20 {ETA}__二月駅_.txt").write_text(
                f'[{positive_id}: "二月駅"]\n駅の外は非常に危険です。\n',
                encoding="utf-8",
            )
            pages = {page_url: {"title": negative_id, "url": page_url}}
            with patch.object(gui_app, "HTML_DIR", html_dir), patch.object(gui_app, "TEXT_DIR", text_dir), patch.object(
                gui_app, "IMAGE_DIR", image_dir
            ), patch.object(gui_app, "BASE_DIR", root), patch.object(
                gui_app, "load_master_database", return_value=pages
            ):
                self.api._find_images_flexible = lambda _level_id: []
                result = self.api.get_level_detail(negative_id)

        self.assertNotIn("二月駅", result["text"])
        self.assertNotIn("駅の外は非常に危険です", result["text"])
        self.assertIn("本文データがありません", result["text"])

    def test_search_candidates_does_not_mark_negative_level_saved_from_positive_text(self):
        negative_id = f"Level -20 {ETA}"
        positive_id = f"Level 20 {ETA}"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html_dir = root / "html"
            text_dir = root / "text"
            html_dir.mkdir()
            text_dir.mkdir()
            (text_dir / f"{positive_id}__二月駅_.txt").write_text("saved", encoding="utf-8")
            pages = {
                "https://example.test/negative": {"title": negative_id, "url": "https://example.test/negative"},
                "https://example.test/positive": {"title": positive_id, "url": "https://example.test/positive"},
            }
            with patch.object(gui_app, "HTML_DIR", html_dir), patch.object(gui_app, "TEXT_DIR", text_dir), patch.object(
                gui_app, "BASE_DIR", root
            ), patch.object(gui_app, "load_master_database", return_value=pages):
                results = self.api.search_candidates("")

        saved_by_title = {item["title"]: item["saved"] for item in results}
        self.assertFalse(saved_by_title[negative_id])
        self.assertTrue(saved_by_title[positive_id])

    def test_search_candidates_does_not_mark_partial_title_collision_as_saved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html_dir = root / "html"
            text_dir = root / "text"
            html_dir.mkdir()
            text_dir.mkdir()
            (html_dir / "Level 0.1.html").write_text("saved Level 0.1", encoding="utf-8")
            pages = {
                "https://example.test/level-0": {"title": "Level 0", "category": "normal", "language": "JP"},
                "https://example.test/level-0-1": {"title": "Level 0.1", "category": "normal", "language": "JP"},
            }
            with patch.object(gui_app, "HTML_DIR", html_dir), patch.object(gui_app, "TEXT_DIR", text_dir), patch.object(
                gui_app, "BASE_DIR", root
            ), patch.object(gui_app, "load_master_database", return_value=pages):
                results = self.api.search_candidates("")

        saved_by_title = {item["title"]: item["saved"] for item in results}
        self.assertFalse(saved_by_title["Level 0"])
        self.assertTrue(saved_by_title["Level 0.1"])

    def test_search_candidates_marks_saved_special_eta_pages(self):
        level_ids = [
            f"Level {chr(0x221A)}-7 {ETA}",
            f"Level Snow Globe H {ETA}",
            f"Level 7/8 {ETA}",
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html_dir = root / "html"
            text_dir = root / "text"
            html_dir.mkdir()
            text_dir.mkdir()
            for level_id in level_ids:
                (html_dir / f"{database.get_html_filename_prefix(level_id)}.html").write_text(
                    "saved", encoding="utf-8"
                )
                text_prefix = database.sanitize_filename(level_id.replace("/", "_"))
                (text_dir / f"{text_prefix}__caption.txt").write_text("saved", encoding="utf-8")
            pages = {
                f"url-{index}": {"title": level_id, "category": "normal_eta", "language": "JP"}
                for index, level_id in enumerate(level_ids)
            }
            with patch.object(gui_app, "HTML_DIR", html_dir), patch.object(gui_app, "TEXT_DIR", text_dir), patch.object(
                gui_app, "BASE_DIR", root
            ), patch.object(gui_app, "load_master_database", return_value=pages):
                results = self.api.search_candidates("")

        self.assertEqual({item["title"] for item in results if item["saved"]}, set(level_ids))
        self.assertEqual({item["title"] for item in results}, set(level_ids))


if __name__ == "__main__":
    unittest.main()
