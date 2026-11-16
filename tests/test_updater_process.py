from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from updater.main import _is_app_running


class ProcessDetectionTests(unittest.TestCase):
    @patch("updater.main.sys.platform", "win32")
    @patch("updater.main.subprocess.run")
    def test_detects_matching_installation_process(self, run):
        run.return_value = subprocess.CompletedProcess([], 10, "", "")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(_is_app_running(Path(tmp)))
        self.assertIn("powershell.exe", run.call_args.args[0][0])
        self.assertTrue(run.call_args.kwargs["env"]["BRNAVI_TARGET"].endswith("BrNavi-GUI.exe"))

    @patch("updater.main.sys.platform", "win32")
    @patch("updater.main.subprocess.run")
    def test_fails_closed_when_process_status_cannot_be_checked(self, run):
        run.side_effect = subprocess.TimeoutExpired("powershell.exe", 10)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                _is_app_running(Path(tmp))

    @patch("updater.main.sys.platform", "win32")
    @patch("updater.main.subprocess.run")
    def test_allows_update_when_process_query_succeeds_without_match(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_is_app_running(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
