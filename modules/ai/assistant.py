from __future__ import annotations
import json
from pathlib import Path
from .conversation import ConversationStore
from .groq_client import GroqClient
from .prompt_manager import build_system_prompt
from .wiki_retriever import WikiRetriever

DEFAULT_SETTINGS = {"model": "openai/gpt-oss-120b", "max_completion_tokens": 4096,
                    "history_messages": 16, "max_context_chars": 26000}


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
        except Exception:
            pass
        return settings

    def _save_settings(self):
        self.settings_path.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")

    def _make_client(self):
        return GroqClient(model=self.settings["model"], max_completion_tokens=self.settings["max_completion_tokens"])

    def get_settings(self):
        return {**self.settings, "provider": "Groq", "api_key_configured": bool(__import__("os").getenv("GROQ_API_KEY"))}

    def update_settings(self, values):
        model = str(values.get("model", self.settings["model"])).strip()
        if not model or len(model) > 120:
            raise ValueError("モデルIDが不正です。")
        try:
            output_tokens = int(values.get("max_completion_tokens", self.settings["max_completion_tokens"]))
            history = int(values.get("history_messages", self.settings["history_messages"]))
        except (TypeError, ValueError):
            raise ValueError("トークン数・履歴数は整数で指定してください。")
        if not 256 <= output_tokens <= 16384:
            raise ValueError("最大出力トークン数は256～16384で指定してください。")
        if not 2 <= history <= 40:
            raise ValueError("会話履歴数は2～40で指定してください。")
        self.settings.update({"model": model, "max_completion_tokens": output_tokens, "history_messages": history})
        self._save_settings()
        self.llm = self._make_client()
        return self.get_settings()

    def get_status(self):
        return {"available": True, "provider": "Groq", "model": self.llm.model,
                "local_wiki": self.retriever.text_dir.exists(), "max_completion_tokens": self.llm.max_completion_tokens}

    def new_conversation(self):
        return self.store.create_conversation()

    def get_history(self, conversation_id):
        return self.store.get_history(conversation_id)

    def list_conversations(self, limit=50):
        return self.store.list_conversations(limit)

    def _fit_history(self, history, current_message):
        # トークナイザー非依存の保守的な文字数予算。日本語は文字数≒トークン数ではないため余裕を持たせる。
        budget = int(self.settings["max_context_chars"])
        kept, used = [], len(current_message)
        for item in reversed(history):
            size = len(item["content"])
            if used + size > budget:
                break
            kept.append(item)
            used += size
        return list(reversed(kept))

    def chat(self, message, conversation_id=None, regenerate=False):
        message = (message or "").strip()
        cid = self.store.ensure_conversation(conversation_id)
        if regenerate:
            message = self.store.last_user_message(cid) or ""
            if not message:
                raise ValueError("再生成できるユーザーメッセージがありません。")
        if not message:
            raise ValueError("メッセージが空です。")
        if len(message) > 12000:
            raise ValueError("メッセージが長すぎます（最大12,000文字）。")
        sources = self.retriever.search(message, top_k=3)
        context = self.retriever.format_context(sources)
        # Wikiコンテキストも過大にならないよう制限
        context = context[:9000]
        history = self.store.get_messages(cid, limit=int(self.settings["history_messages"]))
        if regenerate:
            history = history[:-1] if history and history[-1]["role"] == "assistant" else history
        else:
            history = history[:-1] if history and history[-1]["role"] == "user" else history
        history = self._fit_history(history, message)
        messages = [{"role": "system", "content": build_system_prompt(context)}]
        messages.extend(history)
        messages.append({"role": "user", "content": message})
        if not regenerate:
            self.store.add_message(cid, "user", message)
        result = self.llm.chat(messages)
        answer = result["content"]
        # 出力上限で終了した場合、1回だけ続きを要求して結合する。
        if result.get("finish_reason") == "length":
            continuation = self.llm.chat(messages + [
                {"role": "assistant", "content": answer},
                {"role": "user", "content": "直前の回答が出力上限で途中終了しました。重複を避けて、途切れた箇所の続きだけを出力してください。"}
            ])
            answer += "\n" + continuation["content"]
        if regenerate:
            self.store.remove_last_assistant(cid)
        self.store.add_message(cid, "assistant", answer)
        return {"conversation_id": cid, "response": answer,
                "sources": [{"name": s["source"], "score": s["score"]} for s in sources],
                "model": self.llm.model, "finish_reason": result.get("finish_reason")}
