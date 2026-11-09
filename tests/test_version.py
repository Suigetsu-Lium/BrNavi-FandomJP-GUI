import unittest
import contextlib
import io

from BackroomsNavigationSystem_FandomJP import show_menu
from gui_app import HTML_TEMPLATE
from modules.version import VERSION


class VersionDisplayTests(unittest.TestCase):
    def test_gui_template_exposes_the_shared_application_version(self):
        rendered_template = HTML_TEMPLATE.replace(
            "__VERSION__",
            VERSION,
        )
        self.assertIn(f">v{VERSION}</span>", rendered_template)
        self.assertNotIn("__VERSION__", rendered_template)

    def test_cli_menu_displays_the_shared_version_and_cli_type(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            show_menu()

        self.assertIn(f"v{VERSION} [CLI]", output.getvalue())


if __name__ == "__main__":
    unittest.main()
