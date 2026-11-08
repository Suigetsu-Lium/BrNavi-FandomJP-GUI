import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules import online_wiki


class FakeResponse:
    def __init__(self, payload=None, content=b"", content_type="image/png"):
        self.payload = payload
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class OnlineWikiTests(unittest.TestCase):
    @patch("modules.online_wiki._api_request")
    def test_search_uses_fandom_api_and_returns_categories_and_real_url(self, api):
        api.side_effect = [
            {
                "query": {
                    "search": [
                        {
                            "pageid": 20,
                            "title": "Level 0",
                            "snippet": "<span>Search preview</span>",
                        }
                    ]
                }
            },
            {
                "query": {
                    "pages": [
                        {
                            "pageid": 20,
                            "title": "Level 0",
                            "fullurl": "https://backrooms.fandom.com/ja/wiki/Level_0",
                            "categories": [{"title": "カテゴリ:通常階層"}],
                        }
                    ]
                }
            },
        ]

        pages = online_wiki.search_pages("Level 0")

        self.assertEqual(pages[0]["url"], "https://backrooms.fandom.com/ja/wiki/Level_0")
        self.assertEqual(pages[0]["categories"], ["通常階層"])
        self.assertEqual(pages[0]["snippet"], "Search preview")
        self.assertEqual(api.call_args_list[0].args[0]["srsearch"], "Level 0")

    @patch("modules.online_wiki._api_request")
    def test_search_drops_results_without_canonical_japanese_wiki_url(self, api):
        api.side_effect = [
            {"query": {"search": [{"pageid": 9, "title": "Other site"}]}},
            {
                "query": {
                    "pages": [
                        {
                            "pageid": 9,
                            "fullurl": "https://example.test/ja/wiki/Other_site",
                            "categories": [],
                        }
                    ]
                }
            },
        ]

        self.assertEqual(online_wiki.search_pages("Other site"), [])

    @patch("modules.online_wiki._api_request")
    def test_fetch_page_sanitizes_body_and_resolves_live_images(self, api):
        api.return_value = {
            "parse": {
                "title": "Level 0",
                "displaytitle": "Level 0",
                "text": (
                    '<div class="mw-parser-output"><h2>概要</h2>'
                    '<p>本文 <script>alert(1)</script></p>'
                    '<img src="//static.wikia.nocookie.net/image.png">'
                    '<a href="/ja/wiki/Level_1">Level 1</a></div>'
                ),
            }
        }

        page = online_wiki.fetch_page(
            "https://backrooms.fandom.com/ja/wiki/Level_0"
        )

        self.assertIn("本文", page["html"])
        self.assertNotIn("<script", page["html"])
        self.assertIn("https://static.wikia.nocookie.net/image.png", page["html"])
        self.assertIn("https://backrooms.fandom.com/ja/wiki/Level_1", page["html"])

    def test_fetch_page_rejects_non_japanese_fandom_or_external_url(self):
        for url in (
            "https://example.test/ja/wiki/Level_0",
            "https://backrooms.fandom.com/wiki/Level_0",
            "http://backrooms.fandom.com/ja/wiki/Level_0",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    online_wiki.fetch_page(url)

    @patch("modules.online_wiki.requests.get")
    def test_localize_images_saves_allowed_image_and_rewrites_html(self, get_image):
        with tempfile.TemporaryDirectory() as directory:
            html_path = Path(directory) / "Level_0.html"
            html_path.write_text(
                '<html><head><base href="https://backrooms.fandom.com/ja/wiki/"></head>'
                '<body><img src="https://static.wikia.nocookie.net/image.png">'
                '<img src="https://evil.example/unsafe.png"></body></html>',
                encoding="utf-8",
            )
            get_image.return_value = FakeResponse(content=b"png-data")

            result = online_wiki._localize_images(
                html_path.read_text(encoding="utf-8"),
                html_path,
            )

            self.assertEqual(result, {"saved": 1, "failed": 0})
            saved_html = html_path.read_text(encoding="utf-8")
            self.assertNotIn("<base", saved_html)
            self.assertIn("Level_0_assets/image_01.png", saved_html)
            self.assertIn("https://evil.example/unsafe.png", saved_html)
            self.assertEqual(
                (Path(directory) / "Level_0_assets" / "image_01.png").read_bytes(),
                b"png-data",
            )
            self.assertEqual(get_image.call_count, 1)

    @patch("modules.online_wiki.download_url")
    @patch("modules.online_wiki._localize_images")
    def test_save_page_archives_only_supported_wiki_urls(self, localize, download):
        with tempfile.TemporaryDirectory() as directory:
            html_path = Path(directory) / "Level_0.html"
            html_path.write_text("<html></html>", encoding="utf-8")
            download.return_value = html_path
            localize.return_value = {"saved": 2, "failed": 0}

            result = online_wiki.save_page(
                {
                    "title": "Level 0",
                    "url": "https://backrooms.fandom.com/ja/wiki/Level_0",
                    "categories": ["通常階層"],
                }
            )

            self.assertEqual(result["images_saved"], 2)
            self.assertEqual(download.call_args.kwargs["page_info"]["language"], "JP")
            self.assertEqual(download.call_args.kwargs["delay"], 0)
            with self.assertRaises(ValueError):
                online_wiki.save_page(
                    {"title": "Bad", "url": "https://example.test/article"}
                )


if __name__ == "__main__":
    unittest.main()
