from __future__ import annotations

import bleach
import markdown
import re
from pygments.formatters import HtmlFormatter


ALLOWED_TAGS = set(bleach.sanitizer.ALLOWED_TAGS) | {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "br",
    "pre", "code", "blockquote", "ul", "ol", "li", "table",
    "thead", "tbody", "tr", "th", "td", "div", "span", "input",
}

PYGMENTS_CLASSES = set(
    re.findall(r"\.([A-Za-z0-9_-]+)", HtmlFormatter().get_style_defs())
)


def _allowed_class(tag, name, value):
    if name != "class":
        return False
    classes = value.split()
    if tag == "div":
        return classes == ["codehilite"] or classes == ["ai-table-scroll"]
    return all(
        item.startswith("language-")
        or item.startswith("highlight")
        or item in {"codehilite", "task-list-item", "contains-task-list"}
        or item in PYGMENTS_CLASSES
        for item in classes
    )


def render_markdown(source):
    rendered = markdown.markdown(
        str(source or ""),
        extensions=["fenced_code", "tables", "codehilite", "sane_lists"],
        extension_configs={"codehilite": {"guess_lang": False, "noclasses": False}},
        output_format="html",
    )
    rendered = rendered.replace("<table>", '<div class="ai-table-scroll"><table>')
    rendered = rendered.replace("</table>", "</table></div>")
    sanitized = bleach.clean(
        rendered,
        tags=ALLOWED_TAGS,
        attributes=lambda tag, name, value: (
            name in {"href", "title", "rel", "target"} and tag == "a"
        ) or _allowed_class(tag, name, value),
        protocols={"http", "https", "mailto"},
        strip=True,
    )
    return re.sub(
        r"<a(?=\s)",
        '<a target="_blank" rel="noopener noreferrer"',
        sanitized,
    )


def syntax_highlight_css():
    return HtmlFormatter().get_style_defs(".codehilite")
