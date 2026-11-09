import unittest

from gui_app import HTML_TEMPLATE
from modules.version import APP_VERSION


class VersionDisplayTests(unittest.TestCase):
    def test_gui_template_exposes_the_shared_application_version(self):
        rendered_template = HTML_TEMPLATE.replace(
            "__APP_VERSION__",
            APP_VERSION,
        )
        self.assertIn(f">v{APP_VERSION}</span>", rendered_template)
        self.assertNotIn("__APP_VERSION__", rendered_template)


if __name__ == "__main__":
    unittest.main()
