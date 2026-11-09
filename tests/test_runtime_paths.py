import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.runtime_paths import get_application_dir


class RuntimePathTests(unittest.TestCase):
    def test_source_application_dir_is_repository_root(self):
        self.assertEqual(
            get_application_dir(),
            Path(__file__).resolve().parent.parent,
        )

    def test_frozen_application_dir_is_executable_directory(self):
        executable = Path("C:/portable/BrNavi/BrNavi-GUI.exe")
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", str(executable)),
        ):
            self.assertEqual(
                get_application_dir(),
                executable.resolve().parent,
            )


if __name__ == "__main__":
    unittest.main()
