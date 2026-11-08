from __future__ import annotations

import os

from groq import AsyncGroq, Groq


class GroqClient:
    def __init__(self, model=None, max_completion_tokens=4096, timeout=120.0):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY が設定されていません。Windowsの環境変数を確認してください。")
        self.api_key = api_key
        self.model = model or os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        self.max_completion_tokens = int(max_completion_tokens)
        self.timeout = timeout
        self.client = Groq(api_key=api_key, timeout=timeout, max_retries=2)

    @staticmethod
    def _friendly_error(exc):
        message = str(exc)
        lower = message.lower()
        if "rate_limit" in lower or "rate limit" in lower or "429" in lower:
            return "Groq APIのレート制限に達しました。少し待ってから再試行してください。"
        if "context_length" in lower or "too many tokens" in lower or "maximum context" in lower:
            return "入力と会話履歴がモデルのコンテキスト上限を超えました。履歴を短くして再試行してください。"
        if "authentication" in lower or "401" in lower:
            return "Groq APIキーが無効です。GROQ_API_KEYを確認してください。"
        return f"Groq APIエラー: {message}"

    def chat(self, messages):
        try:
            result = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_completion_tokens=self.max_completion_tokens,
            )
        except Exception as exc:
            raise RuntimeError(self._friendly_error(exc)) from exc
        choice = result.choices[0]
        content = choice.message.content or ""
        if not content.strip():
            raise RuntimeError("Groq APIから空の応答が返されました。")
        return {
            "content": content.strip(),
            "finish_reason": choice.finish_reason,
            "usage": getattr(result, "usage", None),
        }

    async def stream_chat(self, messages, on_delta=None, *, max_completion_tokens=None):
        content = []
        finish_reason = None
        try:
            async with AsyncGroq(
                api_key=self.api_key,
                timeout=self.timeout,
                max_retries=0,
            ) as client:
                stream = await client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_completion_tokens=max_completion_tokens or self.max_completion_tokens,
                    stream=True,
                )
                async with stream:
                    async for chunk in stream:
                        if not chunk.choices:
                            continue
                        choice = chunk.choices[0]
                        delta = choice.delta.content
                        if delta:
                            content.append(delta)
                            if on_delta:
                                on_delta(delta)
                        if choice.finish_reason:
                            finish_reason = choice.finish_reason
        except Exception as exc:
            raise RuntimeError(self._friendly_error(exc)) from exc

        answer = "".join(content)
        if not answer.strip():
            raise RuntimeError("Groq APIから空の応答が返されました。")
        return {"content": answer.strip(), "finish_reason": finish_reason}

    async def generate_title(self, prompt):
        messages = [
            {
                "role": "system",
                "content": "ユーザーの質問に基づき、日本語の短い会話タイトルだけを出力してください。"
                "説明、引用符、Markdownは付けず、30文字以内にしてください。",
            },
            {"role": "user", "content": prompt[:1200]},
        ]
        result = await self.stream_chat(
            messages, max_completion_tokens=min(128, self.max_completion_tokens)
        )
        return " ".join(result["content"].split()).strip("「」『』\"'")[:80]
