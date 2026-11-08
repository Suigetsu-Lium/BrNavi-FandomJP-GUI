from __future__ import annotations
import os
from groq import Groq


class GroqClient:
    def __init__(self, model=None, max_completion_tokens=4096, timeout=120.0):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY が設定されていません。Windowsの環境変数を確認してください。")
        self.model = model or os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        self.max_completion_tokens = int(max_completion_tokens)
        self.client = Groq(api_key=api_key, timeout=timeout, max_retries=2)

    def chat(self, messages):
        try:
            result = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_completion_tokens=self.max_completion_tokens,
            )
        except Exception as exc:
            message = str(exc)
            lower = message.lower()
            if "rate_limit" in lower or "rate limit" in lower or "429" in lower:
                raise RuntimeError("Groq APIのレート制限に達しました。少し待ってから再試行してください。") from exc
            if "context_length" in lower or "too many tokens" in lower or "maximum context" in lower:
                raise RuntimeError("入力と会話履歴がモデルのコンテキスト上限を超えました。履歴を短くして再試行してください。") from exc
            if "authentication" in lower or "401" in lower:
                raise RuntimeError("Groq APIキーが無効です。GROQ_API_KEYを確認してください。") from exc
            raise RuntimeError(f"Groq APIエラー: {message}") from exc
        choice = result.choices[0]
        content = choice.message.content or ""
        if not content.strip():
            raise RuntimeError("Groq APIから空の応答が返されました。")
        return {"content": content.strip(), "finish_reason": choice.finish_reason,
                "usage": getattr(result, "usage", None)}
