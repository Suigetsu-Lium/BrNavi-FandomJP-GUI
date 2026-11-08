import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.ai.assistant import AIAssistant
from modules.ai.wiki_retriever import WikiRetriever


class FakeGroqClient:
    replies = []
    model = "test-model"
    max_completion_tokens = 4096

    def __init__(self, **kwargs):
        self.responses = list(type(self).replies)

    async def stream_chat(self, messages, on_delta=None, max_completion_tokens=None):
        if not self.responses:
            raise AssertionError("Unexpected Groq request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if on_delta:
            on_delta(response["content"])
        return response

    async def generate_title(self, prompt):
        return "テストタイトル"


class AssistantTests(unittest.TestCase):
    def create_assistant(self, directory, replies):
        FakeGroqClient.replies = replies
        with patch("modules.ai.assistant.GroqClient", FakeGroqClient):
            assistant = AIAssistant(directory, directory)
        assistant.retriever.online_search = False
        return assistant

    def test_chat_persists_response_version_and_stream_delta(self):
        with tempfile.TemporaryDirectory() as directory:
            assistant = self.create_assistant(
                directory, [{"content": "# 回答", "finish_reason": "stop"}]
            )
            cid = assistant.new_conversation()
            generation = assistant.store.begin_generation(cid, "質問")
            deltas = []
            result = asyncio.run(assistant.chat(generation, deltas.append))

            self.assertEqual(deltas, ["# 回答"])
            self.assertEqual(result["response"], "# 回答")
            self.assertEqual(result["version_number"], 1)
            history = assistant.get_history(cid)
            self.assertEqual(history[1]["content"], "# 回答")
            self.assertEqual(history[1]["versions"][0]["content"], "# 回答")

    def test_live_external_source_is_labeled_and_online_errors_are_visible(self):
        source = {
            "title": "Level 0",
            "url": "https://backrooms.fandom.com/wiki/Level_0",
            "source_kind": "external",
            "status": "retrieved_live",
            "retrieved_at": "2025-01-02T03:04:05+00:00",
            "retrieval_time_basis": "live_api_retrieval_time",
            "page_updated_at": "2025-01-01T00:00:00Z",
            "excerpt": "Verified article text.",
        }

        reference = AIAssistant._format_source_references([source])
        warning = AIAssistant._format_search_warnings(
            {
                "external_online_status": "failed",
                "online_errors": [
                    {
                        "source": "英語版Backrooms Wiki",
                        "error": "connection timed out",
                    }
                ],
            }
        )

        self.assertIn("英語版Wiki・オンライン取得", reference)
        self.assertIn("取得日時:", reference)
        self.assertIn("Wikiページ更新日時:", reference)
        self.assertIn("connection timed out", warning)

    def test_length_finish_requests_one_continuation(self):
        with tempfile.TemporaryDirectory() as directory:
            assistant = self.create_assistant(
                directory,
                [
                    {"content": "途中", "finish_reason": "length"},
                    {"content": "の続き", "finish_reason": "stop"},
                ],
            )
            cid = assistant.new_conversation()
            generation = assistant.store.begin_generation(cid, "質問")
            result = asyncio.run(assistant.chat(generation))
            self.assertEqual(result["response"], "途中の続き")
            self.assertEqual(result["finish_reason"], "stop")
            self.assertEqual(len(assistant.get_history(cid)[1]["versions"]), 1)

    def test_failed_regeneration_leaves_current_answer_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            assistant = self.create_assistant(
                directory,
                [
                    {"content": "回答1", "finish_reason": "stop"},
                    RuntimeError("API error"),
                ],
            )
            cid = assistant.new_conversation()
            initial = assistant.store.begin_generation(cid, "質問")
            asyncio.run(assistant.chat(initial))
            old = assistant.get_history(cid)[1]
            regeneration = assistant.store.begin_generation(
                cid, "", "regenerate", old["id"]
            )
            with self.assertRaisesRegex(RuntimeError, "API error"):
                asyncio.run(assistant.chat(regeneration))
            assistant.store.finish_generation_error(regeneration["request_id"])
            current = assistant.get_history(cid)[1]
            self.assertEqual(current["content"], "回答1")
            self.assertEqual(len(current["versions"]), 1)

    def test_wiki_lookup_without_sources_returns_uncertainty_without_groq(self):
        with tempfile.TemporaryDirectory() as directory:
            assistant = self.create_assistant(directory, [])
            assistant.retriever = WikiRetriever(
                directory,
                html_dir=Path(directory) / "html",
                master_pages=[],
                online_search=False,
            )
            cid = assistant.new_conversation()
            generation = assistant.store.begin_generation(cid, "二月駅とは")

            result = asyncio.run(assistant.chat(generation))

            self.assertIn("確認できませんでした", result["response"])
            self.assertIn("具体的な設定は断定できません", result["response"])
            self.assertEqual(result["finish_reason"], "no_verified_sources")
            self.assertEqual(result["sources"], [])

    def test_grounded_answer_is_verified_and_persisted_with_source_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            html_dir = root / "html"
            html_dir.mkdir()
            url = "https://backrooms.fandom.com/ja/wiki/Entity_1"
            (html_dir / "entity-1.html").write_text(
                "<!-- BRNAVI_META_INFO: TITLE=Entity 1 | "
                f"URL={url} | LANGUAGE=JP -->\n"
                "<html><body><h1 class='mw-page-title-main'>Entity 1</h1>"
                "<div class='mw-parser-output'><p>Entity 1は夜行性である。</p>"
                "</div></body></html>",
                encoding="utf-8",
            )
            assistant = self.create_assistant(
                directory,
                [
                    {
                        "content": "夜行性で、寿命は200年です。",
                        "finish_reason": "stop",
                    },
                    {
                        "content": "資料で確認できるのは夜行性という記述です。寿命は資料から確認できません。",
                        "finish_reason": "stop",
                    },
                ],
            )
            assistant.retriever = WikiRetriever(
                root / "text",
                html_dir=html_dir,
                master_pages=[],
                online_search=False,
            )
            cid = assistant.new_conversation()
            generation = assistant.store.begin_generation(cid, "Entity 1の生態は？")

            result = asyncio.run(assistant.chat(generation))

            self.assertIn("資料で確認できる", result["response"])
            self.assertNotIn("寿命は200年", result["response"])
            self.assertIn(url, result["response"])
            self.assertEqual(result["sources"][0]["source_kind"], "japanese_wiki")
            self.assertIn("file_modified_at", result["sources"][0])
            saved = assistant.get_history(cid)[1]["sources"]
            self.assertEqual(saved[0]["url"], url)
            self.assertIn("夜行性", saved[0]["excerpt"])


if __name__ == "__main__":
    unittest.main()
