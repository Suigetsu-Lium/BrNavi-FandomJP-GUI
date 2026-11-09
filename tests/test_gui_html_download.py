import contextlib
import io
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import gui_app
from modules.operation_context import OperationCancelled, OperationContext


class OnlineWikiGuiTests(unittest.TestCase):
    def setUp(self):
        self.api = gui_app.BackroomsAPI.__new__(gui_app.BackroomsAPI)
        self.api._maintenance_lock = threading.Lock()
        self.api._maintenance_cancel_event = threading.Event()
        self.api._maintenance_done_event = threading.Event()
        self.api._maintenance_done_event.set()
        self.api._current_online_wiki_url = ""

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

    def test_cancel_request_is_cooperative_and_requires_active_task(self):
        idle = self.api.cancel_maintenance_task()
        self.assertEqual(idle["status"], "idle")
        self.assertFalse(self.api._maintenance_cancel_event.is_set())

        self.api._maintenance_lock.acquire()
        self.api._maintenance_done_event.clear()
        try:
            result = self.api.cancel_maintenance_task()
            self.assertEqual(result["status"], "cancelling")
            self.assertTrue(self.api._maintenance_cancel_event.is_set())
        finally:
            self.api._maintenance_done_event.set()
            self.api._maintenance_lock.release()

    def test_wiki_has_its_own_tab_and_gui_execution_log_is_removed(self):
        self.assertIn('id="view-wiki"', gui_app.HTML_TEMPLATE)
        self.assertIn("switchTab('wiki')", gui_app.HTML_TEMPLATE)
        self.assertIn('id="view-maintenance"', gui_app.HTML_TEMPLATE)
        self.assertIn('id="online-wiki-frame"', gui_app.HTML_TEMPLATE)
        self.assertIn(
            "document.getElementById('online-wiki-frame').src = result.url",
            gui_app.HTML_TEMPLATE,
        )
        self.assertNotIn('id="task-log"', gui_app.HTML_TEMPLATE)
        self.assertNotIn("function appendLog", gui_app.HTML_TEMPLATE)
        self.assertNotIn("open_online_wiki_browser", gui_app.HTML_TEMPLATE)

    def test_wiki_frame_load_handler_closes_before_url_refresh_function(self):
        self.assertIn(
            "await updateOnlineWikiCurrentUrl();\n"
            "    }\n\n"
            "    async function updateOnlineWikiCurrentUrl()",
            gui_app.HTML_TEMPLATE,
        )

    def test_level_list_data_tools_tab_uses_level_list_tiles_not_search_results(self):
        self.assertIn("switchTab('data-tools')", gui_app.HTML_TEMPLATE)
        self.assertIn('id="level-list-language"', gui_app.HTML_TEMPLATE)
        self.assertIn('id="level-list-grid"', gui_app.HTML_TEMPLATE)
        self.assertIn('class="flex flex-col flex-1 min-h-0 overflow-y-auto border-y', gui_app.HTML_TEMPLATE)
        self.assertIn("tile.oncontextmenu = event =>", gui_app.HTML_TEMPLATE)
        self.assertIn("deselectLevelListPage(page)", gui_app.HTML_TEMPLATE)
        self.assertIn("selected ? \"bg-[#e7dfca] text-[#5b5343]\"", gui_app.HTML_TEMPLATE)
        self.assertIn("selected ? \"border-[#713b09] bg-[#91520f] text-white shadow-sm\"", gui_app.HTML_TEMPLATE)
        self.assertIn('id="level-list-clear-selection"', gui_app.HTML_TEMPLATE)
        self.assertIn("event.shiftKey", gui_app.HTML_TEMPLATE)
        self.assertIn("pywebview.api.get_level_list_pages(language)", gui_app.HTML_TEMPLATE)
        self.assertIn("pywebview.api.start_level_list_offline_save", gui_app.HTML_TEMPLATE)
        self.assertIn("onclick=\"runLevelListOfflineSave()\"", gui_app.HTML_TEMPLATE)
        self.assertIn(">Level情報のオフライン保存</button>", gui_app.HTML_TEMPLATE)

    @patch("gui_app.get_level_list_languages", return_value=["JP", "EN"])
    def test_level_list_languages_are_exposed_to_the_gui(self, get_languages):
        result = self.api.get_level_list_languages()

        self.assertEqual(result, {"languages": ["JP", "EN"]})
        get_languages.assert_called_once_with()

    @patch("gui_app.load_level_list_pages", return_value=[{"title": "Level 0"}])
    def test_level_list_pages_are_loaded_for_the_selected_language(self, load_pages):
        result = self.api.get_level_list_pages("JP")

        self.assertEqual(result, {"pages": [{"title": "Level 0"}]})
        load_pages.assert_called_once_with("JP")

    @patch.object(gui_app.BackroomsAPI, "run_maintenance_task")
    def test_level_list_offline_save_rejects_external_pages_before_starting(self, run_task):
        result = self.api.start_level_list_offline_save(
            [{"title": "External", "url": "https://example.com/wiki/Page"}]
        )

        self.assertEqual(result["status"], "error")
        run_task.assert_not_called()

    @patch.object(gui_app.BackroomsAPI, "run_maintenance_task")
    def test_level_list_offline_save_starts_with_selected_pages_and_overwrite(self, run_task):
        run_task.return_value = {"status": "started"}
        page = {
            "title": "Level 0",
            "url": "https://backrooms.fandom.com/wiki/Level_0",
        }

        result = self.api.start_level_list_offline_save([page], overwrite=True)

        self.assertEqual(result["status"], "started")
        run_task.assert_called_once_with(
            "save_level_list_offline",
            options={"pages": [page], "overwrite": True},
        )

    def test_maintenance_panel_exposes_service_progress_and_stop_controls(self):
        self.assertIn("maintenance-update-mode", gui_app.HTML_TEMPLATE)
        self.assertIn("maintenance-progress", gui_app.HTML_TEMPLATE)
        self.assertIn("maintenance-log", gui_app.HTML_TEMPLATE)
        self.assertIn("cancelMaintenanceTask()", gui_app.HTML_TEMPLATE)
        self.assertIn("pywebview.api.cancel_maintenance_task()", gui_app.HTML_TEMPLATE)
        self.assertIn("pywebview.api.run_maintenance_task(taskName, null, options)", gui_app.HTML_TEMPLATE)

    def test_about_dialog_has_version_model_and_license_links(self):
        self.assertIn('id="about-dialog"', gui_app.HTML_TEMPLATE)
        self.assertIn("get_about_info()", gui_app.HTML_TEMPLATE)
        self.assertIn("openAboutLink('third_party')", gui_app.HTML_TEMPLATE)
        self.assertIn("openAboutLink('license')", gui_app.HTML_TEMPLATE)
        self.assertIn("pywebview.api.open_about_link", gui_app.HTML_TEMPLATE)

    def test_wiki_tab_opens_default_homepage_for_free_browsing(self):
        default_url = "https://backrooms.fandom.com/ja/wiki/Backrooms_Wiki"

        self.assertIn(f'value="{default_url}"', gui_app.HTML_TEMPLATE)
        self.assertIn("if (tab === 'wiki' && !onlineWikiDefaultOpened)", gui_app.HTML_TEMPLATE)
        self.assertIn("openDirectWikiUrl();", gui_app.HTML_TEMPLATE)
        self.assertIn(
            "document.getElementById('online-wiki-url').value = onlineWikiCurrentUrl;",
            gui_app.HTML_TEMPLATE,
        )

    @patch.object(gui_app.BackroomsAPI, "_notify_maintenance_task_completed")
    @patch("gui_app.run_maintenance_operation")
    def test_maintenance_task_reports_status_without_gui_log_stream(
        self, run_operation, notify
    ):
        run_operation.return_value = SimpleNamespace(
            success=True,
            message="既存データベースを更新しました。",
        )
        self.api._maintenance_lock.acquire()
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            self.api._execute_task("update_master", options={"mode": 1})

        self.assertIn("既存データベース更新", output.getvalue())
        notify.assert_called_once_with(True, "既存データベースを更新しました。")
        self.assertFalse(self.api._maintenance_lock.locked())
        args, kwargs = run_operation.call_args
        self.assertEqual(args, ("update_master",))
        self.assertEqual(kwargs["options"], {"mode": 1})
        self.assertIsInstance(kwargs["context"], OperationContext)

    @patch.object(gui_app.BackroomsAPI, "_notify_maintenance_task_completed")
    @patch("gui_app.run_maintenance_operation", side_effect=RuntimeError("test failure"))
    def test_maintenance_task_reports_failures(self, run_operation, notify):
        self.api._maintenance_lock.acquire()
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            self.api._execute_task("update_master")

        self.assertIn("test failure", output.getvalue())
        notify.assert_called_once_with(
            False, "既存データベース更新に失敗しました: test failure"
        )
        self.assertFalse(self.api._maintenance_lock.locked())

    @patch.object(gui_app.BackroomsAPI, "_notify_maintenance_task_completed")
    @patch("gui_app.run_maintenance_operation", side_effect=OperationCancelled())
    def test_maintenance_task_reports_cooperative_cancellation(
        self, run_operation, notify
    ):
        self.api._maintenance_lock.acquire()

        self.api._execute_task("extract_info")

        args, kwargs = notify.call_args
        self.assertFalse(args[0])
        self.assertIn("停止", args[1])
        self.assertTrue(kwargs["cancelled"])
        self.assertFalse(self.api._maintenance_lock.locked())

    def test_embedded_browser_navigation_rejects_non_wiki_urls(self):
        result = self.api.navigate_online_wiki_frame("https://example.com/")

        self.assertEqual(result["status"], "error")

    def test_embedded_browser_navigation_accepts_and_tracks_wiki_url(self):
        result = self.api.navigate_online_wiki_frame(
            "https://backrooms.fandom.com/ja/wiki/Level_0"
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            self.api.get_current_online_wiki_url(),
            {
                "url": "https://backrooms.fandom.com/ja/wiki/Level_0",
                "is_wiki_page": True,
            },
        )

    @patch.object(gui_app.BackroomsAPI, "_notify_online_wiki_url")
    def test_embedded_frame_navigation_tracks_actual_wiki_document(self, notify):
        response = unittest.mock.Mock()
        response.url = "https://backrooms.fandom.com/ja/wiki/Level_0"
        response.headers = {"Content-Type": "text/html; charset=UTF-8"}

        self.api._on_main_window_response_received(response)

        self.assertEqual(
            self.api.get_current_online_wiki_url(),
            {"url": response.url, "is_wiki_page": True},
        )
        notify.assert_called_once_with(response.url, True)

    @patch.object(gui_app.BackroomsAPI, "_notify_online_wiki_url")
    def test_embedded_frame_ignores_non_document_responses(self, notify):
        response = unittest.mock.Mock()
        response.url = "https://backrooms.fandom.com/ja/api.php"
        response.headers = {"Content-Type": "application/json"}

        self.api._on_main_window_response_received(response)

        self.assertEqual(self.api._current_online_wiki_url, "")
        notify.assert_not_called()

    def test_embedded_frame_url_notifications_target_the_main_window(self):
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
    def test_saving_current_embedded_frame_url_uses_decoded_page_title(self, start_save):
        self.api._current_online_wiki_url = (
            "https://backrooms.fandom.com/ja/wiki/%E4%BA%8C%E6%9C%88%E9%A7%85"
        )
        start_save.return_value = {"status": "started"}

        result = self.api.save_current_online_wiki_page()

        self.assertEqual(result["status"], "started")
        start_save.assert_called_once_with({
            "title": "二月駅",
            "url": self.api._current_online_wiki_url,
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
