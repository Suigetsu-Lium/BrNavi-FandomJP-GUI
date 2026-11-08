import asyncio
import unittest
from unittest.mock import patch

from modules.ai.groq_client import GroqClient


class FakeStream:
    def __init__(self):
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.started.set()
        await asyncio.Future()


class FakeCompletions:
    def __init__(self, stream):
        self.stream = stream

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.stream


class FakeAsyncGroq:
    def __init__(self, stream, **kwargs):
        self.chat = type(
            "Chat",
            (),
            {"completions": FakeCompletions(stream)},
        )()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class GroqStreamingTests(unittest.TestCase):
    def test_cancellation_closes_sdk_stream(self):
        async def run():
            stream = FakeStream()
            client = GroqClient.__new__(GroqClient)
            client.api_key = "test-key"
            client.model = "test-model"
            client.max_completion_tokens = 128
            client.timeout = 5

            with patch(
                "modules.ai.groq_client.AsyncGroq",
                lambda **kwargs: FakeAsyncGroq(stream, **kwargs),
            ):
                task = asyncio.create_task(client.stream_chat([{"role": "user", "content": "hi"}]))
                await stream.started.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertTrue(stream.closed.is_set())

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
