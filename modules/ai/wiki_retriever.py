import re
from pathlib import Path
from collections import Counter

TOKEN_RE = re.compile(r"[A-Za-z0-9_#./-]+|[ぁ-んァ-ヶー一-龠]{2,}")

class WikiRetriever:
    def __init__(self, text_dir, max_files=5000):
        self.text_dir = Path(text_dir)
        self.max_files = max_files

    @staticmethod
    def _tokens(text):
        return [t.lower() for t in TOKEN_RE.findall(text) if len(t) >= 2]

    @staticmethod
    def _read_text(path):
        for encoding in ("utf-8", "utf-8-sig", "cp932"):
            try:
                return path.read_text(encoding=encoding, errors="strict")
            except (UnicodeDecodeError, OSError):
                pass
        return ""

    def search(self, query, top_k=4):
        if not self.text_dir.exists() or not query.strip():
            return []
        tokens = self._tokens(query)
        if not tokens:
            return []
        counts = Counter(tokens)
        scored = []
        try:
            paths = list(self.text_dir.glob("*.txt"))[:self.max_files]
        except OSError:
            return []
        for path in paths:
            body = self._read_text(path)
            if not body:
                continue
            haystack = (path.stem + "\n" + body).lower()
            score = sum(haystack.count(token) * weight for token, weight in counts.items())
            if score <= 0:
                continue
            lower = body.lower()
            positions = [lower.find(t) for t in tokens if lower.find(t) >= 0]
            start = max(0, min(positions) - 450) if positions else 0
            scored.append((score, path, body[start:start + 1800].strip()))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"source": p.name, "score": str(score), "excerpt": excerpt}
                for score, p, excerpt in scored[:top_k]]

    @staticmethod
    def format_context(results):
        return "\n\n".join(
            f"### 資料 {i}: {item['source']}\n{item['excerpt']}"
            for i, item in enumerate(results, 1)
        )
