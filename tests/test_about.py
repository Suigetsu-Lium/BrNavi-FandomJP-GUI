import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gui_app
from modules.version import VERSION


class AboutDialogApiTests(unittest.TestCase):
    def setUp(self):
        self.api = gui_app.BackroomsAPI.__new__(gui_app.BackroomsAPI)
        self.api.ai = None

    def test_about_info_reads_model_setting_without_exposing_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "database" / "ai"
            data_dir.mkdir(parents=True)
            (data_dir / "settings.json").write_text(
                json.dumps({"model": "custom/model-id", "api_key": "must-not-leak"}),
                encoding="utf-8",
            )
            with patch.object(gui_app, "BASE_DIR", Path(directory)):
                info = self.api.get_about_info()

        self.assertEqual(info["name"], "BrNavi Fandom JP")
        self.assertEqual(info["version"], VERSION)
        self.assertEqual(info["application_type"], "GUI")
        self.assertEqual(info["provider"], "Groq")
        self.assertEqual(info["model"], "custom/model-id")
        self.assertNotIn("api_key", info)
        self.assertNotIn("GROQ_API_KEY", info)

    @patch("gui_app.webbrowser.open", return_value=True)
    def test_about_repository_link_opens_in_default_browser(self, open_browser):
        result = self.api.open_about_link("repository")

        self.assertEqual(result, {"opened": True})
        open_browser.assert_called_once_with(
            "https://github.com/Suigetsu-Lium/BrNavi-FandomJP-GUI",
            new=2,
        )

    @patch("gui_app.webbrowser.open")
    def test_about_api_rejects_unlisted_urls(self, open_browser):
        result = self.api.open_about_link("https://example.com")

        self.assertIn("error", result)
        open_browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
