import asyncio
import queue
import tempfile
import threading
import unittest
from types import SimpleNamespace

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
