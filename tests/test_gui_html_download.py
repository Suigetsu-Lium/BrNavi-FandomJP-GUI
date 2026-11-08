import contextlib
import io
import threading
import unittest
from unittest.mock import patch

import gui_app


class HTMLDownloadPickerTests(unittest.TestCase):
    def setUp(self):
        self.api = gui_app.BackroomsAPI.__new__(gui_app.BackroomsAPI)
        self.api._maintenance_lock = threading.Lock()

    @patch("gui_app.get_category_label", side_effect=lambda value: value.upper())
    @patch("gui_app.load_master_database")
    def test_page_list_exposes_searchable_tags(self, load_pages, _category_label):
        load_pages.return_value = [
            {
                "title": "Level 0",
                "url": "https://backrooms.fandom.com/ja/wiki/Level_0",
                "language": "JP",
                "category": "normal|normal_eta",
                "source_type": "wiki",
            }
        ]

        result = self.api.get_html_download_pages()

        self.assertEqual(len(result["pages"]), 1)
        self.assertEqual(
            result["pages"][0]["tags"],
            ["NORMAL", "NORMAL_ETA", "JP", "wiki"],
        )

    @patch("gui_app.load_master_database")
    def test_download_rejects_urls_not_in_master_database(self, load_pages):
        load_pages.return_value = [
            {"title": "Level 0", "url": "https://example.test/level-0"}
        ]

        result = self.api.start_html_download(["https://example.test/other"])

        self.assertEqual(result["status"], "error")
        self.assertIn("一覧が更新", result["message"])

    @patch.object(gui_app.BackroomsAPI, "run_maintenance_task")
    @patch("gui_app.load_master_database")
    def test_download_starts_only_master_urls_and_deduplicates_selection(
        self, load_pages, run_task
    ):
        url = "https://example.test/level-0"
        load_pages.return_value = [{"title": "Level 0", "url": url}]
        run_task.return_value = {"status": "started"}

        result = self.api.start_html_download([url, url])

        self.assertEqual(result["status"], "started")
        run_task.assert_called_once_with("download_selected_html", [url])

    def test_old_interactive_gui_download_route_is_rejected(self):
        result = self.api.run_maintenance_task("download_html")

        self.assertEqual(result["status"], "error")
        self.assertIn("一覧から", result["message"])

    @patch("gui_app.download_html_pages")
    @patch("gui_app.load_master_database")
    @patch("gui_app.webview.windows", new_callable=list)
    def test_selected_download_logs_to_cli_and_reports_completion(
        self, windows, load_pages, download_pages
    ):
        page = {"title": "Level 0", "url": "https://example.test/level-0"}
        load_pages.return_value = [page]
        download_pages.return_value = {"downloaded": 1, "skipped": 0, "failed": 0}
        window = unittest.mock.Mock()
        windows.append(window)
        self.api._maintenance_lock.acquire()
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            self.api._execute_task("download_selected_html", [page["url"]])

        self.assertIn("GUIで選択された 1 件", output.getvalue())
        self.assertIn("取得 1 件", output.getvalue())
        download_pages.assert_called_once_with([page], overwrite=False)
        window.evaluate_js.assert_called_once()
        self.assertIn("onHTMLDownloadCompleted", window.evaluate_js.call_args.args[0])
        self.assertFalse(self.api._maintenance_lock.locked())


if __name__ == "__main__":
    unittest.main()
