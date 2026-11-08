import contextlib
import io
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import gui_app


class OnlineWikiGuiTests(unittest.TestCase):
    def setUp(self):
        self.api = gui_app.BackroomsAPI.__new__(gui_app.BackroomsAPI)
        self.api._maintenance_lock = threading.Lock()
        self.api._wiki_browser_window = None

    @patch("gui_app.load_master_database")
    @patch("gui_app.search_online_wiki_pages")
    def test_online_wiki_search_does_not_read_master_database(
        self, search_pages, load_master
    ):
        search_pages.return_value = [{"title": "Level 0"}]

        result = self.api.search_online_wiki("Level 0")

        self.assertEqual(result["pages"], [{"title": "Level 0"}])
        load_master.assert_not_called()
        search_pages.assert_called_once_with("Level 0")

    @patch("gui_app.fetch_online_wiki_page")
    def test_get_online_page_fetches_from_live_wiki(self, fetch_page):
        fetch_page.return_value = {"title": "Level 0", "html": "<p>Body</p>"}

        result = self.api.get_online_wiki_page(
            "https://backrooms.fandom.com/ja/wiki/Level_0"
        )

        self.assertEqual(result["page"]["html"], "<p>Body</p>")
        fetch_page.assert_called_once_with(
            "https://backrooms.fandom.com/ja/wiki/Level_0"
        )

    @patch.object(gui_app.BackroomsAPI, "run_maintenance_task")
    def test_online_wiki_page_save_starts_selected_page_task(self, run_task):
        run_task.return_value = {"status": "started"}
        page = {
            "title": "Level 0",
            "url": "https://backrooms.fandom.com/ja/wiki/Level_0",
            "categories": ["通常階層"],
        }

        result = self.api.start_online_wiki_save(page)

        self.assertEqual(result["status"], "started")
        run_task.assert_called_once_with("save_online_wiki_page", page)

    def test_master_database_download_route_is_rejected(self):
        result = self.api.run_maintenance_task("download_html")

        self.assertEqual(result["status"], "error")
        self.assertIn("オンラインWiki検索", result["message"])

    def test_wiki_has_its_own_tab_and_gui_execution_log_is_removed(self):
        self.assertIn('id="view-wiki"', gui_app.HTML_TEMPLATE)
        self.assertIn("switchTab('wiki')", gui_app.HTML_TEMPLATE)
        self.assertIn('id="view-maintenance"', gui_app.HTML_TEMPLATE)
        self.assertNotIn('id="task-log"', gui_app.HTML_TEMPLATE)
        self.assertNotIn("function appendLog", gui_app.HTML_TEMPLATE)

    @patch.object(gui_app.BackroomsAPI, "_notify_maintenance_task_completed")
    @patch("gui_app.run_update_database")
    def test_maintenance_task_reports_status_without_gui_log_stream(
        self, update_database, notify
    ):
        self.api._maintenance_lock.acquire()
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            self.api._execute_task("update_master")

        self.assertIn("既存データベース更新", output.getvalue())
        notify.assert_called_once_with(True, "既存データベース更新が完了しました。")
        self.assertFalse(self.api._maintenance_lock.locked())
        update_database.assert_called_once_with()

    @patch.object(gui_app.BackroomsAPI, "_notify_maintenance_task_completed")
    @patch("gui_app.run_update_database", side_effect=RuntimeError("test failure"))
    def test_maintenance_task_reports_failures(self, update_database, notify):
        self.api._maintenance_lock.acquire()
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            self.api._execute_task("update_master")

        self.assertIn("test failure", output.getvalue())
        notify.assert_called_once_with(
            False, "既存データベース更新に失敗しました: test failure"
        )
        self.assertFalse(self.api._maintenance_lock.locked())

    @patch("gui_app.webview.create_window")
    def test_open_online_wiki_browser_rejects_non_wiki_urls(self, create_window):
        result = self.api.open_online_wiki_browser("https://example.com/")

        self.assertEqual(result["status"], "error")
        create_window.assert_not_called()

    @patch("gui_app.webview.create_window")
    def test_open_online_wiki_browser_creates_direct_browser_window(self, create_window):
        class Event:
            def __init__(self):
                self.callbacks = []

            def __iadd__(self, callback):
                self.callbacks.append(callback)
                return self

        browser = unittest.mock.Mock()
        browser.events = SimpleNamespace(closed=Event(), loaded=Event())
        create_window.return_value = browser

        result = self.api.open_online_wiki_browser(
            "https://backrooms.fandom.com/ja/wiki/Level_0"
        )

        self.assertEqual(result["status"], "opened")
        create_window.assert_called_once()
        self.assertEqual(
            create_window.call_args.kwargs["url"],
            "https://backrooms.fandom.com/ja/wiki/Level_0",
        )
        self.assertIs(self.api._wiki_browser_window, browser)
        self.assertIn(self.api._on_wiki_browser_closed, browser.events.closed.callbacks)
        self.assertIn(self.api._on_wiki_browser_loaded, browser.events.loaded.callbacks)

    def test_current_browser_url_is_validated_before_saving(self):
        browser = unittest.mock.Mock()
        browser.get_current_url.return_value = "https://example.com/"
        self.api._wiki_browser_window = browser

        result = self.api.get_current_online_wiki_url()

        self.assertEqual(result, {
            "url": "https://example.com/",
            "is_wiki_page": False,
        })

    def test_browser_url_notifications_target_the_main_window(self):
        main_window = unittest.mock.Mock()
        self.api._main_window = main_window

        self.api._notify_online_wiki_url(
            "https://backrooms.fandom.com/ja/wiki/Level_0",
            True,
        )

        self.assertIn(
            "onOnlineWikiBrowserUrlChanged",
            main_window.evaluate_js.call_args.args[0],
        )

    @patch.object(gui_app.BackroomsAPI, "start_online_wiki_save")
    def test_saving_current_browser_url_uses_decoded_page_title(self, start_save):
        browser = unittest.mock.Mock()
        browser.get_current_url.return_value = (
            "https://backrooms.fandom.com/ja/wiki/%E4%BA%8C%E6%9C%88%E9%A7%85"
        )
        self.api._wiki_browser_window = browser
        start_save.return_value = {"status": "started"}

        result = self.api.save_current_online_wiki_page()

        self.assertEqual(result["status"], "started")
        start_save.assert_called_once_with({
            "title": "二月駅",
            "url": browser.get_current_url.return_value,
            "categories": [],
        })

    @patch("gui_app.save_online_wiki_page")
    def test_save_task_reports_completion(self, save_page):
        page = {
            "title": "Level 0",
            "url": "https://backrooms.fandom.com/ja/wiki/Level_0",
            "categories": [],
        }
        save_page.return_value = {
            "title": "Level 0",
            "path": "downloaded_fandom_html/Level_0.html",
            "images_saved": 2,
            "images_failed": 0,
        }
        window = unittest.mock.Mock()
        self.api._main_window = window
        self.api._maintenance_lock.acquire()
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            self.api._execute_task("save_online_wiki_page", page)

        self.assertIn("Wikiページを保存します", output.getvalue())
        save_page.assert_called_once_with(page)
        window.evaluate_js.assert_called_once()
        self.assertIn("onOnlineWikiSaveCompleted", window.evaluate_js.call_args.args[0])
        self.assertFalse(self.api._maintenance_lock.locked())


if __name__ == "__main__":
    unittest.main()
