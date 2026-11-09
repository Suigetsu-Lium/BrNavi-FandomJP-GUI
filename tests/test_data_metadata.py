import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.extractor import extract_danger, extract_understanding
from modules.ai.wiki_retriever import WikiRetriever
from modules.webview_fallback import EmbeddedBrowserFallback
from modules import network


class DataMetadataTests(unittest.TestCase):
    def test_danger_values_keep_special_suffix_and_normalize_numeric_value(self):
        cases = [
            ("危険度: 6 / 5 — 致命的な環境", "6/5 — 致命的な環境", "6/5"),
            ("危険度: 可変（夜間は3/5）", "可変（夜間は3/5）", "3/5"),
            ("危険度: 条件付き（MBTIにより変動）", "条件付き（MBTIにより変動）", "条件付き"),
            ("[危険度]\nN/A（検討中）", "N/A（検討中）", "N/A"),
        ]
        for source, display, search in cases:
            with self.subTest(source=source):
                self.assertEqual(extract_danger(source), (display, search))

    def test_comprehension_supports_percent_and_unknown_values(self):
        self.assertEqual(extract_understanding("理解度: 75% — 部分理解"), ("75% — 部分理解", "75"))
        self.assertEqual(extract_understanding("[理解度]\nN/A（調査中）"), ("N/A（調査中）", ""))

    def test_network_metadata_loader_indexes_level_code_and_title(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.csv"
            summary.write_text(
                "階層コード,メタタイトル,危険度,危険度検索値,理解度,理解度検索値\n"
                "Level 20 η,二月駅,条件付き（個人差）,条件付き,40%,40\n",
                encoding="utf-8-sig",
            )
            with patch.object(network, "SUMMARY_CSV", summary):
                data = network._load_extracted_metadata()
            self.assertEqual(data[network._metadata_key("Level 20 η")]["Danger"], "条件付き（個人差）")
            self.assertEqual(data[network._metadata_key("二月駅")]["Comprehension"], "40%")

    def test_missing_level_detection_uses_local_article_availability(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text_dir = root / "extracted_fandom_levels"
            text_dir.mkdir()
            retriever = WikiRetriever(
                text_dir,
                master_pages=[{"title": "Level 200 η", "url": "https://backrooms.fandom.com/ja/wiki/Level_200_%CE%B7", "language": "JP"}],
                online_search=False,
            )
            self.assertEqual(
                retriever.find_missing_local_level("Level 200 ηの移動条件を教えて"),
                {"level_id": "Level 200 η", "query": "Level 200 η"},
            )
            (text_dir / "Level_200_η.txt").write_text("本文: 出入口を含む資料", encoding="utf-8")
            retriever._indexed_signature = None
            self.assertIsNone(retriever.find_missing_local_level("Level 200 ηの移動条件を教えて"))


class WebViewFallbackTests(unittest.TestCase):
    def test_fetch_reads_dom_from_the_supported_public_wiki(self):
        class LoadedEvent:
            def __iadd__(self, callback):
                self.callback = callback
                return self

        class Window:
            def __init__(self):
                self.events = type("Events", (), {"loaded": LoadedEvent()})()
                self.url = ""

            def get_current_url(self):
                return self.url

            def load_url(self, url):
                self.url = url
                self.events.loaded.callback(self)

            def evaluate_js(self, _script):
                return {"title": "Level 0", "url": self.url, "html": "<p>本文</p>", "text": "本文"}

        fallback = EmbeddedBrowserFallback(Window())
        page = fallback.fetch("https://backrooms.fandom.com/ja/wiki/Level_0")
        self.assertEqual(page["title"], "Level 0")
        self.assertEqual(page["html"], "<p>本文</p>")
        with self.assertRaises(ValueError):
            fallback.fetch("https://example.com/")


if __name__ == "__main__":
    unittest.main()
