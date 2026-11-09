"""DOM retrieval fallback for public Fandom pages blocked by HTTP clients."""

import json
import threading
from urllib.parse import urlparse


class EmbeddedBrowserFallback:
    def __init__(self, window=None, timeout=30):
        self.window = None
        self.timeout = timeout
        self._condition = threading.Condition()
        self._expected = None
        self._loaded = False
        if window is not None:
            self.attach(window)

    def attach(self, window):
        self.window = window
        window.events.loaded += self._on_loaded

    def _on_loaded(self, *_args):
        window = self.window
        try:
            current_url = window.get_current_url() if window else ""
        except Exception:
            current_url = ""
        with self._condition:
            if self._expected:
                expected, current = urlparse(self._expected), urlparse(current_url or "")
                if current.hostname == expected.hostname and current.path == expected.path:
                    self._loaded = True
                    self._condition.notify_all()

    def fetch(self, url):
        parsed = urlparse(str(url or ""))
        if parsed.scheme != "https" or parsed.hostname != "backrooms.fandom.com":
            raise ValueError("ブラウザー取得は日本語版Backrooms Wikiに限定されています。")
        if self.window is None:
            raise RuntimeError("内蔵ブラウザーの取得フォールバックが初期化されていません。")
        with self._condition:
            self._expected = url
            self._loaded = False
        self.window.load_url(url)
        with self._condition:
            if not self._condition.wait_for(lambda: self._loaded, timeout=self.timeout):
                raise TimeoutError("内蔵ブラウザーでページを読み込めませんでした。")
        result = self.window.evaluate_js(
            "(() => { const root = document.querySelector('#mw-content-text') "
            "|| document.querySelector('.mw-parser-output') || document.body; "
            "return {title: document.title || '', url: location.href, "
            "html: root ? root.innerHTML : '', text: root ? root.innerText : ''}; })()"
        )
        if isinstance(result, str):
            result = json.loads(result)
        if not isinstance(result, dict) or not str(result.get("html") or "").strip():
            raise RuntimeError("内蔵ブラウザーから記事本文を取得できませんでした。")
        title_text = str(result.get("title") or "")
        page_text = str(result.get("text") or "").casefold()
        if (
            "access denied" in title_text.casefold()
            or "just a moment" in title_text.casefold()
            or "access denied" in page_text
            or "verify you are human" in page_text
            or "checking your browser" in page_text
        ):
            raise RuntimeError("Fandomのアクセス確認画面が表示され、記事を取得できませんでした。")
        result["url"] = str(result.get("url") or url)
        return result


_fallback = EmbeddedBrowserFallback()


def set_browser_window(window):
    _fallback.attach(window)


def fetch_public_page(url):
    return _fallback.fetch(url)
