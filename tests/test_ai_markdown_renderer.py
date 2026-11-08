import unittest

from modules.ai.markdown_renderer import render_markdown


class MarkdownRendererTests(unittest.TestCase):
    def test_renders_common_markdown_and_scrollable_tables(self):
        rendered = render_markdown(
            "# Heading\n\n- one\n- two\n\n> quoted\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
            "[safe](https://example.com)"
        )
        self.assertIn("<h1>Heading</h1>", rendered)
        self.assertIn("<ul>", rendered)
        self.assertIn("<blockquote>", rendered)
        self.assertIn('<div class="ai-table-scroll">', rendered)
        self.assertIn("<table>", rendered)
        self.assertIn('href="https://example.com"', rendered)
        self.assertIn('rel="noopener noreferrer"', rendered)

    def test_highlights_fenced_code(self):
        rendered = render_markdown("```python\nprint('hello')\n```")
        self.assertIn("codehilite", rendered)
        self.assertIn("<pre>", rendered)
        self.assertIn("print", rendered)

    def test_removes_active_html_and_unsafe_links(self):
        rendered = render_markdown(
            '<script>alert(1)</script>\n'
            '<img src=x onerror="alert(1)">\n'
            '<a href="javascript:alert(1)">unsafe</a>'
        )
        self.assertNotIn("<script", rendered.lower())
        self.assertNotIn("<img", rendered.lower())
        self.assertNotIn("onerror", rendered.lower())
        self.assertNotIn("javascript:", rendered.lower())


if __name__ == "__main__":
    unittest.main()
