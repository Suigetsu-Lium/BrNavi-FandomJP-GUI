from __future__ import annotations

import json
import os
from pathlib import Path

from .conversation import ConversationStore
from .groq_client import GroqClient
from .prompt_manager import build_system_prompt, build_verification_prompt
from .wiki_retriever import WikiRetriever
from modules.route_service import build_route_context

DEFAULT_SETTINGS = {
    "model": "openai/gpt-oss-120b",
    "max_completion_tokens": 4096,
    "history_messages": 16,
    "max_context_chars": 26000,
}

NO_VERIFIED_SOURCE_RESPONSE = (
    "該当する資料が見つかりませんでした。具体的なLevel番号（例：Level 16 η）を指定して質問するか、"
    "『HTML取得・データ抽出』タブから対象ページのオフライン保存を行ってください。"
)


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

    async def _build_messages(self, cid, prompt, operation):
        sources = await self.retriever.search_with_fallback(prompt, top_k=4)
        context = self.retriever.format_context(sources)[:12000]
        route_context = build_route_context(prompt)
        if route_context:
            context = (context + "\n\n" + "## 経路・場所の補足データ\n" + route_context).strip()
            if route_context.startswith("ローカル経路グラフ"):
                sources.append({
                    "id": f"S{len(sources) + 1}",
                    "name": "ローカル経路CSV",
                    "title": "ローカル経路CSV",
                    "url": "backrooms_nodes.csv / backrooms_edges.csv (local)",
                    "language": "local",
                    "source_kind": "route_data",
                    "file": "backrooms_nodes.csv, backrooms_edges.csv",
                    "file_modified_at": None,
                    "retrieved_at": None,
                    "retrieval_time_basis": "local_route_csv",
                    "status": "retrieved_from_local_archive",
                    "excerpt": route_context,
                })
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

    @staticmethod
    def _format_source_references(sources):
        lines = ["## 情報源・根拠"]
        for source in sources:
            if source["source_kind"] == "japanese_wiki":
                kind = "日本語Wiki"
            elif source["source_kind"] == "route_data":
                kind = "ローカル経路データ"
            elif source["source_kind"] == "web":
                kind = "一般Web（オンライン取得）"
            elif source.get("status") == "retrieved_live":
                kind = "英語版Wiki（オンライン取得）"
            else:
                kind = "外部情報（ローカル保存の非日本語資料。オンラインで未確認）"
            title = source["title"].replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")
            url = source["url"]
            url_label = f"[{url}]({url})" if source.get("source_kind") != "route_data" else url
            excerpt = " ".join(source.get("excerpt", "").split())
            if len(excerpt) > 360:
                excerpt = excerpt[:357].rstrip() + "..."
            retrieved_at = source.get("retrieved_at") or "不明"
            if source.get("retrieval_time_basis") == "local_route_csv":
                lines.append(
                    f"- [{kind}] {title} ({url_label}) - "
                    f"参照ファイル: {source.get('file') or url}"
                )
            elif source.get("retrieval_time_basis") == "live_api_retrieval_time":
                date_label = "取得日時"
            else:
                date_label = "ローカルファイル更新日時"
            if source.get("retrieval_time_basis") != "local_route_csv":
                lines.append(
                    f"- [{kind}] {title} ({url_label}) - "
                    f"{date_label}: {retrieved_at} UTC"
                )
            if source.get("page_updated_at"):
                lines.append(f"  - Wikiページ更新日時: {source['page_updated_at']} UTC")
            if excerpt:
                lines.append(f"  - 該当箇所: 「{excerpt}」")
        return "\n".join(lines)

    @staticmethod
    def _format_search_warnings(search_status):
        lines = []
        if search_status.get("japanese_online_status") == "no_results":
            lines.append(
                "- 日本語版Wikiのオンライン検索では検索結果がありませんでした。"
            )
        if search_status.get("external_online_status") == "failed":
            lines.append("- 英語版Wikiのオンライン検索に失敗しました。")
        elif search_status.get("external_online_status") == "no_results":
            lines.append("- 英語版Wikiのオンライン検索では該当する本文を確認できませんでした。")
        if search_status.get("web_status") == "failed":
            lines.append("- 一般Web検索に失敗しました。")
        for error in search_status.get("online_errors", []):
            source = error.get("source", "Wiki")
            title = error.get("title")
            detail = error.get("error", "詳細不明")
            context = f" ({title})" if title else ""
            lines.append(f"- {source}{context}: {detail}")
        return "\n".join(lines)

    async def _verify_answer(self, question, draft, sources):
        verification_messages = build_verification_prompt(question, draft, sources)
        verified = await self.llm.stream_chat(verification_messages)
        answer = verified["content"].strip()
        if not answer:
            raise RuntimeError("出典検証で有効な回答を生成できませんでした。")
        if verified["finish_reason"] == "length":
            continuation = await self.llm.stream_chat(
                verification_messages
                + [
                    {"role": "assistant", "content": answer},
                    {
                        "role": "user",
                        "content": "出力上限で途切れました。資料に根拠のある検証済み回答の続きを返してください。",
                    },
                ]
            )
            answer += continuation["content"]
        return answer

    async def generate_title(self, conversation_id, prompt):
        try:
            title = await self.llm.generate_title(prompt)
        except Exception:
            return False
        return self.store.generate_title(conversation_id, title)

    async def chat(self, generation, on_delta=None):
        cid = generation["conversation_id"]
        prompt = generation["prompt"]
        messages, sources = await self._build_messages(
            cid, prompt, generation["operation"]
        )
        search_status = self.retriever.last_search_status
        if not sources:
            answer = NO_VERIFIED_SOURCE_RESPONSE
            if on_delta:
                on_delta(answer)
            saved = self.store.finish_generation(
                generation["request_id"], answer, []
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
                "prompt": prompt,
                "sources": [],
                "search_status": search_status,
                "model": self.llm.model,
                "finish_reason": "no_verified_sources",
            }
        try:
            result = await self.llm.stream_chat(messages, on_delta=on_delta)
        except RuntimeError as exc:
            if "コンテキスト上限" not in str(exc):
                raise
            system = build_system_prompt(
                self.retriever.format_context(sources)[:5000]
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

        if sources:
            answer = await self._verify_answer(prompt, answer, sources)
            answer += "\n\n" + self._format_source_references(sources)
        warnings = self._format_search_warnings(search_status)
        if warnings:
            answer += "\n\n## 検索に関する注意\n" + warnings

        saved = self.store.finish_generation(
            generation["request_id"],
            answer,
            sources,
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
            "sources": sources,
            "model": self.llm.model,
            "finish_reason": finish_reason,
        }
