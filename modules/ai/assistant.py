from __future__ import annotations

import json
import os
from pathlib import Path

from .conversation import ConversationStore
from .groq_client import GroqClient
from .prompt_manager import build_system_prompt
from .wiki_retriever import WikiRetriever

DEFAULT_SETTINGS = {
    "model": "openai/gpt-oss-120b",
    "max_completion_tokens": 4096,
    "history_messages": 16,
    "max_context_chars": 26000,
}


class AIAssistant:
    def __init__(self, text_dir, data_dir, model=None):
        self.store = ConversationStore(data_dir)
        self.retriever = WikiRetriever(text_dir)
        self.data_dir = Path(data_dir)
        self.settings_path = self.data_dir / "settings.json"
        self.settings = self._load_settings()
        if model:
            self.settings["model"] = model
        self.llm = self._make_client()

    def _load_settings(self):
        settings = dict(DEFAULT_SETTINGS)
        try:
            if self.settings_path.exists():
                saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
                for key in settings:
                    if key in saved:
                        settings[key] = saved[key]
        except (OSError, ValueError, TypeError):
            pass
        return settings

    def _save_settings(self):
        self.settings_path.write_text(
            json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _make_client(self):
        return GroqClient(
            model=self.settings["model"],
            max_completion_tokens=self.settings["max_completion_tokens"],
        )

    def get_settings(self):
        return {
            **self.settings,
            "provider": "Groq",
            "api_key_configured": bool(os.getenv("GROQ_API_KEY")),
        }

    def update_settings(self, values):
        model = str(values.get("model", self.settings["model"])).strip()
        if not model or len(model) > 120:
            raise ValueError("モデルIDが不正です。")
        try:
            output_tokens = int(
                values.get("max_completion_tokens", self.settings["max_completion_tokens"])
            )
            history = int(values.get("history_messages", self.settings["history_messages"]))
        except (TypeError, ValueError):
            raise ValueError("トークン数・履歴数は整数で指定してください。")
        if not 256 <= output_tokens <= 16384:
            raise ValueError("最大出力トークン数は256～16384で指定してください。")
        if not 2 <= history <= 40:
            raise ValueError("会話履歴数は2～40で指定してください。")
        self.settings.update(
            {
                "model": model,
                "max_completion_tokens": output_tokens,
                "history_messages": history,
            }
        )
        self._save_settings()
        self.llm = self._make_client()
        return self.get_settings()

    def get_status(self):
        return {
            "available": True,
            "provider": "Groq",
            "model": self.llm.model,
            "local_wiki": self.retriever.text_dir.exists(),
            "max_completion_tokens": self.llm.max_completion_tokens,
        }

    def new_conversation(self):
        return self.store.create_conversation()

    def get_history(self, conversation_id):
        return self.store.get_history(conversation_id)

    def list_conversations(self, limit=50):
        return self.store.list_conversations(limit)

    def _build_messages(self, cid, prompt, operation):
        sources = self.retriever.search(prompt, top_k=3)
        context = self.retriever.format_context(sources)[:9000]
        system = build_system_prompt(context)
        history = self.store.get_messages(
            cid, limit=int(self.settings["history_messages"]) + 1
        )
        if history and history[-1]["role"] == "user":
            history = history[:-1]
        if operation == "regenerate" and history and history[-1]["role"] == "assistant":
            history = history[:-1]

        budget = max(
            1000,
            int(self.settings["max_context_chars"]) - len(system) - len(prompt),
        )
        while history and sum(len(item["content"]) for item in history) > budget:
            history.pop(0)
        messages = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": prompt})
        return messages, sources

    async def generate_title(self, conversation_id, prompt):
        try:
            title = await self.llm.generate_title(prompt)
        except Exception:
            return False
        return self.store.generate_title(conversation_id, title)

    async def chat(self, generation, on_delta=None):
        cid = generation["conversation_id"]
        prompt = generation["prompt"]
        messages, sources = self._build_messages(
            cid, prompt, generation["operation"]
        )
        try:
            result = await self.llm.stream_chat(messages, on_delta=on_delta)
        except RuntimeError as exc:
            if "コンテキスト上限" not in str(exc):
                raise
            sources = self.retriever.search(prompt, top_k=1)
            system = build_system_prompt(
                self.retriever.format_context(sources)[:3000]
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]
            result = await self.llm.stream_chat(messages, on_delta=on_delta)
        answer = result["content"]
        finish_reason = result["finish_reason"]

        if finish_reason == "length":
            continuation_messages = messages + [
                {"role": "assistant", "content": answer},
                {
                    "role": "user",
                    "content": "直前の回答が出力上限で途中終了しました。重複を避けて、途切れた箇所の続きだけを出力してください。",
                },
            ]
            continuation = await self.llm.stream_chat(
                continuation_messages, on_delta=on_delta
            )
            answer += continuation["content"]
            finish_reason = continuation["finish_reason"]

        saved = self.store.finish_generation(
            generation["request_id"],
            answer,
            [{"name": source["source"], "score": source["score"]} for source in sources],
        )
        if not saved:
            return {
                "cancelled": True,
                "conversation_id": cid,
                "request_id": generation["request_id"],
                "operation": generation["operation"],
            }

        return {
            "conversation_id": cid,
            "request_id": generation["request_id"],
            "operation": generation["operation"],
            "message_id": saved["message_id"],
            "version_number": saved["version_number"],
            "response": answer,
            "sources": [{"name": source["source"], "score": source["score"]} for source in sources],
            "model": self.llm.model,
            "finish_reason": finish_reason,
        }
