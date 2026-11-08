import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.ai.wiki_retriever import WikiRetriever


class WikiRetrieverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.text_dir = self.root / "extracted_fandom_levels"
        self.html_dir = self.root / "downloaded_fandom_html"
        self.text_dir.mkdir()
        self.html_dir.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def add_page(self, filename, title, url, language, body):
        path = self.html_dir / filename
        path.write_text(
            "<!-- BRNAVI_META_INFO: "
            f"TITLE={title} | URL={url} | LANGUAGE={language} "
            "-->\n"
            "<html><head><title>Backrooms Wiki</title></head><body>"
            f"<h1 class='mw-page-title-main'>{title}</h1>"
            f"<div class='mw-parser-output'><p>{body}</p></div>"
            "</body></html>",
            encoding="utf-8",
        )
        return path

    def retriever(self):
        return WikiRetriever(self.text_dir, html_dir=self.html_dir, master_pages=[])

    def test_japanese_query_suffix_matches_page_title_and_preserves_citation(self):
        self.add_page(
            "february.html",
            "二月駅",
            "https://backrooms.fandom.com/ja/wiki/二月駅",
            "JP",
            "二月駅は、地下に存在する駅として記録されている。",
        )

        results = self.retriever().search("二月駅とは")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source_kind"], "japanese_wiki")
        self.assertEqual(results[0]["title"], "二月駅")
        self.assertEqual(results[0]["url"], "https://backrooms.fandom.com/ja/wiki/二月駅")
        self.assertIn("地下に存在する駅", results[0]["excerpt"])
        self.assertTrue(results[0]["retrieved_at"])

    def test_level_zero_title_is_ranked_before_body_only_match(self):
        self.add_page(
            "other.html",
            "記録資料",
            "https://backrooms.fandom.com/ja/wiki/記録資料",
            "JP",
            "Level 0 についての短い言及がある。",
        )
        self.add_page(
            "level-zero.html",
            "Level 0",
            "https://backrooms.fandom.com/ja/wiki/Level_0",
            "JP",
            "Level 0は黄色い壁紙の廊下と部屋が続く空間として記載されている。",
        )

        results = self.retriever().search("Level 0とは")

        self.assertEqual(results[0]["title"], "Level 0")

    def test_unrelated_level_page_is_not_evidence_for_a_fictional_level(self):
        self.add_page(
            "level-zero.html",
            "Level 0",
            "https://backrooms.fandom.com/ja/wiki/Level_0",
            "JP",
            "Level 0は黄色い廊下と部屋が続く空間として記載されている。",
        )

        results = self.retriever().search("架空のLevel 999とは")

        self.assertEqual(results, [])

    def test_non_japanese_archive_is_not_used_when_japanese_match_exists(self):
        self.add_page(
            "jp.html",
            "Level 0",
            "https://backrooms.fandom.com/ja/wiki/Level_0",
            "JP",
            "日本語版では黄色い廊下と記載されている。",
        )
        self.add_page(
            "en.html",
            "Level 0",
            "https://backrooms.fandom.com/wiki/Level_0",
            "EN",
            "The English page describes a different detail.",
        )

        results = self.retriever().search("Level 0")

        self.assertEqual([source["source_kind"] for source in results], ["japanese_wiki"])

    def test_explicit_comparison_returns_distinct_language_sources(self):
        self.add_page(
            "jp.html",
            "Level 0",
            "https://backrooms.fandom.com/ja/wiki/Level_0",
            "JP",
            "日本語版では黄色い廊下と記載されている。",
        )
        self.add_page(
            "en.html",
            "Level 0",
            "https://backrooms.fandom.com/wiki/Level_0",
            "EN",
            "The English page describes a different detail.",
        )

        results = self.retriever().search("Level 0の日本語版と英語版の違い")

        self.assertEqual(
            {source["source_kind"] for source in results},
            {"japanese_wiki", "external"},
        )

    def test_duplicate_local_text_and_html_collapse_to_one_cited_page(self):
        self.add_page(
            "level-zero.html",
            "Level 0",
            "https://backrooms.fandom.com/ja/wiki/Level_0",
            "JP",
            "Level 0は黄色い廊下が続く。",
        )
        (self.text_dir / "Level_0.txt").write_text(
            "Level 0は黄色い廊下が続く。部屋には扉がある。",
            encoding="utf-8",
        )
        retriever = WikiRetriever(
            self.text_dir,
            html_dir=self.html_dir,
            master_pages=[
                {
                    "title": "Level 0",
                    "url": "https://backrooms.fandom.com/ja/wiki/Level_0",
                    "language": "JP",
                }
            ],
        )

        results = retriever.search("Level 0")

        self.assertEqual(len(results), 1)
        self.assertIn("部屋には扉", results[0]["excerpt"])

    def test_online_search_uses_fetched_page_body_and_api_canonical_url(self):
        requests = []

        class FakeResponse:
            def __init__(self, data):
                self.data = data

            def raise_for_status(self):
                return None

            def json(self):
                return self.data

        class FakeSession:
            async def get(self, endpoint, params, **kwargs):
                requests.append((endpoint, params))
                if params.get("list") == "search":
                    return FakeResponse(
                        {
                            "query": {
                                "search": [
                                    {
                                        "pageid": 20,
                                        "title": "Level 20 η",
                                        "snippet": "検索スニペットは根拠にしない。",
                                    }
                                ]
                            }
                        }
                    )
                if params.get("prop") == "info":
                    return FakeResponse(
                        {
                            "query": {
                                "pages": [
                                    {
                                        "pageid": 20,
                                        "fullurl": "https://backrooms.fandom.com/ja/wiki/Level_20_%CE%B7",
                                        "touched": "2025-01-02T03:04:05Z",
                                    }
                                ]
                            }
                        }
                    )
                return FakeResponse(
                    {
                        "parse": {
                            "title": "Level 20 η",
                            "text": (
                                '<div class="mw-parser-output">'
                                "<p>二月駅はLevel 20 ηの別名として記録されている。</p>"
                                "</div>"
                            ),
                        }
                    }
                )

        class FakeAsyncSession:
            async def __aenter__(self):
                return FakeSession()

            async def __aexit__(self, *args):
                return None

        retriever = self.retriever()
        retriever.online_search = True
        with patch("modules.ai.wiki_retriever.AsyncSession", FakeAsyncSession):
            results = asyncio.run(retriever._online_search("二月駅とは", "ja"))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Level 20 η")
        self.assertEqual(
            results[0]["url"],
            "https://backrooms.fandom.com/ja/wiki/Level_20_%CE%B7",
        )
        self.assertIn("二月駅はLevel 20 η", results[0]["excerpt"])
        self.assertNotIn("検索スニペット", results[0]["excerpt"])
        self.assertEqual(requests[0][1]["srsearch"], "二月駅")
        self.assertEqual(results[0]["status"], "retrieved_live")
        self.assertEqual(results[0]["retrieval_time_basis"], "live_api_retrieval_time")

    def test_online_fallback_searches_english_only_after_japanese_no_results(self):
        retriever = self.retriever()
        retriever.online_search = True
        calls = []

        async def fake_online_search(query, language):
            calls.append(language)
            if language == "ja":
                return []
            return [
                {
                    "title": "Level 999",
                    "url": "https://backrooms.fandom.com/wiki/Level_999",
                    "language": "EN",
                    "source_kind": "external",
                    "excerpt": "Verified English article body.",
                    "status": "retrieved_live",
                }
            ]

        with patch.object(retriever, "_online_search", side_effect=fake_online_search):
            results = asyncio.run(retriever.search_with_fallback("Level 999とは"))

        self.assertEqual(calls, ["ja", "en"])
        self.assertEqual(results[0]["source_kind"], "external")
        self.assertEqual(retriever.last_search_status["japanese_online_status"], "no_results")
        self.assertEqual(retriever.last_search_status["external_online_status"], "found")

    def test_verified_japanese_online_page_prevents_english_fallback(self):
        retriever = self.retriever()
        retriever.online_search = True
        calls = []

        async def fake_online_search(query, language):
            calls.append(language)
            return [
                {
                    "id": "S1",
                    "title": "二月駅",
                    "url": "https://backrooms.fandom.com/ja/wiki/Level_20_%CE%B7",
                    "language": "JP",
                    "source_kind": "japanese_wiki",
                    "excerpt": "二月駅について確認できる本文。",
                    "status": "retrieved_live",
                }
            ]

        with patch.object(retriever, "_online_search", side_effect=fake_online_search):
            results = asyncio.run(retriever.search_with_fallback("二月駅とは"))

        self.assertEqual(calls, ["ja"])
        self.assertEqual(results[0]["source_kind"], "japanese_wiki")
        self.assertEqual(results[0]["id"], "S1")
        self.assertEqual(retriever.last_search_status["status"], "found")

    def test_explicit_comparison_fetches_both_japanese_and_english_pages(self):
        retriever = self.retriever()
        retriever.online_search = True
        calls = []

        async def fake_online_search(query, language):
            calls.append(language)
            return [
                {
                    "id": "S1",
                    "title": "Level 0",
                    "url": (
                        "https://backrooms.fandom.com/ja/wiki/Level_0"
                        if language == "ja"
                        else "https://backrooms.fandom.com/wiki/Level_0"
                    ),
                    "language": "JP" if language == "ja" else "EN",
                    "source_kind": "japanese_wiki" if language == "ja" else "external",
                    "excerpt": "Verified article body.",
                    "status": "retrieved_live",
                }
            ]

        with patch.object(retriever, "_online_search", side_effect=fake_online_search):
            results = asyncio.run(
                retriever.search_with_fallback(
                    "Level 0の日本語版と英語版の違い"
                )
            )

        self.assertEqual(calls, ["ja", "en"])
        self.assertEqual(
            {source["source_kind"] for source in results},
            {"japanese_wiki", "external"},
        )
        self.assertEqual(retriever.last_search_status["external_online_status"], "found")

    def test_online_search_failure_is_not_reported_as_page_absence(self):
        retriever = self.retriever()
        retriever.online_search = True
        calls = []

        async def fake_online_search(query, language):
            calls.append(language)
            raise RuntimeError("connection timed out")

        with patch.object(retriever, "_online_search", side_effect=fake_online_search):
            results = asyncio.run(retriever.search_with_fallback("二月駅とは"))

        response = retriever.no_source_response("二月駅とは", retriever.last_search_status)
        self.assertEqual(results, [])
        self.assertEqual(calls, ["ja"])
        self.assertEqual(retriever.last_search_status["status"], "japanese_online_search_failed")
        self.assertIn("検索にも失敗", response)
        self.assertIn("connection timed out", response)
        self.assertNotIn("ページを確認できませんでした。", response)


if __name__ == "__main__":
    unittest.main()
