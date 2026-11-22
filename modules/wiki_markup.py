"""Presentation-only formatting for lightweight MediaWiki-style link markup."""

import re
from urllib.parse import unquote, urlsplit


_WIKI_LINK_RE = re.compile(r"\[\[([^\[\]\r\n]*?)\]\]")


def _link_target_label(target):
    """Return a readable article title for a target without an explicit label."""
    value = target.strip()
    if not value:
        return ""

    if value.startswith(("http://", "https://", "/")):
        try:
            parsed = urlsplit(value)
            path = unquote(parsed.path)
            marker = "/wiki/"
            if marker in path:
                title = path.split(marker, 1)[1]
            else:
                title = path.rstrip("/").rsplit("/", 1)[-1]
            title = title.replace("_", " ").strip()
            if title:
                return title
        except ValueError:
            pass

    return value.replace("_", " ")


def format_wiki_links(text):
    """Replace Wiki link markup with readable plain text.

    This function never mutates its input or stored source data. Malformed
    markup is left untouched, and HTML escaping remains the caller's job.
    """
    if text is None:
        return ""
    source = str(text)

    def replace(match):
        markup = match.group(1)
        target, separator, label = markup.partition("|")
        target = target.strip()
        if separator:
            visible = label.strip()
            return visible or _link_target_label(target) or match.group(0)
        return _link_target_label(target) or match.group(0)

    return _WIKI_LINK_RE.sub(replace, source)
