import asyncio
import queue
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from gui_app import BackroomsAPI
from modules.ai.conversation import ConversationStore


class BlockingAssistant:
    def __init__(self, store):
        self.store = store
        self.started = threading.Event()

    async def chat(self, generation, on_delta=None):
        self.started.set()
        await asyncio.Future()


class GUIBridgeTests(unittest.TestCase):
    def test_ai_answer_links_stay_in_app_and_reject_local_addresses(self):
        api = BackroomsAPI.__new__(BackroomsAPI)

        result = api.navigate_ai_answer_link("https://example.com/reference")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(api._current_online_wiki_url, "https://example.com/reference")
        for url in (
            "javascript:alert(1)",
            "file:///C:/secret.txt",
            "http://127.0.0.1/admin",
            "http://192.168.1.2/",
        ):
            with self.subTest(url=url):
                self.assertEqual(api.navigate_ai_answer_link(url)["status"], "error")

    def test_ai_source_download_requires_explicit_consent(self):
        api = BackroomsAPI.__new__(BackroomsAPI)
        api.run_maintenance_task = Mock(return_value={"status": "started"})
        page = {
            "title": "Level 16 η",
            "url": "https://backrooms.fandom.com/ja/wiki/Level_16_%CE%B7",
            "categories": [],
        }

        rejected = api.start_ai_source_save(page, "Level 16 ηについて", False)
        self.assertEqual(rejected["status"], "error")
        api.run_maintenance_task.assert_not_called()

        accepted = api.start_ai_source_save(page, "Level 16 ηについて", True, "flow-1")
        self.assertEqual(accepted["status"], "started")
        api.run_maintenance_task.assert_called_once_with(
            "save_online_wiki_page",
            page,
            options={"ai_prompt": "Level 16 ηについて", "ai_flow_id": "flow-1"},
        )

    def test_cancelled_request_keeps_user_message_without_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(directory)
            assistant = BlockingAssistant(store)
            api = BackroomsAPI.__new__(BackroomsAPI)
            api.ai = assistant
            api._ai_jobs = {}
            api._ai_jobs_lock = threading.Lock()
            responses = queue.Queue()
            api._send_ai_result = responses.put
            api._send_ai_stream = lambda payload: None
            api._send_ai_title_update = lambda conversation_id: None
            cid = store.create_conversation()

            started = api._start_ai_generation(cid, "質問", "send", None, "request-1")
            self.assertTrue(started["accepted"])
            self.assertTrue(assistant.started.wait(timeout=5))
            self.assertTrue(api.ai_cancel_generation("request-1")["accepted"])

            response = responses.get(timeout=5)
            self.assertTrue(response["cancelled"])
            self.assertEqual(response["conversation_id"], cid)
            self.assertEqual(response["request_id"], "request-1")
            self.assertEqual(
                [item["role"] for item in store.get_history(cid)],
                ["user"],
            )
            self.assertEqual(
                store.get_generation("request-1")["status"], "cancelled"
            )


if __name__ == "__main__":
    unittest.main()
