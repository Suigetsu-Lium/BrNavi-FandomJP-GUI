# -*- coding: utf-8 -*-
import asyncio
import ipaddress
import os
import json
import base64
import hashlib
import csv
import re
import sys
import threading
import unicodedata
import warnings
import webbrowser
from pathlib import Path
from urllib.parse import unquote, urlsplit

warnings.filterwarnings("ignore", category=UserWarning)

import webview

# modules ディレクトリから既存ロジックを読み込み
from modules.database import (
    get_html_filename_prefix,
    load_master_database,
    open_local_html,
    sanitize_filename,
)
from modules.extractor import extract_article_attribution, extract_danger, extract_understanding

# 保守・更新サービス
from modules.operation_context import OperationCancelled, OperationContext
from modules.app_settings import AppSettingsStore
from modules.operations import (
    get_level_list_languages,
    is_supported_level_list_url,
    load_level_list_pages,
    run_maintenance_operation,
)
from modules.online_wiki import fetch_page as fetch_online_wiki_page
from modules.online_wiki import is_supported_wiki_url
from modules.online_wiki import save_page as save_online_wiki_page
from modules.online_wiki import search_pages as search_online_wiki_pages
from modules.route_service import search_routes as search_local_routes
from modules.wiki_markup import format_wiki_links
from modules.runtime_paths import get_application_dir
from modules.data_cleanup import (
    CleanupSafetyError,
    create_cleanup_plan,
    delete_cleanup_plan,
)
from modules.system_dashboard import format_clock, find_local_images, get_network_kind, get_power_status, list_local_images, normalize_media_identifier, local_saved_level_list, storage_summary
from modules.version import VERSION
from modules.webview_fallback import set_browser_window

# AI Assistant（Groq / ローカルWiki検索）
AI_DEFAULT_SETTINGS = {"model": "openai/gpt-oss-120b"}
try:
    from modules.ai.assistant import AIAssistant, DEFAULT_SETTINGS as AI_DEFAULT_SETTINGS
    from modules.ai.markdown_renderer import render_markdown, syntax_highlight_css
    AI_AVAILABLE = True
    AI_IMPORT_ERROR = None
except Exception as e:
    AIAssistant = None
    render_markdown = None
    syntax_highlight_css = None
    AI_AVAILABLE = False
    AI_IMPORT_ERROR = e
    print(f"[AI] モジュールを読み込めません ({type(e).__name__}): {e}")

BASE_DIR = get_application_dir()
IMAGE_DIR = BASE_DIR / "downloaded_images"
TEXT_DIR = BASE_DIR / "extracted_fandom_levels"
HTML_DIR = BASE_DIR / "downloaded_fandom_html"
APP_SETTINGS = AppSettingsStore(BASE_DIR / "database" / "app_settings.json")

# 🌟 ご指定のカテゴリ優先順位（日本独自階層 η を最優先）
CATEGORY_PRIORITY = {
    "normal_eta": 1,     # 1. 通常階層 η
    "sublevel_eta": 2,   # 2. 通常亜階層 η
    "enigmatic_eta": 3,  # 3. 番外階層 η
    "enigmatic_sublevel_eta": 4,
    "normal": 5,         # 通常階層
    "sublevel": 6,       # 通常亜階層
    "enigmatic": 7,      # 番外階層
}


class BackroomsAPI:
    """PythonとWebViewを繋ぐAPIブリッジ"""

    _candidate_catalog_fallback_lock = threading.RLock()
    def __init__(self):
        self.current_info = None
        self.ai = None
        self.ai_error = None
        self._ai_jobs = {}
        self._ai_jobs_lock = threading.Lock()
        self._maintenance_lock = threading.Lock()
        self._activity_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._cleanup_plans = {}
        self._maintenance_cancel_event = threading.Event()
        self._maintenance_done_event = threading.Event()
        self._maintenance_done_event.set()
        self._current_online_wiki_url = ""
        self._candidate_catalog = None
        self._candidate_catalog_lock = threading.RLock()
        self._auto_update_lock = threading.Lock()
        self._auto_update_scheduler_started = False
        if AI_AVAILABLE:
            try:
                self.ai = AIAssistant(
                    text_dir=TEXT_DIR,
                    data_dir=BASE_DIR / "database" / "ai"
                )
            except Exception as e:
                self.ai_error = str(e)
                print(f"[AI] 初期化エラー: {e}")
        else:
            self.ai_error = (
                "AIモジュールを読み込めません "
                f"({type(AI_IMPORT_ERROR).__name__}): {AI_IMPORT_ERROR}"
                if AI_IMPORT_ERROR
                else "AIモジュールを読み込めません。"
            )

    def refresh_application_data(self):
        lock = getattr(self, "_candidate_catalog_lock", None)
        if lock is None:
            lock = self._candidate_catalog_fallback_lock
        with lock:
            self._candidate_catalog = None
        return {"status": "reloaded"}

    def get_environment_settings(self):
        settings = APP_SETTINGS.get()
        due, next_due = APP_SETTINGS.is_due()
        settings["update_due"] = due
        settings["next_catalog_update_at"] = next_due.isoformat(timespec="seconds") if next_due else None
        return settings

    def get_system_status(self):
        return {"network": get_network_kind(), "power": get_power_status()}

    def get_storage_summary(self):
        return storage_summary(BASE_DIR)

    def get_cleanup_preview(self, categories):
        if self._cleanup_lock.locked():
            return {"status": "busy", "error": "保存済みデータの削除中です。"}
        try:
            plan = create_cleanup_plan(BASE_DIR, categories)
        except (ValueError, CleanupSafetyError, OSError) as error:
            return {"status": "error", "error": str(error)}
        self._cleanup_plans = {plan["plan_id"]: plan}
        return {
            "status": "ready",
            "plan_id": plan["plan_id"],
            "categories": plan["category_summaries"],
            "items": [
                {
                    "category": item["category"],
                    "category_label": item["category_label"],
                    "path": item["path"],
                    "kind": item["kind"],
                    "root": bool(item.get("root", False)),
                    "bytes": item["bytes"],
                }
                for item in plan["items"]
            ],
            "total_files": plan["total_files"],
            "total_bytes": plan["total_bytes"],
            "confirmation_keyword": "DELETE",
        }

    def execute_cleanup_data(self, plan_id, confirmation):
        plan = self._cleanup_plans.get(str(plan_id or ""))
        if plan is None:
            return {"status": "error", "error": "削除計画がありません。対象を再計測してください。"}
        if not self._activity_lock.acquire(blocking=False):
            return {"status": "busy", "error": "別の保守・削除処理が開始されています。"}
        acquired_cleanup_lock = False
        try:
            if self._maintenance_lock.locked() or not self._maintenance_done_event.is_set():
                return {"status": "busy", "error": "ダウンロード・一覧更新・CSV生成等の保守処理中は削除できません。"}
            with self._ai_jobs_lock:
                if self._ai_jobs:
                    return {"status": "busy", "error": "AI応答生成中は削除できません。生成完了後に再試行してください。"}
            acquired_cleanup_lock = self._cleanup_lock.acquire(blocking=False)
            if not acquired_cleanup_lock:
                return {"status": "busy", "error": "別の削除処理が実行中です。"}
        finally:
            self._activity_lock.release()

        try:
            result = delete_cleanup_plan(BASE_DIR, plan, confirmation)
            if "ai" in plan["categories"] and self.ai:
                from modules.ai.conversation import ConversationStore
                self.ai.store = ConversationStore(self.ai.data_dir)
            if {"articles", "index"} & set(plan["categories"]):
                self._candidate_catalog = None
                if self.ai:
                    from modules.ai.wiki_retriever import WikiRetriever
                    self.ai.retriever = WikiRetriever(TEXT_DIR)
            return {
                "status": "deleted",
                "files": result["files"],
                "bytes": result["bytes"],
                "removed_files": result["removed_files"],
                "removed_directories": result["removed_directories"],
                "categories": result["categories"],
            }
        except (ValueError, CleanupSafetyError, OSError, RuntimeError) as error:
            return {"status": "error", "error": str(error)}
        finally:
            self._cleanup_plans.pop(str(plan_id), None)
            self._cleanup_lock.release()

    def get_local_image_gallery(self):
        result = []
        known_pages = [dict(page) for page in load_master_database().values()]
        known_titles = [page.get("title", "") for page in known_pages]
        for item in list_local_images(
            IMAGE_DIR,
            known_titles=known_titles,
            known_pages=known_pages,
        ):
            item.pop("path", None)
            result.append(item)
        return result

    def get_legacy_image_rename_preview(self):
        from modules.image_downloader import plan_legacy_image_renames
        plan = plan_legacy_image_renames()
        return {
            "count": len(plan["items"]),
            "skipped": int(plan["skipped"]),
            "items": [
                {"source": item["source"].name, "target": item["target"].name}
                for item in plan["items"]
            ],
        }

    def run_legacy_image_rename(self):
        return self.run_maintenance_task("rename_legacy_images")

    def get_local_saved_levels(self, candidates=None):
        if candidates is None:
            candidates = self.search_candidates("")
        return local_saved_level_list(candidates)

    def get_local_image_data(self, filename):
        name = Path(str(filename or "")).name
        path = IMAGE_DIR / name
        if not name or path.resolve().parent != IMAGE_DIR.resolve() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
            return {"error": "画像名が無効です。"}
        try:
            mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}.get(path.suffix.lower(), "application/octet-stream")
            return {"src": f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")}
        except OSError as error:
            return {"error": str(error)}

    def get_clock_text(self, timezone_name=None):
        settings = APP_SETTINGS.get()
        return format_clock(timezone_name=timezone_name or settings.get("timezone", "Asia/Tokyo"))

    def save_environment_settings(self, values):
        if not isinstance(values, dict):
            return {"error": "環境設定の形式が正しくありません。"}
        try:
            APP_SETTINGS.update(values)
            return self.get_environment_settings()
        except OSError as error:
            return {"error": f"環境設定を保存できません: {error}"}

    def start_auto_database_update(self):
        with self._auto_update_lock:
            if self._auto_update_scheduler_started:
                return {"status": "checked"}
            self._auto_update_scheduler_started = True
        scheduler = threading.Thread(
            target=self._catalog_update_schedule_loop,
            name="catalog-update-scheduler",
            daemon=True,
        )
        scheduler.start()
        return self._start_due_catalog_update()

    def _catalog_update_schedule_loop(self):
        while True:
            threading.Event().wait(60)
            self._start_due_catalog_update()

    def _start_due_catalog_update(self):
        due, next_due = APP_SETTINGS.is_due()
        if not due:
            return {
                "status": "not_due",
                "next_update_at": next_due.isoformat(timespec="seconds") if next_due else None,
            }
        if self._maintenance_lock.locked():
            return {"status": "busy", "message": "他の処理の終了後に自動更新します。"}
        try:
            APP_SETTINGS.record_attempt()
            result = self.run_maintenance_task(
                "update_master", options={"mode": 1, "automatic": True}
            )
        except (OSError, RuntimeError) as error:
            return {"status": "error", "message": f"自動更新を開始できません: {error}"}
        if result.get("status") != "started":
            self._record_catalog_update_result(False)
            return result
        return {"status": "started", "message": "一覧データの自動更新を開始しました。"}

    @staticmethod
    def _record_catalog_update_result(success, cancelled=False):
        try:
            APP_SETTINGS.record_result(success, cancelled=cancelled)
        except OSError as error:
            print(f"環境設定へ更新結果を保存できません: {error}")

    # --- AI Assistant API ---
    def ai_get_status(self):
        if not self.ai:
            return {"available": False, "error": self.ai_error or "AIが初期化されていません。"}
        return self.ai.get_status()

    def get_about_info(self):
        settings = self.ai.get_settings() if self.ai else {}
        model = settings.get("model")
        if not model:
            settings_path = BASE_DIR / "database" / "ai" / "settings.json"
            try:
                saved_settings = json.loads(settings_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                saved_settings = {}
            model = saved_settings.get("model") if isinstance(saved_settings, dict) else None
        return {
            "name": "BrNavi Fandom JP",
            "version": VERSION,
            "application_type": "GUI",
            "provider": "Groq",
            "model": model or AI_DEFAULT_SETTINGS["model"],
            "repository": "https://github.com/Suigetsu-Lium/BrNavi-FandomJP-GUI",
        }

    def open_about_link(self, destination):
        urls = {
            "repository": "https://github.com/Suigetsu-Lium/BrNavi-FandomJP-GUI",
            "releases": "https://github.com/Suigetsu-Lium/BrNavi-FandomJP-GUI/releases",
            "license": "https://github.com/Suigetsu-Lium/BrNavi-FandomJP-GUI/blob/main/LICENSE",
            "cc_by_sa": "https://creativecommons.org/licenses/by-sa/3.0/deed.ja",
            "third_party": (
                "https://github.com/Suigetsu-Lium/BrNavi-FandomJP-GUI/"
                "blob/main/THIRD_PARTY_NOTICES.md"
            ),
        }
        url = urls.get(str(destination))
        if not url:
            return {"error": "リンク先が不正です。"}
        if not webbrowser.open(url, new=2):
            return {"error": "既定のブラウザーを起動できませんでした。"}
        return {"opened": True}

    def ai_new_conversation(self):
        if not self.ai:
            return {"error": self.ai_error or "AIが利用できません。"}
        return {"conversation_id": self.ai.new_conversation()}

    def ai_get_history(self, conversation_id):
        if not self.ai or not conversation_id:
            return []
        history = self.ai.get_history(str(conversation_id))
        for item in history:
            if item["role"] == "assistant":
                for version in item.get("versions", []):
                    version["html"] = render_markdown(version["content"])
                current = next(
                    (
                        version for version in item.get("versions", [])
                        if version["version_number"] == item["current_version_number"]
                    ),
                    None,
                )
                item["html"] = current["html"] if current else render_markdown(item["content"])
        return history

    def ai_list_conversations(self):
        return self.ai.list_conversations() if self.ai else []

    def ai_get_settings(self):
        return self.ai.get_settings() if self.ai else {"error": self.ai_error or "AIが利用できません。"}

    def ai_update_settings(self, values):
        if not self.ai:
            return {"error": self.ai_error or "AIが利用できません。"}
        try:
            return self.ai.update_settings(values or {})
        except Exception as e:
            return {"error": str(e)}

    def ai_render_markdown(self, content):
        if not render_markdown:
            return {"error": "Markdownレンダラーを読み込めません。"}
        return {"html": render_markdown(content)}

    def ai_get_active_request(self, conversation_id):
        return self.ai.store.get_active_request(str(conversation_id)) if self.ai else None

    def ai_set_current_version(self, message_id, version_number):
        if not self.ai:
            return {"error": self.ai_error or "AIが利用できません。"}
        if not self.ai.store.set_current_version(int(message_id), int(version_number)):
            return {"error": "回答バージョンを切り替えられません。"}
        return {"ok": True}

    def ai_set_rating(self, message_id, version_number, rating):
        if not self.ai:
            return {"error": self.ai_error or "AIが利用できません。"}
        rating = rating or None
        if not self.ai.store.set_rating(int(message_id), int(version_number), rating):
            return {"error": "回答バージョンが見つかりません。"}
        return {"ok": True}

    def ai_rename_conversation(self, conversation_id, title):
        if not self.ai:
            return {"error": self.ai_error or "AIが利用できません。"}
        try:
            if not self.ai.store.rename_conversation(str(conversation_id), title):
                return {"error": "会話が見つかりません。"}
            return {"ok": True}
        except ValueError as e:
            return {"error": str(e)}

    def ai_delete_conversation(self, conversation_id):
        if not self.ai:
            return {"error": self.ai_error or "AIが利用できません。"}
        cid = str(conversation_id)
        active = self.ai.store.get_active_request(cid)
        if active:
            self.ai_cancel_generation(active["request_id"])
        return {"ok": self.ai.store.delete_conversation(cid)}

    def ai_regenerate(self, conversation_id, message_id, request_id):
        if not self.ai:
            return {"accepted": False, "error": self.ai_error or "AIが利用できません。"}
        return self._start_ai_generation(
            str(conversation_id), "", "regenerate", int(message_id), str(request_id)
        )

    def ai_send_message(self, message, conversation_id=None, request_id=None):
        if not self.ai:
            return {"accepted": False, "error": self.ai_error or "AIが利用できません。"}
        message = str(message or "").strip()
        if not message or len(message) > 12000:
            return {"accepted": False, "error": "メッセージは1～12,000文字で入力してください。"}
        return self._start_ai_generation(
            str(conversation_id or ""),
            message,
            "send",
            None,
            str(request_id or ""),
        )

    def _start_ai_generation(self, conversation_id, message, operation, message_id, request_id):
        if not self._activity_lock.acquire(blocking=False):
            return {"accepted": False, "error": "保守・削除処理の開始中です。少し待ってから再試行してください。"}
        try:
            if self._cleanup_lock.locked():
                return {"accepted": False, "error": "保存済みデータの削除中はAI応答を開始できません。"}
            return self._start_ai_generation_unlocked(
                conversation_id, message, operation, message_id, request_id
            )
        finally:
            self._activity_lock.release()

    def _start_ai_generation_unlocked(self, conversation_id, message, operation, message_id, request_id):
        try:
            generation = self.ai.store.begin_generation(
                conversation_id,
                message,
                operation=operation,
                message_id=message_id,
                request_id=request_id or None,
            )
        except Exception as e:
            return {"accepted": False, "error": str(e)}

        request_id = generation["request_id"]
        job = {"loop": None, "task": None, "cancel_requested": False}
        with self._ai_jobs_lock:
            self._ai_jobs[request_id] = job

        def worker():
            async def run():
                loop = asyncio.get_running_loop()
                task = asyncio.current_task()
                with self._ai_jobs_lock:
                    job["loop"] = loop
                    job["task"] = task
                    should_cancel = job["cancel_requested"]
                if should_cancel:
                    task.cancel()
                try:
                    result = await self.ai.chat(
                        generation,
                        on_delta=lambda text: self._send_ai_stream(
                            {
                                "conversation_id": generation["conversation_id"],
                                "request_id": request_id,
                                "text": text,
                            }
                        ),
                    )
                    if result.get("cancelled"):
                        self.ai.store.finish_generation_error(request_id, cancelled=True)
                        self._send_ai_result(
                            {
                                "ok": False,
                                "cancelled": True,
                                "conversation_id": generation["conversation_id"],
                                "request_id": request_id,
                                "operation": operation,
                            }
                        )
                        return
                    result["rendered_html"] = render_markdown(result["response"])
                    self._send_ai_result({"ok": True, **result})
                    if generation["should_generate_title"]:
                        await self.ai.generate_title(
                            generation["conversation_id"], generation["prompt"]
                        )
                        self._send_ai_title_update(generation["conversation_id"])
                except asyncio.CancelledError:
                    self.ai.store.finish_generation_error(request_id, cancelled=True)
                    self._send_ai_result(
                        {
                            "ok": False,
                            "cancelled": True,
                            "conversation_id": generation["conversation_id"],
                            "request_id": request_id,
                            "operation": operation,
                        }
                    )
                except Exception as e:
                    self.ai.store.finish_generation_error(request_id)
                    self._send_ai_result(
                        {
                            "ok": False,
                            "error": str(e),
                            "conversation_id": generation["conversation_id"],
                            "request_id": request_id,
                            "operation": operation,
                        }
                    )

            try:
                asyncio.run(run())
            finally:
                with self._ai_jobs_lock:
                    self._ai_jobs.pop(request_id, None)

        threading.Thread(target=worker, daemon=True).start()
        return {
            "accepted": True,
            "conversation_id": generation["conversation_id"],
            "request_id": request_id,
            "operation": operation,
        }

    def ai_cancel_generation(self, request_id):
        if not self.ai:
            return {"accepted": False, "error": self.ai_error or "AIが利用できません。"}
        request_id = str(request_id)
        if not self.ai.store.mark_cancel_requested(request_id):
            return {"accepted": False, "error": "生成はすでに終了しています。"}
        with self._ai_jobs_lock:
            job = self._ai_jobs.get(request_id)
            if job:
                job["cancel_requested"] = True
                loop, task = job["loop"], job["task"]
            else:
                loop, task = None, None
        if loop and task:
            loop.call_soon_threadsafe(task.cancel)
        else:
            self.ai.store.finish_generation_error(request_id, cancelled=True)
        return {"accepted": True, "request_id": request_id}

    @staticmethod
    def _send_ai_result(payload):
        try:
            encoded = json.dumps(payload, ensure_ascii=True)
            if webview.windows:
                webview.windows[0].evaluate_js(f"onAIResponse({encoded});")
        except Exception as e:
            print(f"[AI] GUIへの応答送信に失敗: {e}")

    @staticmethod
    def _send_ai_stream(payload):
        try:
            encoded = json.dumps(payload, ensure_ascii=True)
            if webview.windows:
                webview.windows[0].evaluate_js(f"onAIStream({encoded});")
        except Exception as e:
            print(f"[AI] ストリーミング表示に失敗: {e}")

    @staticmethod
    def _send_ai_title_update(conversation_id):
        try:
            encoded = json.dumps(conversation_id, ensure_ascii=True)
            if webview.windows:
                webview.windows[0].evaluate_js(f"onAITitleUpdated({encoded});")
        except Exception as e:
            print(f"[AI] 会話名の更新表示に失敗: {e}")

    def _get_sort_key(self, item):
        """指定カテゴリ優先順 ＋ 自然な数値昇順 (Level 0, 1, 2... 10) でソート"""
        cat = item.get("category", "")
        cats = [c.strip() for c in cat.split("|") if c.strip()]
        
        # 複数カテゴリがある場合は最も優先度が高い数値を採用
        if cats:
            priority = min([CATEGORY_PRIORITY.get(c, 99) for c in cats])
        else:
            priority = CATEGORY_PRIORITY.get(cat, 99)

        title = item.get("title", "")
        # Level番号を数値として抽出して綺麗に順番通り並べる
        m = re.search(r"Level\s+(-?\d+(?:\.\d+)?)", title, re.IGNORECASE)
        num = float(m.group(1)) if m else 999999.0

        return (priority, num, title.casefold())

    @staticmethod
    def _extract_level_id_from_filename(path):
        """Extract a Level identifier from a locally saved filename."""
        stem = Path(path).stem
        # TXT exports may append a subtitle after a double-underscore marker.
        if "__" in stem:
            stem = stem.split("__", 1)[0]
        elif "_ _" in stem:
            stem = stem.split("_ _", 1)[0]
        image_marker = stem.casefold().find(" - image")
        if image_marker >= 0:
            stem = stem[:image_marker]
        name = " ".join(stem.replace("_", " ").split())
        return name or None
    @staticmethod
    def _filename_matches_level_id(path, level_id):
        """Match saved HTML/TXT files using normalized title and filename keys."""
        path = Path(path)
        if path.suffix.casefold() not in {".html", ".htm", ".txt"}:
            return False
        # A title prefix is not an identity: "Level 0" must never claim
        # files belonging to "Level 0.1", "Level 0 η", etc.
        filename_title = BackroomsAPI._extract_level_id_from_filename(path)
        def exact_key(value):
            text = unicodedata.normalize("NFKC", str(value or "")).strip()
            text = text.replace("\\", "/").replace("/", " ")
            text = text.replace("_", " ")
            return " ".join(text.split()).casefold()
        stem_key = exact_key(filename_title or path.stem)
        title_key = exact_key(level_id)
        return bool(title_key and stem_key == title_key)


    def _normalize_level_number_query(self, query):
        """'20', 'Level 20', 'Level 20 η'を数値検索として解釈する。"""
        value = query.strip()
        value = re.sub(r"^level\s*", "", value, flags=re.IGNORECASE).strip()
        match = re.fullmatch(r"(-?\d+(?:\.\d+)?)(?:\s*η)?", value, re.IGNORECASE)
        return match.group(1) if match else None

    def search_candidates(self, query):
        lock = getattr(self, "_candidate_catalog_lock", None)
        if lock is None:
            lock = self._candidate_catalog_fallback_lock
        with lock:
            return self._search_candidates_locked(query)

    def _search_candidates_locked(self, query):
        """Master Database全体を検索し、ローカル保存状態を添えて返す。"""
        query = re.sub(r"\s+", " ", str(query or "")).strip().casefold()
        number_query = self._normalize_level_number_query(query) if query else None
        catalog = getattr(self, "_candidate_catalog", None)
        if catalog is not None:
            return self._filter_candidate_catalog(catalog, query, number_query)

        saved_titles = {}
        local_files = []
        for directory, suffix in ((TEXT_DIR, ".txt"), (HTML_DIR, ".html")):
            if directory.exists():
                local_files.extend(directory.glob(f"*{suffix}"))

        def normalized_identity(value):
            text = unicodedata.normalize("NFKC", str(value or "")).strip()
            text = text.replace("\\", "/").replace("/", " ")
            text = text.replace("_", " ")
            return " ".join(text.split()).casefold()

        local_files_by_key = {}
        for path in local_files:
            filename_title = self._extract_level_id_from_filename(path)
            key = normalized_identity(filename_title or path.stem)
            if key:
                local_files_by_key.setdefault(key, []).append(path)

        pages = load_master_database() or {}
        # Most saved HTML files already have a title-matching filename. Only
        # inspect metadata headers for orphan filenames; reading every HTML
        # file on each initial catalog build is especially slow on OneDrive.
        page_identity_counts = {}
        for page in pages.values():
            title_key = normalized_identity(page.get("title"))
            if title_key:
                page_identity_counts[title_key] = page_identity_counts.get(title_key, 0) + 1
        page_identity_keys = set(page_identity_counts)
        duplicate_page_identity_keys = {
            key for key, count in page_identity_counts.items() if count > 1
        }
        html_by_url = {}
        if HTML_DIR.exists() and page_identity_keys:
            for candidate_html in HTML_DIR.glob("*.html"):
                candidate_key = normalized_identity(
                    self._extract_level_id_from_filename(candidate_html) or candidate_html.stem
                )
                # A unique title can be matched without reading its HTML header.
                # Duplicate titles must be resolved by canonical URL to avoid
                # attaching one article's local file to every same-title page.
                if candidate_key in page_identity_keys and candidate_key not in duplicate_page_identity_keys:
                    continue
                try:
                    with candidate_html.open("r", encoding="utf-8-sig", errors="replace") as stream:
                        header = stream.read(8192)
                except OSError:
                    continue
                url_match = re.search(r"BRNAVI_META_INFO:.*?\bURL=([^|\s>]+)", header)
                if url_match:
                    canonical_url = url_match.group(1).strip()
                    if canonical_url:
                        html_by_url.setdefault(canonical_url, candidate_html)

        matched_local_files = set()
        saved_files_by_url = {}
        for page in pages.values():
            title = str(page.get("title", "") or "").strip()
            if not title:
                continue
            title_key = normalized_identity(title)
            matched_files = (
                []
                if title_key in duplicate_page_identity_keys
                else list(local_files_by_key.get(title_key, ()))
            )

            # Prefer the canonical article URL when associating local HTML/TXT.
            # Japanese titles can be mojibaked or sanitized differently in the
            # exported filenames, so title-only matching can create a second,
            # orphaned local article beside the actual catalog entry.
            page_url = str(page.get("url") or "").strip()
            if page_url:
                candidate_html = html_by_url.get(page_url)
                if candidate_html is not None:
                    matched_files.append(candidate_html)
                    sibling_text = TEXT_DIR / f"{candidate_html.stem}.txt"
                    if sibling_text.is_file():
                        matched_files.append(sibling_text)

            # Do not associate TXT files by Latin-token similarity. This
            # loses meaningful signs (Level -20 vs Level 20) and can attach
            # an unrelated article. Only exact filename or canonical HTML
            # sibling associations above are accepted.

            matched_files = list(dict.fromkeys(matched_files))
            if matched_files:
                if page_url:
                    saved_files_by_url[page_url.casefold()] = matched_files[0]
                else:
                    saved_titles[title.casefold()] = matched_files[0]
                matched_local_files.update(matched_files)

        for local_file in local_files:
            if local_file in matched_local_files:
                continue
            level_id = self._extract_level_id_from_filename(local_file)
            if level_id:
                saved_titles.setdefault(level_id.casefold(), local_file)
        metadata_by_title = {}
        summary_path = BASE_DIR / "extracted_fandom_levels_summary.csv"
        if summary_path.exists():
            try:
                with summary_path.open("r", encoding="utf-8-sig", newline="") as stream:
                    for row in csv.DictReader(stream):
                        code = str(row.get("階層コード") or row.get("code") or "").strip()
                        if code:
                            metadata_by_title[code.casefold()] = row
            except (OSError, csv.Error):
                pass
        unique_results = {}
        for page in pages.values():
            title = str(page.get("title", "") or "").strip()
            if not title:
                continue
            metadata = metadata_by_title.get(title.casefold(), {})
            aliases = page.get("aliases", metadata.get("別名", metadata.get("aliases", []))) or []
            if isinstance(aliases, str):
                aliases = re.split(r"[|,、]", aliases)
            aliases = [str(value).strip() for value in aliases if str(value).strip()]
            category = str(page.get("category", metadata.get("カテゴリ", "")) or "")
            subtitle = str(page.get("subtitle", page.get("meta_title", metadata.get("メタタイトル", ""))) or "")
            page_url = str(page.get("url") or "").strip()
            saved_file = (
                saved_files_by_url.get(page_url.casefold())
                if page_url else saved_titles.get(title.casefold())
            )
            # Do not infer a saved article from a filename that merely starts
            # with this title. Local presence is assigned only by exact identity.
            result_key = str(page.get("url") or title).casefold()
            unique_results[result_key] = {
                "title": title,
                "subtitle": subtitle,
                "meta_title": page.get("meta_title", ""),
                "meta_title_ja": page.get("meta_title_ja", ""),
                "has_strikethrough": bool(page.get("has_strikethrough", False)),
                "strikethrough_spans": page.get("strikethrough_spans", "[]"),
                "aliases": aliases,
                "category": category,
                "category_label": page.get("category_label", metadata.get("カテゴリ名", "")),
                "source_type": page.get("source_type", ""),
                "url": page.get("url", metadata.get("URL", "")),
                "indexes": page.get("indexes", ""),
                "index_memberships": page.get("index_memberships", "[]"),
                "section_path": page.get("section_path", "[]"),
                "section_order": page.get("section_order", "0"),
                "page_order": page.get("page_order", "0"),
                "language": page.get("language", "JP"),
                "saved": bool(saved_file),
            }

        # Master Database未生成の環境でも保存済み記事を一覧できる。
        for title_key, path in saved_titles.items():
            if any(str(item.get("title", "")).casefold() == title_key for item in unique_results.values()):
                continue
            title = self._extract_level_id_from_filename(path)
            if title:
                unique_results["local:" + title_key] = {
                    "title": title, "subtitle": "", "aliases": [],
                    "category": "", "category_label": "", "source_type": "",
                    "url": "", "indexes": "", "language": "JP", "saved": True,
                }

        catalog = list(unique_results.values())
        catalog.sort(key=self._get_sort_key)
        self._candidate_catalog = catalog[:2000]
        return self._filter_candidate_catalog(self._candidate_catalog, query, number_query)

    @staticmethod
    def _filter_candidate_catalog(catalog, query, number_query=None):
        if not query:
            return list(catalog)
        results = []
        wants_eta = bool(re.search(r"η|イータ", query, re.I))
        for item in catalog:
            if number_query is not None:
                match = re.search(r"Level\s*(-?\d+(?:\.\d+)?)", item["title"], re.I)
                is_eta = bool(re.search(r"η|イータ", item["title"], re.I))
                if match and match.group(1) == number_query and (not wants_eta or is_eta):
                    results.append(item)
                continue
            values = [item.get("title", ""), item.get("subtitle", ""), item.get("category", "")]
            aliases = item.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            values.extend(aliases)
            if any(query in str(value).casefold() for value in values):
                results.append(item)
        return results

    def _find_images_flexible(self, level_id):
        """Find local article images using the same normalized key as the gallery."""
        return find_local_images(IMAGE_DIR, level_id)

    def get_level_detail(self, level_id, page_url=None):
        """本文TXTからメタタイトル・危険度・理解度を直接パースし、画像を確実に連結"""
        master = load_master_database()
        page_info = master.get(str(page_url or "").strip()) if page_url else None
        if page_info is None and not page_url:
            page_info = next(
                (page for page in master.values()
                 if str(page.get("title", "")).strip().casefold() == str(level_id).strip().casefold()),
                None,
            )
        html_file = None
        if page_info and page_info.get("url") and HTML_DIR.exists():
            expected_url_marker = "URL=" + page_info["url"]
            for candidate_html in HTML_DIR.glob("*.html"):
                try:
                    with candidate_html.open("r", encoding="utf-8", errors="ignore") as stream:
                        header = stream.read(8192)
                except OSError:
                    continue
                if expected_url_marker in header:
                    html_file = candidate_html
                    break
        if html_file is None and not page_url and HTML_DIR.exists():
            html_file = next(
                (path for path in HTML_DIR.glob("*.html")
                 if self._filename_matches_level_id(path, level_id)),
                None,
            )
        if html_file is None:
            html_file = HTML_DIR / f"{get_html_filename_prefix(level_id)}.html"
        # 本文TXTファイルの探索：ファイル名先頭のLevel IDを完全一致で照合
        text_file = None
        if html_file and html_file.is_file() and TEXT_DIR.exists():
            corresponding_text = TEXT_DIR / f"{html_file.stem}.txt"
            if corresponding_text.is_file():
                text_file = corresponding_text
        if TEXT_DIR.exists() and (not page_url or page_info):
            requested_id = re.sub(r"\s+", " ", level_id.strip()).casefold()
            for candidate_text in TEXT_DIR.glob("*.txt"):
                file_level_id = self._extract_level_id_from_filename(candidate_text)
                if (
                    (file_level_id and file_level_id.casefold() == requested_id)
                    or self._filename_matches_level_id(candidate_text, level_id)
                ):
                    text_file = candidate_text
                    break

        # Never fall back to a different article's TXT based on partial
        # Latin tokens. Signs and punctuation are meaningful in Level IDs
        # (e.g. Level -20 η is not Level 20 η).

        raw_text = ""
        if text_file and text_file.exists():
            try:
                with open(text_file, "r", encoding="utf-8-sig", errors="ignore") as f:
                    raw_text = f.read()
            except Exception:
                pass

        # 🌟 本文TXTの先頭から直接メタ情報を抽出
        meta_title = ""
        danger = "情報なし"
        comprehension = "情報なし"

        # 本文中の最初のLevel見出しを無条件採用しない。
        # ファイル名から得た階層IDと見出しの階層コードが一致する場合だけ
        # メタタイトルとして表示する（別階層の誤表示を防止）。
        title_pattern = re.compile(
            r'^\[(Level\s+[^：:\]]+)(?:[：:]\s*["「『]?([^"」』\]]+)["」』]?)?\]$',
            re.IGNORECASE | re.MULTILINE
        )
        for title_m in title_pattern.finditer(raw_text):
            heading_code = re.sub(r"\s+", " ", title_m.group(1)).strip()
            requested_code = re.sub(r"\s+", " ", level_id).strip()
            if heading_code.casefold() == requested_code.casefold():
                meta_title = (title_m.group(2) or "").strip()
                break

        if page_info:
            # 一覧HTMLのメタタイトルは装飾情報と対になる正本として優先する。
            meta_title = str(page_info.get("meta_title") or meta_title).strip()
        meta_title_ja = str(page_info.get("meta_title_ja") or "").strip() if page_info else ""
        has_strikethrough = bool(page_info.get("has_strikethrough")) if page_info else False
        strikethrough_spans = page_info.get("strikethrough_spans", "[]") if page_info else "[]"

        parsed_danger, _ = extract_danger(raw_text)
        parsed_comprehension, _ = extract_understanding(raw_text)
        if parsed_danger:
            danger = parsed_danger
        if parsed_comprehension:
            comprehension = parsed_comprehension
        if danger == "情報なし" or comprehension == "情報なし":
            summary_file = BASE_DIR / "extracted_fandom_levels_summary.csv"
            try:
                with summary_file.open("r", encoding="utf-8-sig", newline="") as stream:
                    for row in csv.DictReader(stream):
                        code = re.sub(r"\s+", " ", str(row.get("階層コード", "")).strip()).casefold()
                        if code != re.sub(r"\s+", " ", level_id.strip()).casefold():
                            continue
                        if danger == "情報なし" and row.get("危険度"):
                            danger = row["危険度"].strip()
                        if comprehension == "情報なし" and row.get("理解度"):
                            comprehension = row["理解度"].strip()
                        break
            except (OSError, csv.Error, UnicodeError):
                pass

        # 本文から先頭のメタ情報ブロック（タイトル・理解度・危険度・写真キャプション等）を除外
        cleaned_text = raw_text
        # 表示対象ページ自身の見出しだけを除去する。他階層の見出しは残す。
        escaped_level = re.escape(level_id)
        cleaned_text = re.sub(
            rf'^\[({escaped_level})(?:[：:].*?)?\]\s*$',
            '', cleaned_text, flags=re.MULTILINE | re.IGNORECASE
        )
        cleaned_text = re.sub(r'このウィキに初めて投稿された[^\n]+\n?', '', cleaned_text)
        cleaned_text = re.sub(r'\[理解度\]\s*\n\s*[^\n\[]+\n?', '', cleaned_text)
        cleaned_text = re.sub(r'\[危険度\]\s*\n\s*[^\n\[]+\n?', '', cleaned_text)
        cleaned_text = cleaned_text.strip()

        article_attribution = ""
        if html_file and html_file.is_file():
            try:
                source_html = html_file.read_text(encoding="utf-8", errors="ignore")
                article_attribution = extract_article_attribution(source_html)
            except OSError:
                article_attribution = ""

        # URLを持つ記事は、そのURLから生成したキーの画像だけを表示する。
        # タイトル・階層IDによる画像検索は別記事混入の原因になるため行わない。
        page_url_value = str(page_info.get("url", "") if page_info else "").strip()
        if page_url_value:
            page_key = hashlib.sha256(
                page_url_value.rstrip("/").encode("utf-8")
            ).hexdigest()[:16]
            image_paths = sorted(IMAGE_DIR.glob(f"page_{page_key}_image*")) if IMAGE_DIR.exists() else []
        else:
            image_paths = self._find_images_flexible(level_id)
        images_base64 = []
        for img_path in image_paths:
            try:
                ext = img_path.suffix.lower().replace(".", "")
                mime = "image/jpeg" if ext in ["jpg", "jpeg"] else f"image/{ext}"
                with open(img_path, "rb") as f:
                    b64_data = base64.b64encode(f.read()).decode("utf-8")
                    images_base64.append({
                        "name": img_path.name,
                        "src": f"data:{mime};base64,{b64_data}"
                    })
            except Exception:
                pass

        # Master DB からカテゴリ・URL情報を取得
        self.current_info = {
            "url": page_info.get("url", "") if page_info else "",
            "html_file": html_file,
        }

        category = page_info.get("category", "normal") if page_info else "normal"
        language = page_info.get("language", "JP") if page_info else "JP"
        source_type = page_info.get("source_type", "original") if page_info else "original"
        translation_section = page_info.get("translation_section", "") if page_info else ""
        section_number = page_info.get("section_number", "") if page_info else ""

        return {
            "level_id": level_id,
            "title": meta_title or (page_info.get("title", "") if page_info else ""),
            "meta_title_ja": meta_title_ja,
            "has_strikethrough": has_strikethrough,
            "strikethrough_spans": strikethrough_spans,
            "url": self.current_info["url"],
            "html_path": str(html_file) if html_file and html_file.is_file() else "",
            "category": category,
            "language": language,
            "source_type": source_type,
            "translation_section": translation_section,
            "section_number": section_number,
            "danger": danger,
            "comprehension": comprehension,
            "attribution": article_attribution,
            "text": cleaned_text or "※ 本文データがありません。",
            "images": images_base64
        }

    def get_current_fandom_url(self):
        if self.current_info and self.current_info.get("url"):
            return {"url": self.current_info["url"]}
        return {"error": "FandomページのURLがありません。"}

    def search_routes(self, start, goal, options=None):
        try:
            allowed = {
                "include_uncertain", "include_unknown", "include_rumor",
                "include_closed", "include_needs_review",
            }
            filters = {
                key: bool(value)
                for key, value in (options or {}).items()
                if key in allowed
            }
            result = search_local_routes(start, goal, **filters)
            # Keep canonical/raw fields intact; add separate presentation-only values.
            for infos in result.get("edge_info", {}).values():
                for info in infos:
                    info["condition_display"] = format_wiki_links(info.get("condition", ""))
                    for evidence in info.get("evidence", []):
                        evidence["condition_display"] = format_wiki_links(
                            evidence.get("condition", "")
                        )
            return result
        except Exception as error:
            return {"error": f"経路検索に失敗しました: {error}", "routes": [], "start_candidates": [], "goal_candidates": []}

    def open_local_browser_html(self):
        if self.current_info and self.current_info.get("html_file") and self.current_info["html_file"].exists():
            open_local_html(self.current_info["html_file"])
            return {"status": "success"}
        return {"status": "error", "message": "ローカルHTMLがありません。"}

    def get_level_list_languages(self):
        try:
            return {"languages": get_level_list_languages()}
        except OSError as error:
            return {"error": str(error)}

    def get_level_list_pages(self, language):
        try:
            return {"pages": load_level_list_pages(language)}
        except (OSError, ValueError) as error:
            return {"error": str(error)}

    def start_level_list_offline_save(self, pages, overwrite=False):
        if not isinstance(pages, list) or not pages:
            return {"status": "error", "message": "オフライン保存するレベルを選択してください。"}
        validated_pages = []
        for page in pages:
            if (
                not isinstance(page, dict)
                or not page.get("title")
                or not is_supported_level_list_url(page.get("url"))
            ):
                return {"status": "error", "message": "選択リストに不正なページが含まれています。"}
            validated_pages.append(dict(page))
        return self.run_maintenance_task(
            "save_level_list_offline",
            options={
                "pages": validated_pages,
                "overwrite": bool(overwrite),
            },
        )

    # --- 保守・更新タスク ---
    def search_online_wiki(self, query):
        try:
            pages = search_online_wiki_pages(query)
            return {"pages": pages}
        except Exception as error:
            return {"pages": [], "error": str(error)}

    def get_online_wiki_page(self, url):
        try:
            return {"page": fetch_online_wiki_page(url)}
        except Exception as error:
            return {"error": str(error)}

    def navigate_online_wiki_frame(self, url):
        url = str(url or "").strip()
        if not is_supported_wiki_url(url):
            return {
                "status": "error",
                "message": "日本語版Backrooms WikiのURLを指定してください。",
            }
        self._current_online_wiki_url = url
        return {"status": "ok", "url": url}

    @staticmethod
    def _is_article_wiki_url(url):
        if not is_supported_wiki_url(url):
            return False
        parsed = urlsplit(url)
        title = unquote(parsed.path.split("/ja/wiki/", 1)[-1]).strip("/")
        namespace = title.split(":", 1)[0].casefold() if ":" in title else ""
        if not title or namespace in {
            "special", "category", "file", "image", "user", "template", "help",
            "mediawiki", "talk", "カテゴリ", "ファイル", "利用者", "テンプレート", "トーク",
        }:
            return False
        if title.casefold().endswith((".js", ".css", ".json", ".php", ".png", ".jpg", ".svg")):
            return False
        return True

    def navigate_ai_answer_link(self, url):
        url = str(url or "").strip()
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").casefold()
        except ValueError:
            return {"status": "error", "message": "リンク先URLが正しくありません。"}
        try:
            address = ipaddress.ip_address(host.strip("[]")) if host else None
        except ValueError:
            address = None
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username
            or parsed.password
            or host == "localhost"
            or host.endswith(".local")
            or host.endswith((".localhost", ".internal", ".lan"))
            or (not address and "." not in host)
            or (address and not address.is_global)
        ):
            return {"status": "error", "message": "安全なWebリンクではありません。"}
        self._current_online_wiki_url = url
        return {"status": "ok", "url": url}

    def start_ai_source_save(self, page, prompt, user_consented=False, flow_id="", rebuild_network=False):
        if user_consented is not True:
            return {"status": "error", "message": "資料の保存には利用者の承諾が必要です。"}
        if not isinstance(page, dict) or not page.get("title") or not is_supported_wiki_url(page.get("url")):
            return {"status": "error", "message": "保存対象は日本語版Backrooms Wikiの記事を指定してください。"}
        return self.run_maintenance_task(
            "save_online_wiki_page",
            dict(page),
            options={
                "ai_prompt": str(prompt or "")[:12000],
                "ai_flow_id": str(flow_id or "")[:100],
                "ai_rebuild_network": bool(rebuild_network),
            },
        )

    def _on_main_window_response_received(self, response):
        url = str(getattr(response, "url", "") or "")
        headers = getattr(response, "headers", {}) or {}
        content_type = next(
            (
                str(value)
                for key, value in headers.items()
                if str(key).lower() == "content-type"
            ),
            "",
        )
        if (
            not self._is_article_wiki_url(url)
            or "text/html" not in content_type.lower()
        ):
            return
        self._current_online_wiki_url = url
        self._notify_online_wiki_url(url, self._is_article_wiki_url(url))

    def _notify_online_wiki_url(self, url, is_wiki_page):
        main_window = getattr(self, "_main_window", None)
        if main_window is None:
            return
        payload = json.dumps(
            {"url": url, "is_wiki_page": is_wiki_page},
            ensure_ascii=True,
        )
        try:
            main_window.evaluate_js(
                f"onOnlineWikiBrowserUrlChanged({payload})"
            )
        except Exception as error:
            print(f"[GUI] WikiブラウザーURLの画面通知に失敗: {error}")

    def _notify_online_wiki_batch_progress(self, current, total, result):
        main_window = getattr(self, "_main_window", None)
        if main_window is None:
            return
        payload = json.dumps(
            {"current": current, "total": total, **result},
            ensure_ascii=True,
        )
        try:
            main_window.evaluate_js(
                f"onOnlineWikiBatchSaveProgress({payload})"
            )
        except Exception as error:
            print(f"[GUI] Wiki一括保存の進捗通知に失敗: {error}")

    def get_current_online_wiki_url(self):
        url = self._current_online_wiki_url
        return {
            "url": url,
            "is_wiki_page": self._is_article_wiki_url(url),
        }

    def save_current_online_wiki_page(self):
        current = self.get_current_online_wiki_url()
        if not current["is_wiki_page"]:
            return {
                "status": "error",
                "message": "ブラウザーで日本語版Backrooms Wikiのページを開いてください。",
            }
        url = current["url"]
        title = unquote(urlsplit(url).path.rsplit("/", 1)[-1]).replace("_", " ")
        return self.start_online_wiki_save(
            {"title": title, "url": url, "categories": []}
        )

    def start_online_wiki_save(self, page):
        if not isinstance(page, dict):
            return {"status": "error", "message": "保存するWikiページが不正です。"}
        if not page.get("title") or not page.get("url"):
            return {"status": "error", "message": "ページ名またはURLがありません。"}
        if not is_supported_wiki_url(page["url"]):
            return {"status": "error", "message": "日本語版Backrooms WikiのURLを指定してください。"}
        return self.run_maintenance_task("save_online_wiki_page", page)

    def run_maintenance_task(self, task_name, selected_urls=None, options=None):
        if not self._activity_lock.acquire(blocking=False):
            return {"status": "busy", "message": "別の保守・削除処理を開始しています。"}
        try:
            if self._cleanup_lock.locked():
                return {"status": "busy", "message": "保存済みデータの削除中は保守処理を開始できません。"}
            return self._run_maintenance_task_unlocked(task_name, selected_urls, options)
        finally:
            self._activity_lock.release()

    def _run_maintenance_task_unlocked(self, task_name, selected_urls=None, options=None):
        if task_name in {"download_html", "download_selected_html"}:
            return {
                "status": "error",
                "message": "オンラインWiki検索からページを選択してください。",
            }
        if not self._maintenance_lock.acquire(blocking=False):
            return {"status": "busy", "message": "別の保守タスクが実行中です。"}
        self._maintenance_cancel_event.clear()
        self._maintenance_done_event.clear()
        worker = threading.Thread(
            target=self._execute_task,
            args=(task_name, selected_urls, options),
            daemon=True,
        )
        try:
            worker.start()
        except Exception:
            self._maintenance_done_event.set()
            self._maintenance_lock.release()
            raise
        return {"status": "started"}

    def cancel_maintenance_task(self):
        if (
            not self._maintenance_lock.locked()
            or self._maintenance_done_event.is_set()
        ):
            return {"status": "idle", "message": "実行中の保守タスクはありません。"}
        self._maintenance_cancel_event.set()
        return {"status": "cancelling", "message": "安全な停止位置で処理を停止します。"}

    def _execute_task(self, task_name, selected_urls=None, options=None):
        if task_name in {"save_online_wiki_page", "save_online_wiki_pages"}:
            batch = task_name == "save_online_wiki_pages"
            pages = selected_urls if batch else [selected_urls]
            payload = {"ok": False}
            results = []
            try:
                for index, page in enumerate(pages, 1):
                    if self._maintenance_cancel_event.is_set():
                        break
                    print(
                        f"GUIから選択されたWikiページを保存します: "
                        f"{page.get('title', '')}"
                    )
                    try:
                        result = save_online_wiki_page(page)
                        results.append({"ok": True, **result})
                    except Exception as error:
                        print(f"Wikiページ保存エラー: {error}")
                        results.append({
                            "ok": False,
                            "title": page.get("title", ""),
                            "error": str(error),
                        })
                    if batch:
                        self._notify_online_wiki_batch_progress(
                            index,
                            len(pages),
                            results[-1],
                        )
                if batch:
                    succeeded = sum(1 for result in results if result["ok"])
                    payload = {
                        "ok": succeeded == len(pages),
                        "cancelled": self._maintenance_cancel_event.is_set(),
                        "saved": succeeded,
                        "failed": len(results) - succeeded,
                        "total": len(pages),
                        "results": results,
                    }
                else:
                    payload = results[0] if results else {
                        "ok": False,
                        "cancelled": self._maintenance_cancel_event.is_set(),
                        "error": "保存処理を開始できませんでした。",
                    }
                    if payload.get("ok"):
                        try:
                            pipeline_result = run_maintenance_operation(
                                "refresh_saved_wiki_page",
                                options={"html_path": payload.get("path", "")},
                                context=OperationContext(
                                    cancel_event=self._maintenance_cancel_event,
                                    on_log=self._notify_maintenance_log,
                                    on_progress=self._notify_maintenance_progress,
                                ),
                            )
                            details = pipeline_result.details or {}
                            payload["pipeline_ok"] = bool(pipeline_result.success)
                            payload["extraction_completed"] = bool(details.get("extraction_completed", False))
                            payload["network_updated"] = bool(details.get("network_updated", False))
                            payload["pipeline_message"] = pipeline_result.message
                            if not pipeline_result.success:
                                payload["pipeline_error"] = pipeline_result.message
                                payload["network_update_error"] = pipeline_result.message
                        except OperationCancelled:
                            payload["pipeline_ok"] = False
                            payload["network_updated"] = False
                            payload["cancelled"] = True
                            payload["pipeline_error"] = "抽出パイプラインを停止しました。"
                            payload["network_update_error"] = payload["pipeline_error"]
                        except Exception as error:
                            payload["pipeline_ok"] = False
                            payload["network_updated"] = False
                            payload["pipeline_error"] = str(error)
                            payload["network_update_error"] = str(error)
                        # Rebuild the in-memory catalog so newly saved Levels are immediately searchable.
                        self._candidate_catalog = None
            finally:
                self._maintenance_done_event.set()
                try:
                    encoded = json.dumps(payload, ensure_ascii=True)
                    main_window = getattr(self, "_main_window", None)
                    if main_window is not None:
                        callback = (
                            "onOnlineWikiBatchSaveCompleted"
                            if batch else "onOnlineWikiSaveCompleted"
                        )
                        main_window.evaluate_js(
                            f"{callback}({encoded})"
                        )
                        if options and options.get("ai_flow_id"):
                            save_payload = {
                                **payload,
                                "prompt": options.get("ai_prompt", ""),
                                "flow_id": options.get("ai_flow_id", ""),
                            }
                            main_window.evaluate_js(
                                "onAIMissingSourceSaveCompleted(" +
                                json.dumps(save_payload, ensure_ascii=True) + ")"
                            )
                except Exception as error:
                    print(f"[GUI] Wiki保存完了通知に失敗: {error}")
                self._maintenance_lock.release()
            return

        task_labels = {
            "save_level_list_offline": "Level情報のオフライン保存",
            "update_master": "Level一覧データ更新",
            "extract_info": "情報データベース更新",
            "update_network": "ネットワーク更新",
            "refresh_saved_wiki_page": "Refresh saved HTML and extract",
        }
        task_label = task_labels.get(task_name)
        if task_label is None:
            self._maintenance_done_event.set()
            self._notify_maintenance_task_completed(
                False, f"未対応の保守タスクです: {task_name}"
            )
            self._maintenance_lock.release()
            return

        print(f"保守タスクを開始します: {task_label}")
        context = OperationContext(
            cancel_event=getattr(
                self, "_maintenance_cancel_event", threading.Event()
            ),
            on_log=self._notify_maintenance_log,
            on_progress=self._notify_maintenance_progress,
        )
        try:
            result = run_maintenance_operation(
                task_name,
                options=options,
                context=context,
            )
            if task_name == "update_master":
                self._record_catalog_update_result(result.success)
            print(f"保守タスクが完了しました: {task_label}")
            self._maintenance_done_event.set()
            self._notify_maintenance_task_completed(
                result.success,
                result.message,
            )
        except OperationCancelled:
            if task_name == "update_master":
                self._record_catalog_update_result(False, cancelled=True)
            message = f"{task_label}を安全な停止位置で停止しました。"
            if task_name in {"extract_info", "update_network"}:
                message += "既存の出力データは維持されています。"
            else:
                message += "完了済みステップの出力は維持されています。"
            print(message)
            self._maintenance_done_event.set()
            self._notify_maintenance_task_completed(
                False, message, cancelled=True
            )
        except Exception as error:
            if task_name == "update_master":
                self._record_catalog_update_result(False)
            print(f"保守タスクに失敗しました ({task_label}): {error}")
            context.log(f"{type(error).__name__}: {error}", "error")
            self._maintenance_done_event.set()
            self._notify_maintenance_task_completed(
                False, f"{task_label}に失敗しました: {error}"
            )
        finally:
            self._maintenance_done_event.set()
            self._maintenance_lock.release()

    def _notify_maintenance_log(self, message, level="info"):
        main_window = getattr(self, "_main_window", None)
        if main_window is None:
            return
        payload = json.dumps(
            {"message": message, "level": level},
            ensure_ascii=True,
        )
        try:
            main_window.evaluate_js(f"onMaintenanceLog({payload})")
        except Exception as error:
            print(f"[GUI] 保守タスクログの画面通知に失敗: {error}")

    def _notify_maintenance_progress(self, current, total, message):
        main_window = getattr(self, "_main_window", None)
        if main_window is None:
            return
        payload = json.dumps(
            {"current": current, "total": total, "message": message},
            ensure_ascii=True,
        )
        try:
            main_window.evaluate_js(f"onMaintenanceProgress({payload})")
        except Exception as error:
            print(f"[GUI] 保守タスク進捗の画面通知に失敗: {error}")

    def _notify_maintenance_task_completed(
        self, succeeded, message, cancelled=False
    ):
        self._candidate_catalog = None
        main_window = getattr(self, "_main_window", None)
        if main_window is None:
            return
        payload = json.dumps(
            {
                "ok": succeeded,
                "message": message,
                "cancelled": cancelled,
            },
            ensure_ascii=True,
        )
        try:
            main_window.evaluate_js(
                f"onMaintenanceTaskCompleted({payload})"
            )
        except Exception as error:
            print(f"[GUI] 保守タスク状態の画面通知に失敗: {error}")


HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>Backrooms Navigation System - Fandom JP</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    :root {
      --bg-fandom: #e5d9b8;
      --surface-fandom: #efe5c9;
      --surface-raised: #f7f0dc;
      --surface-soft: #e4d6b2;
      --border-fandom: #d3c29a;
      --header-amber: #91520f;
      --accent-amber: #a45b0b;
      --text-primary: #29261f;
      --text-secondary: #655d4d;
    }

    body {
      background-color: var(--bg-fandom);
      color: var(--text-primary);
      font-family: "Yu Gothic UI", "Meiryo UI", "Noto Sans JP", "Meiryo", sans-serif;
      font-size: 14px;
      line-height: 1.6;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      text-rendering: optimizeLegibility;
    }

    .toc-box {
      background-color: var(--surface-fandom);
      border: 1px solid var(--border-fandom);
    }

    /* Wiki Infobox（情報カード） */
    .wiki-infobox {
      width: 320px;
      background: var(--surface-raised);
      border: 1px solid var(--border-fandom);
      box-shadow: 0 2px 6px rgba(58, 43, 20, 0.1);
    }
    .wiki-infobox-header {
      background-color: var(--header-amber);
      color: #ffffff;
      font-weight: bold;
      text-align: center;
      padding: 8px 12px;
    }
    .wiki-infobox table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }
    .wiki-infobox th {
      background-color: var(--surface-fandom);
      border-top: 1px solid var(--border-fandom);
      border-bottom: 1px solid var(--border-fandom);
      padding: 6px 10px;
      text-align: left;
      width: 38%;
      font-weight: 600;
    }
    .wiki-infobox td {
      border-top: 1px solid var(--border-fandom);
      border-bottom: 1px solid var(--border-fandom);
      padding: 6px 10px;
    }

    /* 本文のWiki小見出し */
    .wiki-heading {
      font-size: 18px;
      font-weight: bold;
      color: var(--text-primary);
      border-bottom: 1px solid var(--border-fandom);
      padding-bottom: 4px;
      margin-top: 24px;
      margin-bottom: 10px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .article-kind-item .wiki-heading {
      color: #8a4b12;
      border-bottom: 2px solid #d7b36a;
    }
    .article-kind-item p {
      line-height: 1.85;
    }
    .ai-table-scroll {
      max-width: 100%;
      overflow-x: auto;
      margin: 0.75rem 0;
    }
    .ai-answer-content table {
      border-collapse: collapse;
      min-width: max-content;
    }
    .ai-answer-content th, .ai-answer-content td {
      border: 1px solid #e2d8b5;
      padding: 0.4rem 0.6rem;
      text-align: left;
    }
    .ai-answer-content th { background: #f7f0d4; }
    .ai-answer-content pre {
      max-width: 100%;
      overflow-x: auto;
      padding: 0.75rem;
      background: #1a1714;
      color: #f8f6ef;
      border-radius: 0.375rem;
    }
    .online-wiki-content {
      color: #29261f;
      font-size: 0.9rem;
      line-height: 1.75;
      overflow-wrap: anywhere;
    }
    .online-wiki-content h2, .online-wiki-content h3, .online-wiki-content h4 {
      margin: 1.4rem 0 0.6rem;
      padding-bottom: 0.3rem;
      border-bottom: 1px solid #d3c29a;
      font-weight: 700;
    }
    .online-wiki-content p, .online-wiki-content ul, .online-wiki-content ol,
    .online-wiki-content blockquote, .online-wiki-content figure {
      margin: 0.65rem 0;
    }
    .online-wiki-content a { color: #8a4b0d; text-decoration: underline; }
    .online-wiki-content img { max-width: 100%; height: auto; margin: 0.5rem auto; }
    .online-wiki-content table { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; }
    .online-wiki-content th, .online-wiki-content td {
      border: 1px solid #d3c29a;
      padding: 0.35rem 0.55rem;
    }
    .online-wiki-content th { background: #efe5c9; }
    .online-wiki-content blockquote { border-left: 3px solid #a45b0b; padding-left: 0.8rem; color: #655d4d; }
    .ai-answer-content p { margin: 0.5rem 0; }
    .ai-answer-content blockquote {
      border-left: 3px solid #b45309;
      padding-left: 0.75rem;
      color: var(--text-secondary);
    }
    .ai-answer-content a { color: #92400e; text-decoration: underline; }
    .ai-answer-content th { background: var(--surface-fandom); }

    [class~="bg-[#fbf6de]"] { background-color: var(--bg-fandom) !important; }
    [class~="bg-[#f7f0d4]"],
    [class~="bg-[#f5eed3]"],
    [class~="bg-[#f2e8c5]"] { background-color: var(--surface-fandom) !important; }
    [class~="bg-[#fffdf5]"],
    [class~="bg-[#fffaf0]"],
    [class~="bg-[#fdfaf0]"],
    [class~="bg-white"] { background-color: var(--surface-raised) !important; }
    [class~="bg-[#eee3bc]"],
    [class~="bg-[#f4e7b8]"],
    [class~="bg-[#ead9ad]"] { background-color: var(--surface-soft) !important; }
    [class~="hover:bg-[#eadfae]"]:hover { background-color: #d9c99f !important; }
    [class~="hover:bg-[#fffdf2]"]:hover { background-color: #eee4c9 !important; }
    [class~="border-[#e2d8b5]"],
    [class~="border-[#dfd3a8]"],
    [class~="border-[#d6c79a]"] { border-color: var(--border-fandom) !important; }
    [class~="bg-[#b45309]"] { background-color: var(--accent-amber) !important; }
    [class~="hover:bg-[#92400e]"]:hover { background-color: #78440f !important; }
    [class~="text-gray-800"],
    [class~="text-[#202124]"] { color: var(--text-primary) !important; }
    [class~="text-gray-600"],
    [class~="text-gray-500"] { color: var(--text-secondary) !important; }
    [class~="text-[#b45309]"] { color: var(--accent-amber) !important; }
    input, textarea, select {
      font: inherit;
      line-height: 1.5;
    }
    button {
      font-family: inherit;
      transition: background-color 140ms ease, border-color 140ms ease, color 140ms ease;
    }

    body[data-theme="dark"] {
      --bg-fandom: #1e1c17;
      --surface-fandom: #27241d;
      --surface-raised: #2f2b22;
      --surface-soft: #24211a;
      --border-fandom: #443e31;
      --header-amber: #15130f;
      --accent-amber: #ffc500;
      --text-primary: #edeae2;
      --text-secondary: #b5afa2;
      color-scheme: dark;
    }
    body[data-theme="dark"] [class*="bg-[#"],
    body[data-theme="dark"] [class~="bg-white"] { background-color: var(--surface-fandom) !important; }
    body[data-theme="dark"] [class~="bg-[#fbf6de]"] { background-color: var(--bg-fandom) !important; }
    body[data-theme="dark"] [class~="bg-[#f7f0d4]"],
    body[data-theme="dark"] [class~="bg-[#f5eed3]"],
    body[data-theme="dark"] [class~="bg-[#f2e8c5]"] { background-color: var(--surface-fandom) !important; }
    body[data-theme="dark"] [class~="bg-[#fffdf5]"],
    body[data-theme="dark"] [class~="bg-[#fffaf0]"],
    body[data-theme="dark"] [class~="bg-[#fdfaf0]"],
    body[data-theme="dark"] [class~="bg-white"] { background-color: var(--surface-raised) !important; }
    body[data-theme="dark"] [class~="bg-[#eee3bc]"],
    body[data-theme="dark"] [class~="bg-[#f4e7b8]"],
    body[data-theme="dark"] [class~="bg-[#ead9ad]"] { background-color: var(--surface-soft) !important; }
    body[data-theme="dark"] [class*="border-[#"] { border-color: var(--border-fandom) !important; }
    body[data-theme="dark"] [class*="text-gray-"],
    body[data-theme="dark"] [class*="text-[#"] { color: var(--text-primary) !important; }
    body[data-theme="dark"] [class~="bg-gray-800"],
    body[data-theme="dark"] [class~="bg-gray-700"] { background-color: #211e18 !important; }
    body[data-theme="dark"] header { background-color: #14120e !important; border-bottom: 1px solid #3a3428; }
    body[data-theme="dark"] a { color: #ffc500; }
    body[data-theme="dark"] input, body[data-theme="dark"] textarea,
    body[data-theme="dark"] select { color: #edeae2; background-color: #191713; border-color: #4d4637; }
    body[data-theme="dark"] .wiki-infobox-header { background-color: #8c6218; color: #fffaf0; }
    body[data-theme="dark"] .online-wiki-content { color: #edeae2; }
    body[data-theme="dark"] .online-wiki-content a, body[data-theme="dark"] .ai-answer-content a { color: #ffc500; }
    body[data-theme="dark"] .online-wiki-content th, body[data-theme="dark"] .ai-answer-content th { background: #332e24; }
    body[data-theme="dark"] .online-wiki-content th, body[data-theme="dark"] .online-wiki-content td,
    body[data-theme="dark"] .ai-answer-content th, body[data-theme="dark"] .ai-answer-content td { border-color: #4d4637; }

    .print-attribution { display: none; }

    /* Level図鑑の記事印刷 */
    @media print {
      @page {
        size: A4 portrait;
        margin: 12mm;
      }

      html,
      body {
        width: auto !important;
        height: auto !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: visible !important;
        background: #ffffff !important;
      }

      body * {
        visibility: hidden !important;
      }

      /* スクロール領域による印刷時の切り抜きを防ぐ */
      body > main,
      #view-explorer,
      #view-explorer > section,
      #explorer-article-view {
        display: block !important;
        width: auto !important;
        height: auto !important;
        max-height: none !important;
        overflow: visible !important;
        padding: 0 !important;
        margin: 0 !important;
      }

      #view-explorer > aside {
        display: none !important;
      }

      #wiki-page-container,
      #wiki-page-container * {
        visibility: visible !important;
      }

      #wiki-page-container {
        display: block !important;
        position: absolute !important;
        left: 0 !important;
        top: 0 !important;
        width: 100% !important;
        max-width: none !important;
        margin: 0 !important;
        padding: 0 !important;
        border: none !important;
        border-radius: 0 !important;
        box-shadow: none !important;
        background: #ffffff !important;
        color: #202124 !important;
      }

      /*
       * 綴じ代の安全領域。
       * マーカーを有効にした場合、記事内の見出し・本文・Infobox・
       * 出典欄を同じ基準で内側へ寄せ、固定マーカーとの重なりを防ぐ。
       * マーカー無効時は従来どおり余白を追加しない。
       */
      #wiki-page-container {
        box-sizing: border-box !important;
      }
      body[data-print-staple="true"][data-staple-edge="left"] #wiki-page-container {
        padding-left: 8mm !important;
      }
      body[data-print-staple="true"][data-staple-edge="top"] #wiki-page-container {
        padding-top: 8mm !important;
      }

      .print-exclude,
      #explorer-unsaved-actions,
      #print-options-panel {
        display: none !important;
      }

      /* ホチキス位置マーカー。fixed要素はChromium印刷で各ページに繰り返す */
      #print-staple-markers {
        display: none;
      }
      body[data-print-staple="true"] #print-staple-markers {
        display: block !important;
        visibility: visible !important;
        position: fixed !important;
        inset: 0 !important;
        z-index: 99999 !important;
        pointer-events: none !important;
      }
      #print-staple-markers .staple-mark {
        position: absolute !important;
        display: block !important;
        width: 5mm !important;
        height: 0 !important;
        border-top: 0.25mm solid #111 !important;
      }
      body[data-staple-edge="left"] #print-staple-markers .staple-mark:nth-child(1) {
        left: 1mm !important; top: 25% !important;
      }
      body[data-staple-edge="left"] #print-staple-markers .staple-mark:nth-child(2) {
        left: 1mm !important; top: 75% !important;
      }
      body[data-staple-edge="top"] #print-staple-markers .staple-mark:nth-child(1) {
        top: 1mm !important; left: 30% !important;
        transform: rotate(90deg) !important; transform-origin: left center !important;
      }
      body[data-staple-edge="top"] #print-staple-markers .staple-mark:nth-child(2) {
        top: 1mm !important; left: 70% !important;
        transform: rotate(90deg) !important; transform-origin: left center !important;
      }

      #wiki-page-container .sticky {
        position: static !important;
      }

      /* Infoboxを右上に固定し、本文を下側で全幅に回り込ませる */
      #wiki-page-container > .flex.space-x-6 {
        display: block !important;
      }

      #wiki-page-container .wiki-infobox {
        float: right !important;
        position: static !important;
        width: 320px !important;
        max-width: 48% !important;
        margin: 0 0 12px 24px !important;
        break-inside: avoid;
      }

      #wiki-page-container > .flex.space-x-6 > .flex-1 {
        display: block !important;
        width: auto !important;
        min-width: 0 !important;
      }

      #wiki-page-container img {
        max-width: 100% !important;
        break-inside: avoid;
      }

      #print-attribution {
        display: block !important;
        visibility: visible !important;
        margin-top: 12mm !important;
        padding-top: 3mm !important;
        border-top: 0.25mm solid #888 !important;
        color: #333 !important;
        font-size: 8pt !important;
        line-height: 1.5 !important;
        break-inside: avoid !important;
      }
      #print-attribution a {
        color: #222 !important;
        text-decoration: underline !important;
        overflow-wrap: anywhere !important;
      }

      #wiki-page-container,
      #wiki-page-container * {
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
      }
    }
  </style>
</head>
<body class="flex flex-col h-screen overflow-hidden select-text">

  <!-- トップナビバー -->
  <header class="bg-[#1f1a14] text-white px-5 py-2.5 flex justify-between items-center shadow-md select-none gap-3">
    <div class="flex items-center space-x-3">
      <div class="w-8 h-8 rounded-lg bg-[#b45309] flex items-center justify-center font-bold text-lg text-white shadow">
        ♾️
      </div>
      <div>
        <h1 class="text-sm font-bold tracking-wide">Backrooms Wiki (Fandom JP) <span class="ml-1 text-[10px] font-medium text-gray-400">v__VERSION__</span></h1>
        <p class="text-[10px] text-gray-400">Navigation & Database System</p>
      </div>
    </div>
    <div class="flex space-x-1 overflow-x-auto">
      <button onclick="switchTab('explorer')" id="tab-btn-explorer" class="px-4 py-1.5 rounded text-xs font-semibold bg-[#b45309] text-white transition shadow">📖 Level 図鑑・検索</button>
      <button onclick="switchTab('wiki')" id="tab-btn-wiki" class="px-4 py-1.5 rounded text-xs font-semibold bg-gray-800 text-gray-300 hover:bg-gray-700 transition">🌐 Wiki閲覧・保存</button>
      <button onclick="switchTab('data-tools')" id="tab-btn-data-tools" class="px-4 py-1.5 rounded text-xs font-semibold bg-gray-800 text-gray-300 hover:bg-gray-700 transition">⬇️ HTML取得・データ抽出</button>
      <button onclick="switchTab('routes')" id="tab-btn-routes" class="px-4 py-1.5 rounded text-xs font-semibold bg-gray-800 text-gray-300 hover:bg-gray-700 transition">🧭 経路・フロー</button>
      <button onclick="switchTab('settings')" id="tab-btn-settings" class="px-4 py-1.5 rounded text-xs font-semibold bg-gray-800 text-gray-300 hover:bg-gray-700 transition">⚙️ 環境設定</button>
      <button onclick="switchTab('ai')" id="tab-btn-ai" class="px-4 py-1.5 rounded text-xs font-semibold bg-gray-800 text-gray-300 hover:bg-gray-700 transition">🤖 AI Assistant</button>
      <button onclick="reloadApplicationData()" id="reload-application-data" class="px-3 py-1.5 rounded text-xs bg-[#66502f] text-white">Reload data</button>
      <button onclick="showAbout()" id="about-button" class="px-3 py-1.5 rounded text-xs font-semibold bg-gray-700 text-gray-200 hover:bg-gray-600 transition">アプリ情報</button>
    </div>
    <div class="flex shrink-0 items-center gap-2 text-[10px] text-gray-300">
      <div class="text-right leading-4"><div id="header-clock">時刻を取得中...</div><div><span id="header-network">接続確認中</span> · <span id="header-power">電源確認中</span></div></div>
      <button id="theme-toggle" onclick="toggleTheme()" title="テーマ切り替え" aria-label="テーマ切り替え" class="rounded px-2 py-1 text-lg hover:bg-gray-700">🌙</button>
    </div>
  </header>

  <div id="about-dialog" class="hidden fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4"
    onclick="if (event.target === this) closeAbout()">
    <section role="dialog" aria-modal="true" aria-labelledby="about-title"
      class="w-full max-w-lg bg-[#fbf6de] border border-[#d3c29a] rounded-lg shadow-xl overflow-hidden">
      <div class="px-5 py-3 bg-[#1f1a14] text-white flex items-center justify-between">
        <h2 id="about-title" class="font-bold">アプリケーション情報</h2>
        <button onclick="closeAbout()" class="px-2 py-1 rounded hover:bg-gray-700" aria-label="閉じる">×</button>
      </div>
      <div class="p-5 space-y-4 text-sm">
        <dl class="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-2">
          <dt class="text-gray-500">アプリ名</dt><dd id="about-name" class="font-medium"></dd>
          <dt class="text-gray-500">バージョン</dt><dd id="about-version" class="font-medium"></dd>
          <dt class="text-gray-500">種別</dt><dd id="about-type" class="font-medium"></dd>
          <dt class="text-gray-500">LLMプロバイダー</dt><dd id="about-provider" class="font-medium"></dd>
          <dt class="text-gray-500">モデル</dt><dd id="about-model" class="font-mono text-xs break-all"></dd>
        </dl>
        <section class="border-t border-[#d3c29a] pt-3">
          <h3 class="font-semibold mb-2">リンク・ライセンス</h3>
          <p class="text-xs text-gray-600 mb-2">個人開発の非公式ツールです。Fandom, Inc.およびBackrooms Wiki運営とは関係ありません。Wiki記事の文章は原則CC BY-SA 3.0ですが、個別ページ・画像には異なる条件や第三者素材が含まれる場合があります。</p>
          <div class="flex flex-wrap gap-2">
            <button onclick="openAboutLink('repository')" class="px-3 py-1 rounded border border-[#d3c29a] bg-white hover:bg-[#f2e8c5] text-xs">GitHubリポジトリ</button>
            <button onclick="openAboutLink('releases')" class="px-3 py-1 rounded border border-[#d3c29a] bg-white hover:bg-[#f2e8c5] text-xs">更新情報</button>
            <button onclick="openAboutLink('license')" class="px-3 py-1 rounded border border-[#d3c29a] bg-white hover:bg-[#f2e8c5] text-xs">MIT License</button>
            <button onclick="openAboutLink('cc_by_sa')" class="px-3 py-1 rounded border border-[#d3c29a] bg-white hover:bg-[#f2e8c5] text-xs">CC BY-SA 3.0</button>
            <button onclick="openAboutLink('third_party')" class="px-3 py-1 rounded border border-[#d3c29a] bg-white hover:bg-[#f2e8c5] text-xs">第三者ライセンス</button>
          </div>
        </section>
        <p id="about-link-status" class="min-h-4 text-xs text-red-700" aria-live="polite"></p>
        <div class="flex justify-end">
          <button onclick="closeAbout()" class="px-4 py-1.5 rounded bg-[#91520f] text-white hover:bg-[#75410b]">閉じる</button>
        </div>
      </div>
    </section>
  </div>

  <div id="image-lightbox" class="hidden fixed inset-0 z-[120] flex items-center justify-center bg-black/90 p-4" onclick="if(event.target===this) closeImageLightbox()" role="dialog" aria-modal="true" aria-label="画像拡大表示">
    <button onclick="closeImageLightbox()" aria-label="閉じる" class="absolute right-5 top-4 rounded bg-black/50 px-3 py-1 text-2xl text-white">×</button>
    <button onclick="stepImageLightbox(-1)" aria-label="前の画像" class="absolute left-3 rounded bg-black/50 px-4 py-3 text-2xl text-white">←</button>
    <figure class="max-h-full max-w-[88vw] text-center"><img id="lightbox-image" class="mx-auto max-h-[78vh] max-w-full object-contain" alt=""><figcaption id="lightbox-caption" class="mt-3 text-sm text-white"></figcaption><button id="lightbox-open-level" onclick="openLightboxLevel()" class="mt-3 rounded bg-amber-500 px-3 py-1.5 text-xs font-semibold text-black">このLevelの記事を開く</button></figure>
    <button onclick="stepImageLightbox(1)" aria-label="次の画像" class="absolute right-3 rounded bg-black/50 px-4 py-3 text-2xl text-white">→</button>
  </div>

  <main class="flex-1 flex overflow-hidden">

    <!-- 1. 📖 Level 図鑑 ＆ 検索タブ -->
    <div id="view-explorer" class="flex-1 flex overflow-hidden">

      <!-- 左ペイン：予測検索とカテゴリ別リンクインデックス -->
      <aside class="w-80 border-r border-[#e2d8b5] bg-[#f7f0d4] flex flex-col select-none">
        <div class="relative z-20 p-3 border-b border-[#e2d8b5] bg-[#f2e8c5]">
          <input type="search" id="search-input" role="combobox" aria-autocomplete="list" aria-controls="level-search-suggestions" aria-expanded="false" placeholder="階層・副題・別名・カテゴリを検索..."
            oninput="onSearchInput()" onfocus="showLevelSearchSuggestions()"
            onkeydown="onLevelSearchKeydown(event)" autocomplete="off"
            class="w-full px-3 py-2 bg-white border border-[#d6c79a] rounded text-xs focus:outline-none focus:border-[#b45309] shadow-inner">
          <div id="level-search-suggestions" role="listbox" class="hidden absolute left-3 right-3 top-full max-h-80 overflow-y-auto rounded-b-lg border border-[#d6c79a] bg-white shadow-xl"></div>
        </div>
        <div class="px-3 py-1.5 text-[11px] font-bold text-gray-600 bg-[#eee3bc] border-b border-[#e2d8b5] flex justify-between">
          <span>階層の一覧</span>
          <span><strong id="candidate-count">0</strong> 件</span>
        </div>
        <div id="candidate-list" class="flex-1 overflow-y-auto p-3 text-xs"></div>
      </aside>

      <!-- 右ペイン：本家Wiki風の一覧ハブ・番号一覧・記事詳細 -->
      <section class="flex-1 overflow-y-auto p-8 bg-[#fbf6de]">
        <div class="mx-auto mb-4 flex max-w-6xl gap-2 border-b border-[#e2d8b5] pb-2">
          <button id="explorer-subtab-article" onclick="switchExplorerSubtab('article')" class="rounded px-3 py-1.5 text-sm font-semibold bg-[#b45309] text-white">記事・図鑑ビュー</button>
          <button id="explorer-subtab-gallery" onclick="switchExplorerSubtab('gallery')" class="rounded px-3 py-1.5 text-sm font-semibold bg-[#eee3bc]">画像ギャラリー</button>
        </div>
        <div id="explorer-article-tabs" class="mx-auto mb-3 max-w-6xl overflow-hidden rounded-t-xl border border-[#d4c39b] bg-[#e9ddb9] shadow-sm" aria-label="Article browser">
          <div class="flex min-w-0 items-end gap-1 px-2 pt-2">
            <div id="explorer-tab-strip" class="flex min-w-0 flex-1 items-end gap-1 overflow-x-auto" role="tablist" aria-label="Article tabs"></div>
            <button type="button" onclick="createExplorerTab()" title="New tab" aria-label="New tab" class="mb-0.5 h-8 w-8 shrink-0 rounded-lg text-lg text-[#48515d] hover:bg-white">+</button>
          </div>
          <div class="flex items-center gap-1 border-t border-[#d4c39b] bg-[#f4ebd3] px-3 py-2">
            <button type="button" id="explorer-history-back" onclick="goExplorerTabBack()" disabled title="Back" aria-label="Back" class="h-8 w-8 shrink-0 rounded-full text-lg text-[#765b31] hover:bg-[#eadbb7] disabled:opacity-30">&#8592;</button>
            <button type="button" id="explorer-history-forward" onclick="goExplorerTabForward()" disabled title="Forward" aria-label="Forward" class="h-8 w-8 shrink-0 rounded-full text-lg text-[#765b31] hover:bg-[#eadbb7] disabled:opacity-30">&#8594;</button>
            <button type="button" onclick="reloadExplorerTab()" title="Reload" aria-label="Reload" class="h-8 w-8 shrink-0 rounded-full text-[#765b31] hover:bg-[#eadbb7]">&#8635;</button>
            <div class="ml-2 flex min-w-0 flex-1 items-center gap-2 rounded-full border border-[#ddcfaa] bg-[#fbf5e5] px-3 py-1.5 text-xs text-[#765f3c]">
              <span class="shrink-0 text-[#54845b]">&#9679;</span>
              <span id="explorer-address" class="min-w-0 flex-1 truncate">Backrooms Wiki</span>
            </div>
          </div>
        </div>
        <div id="explorer-article-view">
        <div id="explorer-index-home" class="mx-auto max-w-5xl">
          <div class="mb-5 text-center">
            <div class="text-xs text-[#8c6b12]">Backrooms Wiki — 一覧</div>
            <h2 class="mt-1 text-3xl font-semibold text-[#3f3828]">階層の一覧</h2>
            <p class="mt-2 text-sm text-gray-600">カテゴリを選ぶと、番号帯ごとの一覧を開きます。</p>
          </div>
          <div id="explorer-category-tiles" class="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4" aria-label="階層カテゴリ一覧"></div>
        </div>

        <div id="explorer-index-local-saved" class="hidden mx-auto max-w-6xl">
          <div class="mb-4 flex items-center gap-3 border-b border-[#d8c99e] pb-3">
            <button type="button" onclick="showExplorerIndexHome()" class="shrink-0 rounded px-3 py-1.5 text-sm font-semibold text-[#91520f] hover:bg-[#fff7dd]">‹ 一覧へ戻る</button>
            <div class="min-w-0 flex-1"><div class="text-xs text-[#8c6b12]">ローカルアーカイブ</div><h2 class="text-2xl font-semibold text-[#3f3828]">ローカル保存済み階層の一覧</h2></div>
            <span id="local-saved-level-count" class="text-xs text-gray-500"></span>
          </div>
          <div id="local-saved-level-list" class="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3"></div>
        </div>

        <div id="explorer-index-category" class="hidden mx-auto max-w-6xl">
          <div class="mb-4 flex items-center gap-3 border-b border-[#d8c99e] pb-3">
            <button type="button" onclick="showExplorerIndexHome()" class="shrink-0 rounded px-3 py-1.5 text-sm font-semibold text-[#91520f] hover:bg-[#fff7dd]">‹ 一覧へ戻る</button>
            <div class="min-w-0 flex-1">
              <div class="text-xs text-[#8c6b12]">Backrooms Wiki — 階層の一覧</div>
              <h2 id="explorer-index-category-title" class="text-2xl font-semibold text-[#3f3828]"></h2>
            </div>
            <span id="explorer-index-total" class="text-xs text-gray-500"></span>
          </div>
          <div class="flex items-start gap-5">
            <div id="explorer-index-sections" class="min-w-0 flex-1 rounded border border-[#e2d8b5] bg-[#fffdf5] px-5 py-3"></div>
            <aside class="sticky top-3 hidden w-64 shrink-0 rounded border border-[#d6c79a] bg-[#fff9df] p-3 lg:block">
              <h3 class="mb-2 border-b border-[#e5d9b9] pb-2 text-sm font-bold">目次</h3>
              <nav id="explorer-index-toc" class="max-h-[75vh] overflow-y-auto text-xs leading-6 text-gray-700"></nav>
            </aside>
          </div>
        </div>

        <div id="wiki-page-container" class="hidden max-w-4xl mx-auto bg-white border border-[#e2d8b5] p-8 rounded shadow-sm relative">
          <button type="button" onclick="showExplorerIndexHome()" class="print-exclude mb-4 rounded px-2 py-1 text-xs font-semibold text-[#91520f] hover:bg-[#fff7dd]">‹ 階層の一覧へ戻る</button>

          <div class="text-xs text-[#8c6b12] mb-1 font-medium">
            分類: <span id="view-category">階層</span>、危険度: <span id="view-danger-tag">-</span>
          </div>

          <div class="flex justify-between items-start border-b border-[#e2d8b5] pb-3 mb-6">
            <div>
              <h2 id="view-level-id" class="text-2xl font-bold text-[#202124]">Level 0 η</h2>
              <div id="view-meta-title" class="text-base font-semibold text-[#b45309] mt-0.5"></div>
            </div>
            <div class="print-exclude flex space-x-2 select-none">
              <button onclick="refreshCurrentSavedHtml()" class="px-2.5 py-1 text-xs bg-[#f4e7b8] hover:bg-[#e8d89f] text-[#784f00] rounded font-medium border border-[#d9c78d]">HTML再確認・抽出</button>
              <button onclick="openCurrentFandomInApp()" class="px-2.5 py-1 text-xs bg-[#f4e7b8] hover:bg-[#e8d89f] text-[#784f00] rounded font-medium border border-[#d9c78d]">🌐 内蔵ブラウザーでFandomを開く</button>
              <button onclick="pywebview.api.open_local_browser_html()" class="px-2.5 py-1 text-xs bg-gray-100 hover:bg-gray-200 text-gray-700 rounded font-medium border border-gray-300">📄 ローカルHTML</button>
              <button type="button" onclick="togglePrintOptions()" class="px-2.5 py-1 text-xs bg-[#e8e0c8] hover:bg-[#d9cfb0] text-gray-800 rounded font-medium border border-[#cfc3a0]">🖨 印刷</button>
            </div>
          </div>

          <div id="print-options-panel" class="print-exclude hidden mb-5 rounded border border-[#d6c79a] bg-[#fffaf0] p-3 text-sm">
            <div class="mb-2 font-semibold text-gray-800">印刷オプション</div>
            <label class="flex items-center gap-2 mb-3">
              <input id="print-staple-enabled" type="checkbox" checked class="accent-amber-700">
              <span>ホチキス位置マーカーを印字する</span>
            </label>
            <label class="flex items-center gap-2 mb-3">
              <span class="shrink-0">マーカー位置</span>
              <select id="print-staple-edge" class="rounded border border-gray-300 px-2 py-1 text-sm">
                <option value="left">左側（A4縦・長辺綴じ）</option>
                <option value="top">上側（A4横・長辺綴じ）</option>
              </select>
            </label>
            <div class="flex gap-2">
              <button type="button" onclick="printLevelArticle()" class="rounded bg-[#91520f] px-3 py-1.5 text-xs font-semibold text-white">印刷ダイアログを開く</button>
              <button type="button" onclick="togglePrintOptions(false)" class="rounded border border-gray-300 px-3 py-1.5 text-xs">閉じる</button>
            </div>
          </div>

          <div id="explorer-unsaved-actions" class="hidden mb-5 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
            <p class="mb-2">この記事はまだローカルに保存されていません。内蔵ブラウザーで開き、表示中の記事を保存できます。</p>
            <button type="button" onclick="openCurrentFandomInApp()" class="rounded bg-[#91520f] px-3 py-1.5 text-xs font-semibold text-white">内蔵ブラウザーで開いて保存</button>
          </div>

          <!-- 本文 ＆ Infobox -->
          <div id="print-staple-markers" aria-hidden="true"><span class="staple-mark"></span><span class="staple-mark"></span></div>
          <div class="flex space-x-6 items-start">

            <!-- 🌟 Wiki Infobox (右側情報カード) -->
            <aside class="wiki-infobox order-2 rounded overflow-hidden select-none sticky top-0">
              <div id="infobox-title" class="wiki-infobox-header">Level 0 η</div>
              
              <!-- 写真 -->
              <div class="p-3 bg-[#fdfaf0] text-center border-b border-[#e5dcbe]">
                <img id="infobox-main-img" src="" alt="Level Image" class="w-full h-48 object-cover rounded border border-[#d6c99c] hidden">
                <div id="infobox-no-img" class="h-40 flex items-center justify-center text-xs text-gray-400 border border-dashed border-gray-300 rounded">
                  ※ 写真データなし
                </div>
                <p id="infobox-caption" class="text-[11px] text-gray-500 mt-2 italic truncate"></p>
              </div>

              <!-- パラメータ表 -->
              <table>
                <tr id="infobox-danger-row"><th>危険度</th><td id="infobox-danger" class="font-bold text-[#b45309]">-</td></tr>
                <tr id="infobox-comprehension-row"><th>理解度</th><td id="infobox-comprehension">-</td></tr>
                <tr><th>カテゴリ</th><td id="infobox-category">-</td></tr>
                <tr><th>言語</th><td id="infobox-lang">-</td></tr>
              </table>
              <div id="infobox-special-fields" class="hidden border-t border-[#e5dcbe]"></div>
            </aside>

            <!-- 本文エリア (左) -->
            <div class="flex-1 order-1 text-sm leading-relaxed text-gray-800">
              <div id="translation-article-banner" class="hidden mb-4 rounded border border-[#d9c58c] bg-[#fff8df] p-3 text-sm text-[#654b20]"></div>
              <div id="article-attribution" class="hidden mb-4 border-b border-[#ded5bd] pb-3 text-xs leading-relaxed text-gray-500"></div>
              <div id="view-article-content" class="space-y-3">
                読み込み中...
              </div>

              <!-- 画像ギャラリー -->
              <div id="gallery-container" class="mt-6 pt-4 border-t border-[#e2d8b5] hidden">
                <div class="wiki-heading">
                  <span>記録写真ギャラリー</span>
                </div>
                <div id="view-image-gallery" class="grid grid-cols-2 gap-4 pt-2"></div>
              </div>
            </div>

          </div>
          <footer id="print-attribution" class="text-xs text-gray-600">
            <p class="font-semibold mb-1">出典・ライセンス情報</p>
            <p>出典記事：<span id="print-source-title"></span></p>
            <p>元記事URL：<a id="print-source-link" class="break-all underline" target="_blank" rel="noopener noreferrer"></a></p>
            <p>記事本文のライセンス（原則）：<a href="https://creativecommons.org/licenses/by-sa/3.0/deed.ja">クリエイティブ・コモンズ 表示-継承 3.0（CC BY-SA 3.0）</a></p>
            <p>個別ページ・画像・第三者素材には異なるライセンス条件が適用される場合があります。各素材の権利表示も確認してください。</p>
            <p class="mt-1">BrNavi Fandom JP は個人開発の非公式ツールです。Fandom, Inc.およびBackrooms Wiki運営とは関係ありません。</p>
          </footer>
        </div>
        </div>
        <section id="explorer-gallery-view" class="hidden mx-auto max-w-6xl">
          <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div><h2 class="text-2xl font-semibold">画像ギャラリー</h2><p class="text-xs text-gray-600">保存済み画像からLevelを逆引きできます。</p></div>
            <div class="flex flex-wrap items-center gap-2">
              <input id="local-gallery-filter" oninput="filterLocalImageGallery()" type="search" class="w-56 rounded border px-3 py-2 text-sm" placeholder="Level名・画像名で検索">
              <button id="legacy-image-rename-button" onclick="previewLegacyImageRename()" class="rounded border border-[#b98b36] bg-[#fff8df] px-3 py-2 text-xs font-semibold text-[#74440b]">旧画像名を移行</button>
            </div>
          </div>
          <div id="legacy-image-rename-status" class="mb-3 text-xs text-gray-600"></div>
          <div id="local-gallery-count" class="mb-3 text-xs text-gray-500"></div>
          <div id="local-gallery-grid" class="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4"></div>
        </section>
      </section>

    </div>

    <!-- 2. 🌐 Wiki閲覧・保存タブ -->
    <div id="view-wiki" class="hidden flex-1 min-h-0 flex flex-col p-3 bg-[#fbf6de] overflow-hidden">
      <div class="w-full flex-1 min-h-0 flex flex-col bg-white border border-[#e2d8b5] p-3 rounded shadow-sm gap-2">
        <div class="flex items-center justify-between gap-3">
          <div class="min-w-0">
            <h2 class="text-base font-bold text-gray-800">🌐 日本語版Backrooms Wiki</h2>
            <p class="text-[11px] text-gray-500">ページ内リンクをたどって自由に閲覧できます。</p>
          </div>
          <span id="html-download-status" class="shrink-0 text-[11px] text-gray-600">閲覧・保存にはインターネット接続が必要です。</span>
        </div>
        <div class="flex gap-2">
          <input id="html-download-search" type="search"
            onkeydown="if(event.key==='Enter') searchOnlineWiki()"
            class="min-w-0 flex-1 border border-[#d6c79a] rounded px-3 py-1.5 text-xs"
            placeholder="Wikiページ名・キーワードでオンライン検索">
          <button id="html-download-search-button" onclick="searchOnlineWiki()"
            class="shrink-0 px-4 py-1.5 text-xs font-semibold bg-[#b45309] text-white rounded disabled:opacity-50">
            検索
          </button>
          <input id="html-download-tag-search" type="search" oninput="filterOnlineWikiResults()"
            class="w-56 border border-[#d6c79a] rounded px-3 py-1.5 text-xs"
            placeholder="タグで絞り込み">
        </div>
        <div id="html-download-results" class="hidden shrink-0 border border-[#dfd3a8] rounded p-2 bg-[#fffdf5]">
          <div class="flex items-center justify-between text-[11px] text-gray-600 mb-1">
            <span id="html-download-count">検索結果</span>
            <button type="button" onclick="clearOnlineWikiResults()" class="text-gray-500 hover:text-gray-800">結果を閉じる</button>
          </div>
          <div id="html-download-grid" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5 max-h-28 overflow-y-auto pr-1"></div>
        </div>
        <div class="flex items-center gap-2">
          <button type="button" onclick="navigateOnlineWikiHistory(-1)" title="Back" class="rounded border px-2 py-1">←</button>
          <button type="button" onclick="navigateOnlineWikiHistory(1)" title="Forward" class="rounded border px-2 py-1">→</button>
          <button type="button" onclick="reloadOnlineWikiFrame()" title="Reload" class="rounded border px-2 py-1">↻</button>
          <input id="online-wiki-url" type="url"
            onkeydown="if(event.key==='Enter') openDirectWikiUrl()"
            class="min-w-0 flex-1 border border-[#d6c79a] rounded px-3 py-1.5 text-xs"
            value="https://backrooms.fandom.com/ja/wiki/Backrooms_Wiki"
            placeholder="https://backrooms.fandom.com/ja/wiki/...">
          <button onclick="openDirectWikiUrl()"
            class="shrink-0 px-3 py-1.5 text-xs font-semibold bg-[#91520f] text-white rounded">
            移動
          </button>
          <button onclick="resetOnlineWikiHome()"
            class="shrink-0 px-3 py-1.5 text-xs font-semibold border border-[#d6c79a] bg-[#f7f0d4] rounded">ホーム</button>
          <button id="online-wiki-redownload-current" onclick="redownloadCurrentOnlineWikiPage()" disabled class="shrink-0 px-3 py-1.5 text-xs font-semibold border border-[#d6c79a] bg-[#fff8df] rounded disabled:opacity-50">上書き再取得</button>
          <button id="online-wiki-save-current" onclick="saveCurrentOnlineWikiPage()" disabled
            class="shrink-0 px-3 py-1.5 text-xs font-semibold bg-[#b45309] text-white rounded disabled:opacity-50">
            表示中を保存
          </button>
          <span id="online-wiki-save-status" class="shrink-0 text-[11px] text-gray-600">保存先: downloaded_fandom_html</span>
        </div>
        <div class="flex-1 min-h-0 overflow-hidden rounded border border-[#d6c79a] bg-white">
          <iframe id="online-wiki-frame" onload="onOnlineWikiFrameLoaded()"
            title="日本語版Backrooms Wiki"
            class="w-full h-full border-0 bg-white"
            referrerpolicy="strict-origin-when-cross-origin"></iframe>
        </div>
        <div class="flex items-center justify-between gap-2 text-[10px] text-gray-500">
          <span id="online-wiki-current-url" class="min-w-0 truncate">トップページを読み込みます...</span>
          <span>表示・閲覧にはFandomの利用規約が適用されます。</span>
        </div>
      </div>
    </div>

    <!-- 3. ⬇️ レベルリスト基準のHTML取得・データ抽出 -->
    <section id="view-data-tools" class="hidden flex-1 min-h-0 flex flex-col p-4 bg-[#fbf6de] overflow-hidden">
      <div class="w-full max-w-6xl mx-auto flex-1 min-h-0 flex flex-col bg-white border border-[#e2d8b5] p-4 rounded shadow-sm gap-3">
        <div class="flex items-center justify-between gap-3">
          <div>
            <h2 class="text-base font-bold text-gray-800">階層の一覧</h2>
            <p class="text-[11px] text-gray-500">Wikiのカテゴリから一覧を選び、階層番号順に閲覧できます。</p>
          </div>
          <span id="level-list-status" class="text-[11px] text-gray-600" aria-live="polite">レベルリストを読み込み中...</span>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <label for="level-list-language" class="text-xs font-semibold text-gray-700">リスト</label>
          <select id="level-list-language" onchange="loadLevelListPages()"
            class="min-w-24 border border-[#d6c79a] rounded px-2 py-1.5 text-xs bg-white"></select>
          <input id="level-list-filter" type="search" oninput="filterLevelListTiles()"
            style="display: none;"
            class="min-w-48 flex-1 border border-[#d6c79a] rounded px-3 py-1.5 text-xs"
            placeholder="この一覧を検索">
          <label class="flex items-center gap-1.5 text-xs text-gray-700">
            <input id="level-list-overwrite" type="checkbox">
            取得済みHTMLを上書き
          </label>
        </div>
        <div id="level-list-categories" class="flex-1 min-h-0 overflow-y-auto py-2"
          style="display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); align-content: start; gap: 1rem;"
          aria-label="階層カテゴリ一覧"></div>
        <div id="level-list-browser" class="flex-1 min-h-0 flex-col gap-3" style="display: none;">
          <div class="flex items-center gap-3 border-b border-[#e2d8b5] pb-2">
            <button type="button" onclick="showLevelListCategories()"
              class="shrink-0 rounded px-3 py-1.5 text-xs font-semibold text-[#91520f] hover:bg-[#fff7dd]">‹ 一覧へ戻る</button>
            <div class="min-w-0 flex-1">
              <h3 id="level-list-category-title" class="text-sm font-bold text-gray-800"></h3>
              <span id="level-list-total" class="text-[11px] text-gray-500"></span>
            </div>
          </div>
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="text-xs text-gray-700">
              <span id="level-list-selection-count" aria-live="polite">0件選択中</span>
            </div>
            <div class="flex flex-wrap gap-2">
              <button id="level-list-download-button" onclick="runLevelListOfflineSave()" disabled
                class="px-3 py-1.5 rounded bg-[#b45309] text-white text-xs font-semibold disabled:opacity-40">Level情報のオフライン保存</button>
              <button id="level-list-clear-selection" onclick="clearLevelListSelection()" disabled
                class="px-3 py-1.5 rounded border border-[#d6c79a] text-xs disabled:opacity-40">選択解除</button>
              <button id="level-list-cancel-button" onclick="cancelMaintenanceTask()" disabled
                class="px-3 py-1.5 rounded bg-red-700 text-white text-xs disabled:opacity-40">処理停止</button>
            </div>
          </div>
          <div class="flex flex-1 min-h-0 gap-4">
            <div id="level-list-grid" class="flex flex-col flex-1 min-w-0 min-h-0 overflow-y-auto border-y border-[#e2d8b5] select-none"
              style="display: flex; flex-direction: column; flex: 1 1 0%; min-width: 0; min-height: 0; overflow-y: auto;"
              aria-label="階層リスト"></div>
            <nav id="level-list-toc" class="hidden w-56 shrink-0 overflow-y-auto rounded border border-[#e2d8b5] p-3 text-xs"
              aria-label="一覧目次"></nav>
          </div>
        </div>
        <div class="border-t border-[#e2d8b5] pt-2 space-y-2">
          <div id="level-list-task-status" class="text-xs text-gray-600" role="status" aria-live="polite">対象を選択して「Level情報のオフライン保存」を実行してください。</div>
          <div class="flex items-center gap-2">
            <progress id="level-list-progress" max="100" value="0" class="hidden flex-1 h-2"></progress>
            <span id="level-list-progress-label" class="text-[11px] text-gray-500">待機中</span>
          </div>
          <pre id="level-list-log" class="max-h-32 overflow-auto whitespace-pre-wrap break-words rounded border border-[#e2d8b5] bg-[#fffdf5] p-2 text-[10px] text-gray-700"></pre>
        </div>
      </div>
    </section>

    <!-- 4. 🧭 経路・視覚フロー -->
    <section id="view-routes" class="hidden flex-1 min-h-0 flex flex-col p-4 bg-[#fbf6de] overflow-y-auto">
      <div class="w-full max-w-6xl mx-auto bg-white border border-[#e2d8b5] p-5 rounded shadow-sm space-y-4">
        <div class="flex flex-wrap justify-between items-start gap-3"><div><h2 class="text-lg font-bold">🧭 Level経路と視覚フロー</h2><p class="text-xs text-gray-600 mt-1">ローカルの有向接続CSVから経路候補を検索します。経路が見つからない場合は環境設定から接続CSVを生成してください。</p></div>
          <button onclick="switchTab('settings'); runTask('update_network')" class="px-3 py-2 rounded bg-[#f5eed3] border border-[#d6c79a] text-xs font-semibold">接続CSVを生成・更新</button></div>
        <div class="flex flex-wrap items-end gap-3"><label class="text-xs font-semibold">出発Level<input id="route-start" class="block mt-1 border border-[#d6c79a] rounded px-3 py-2 text-sm" placeholder="Level 20"></label>
          <span class="pb-2 text-gray-400">→</span><label class="text-xs font-semibold">目的Level<input id="route-goal" class="block mt-1 border border-[#d6c79a] rounded px-3 py-2 text-sm" placeholder="Level 4"></label>
          <button onclick="searchRoutes()" class="px-4 py-2 rounded bg-[#b45309] text-white text-xs font-semibold">経路を検索</button><span id="route-status" class="text-xs text-gray-600" role="status"></span></div>
        <div class="flex flex-wrap gap-x-4 gap-y-2 rounded border border-[#e2d8b5] bg-[#fffdf5] p-3 text-xs text-gray-700">
          <label class="flex items-center gap-2"><input id="route-include-uncertain" type="checkbox" checked>不確実な接続を含める</label>
          <label class="flex items-center gap-2"><input id="route-include-unknown" type="checkbox" checked>状態不明を含める</label>
          <label class="flex items-center gap-2"><input id="route-include-rumor" type="checkbox">噂を含める</label>
          <label class="flex items-center gap-2"><input id="route-include-closed" type="checkbox">閉鎖・消失済みを含める</label>
          <label class="flex items-center gap-2"><input id="route-include-review" type="checkbox" checked>要確認データを含める</label>
        </div>
        <p class="text-[11px] text-gray-500">初期設定では Unknown / Uncertain / NeedsReview も参考情報として検索します。結果は到達可能性を保証しません。各接続の確実性・利用可否・審査状態を確認してください。Rumor、Closed / Disappeared は初期状態で除外し、False / Meta / Rejected は常に除外します。</p>
        <div id="route-results-list" class="space-y-3"><div id="route-results" class="space-y-4"></div></div>
        <div id="route-detail-view" class="hidden space-y-3">
          <button type="button" onclick="showRouteResults()" class="rounded border border-[#d6c79a] px-3 py-2 text-sm font-semibold hover:bg-amber-50">← 検索結果一覧へ戻る</button>
          <div id="route-detail-content" class="rounded-lg border border-[#e2d8b5] bg-[#fffdf5] p-4"></div>
        </div>
      </div>
    </section>

    <!-- 5. ⚙️ 環境設定 -->
    <section id="view-settings" class="hidden flex-1 flex flex-col p-8 bg-[#fbf6de] overflow-y-auto">
      <div class="max-w-3xl mx-auto w-full bg-white border border-[#e2d8b5] p-6 rounded shadow-sm space-y-5">
        <h2 class="text-lg font-bold text-gray-800 border-b border-gray-200 pb-2">⚙️ 環境設定</h2>
        <section class="space-y-3">
          <h3 class="text-sm font-bold text-gray-800">一般設定</h3>
          <label class="flex items-start gap-2 text-xs text-gray-700">
            <input id="auto-update-catalog" type="checkbox" class="mt-0.5">
            <span><b>Level一覧データを自動更新</b><br>初回起動時と前回の更新試行から1か月後以降に、一覧HTMLを取得してデータベースを更新します。更新はバックグラウンドで行います。</span>
          </label>
          <p id="catalog-update-history" class="text-[11px] text-gray-500">最終更新状態を確認中...</p>
        </section>
        <section class="space-y-3 border-t border-[#e2d8b5] pt-4">
          <h3 class="text-sm font-bold text-gray-800">表示・時刻</h3>
          <div class="flex flex-wrap items-center gap-3">
            <button type="button" onclick="toggleTheme()" class="rounded border border-[#d6c79a] bg-[#fffdf5] px-3 py-2 text-xs font-semibold"><span id="settings-theme-icon">🌙</span> テーマ切り替え</button>
            <label class="text-xs">タイムゾーン
              <select id="timezone-select" class="ml-2 rounded border px-2 py-1.5" onchange="saveTimezone(this.value)">
                <option value="Asia/Tokyo">Asia/Tokyo (UTC+09:00 / JST - 日本標準時)</option>
                <option value="BRT">Backrooms Standard Time (UTC+00:00 / BRT)</option>
                <option value="UTC">UTC (協定世界時)</option>
                <option value="America/New_York">America/New_York (EST/EDT)</option>
                <option value="Europe/London">Europe/London (GMT/BST)</option>
                <option value="Asia/Seoul">Asia/Seoul (KST)</option>
                <option value="Asia/Shanghai">Asia/Shanghai (CST)</option>
              </select>
            </label>
          </div>
        </section>
        <section class="space-y-3 border-t border-[#e2d8b5] pt-4">
          <div class="flex items-center justify-between"><h3 class="text-sm font-bold text-gray-800">記憶域（ストレージ）状況</h3><button onclick="refreshStorageSummary()" class="rounded border border-[#d6c79a] px-3 py-1.5 text-xs">記憶域を再計測</button></div>
          <div id="storage-summary" class="space-y-2 text-xs text-gray-600">計測中...</div>
        </section>
        <section class="space-y-3 border-t border-[#e2d8b5] pt-4">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h3 class="text-sm font-bold text-gray-800">保存済みデータ管理・クリーンアップ</h3>
            <button type="button" onclick="refreshStorageSummary()" class="rounded border border-[#d6c79a] px-3 py-1.5 text-xs">対象件数を再計測</button>
          </div>
          <p class="text-xs leading-relaxed text-gray-600">アプリが取得・生成したローカルデータだけをカテゴリ別に削除できます。設定・APIキー・ソースコード・ドキュメントは対象外です。削除前に実際のファイル一覧を確認します。</p>
          <div class="grid gap-2 sm:grid-cols-2">
            <label class="flex items-start gap-2 rounded border border-[#e2d8b5] bg-[#fffdf5] p-3 text-xs">
              <input id="cleanup-category-articles" data-cleanup-category="articles" type="checkbox" checked class="mt-0.5">
              <span><b>記事HTML・抽出データ・画像</b><span class="mt-1 block text-gray-500">保存記事HTML、画像、個別TXT、統合TXT、Summary CSV</span></span>
            </label>
            <label class="flex items-start gap-2 rounded border border-[#e2d8b5] bg-[#fffdf5] p-3 text-xs">
              <input id="cleanup-category-index" data-cleanup-category="index" type="checkbox" checked class="mt-0.5">
              <span><b>一覧インデックス・Master DB</b><span class="mt-1 block text-gray-500">一覧HTML、parsed_index、Level Lists、旧一覧</span></span>
            </label>
            <label class="flex items-start gap-2 rounded border border-[#e2d8b5] bg-[#fffdf5] p-3 text-xs">
              <input id="cleanup-category-routes" data-cleanup-category="routes" type="checkbox" checked class="mt-0.5">
              <span><b>経路CSV</b><span class="mt-1 block text-gray-500">Nodes / Edges / Evidenceの正規3ファイルを一括削除</span></span>
            </label>
            <label class="flex items-start gap-2 rounded border border-amber-300 bg-amber-50 p-3 text-xs">
              <input id="cleanup-category-ai" data-cleanup-category="ai" type="checkbox" class="mt-0.5">
              <span><b>AI会話履歴・移行バックアップ</b><span class="mt-1 block text-gray-600">会話DBと移行バックアップ。AI設定JSONは保護されます。</span></span>
            </label>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <button type="button" onclick="setAllCleanupCategories(true)" class="rounded border border-[#d6c79a] px-3 py-1.5 text-xs">全カテゴリを選択</button>
            <button type="button" onclick="setAllCleanupCategories(false)" class="rounded border border-[#d6c79a] px-3 py-1.5 text-xs">選択解除</button>
            <button id="cleanup-preview-button" type="button" onclick="previewCleanupData()" class="rounded bg-[#91520f] px-4 py-2 text-xs font-semibold text-white">削除対象を確認</button>
          </div>
          <div id="cleanup-status" role="status" aria-live="polite" class="hidden rounded border border-[#dfd3a8] bg-[#fffdf5] p-3 text-xs text-gray-700"></div>
          <div id="cleanup-preview" class="hidden space-y-3 rounded border border-[#d6c79a] bg-[#fffdf5] p-3">
            <div id="cleanup-preview-summary" class="space-y-1 text-xs text-gray-700"></div>
            <div class="max-h-64 overflow-auto rounded border border-[#e2d8b5] bg-white p-2">
              <ul id="cleanup-preview-items" class="space-y-1 font-mono text-[11px] text-gray-700"></ul>
            </div>
            <p class="text-[11px] leading-relaxed text-rose-800">上記に表示されたファイルだけが削除対象です。リンク・ジャンクション・未知の保存先など安全性を確認できない対象がある場合、プレビューまたは削除を中止します。</p>
            <div class="flex flex-wrap gap-2">
              <button id="cleanup-final-step-button" type="button" onclick="openCleanupConfirmation()" class="rounded bg-red-700 px-4 py-2 text-xs font-semibold text-white">最終確認へ進む</button>
              <button type="button" onclick="clearCleanupPreview()" class="rounded border border-[#d6c79a] px-4 py-2 text-xs">閉じる</button>
            </div>
          </div>
          <div id="cleanup-confirmation-dialog" class="hidden fixed inset-0 z-[120] items-center justify-center bg-black/60 p-4" role="dialog" aria-modal="true" aria-labelledby="cleanup-confirmation-title">
            <div class="w-full max-w-lg space-y-4 rounded-lg border-2 border-red-700 bg-[#fffdf5] p-5 shadow-2xl">
              <div class="flex items-center gap-2 text-red-800"><span class="text-2xl">⚠</span><h3 id="cleanup-confirmation-title" class="text-base font-bold">保存済みデータを完全に削除します</h3></div>
              <p id="cleanup-confirmation-summary" class="text-xs leading-relaxed text-gray-700"></p>
              <p class="rounded bg-red-50 p-3 text-xs font-semibold leading-relaxed text-red-900">この操作は元に戻せません。対象のファイル一覧を確認済みの場合のみ実行してください。APIキー・環境設定・アプリ本体は削除されません。</p>
              <label class="block text-xs font-semibold text-gray-800">確認のため <code>DELETE</code> と入力してください。
                <input id="cleanup-confirmation-input" type="text" autocomplete="off" spellcheck="false" oninput="updateCleanupConfirmationButton()" class="mt-2 block w-full rounded border border-red-300 bg-white px-3 py-2 font-mono text-sm" placeholder="DELETE">
              </label>
              <div class="flex flex-wrap justify-end gap-2">
                <button type="button" onclick="closeCleanupConfirmation()" class="rounded border border-[#d6c79a] px-4 py-2 text-xs">キャンセル</button>
                <button id="cleanup-confirm-button" type="button" onclick="confirmCleanupData()" disabled class="rounded bg-red-700 px-4 py-2 text-xs font-bold text-white disabled:cursor-not-allowed disabled:opacity-40">選択したデータを削除</button>
              </div>
            </div>
          </div>
        </section>
        <section class="space-y-3 border-t border-[#e2d8b5] pt-4">
          <h3 class="text-sm font-bold text-gray-800">一覧データの手動更新</h3>
          <p class="text-xs text-gray-600">Level一覧HTMLを再取得し、Master Databaseと各言語のLevel Listを作り直します。更新中も画面を操作できます。</p>
          <div class="flex flex-wrap gap-2">
            <button onclick="runTask('update_master', {mode: 1})" class="px-4 py-2 rounded bg-[#b45309] text-white text-xs font-semibold">一覧HTMLを取得して全更新</button>
            <button onclick="runTask('update_master', {mode: 2})" class="px-4 py-2 rounded border border-[#d6c79a] bg-[#fffdf5] text-xs font-semibold">保存済みHTMLから再構築</button>
          </div>
        </section>
        <section class="space-y-2 border-t border-[#e2d8b5] pt-4">
          <h3 class="text-sm font-bold text-gray-800">接続CSVの生成</h3>
          <p class="text-xs text-gray-600">保存済みのLevel記事HTMLを直接解析します。出力CSVの列形式は変更しません。HTMLが未取得の場合は「HTML取得・データ抽出」タブでLevel記事を保存してください。</p>
          <button onclick="runTask('update_network')" class="px-4 py-2 rounded border border-[#d6c79a] bg-[#fffdf5] text-xs font-semibold">接続CSVを生成・更新</button>
        </section>
        <div id="maintenance-task-status" role="status" aria-live="polite"
          class="min-h-10 rounded border border-[#dfd3a8] bg-[#fffdf5] px-3 py-2 text-xs text-gray-600">
          保守タスクを選択してください。
        </div>
        <div class="flex items-center gap-3">
          <progress id="maintenance-progress" max="100" value="0" class="hidden flex-1 h-2"></progress>
          <span id="maintenance-progress-label" class="text-[11px] text-gray-500">待機中</span>
          <button id="maintenance-cancel" onclick="cancelMaintenanceTask()" disabled
            class="px-3 py-1.5 text-xs bg-red-700 text-white rounded disabled:opacity-40">停止</button>
        </div>
        <pre id="maintenance-log" aria-label="保守タスクログ"
          class="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded border border-[#e2d8b5] bg-[#fffdf5] p-3 text-[11px] text-gray-700"></pre>
      </div>
    </section>

    <!-- 5. BrNavi AI Assistant -->
    <section id="view-ai" class="hidden flex-1 min-w-0 flex flex-col bg-[#fbf6de]">
      <div class="flex items-center justify-between px-5 py-3 bg-[#f2e8c5] border-b border-[#e2d8b5]">
        <div>
          <h2 class="text-sm font-bold text-gray-800">🤖 BrNavi AI Assistant</h2>
          <p id="ai-status" class="text-[10px] text-gray-500 mt-0.5">接続状態を確認中...</p>
        </div>
        <div class="flex gap-2">
          <button onclick="aiToggleProperties()" class="px-3 py-1.5 text-xs bg-white border border-[#d6c79a] rounded">LLMプロパティ</button>
          <button onclick="aiLoadConversationList()" class="px-3 py-1.5 text-xs bg-white border border-[#d6c79a] rounded">履歴更新</button>
          <button onclick="aiNewConversation()" class="px-3 py-1.5 text-xs bg-[#b45309] text-white rounded">＋ 新しい会話</button>
        </div>
      </div>
      <div class="flex flex-1 min-h-0">
        <aside class="w-64 shrink-0 border-r border-[#e2d8b5] bg-[#f7f0d4] flex flex-col">
          <div class="px-3 py-2 text-[11px] font-bold text-gray-600 border-b border-[#e2d8b5]">会話履歴</div>
          <div id="ai-conversation-list" class="flex-1 overflow-y-auto p-2 space-y-1 text-xs">
            <div class="text-gray-400 p-2">履歴はありません</div>
          </div>
        </aside>
        <div class="flex-1 min-w-0 flex flex-col">
          <div id="ai-properties" class="hidden px-4 py-3 bg-[#fffaf0] border-b border-[#e2d8b5] text-xs">
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 items-end">
              <label class="block">プロバイダー<input id="ai-prop-provider" value="Groq" disabled class="block w-full mt-1 px-2 py-1 border rounded bg-gray-100"></label>
              <label class="block">モデルID<input id="ai-prop-model" class="block w-full mt-1 px-2 py-1 border rounded" placeholder="openai/gpt-oss-120b"></label>
              <label class="block">最大出力トークン<input id="ai-prop-tokens" type="number" min="256" max="16384" step="256" class="block w-full mt-1 px-2 py-1 border rounded"></label>
              <label class="block">会話履歴メッセージ数<input id="ai-prop-history" type="number" min="2" max="40" class="block w-full mt-1 px-2 py-1 border rounded"></label>
            </div>
            <div class="flex items-center justify-between mt-3"><span id="ai-prop-key-status" class="text-gray-500">APIキー状態を確認中...</span><button onclick="aiSaveProperties()" class="px-3 py-1.5 rounded bg-[#b45309] text-white">設定を保存</button></div>
          </div>
          <div id="ai-chat-messages" class="flex-1 overflow-y-auto p-5 space-y-4">
            <div class="max-w-3xl mx-auto text-center text-xs text-gray-500 py-12">
              <div class="text-3xl mb-3">♾️</div>
              <p class="font-semibold text-gray-700">BrNavi AI Assistant</p>
              <p class="mt-2">Backroomsについての雑談や、日本語Wikiの調査を始めましょう。</p>
            </div>
          </div>
          <div class="border-t border-[#e2d8b5] bg-[#f7f0d4] p-3">
            <div class="max-w-4xl mx-auto">
              <div class="flex items-end gap-2">
                <textarea id="ai-message-input" rows="2" maxlength="12000"
                  placeholder="メッセージを入力...（Enterで送信 / Shift+Enterで改行）"
                  onkeydown="handleAIKeydown(event)"
                  class="flex-1 resize-y min-h-[48px] max-h-40 px-3 py-2 bg-white border border-[#d6c79a] rounded text-sm focus:outline-none focus:border-[#b45309]"></textarea>
                <button id="ai-send-button" onclick="aiSendOrCancel()"
                  class="px-5 py-3 bg-[#b45309] hover:bg-[#92400e] text-white rounded text-xs font-bold disabled:opacity-50">送信</button>
              </div>
              <p class="text-[10px] text-gray-500 mt-1.5">AIの回答は生成情報です。Wikiの記述・外部情報・考察を区別して確認してください。</p>
            </div>
          </div>
        </div>
      </div>
    </section>

  </main>

  <div id="app-context-menu" class="hidden fixed z-[100] min-w-40 overflow-hidden rounded border border-[#d3c29a] bg-white py-1 text-xs shadow-xl"
    role="menu" aria-label="テキスト操作">
    <button id="app-context-copy" type="button" class="block w-full px-4 py-2 text-left text-gray-800 hover:bg-[#fff3cf]" role="menuitem">コピー</button>
    <button id="app-context-ask-ai" type="button" class="block w-full px-4 py-2 text-left text-gray-800 hover:bg-[#fff3cf]" role="menuitem">AIに聞く</button>
  </div>

  <script>
    let onlineWikiDefaultOpened = false;
    const onlineWikiHomeUrl = 'https://backrooms.fandom.com/ja/wiki/Backrooms_Wiki';
    document.getElementById('ai-chat-messages').addEventListener(
      'click', openAIAnswerLinkFromEvent
    );

    async function showAbout() {
      try {
        const info = await pywebview.api.get_about_info();
        document.getElementById('about-name').textContent = info.name;
        document.getElementById('about-version').textContent = 'v' + info.version;
        document.getElementById('about-type').textContent = info.application_type;
        document.getElementById('about-provider').textContent = info.provider;
        document.getElementById('about-model').textContent = info.model;
        document.getElementById('about-link-status').textContent = '';
        document.getElementById('about-dialog').classList.remove('hidden');
      } catch (error) {
        console.error('アプリ情報を取得できません:', error);
      }
    }

    function closeAbout() {
      document.getElementById('about-dialog').classList.add('hidden');
    }

    async function openAboutLink(destination) {
      const result = await pywebview.api.open_about_link(destination);
      document.getElementById('about-link-status').textContent =
        result.error || '';
    }

    async function reloadApplicationData() {
      const button = document.getElementById('reload-application-data');
      if (button.disabled) return;
      button.disabled = true;
      button.textContent = 'Reloading: cache...';
      try {
        const refreshed = await pywebview.api.refresh_application_data();
        if (refreshed.error) throw new Error(refreshed.error);
        explorerCatalogPromise = null;
        button.textContent = 'Reloading: search and index...';
        await Promise.all([onSearchInput(), loadExplorerIndexPages()]);
        button.textContent = 'Reloading: images...';
        localImages = [];
        const galleryView = document.getElementById('explorer-gallery-view');
        if (galleryView && !galleryView.classList.contains('hidden')) {
          await loadLocalImageGallery();
        }
        levelListLanguagesLoaded = false;
        if (!document.getElementById('view-data-tools').classList.contains('hidden')) {
          button.textContent = 'Reloading: language lists...';
          await loadLevelListLanguages();
        }
        button.textContent = 'Reloaded';
      } catch (error) {
        button.textContent = 'Reload failed';
        console.error(error);
      } finally {
        setTimeout(() => { button.textContent = 'Reload data'; button.disabled = false; }, 1500);
      }
    }

    function switchTab(tab) {
      ['explorer', 'wiki', 'data-tools', 'routes', 'settings', 'ai'].forEach(t => {
        document.getElementById('view-' + t).classList.add('hidden');
        document.getElementById('tab-btn-' + t).className = "px-4 py-1.5 rounded text-xs font-semibold bg-gray-800 text-gray-300 hover:bg-gray-700 transition";
      });
      document.getElementById('view-' + tab).classList.remove('hidden');
      document.getElementById('tab-btn-' + tab).className = "px-4 py-1.5 rounded text-xs font-semibold bg-[#b45309] text-white transition shadow";
      if (tab === 'wiki' && !onlineWikiDefaultOpened) {
        openDirectWikiUrl();
      }
      if (tab === 'data-tools' && !levelListLanguagesLoaded) {
        loadLevelListLanguages();
      }
    }

    let activeTheme = 'light';
    let activeTimezone = 'Asia/Tokyo';
    let explorerImages = [];
    let localImages = [];
    let localSavedLevels = [];
    let lightboxImages = [];
    let lightboxIndex = 0;
    let lightboxLevel = '';

    function applyTheme(theme) {
      activeTheme = theme === 'dark' ? 'dark' : 'light';
      document.body.dataset.theme = activeTheme;
      const icon = activeTheme === 'light' ? '🌙' : '☀️';
      document.getElementById('theme-toggle').textContent = icon;
      document.getElementById('settings-theme-icon').textContent = icon;
    }
    async function toggleTheme() {
      const next = activeTheme === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      try { await pywebview.api.save_environment_settings({theme: next}); }
      catch (error) { console.error('テーマ設定の保存に失敗:', error); }
    }
    async function saveTimezone(timezone) {
      activeTimezone = timezone;
      try {
        const result = await pywebview.api.save_environment_settings({timezone});
        if (result.error) throw new Error(result.error);
        updateHeaderClock();
      } catch (error) { console.error('タイムゾーン設定の保存に失敗:', error); }
    }
    function updateHeaderClock() {
      try {
        const now = new Date();
        const zone = activeTimezone === 'BRT' ? 'UTC' : activeTimezone;
        const parts = Object.fromEntries(new Intl.DateTimeFormat('ja-JP', {
          timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short',
          hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23', timeZoneName: 'short'
        }).formatToParts(now).map(part => [part.type, part.value]));
        const dynamicZoneName = new Intl.DateTimeFormat('en-US', {timeZone: zone, timeZoneName: 'short'})
          .formatToParts(now).find(part => part.type === 'timeZoneName')?.value || parts.timeZoneName;
        const abbr = activeTimezone === 'BRT' ? 'BRT' : ({'Asia/Tokyo':'JST','Asia/Seoul':'KST','Asia/Shanghai':'CST','UTC':'UTC'}[activeTimezone] || dynamicZoneName);
        document.getElementById('header-clock').textContent = `${parts.year}/${parts.month}/${parts.day} (${parts.weekday}) ${parts.hour}:${parts.minute}:${parts.second} ${abbr}`;
      } catch (_) { document.getElementById('header-clock').textContent = new Date().toLocaleString('ja-JP'); }
    }
    async function refreshSystemStatus() {
      try {
        const status = await pywebview.api.get_system_status();
        document.getElementById('header-network').textContent = `${status.network === 'Wi-Fi' ? '📶' : status.network === '有線LAN' ? '🔌' : '○'} ${status.network}`;
        document.getElementById('header-power').textContent = status.power;
      } catch (_) {
        document.getElementById('header-network').textContent = '○ 状態不明';
        document.getElementById('header-power').textContent = '⚡ 状態不明';
      }
    }
    function formatBytes(bytes) {
      const units = ['B', 'KB', 'MB', 'GB', 'TB']; let size = Number(bytes) || 0, i = 0;
      while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
      return `${size.toFixed(i ? 1 : 0)} ${units[i]}`;
    }
    async function refreshStorageSummary() {
      const target = document.getElementById('storage-summary'); target.textContent = '計測中...';
      try {
        const data = await pywebview.api.get_storage_summary();
        const categories = Object.entries(data.categories || {}).map(([label, value]) => `<div class="flex justify-between"><span>${escapeHTML(label)}</span><span>${value.files} 件 · ${formatBytes(value.bytes)}</span></div>`).join('');
        const drive = data.drive || {};
        target.innerHTML = `<div>${categories}</div><p class="mt-2 font-semibold">ローカルデータ合計: ${data.files} 件 · ${formatBytes(data.bytes)}</p><div class="mt-2 flex justify-between"><span>ドライブ使用量</span><span>${formatBytes(drive.used)} / ${formatBytes(drive.total)} · 空き ${formatBytes(drive.free)}</span></div><progress class="mt-1 h-2 w-full" max="100" value="${Number(drive.percent)||0}"></progress>`;
      } catch (error) { target.textContent = `計測できません: ${error}`; }
    }

    let cleanupPlanId = null;

    function setAllCleanupCategories(checked) {
      document.querySelectorAll('[data-cleanup-category]').forEach(input => {
        input.checked = Boolean(checked);
      });
      clearCleanupPreview();
    }

    function clearCleanupPreview() {
      cleanupPlanId = null;
      document.getElementById('cleanup-preview').classList.add('hidden');
      document.getElementById('cleanup-preview-items').replaceChildren();
      closeCleanupConfirmation();
    }

    function showCleanupStatus(message, isError = false) {
      const target = document.getElementById('cleanup-status');
      target.textContent = message;
      target.classList.remove('hidden', 'border-red-300', 'bg-red-50', 'text-red-800');
      if (isError) target.classList.add('border-red-300', 'bg-red-50', 'text-red-800');
    }

    async function previewCleanupData() {
      const button = document.getElementById('cleanup-preview-button');
      const categories = Array.from(document.querySelectorAll('[data-cleanup-category]:checked'))
        .map(input => input.dataset.cleanupCategory);
      if (!categories.length) {
        showCleanupStatus('削除するカテゴリを1つ以上選択してください。', true);
        return;
      }
      button.disabled = true;
      button.textContent = '対象を確認中...';
      clearCleanupPreview();
      try {
        const result = await pywebview.api.get_cleanup_preview(categories);
        if (result.status !== 'ready') throw new Error(result.error || '削除対象を確認できませんでした。');
        cleanupPlanId = result.plan_id;
        const summary = (result.categories || []).map(item =>
          `<div class="flex flex-wrap justify-between gap-2"><span>${escapeHTML(item.label)} · ${item.exists ? '存在' : 'データなし'}</span><span>${item.files} ファイル · ${formatBytes(item.bytes)}</span></div>`
        ).join('');
        document.getElementById('cleanup-preview-summary').innerHTML =
          summary + `<p class="mt-2 border-t border-[#e2d8b5] pt-2 font-bold">削除対象合計：${result.total_files} ファイル · ${formatBytes(result.total_bytes)}</p>`;
        const list = document.getElementById('cleanup-preview-items');
        list.replaceChildren();
        (result.items || []).forEach(item => {
          const row = document.createElement('li');
          row.className = 'break-all';
          row.textContent = (item.kind === 'directory' ? '[DIR] ' : '[FILE] ') +
            item.path + (item.kind === 'file' ? ' · ' + formatBytes(item.bytes) :
              (item.root ? '（保存先フォルダー自体は保持）' : ''));
          list.appendChild(row);
        });
        if (!result.items || !result.items.length) {
          const empty = document.createElement('li');
          empty.className = 'text-gray-500';
          empty.textContent = '対象カテゴリに削除可能なデータはありません。';
          list.appendChild(empty);
        }
        document.getElementById('cleanup-preview').classList.remove('hidden');
        document.getElementById('cleanup-status').classList.add('hidden');
      } catch (error) {
        cleanupPlanId = null;
        showCleanupStatus('プレビューを作成できませんでした: ' + error.message, true);
      } finally {
        button.disabled = false;
        button.textContent = '削除対象を確認';
      }
    }

    function openCleanupConfirmation() {
      if (!cleanupPlanId) return;
      const total = document.getElementById('cleanup-preview-summary').textContent;
      document.getElementById('cleanup-confirmation-summary').textContent =
        total + '。表示された対象のみを削除します。';
      document.getElementById('cleanup-confirmation-input').value = '';
      updateCleanupConfirmationButton();
      const dialog = document.getElementById('cleanup-confirmation-dialog');
      dialog.classList.remove('hidden');
      dialog.style.display = 'flex';
      document.getElementById('cleanup-confirmation-input').focus();
    }

    function closeCleanupConfirmation() {
      const dialog = document.getElementById('cleanup-confirmation-dialog');
      if (!dialog) return;
      dialog.classList.add('hidden');
      dialog.style.display = 'none';
      const input = document.getElementById('cleanup-confirmation-input');
      if (input) input.value = '';
      updateCleanupConfirmationButton();
    }

    function updateCleanupConfirmationButton() {
      const input = document.getElementById('cleanup-confirmation-input');
      const button = document.getElementById('cleanup-confirm-button');
      if (input && button) button.disabled = input.value !== 'DELETE';
    }

    async function confirmCleanupData() {
      if (!cleanupPlanId) return;
      const button = document.getElementById('cleanup-confirm-button');
      const input = document.getElementById('cleanup-confirmation-input');
      if (input.value !== 'DELETE') return;
      button.disabled = true;
      button.textContent = '削除中...';
      try {
        const result = await pywebview.api.execute_cleanup_data(cleanupPlanId, input.value);
        if (result.status !== 'deleted') throw new Error(result.error || '削除できませんでした。');
        const categories = result.categories || [];
        cleanupPlanId = null;
        closeCleanupConfirmation();
        clearCleanupPreview();
        try {
          await refreshAfterCleanup(categories);
          showCleanupStatus(`削除完了：${result.files} ファイル（${formatBytes(result.bytes)}）。図鑑・経路・ストレージ表示を再読み込みしました。`);
        } catch (refreshError) {
          showCleanupStatus(`データ削除は完了しましたが、画面の再読み込みに失敗しました: ${refreshError.message}`, true);
        }
      } catch (error) {
        showCleanupStatus('削除を中止しました: ' + error.message, true);
        closeCleanupConfirmation();
        cleanupPlanId = null;
        document.getElementById('cleanup-preview').classList.add('hidden');
      } finally {
        button.textContent = '選択したデータを削除';
        updateCleanupConfirmationButton();
      }
    }

    async function refreshAfterCleanup(categories) {
      const selected = new Set(categories || []);
      await pywebview.api.refresh_application_data();
      explorerCatalogPromise = null;
      localImages = [];
      if (selected.has('articles')) {
        explorerImages = [];
        lightboxImages = [];
        closeImageLightbox();
        currentExplorerPage = null;
        explorerTabs.forEach(tab => {
          tab.page = null;
          tab.title = 'New tab';
          tab.history = [];
          tab.historyIndex = -1;
        });
        renderExplorerTabs();
        showExplorerIndexHome();
      }
      await Promise.all([
        onSearchInput(),
        loadExplorerIndexPages(),
        refreshStorageSummary(),
      ]);
      const gallery = document.getElementById('explorer-gallery-view');
      if (gallery && !gallery.classList.contains('hidden')) await loadLocalImageGallery();
      if (selected.has('index')) {
        levelListLanguagesLoaded = false;
        if (!document.getElementById('view-data-tools').classList.contains('hidden')) {
          await loadLevelListLanguages();
        }
      }
      routeLastResults = [];
      const routeResults = document.getElementById('route-results');
      if (routeResults) routeResults.replaceChildren();
      document.getElementById('route-detail-content').replaceChildren();
      document.getElementById('route-detail-view').classList.add('hidden');
      document.getElementById('route-results-list').classList.remove('hidden');
      if (selected.has('routes')) {
        document.getElementById('route-status').textContent = '経路CSVが削除されました。再生成後に検索できます。';
      } else {
        document.getElementById('route-status').textContent = '経路検索結果を更新しました。必要に応じて再検索してください。';
        const routeStart = document.getElementById('route-start').value.trim();
        const routeGoal = document.getElementById('route-goal').value.trim();
        if (routeStart && routeGoal) await searchRoutes();
      }
      if (selected.has('ai')) {
        aiConversationId = null;
        document.getElementById('ai-chat-messages').replaceChildren();
        await aiLoadConversationList();
      }
    }
    async function switchExplorerSubtab(tab) {
      const gallery = tab === 'gallery';
      document.getElementById('explorer-article-view').classList.toggle('hidden', gallery);
      document.getElementById('explorer-gallery-view').classList.toggle('hidden', !gallery);
      document.getElementById('explorer-subtab-gallery').className = gallery ? 'rounded px-3 py-1.5 text-sm font-semibold bg-[#b45309] text-white' : 'rounded px-3 py-1.5 text-sm font-semibold bg-[#eee3bc]';
      document.getElementById('explorer-subtab-article').className = !gallery ? 'rounded px-3 py-1.5 text-sm font-semibold bg-[#b45309] text-white' : 'rounded px-3 py-1.5 text-sm font-semibold bg-[#eee3bc]';
      if (gallery && localImages.length === 0) await loadLocalImageGallery();
    }
    async function loadLocalImageGallery() {
      try {
        localImages = await pywebview.api.get_local_image_gallery();
        renderLocalImageGallery();
      } catch (error) { document.getElementById('local-gallery-count').textContent = `画像一覧を読み込めません: ${error}`; }
    }
    function renderLocalImageGallery() {
      const query = (document.getElementById('local-gallery-filter').value || '').trim().toLocaleLowerCase();
      const items = localImages.filter(item => `${item.level_id} ${item.name}`.toLocaleLowerCase().includes(query));
      document.getElementById('local-gallery-count').textContent = `${items.length} / ${localImages.length} 枚`;
      const grid = document.getElementById('local-gallery-grid'); grid.replaceChildren();
      items.forEach(item => {
        const card = document.createElement('article'); card.className = 'overflow-hidden rounded border border-[#e2d8b5] bg-white p-2';
        const img = document.createElement('img'); img.dataset.filename = item.name; img.alt = item.name; img.loading = 'lazy'; img.className = 'h-36 w-full cursor-zoom-in rounded object-cover';
        img.onclick = () => openImageLightbox(items, items.indexOf(item), item.level_id);
        const title = document.createElement('div'); title.className = 'mt-2 truncate text-xs font-semibold'; title.textContent = item.level_id || 'Level不明';
        const name = document.createElement('div'); name.className = 'truncate text-[10px] text-gray-500'; name.textContent = item.name;
        const open = document.createElement('button'); open.className = 'mt-2 text-xs text-amber-700 underline'; open.textContent = 'このLevelの記事を開く';
        open.disabled = !item.level_id; open.onclick = () => openGalleryLevel(item.level_id);
        card.append(img, title, name, open); grid.append(card);
      });
      if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver(entries => entries.forEach(entry => {
          if (entry.isIntersecting) { observer.unobserve(entry.target); loadLocalImageIntoElement(entry.target); }
        }), {rootMargin: '200px'});
        grid.querySelectorAll('img[data-filename]').forEach(img => observer.observe(img));
      } else grid.querySelectorAll('img[data-filename]').forEach(loadLocalImageIntoElement);
    }
    async function loadLocalImageIntoElement(img) {
      try {
        const result = await pywebview.api.get_local_image_data(img.dataset.filename);
        if (result.src) img.src = result.src;
      } catch (_) { img.alt = '画像を読み込めません'; }
    }
    function filterLocalImageGallery() { renderLocalImageGallery(); }
    async function previewLegacyImageRename() {
      const button = document.getElementById('legacy-image-rename-button');
      const status = document.getElementById('legacy-image-rename-status');
      button.disabled = true;
      try {
        const preview = await pywebview.api.get_legacy_image_rename_preview();
        if (!preview.count) {
          status.textContent = `移行可能な画像はありません。判定保留 ${preview.skipped} 件`;
          return;
        }
        const examples = preview.items.slice(0, 8).map(item => `${item.source} → ${item.target}`).join('\n');
        const extra = preview.count > 8 ? `\nほか ${preview.count - 8} 件` : '';
        if (!confirm(`旧命名方式の画像 ${preview.count} 件を新方式へリネームします。\n判定保留: ${preview.skipped} 件（変更しません）\n\n${examples}${extra}\n\n既存ファイルは上書きしません。実行しますか？`)) return;
        status.textContent = '画像名を移行しています...';
        await runTask('rename_legacy_images');
      } catch (error) {
        status.textContent = `移行確認に失敗しました: ${error}`;
      } finally {
        button.disabled = false;
      }
    }
    async function openGalleryLevel(level) {
      if (!level) return;
      await switchExplorerSubtab('article');
      document.getElementById('explorer-index-home').classList.add('hidden');
      document.getElementById('explorer-index-category').classList.add('hidden');
      document.getElementById('explorer-index-local-saved').classList.add('hidden');
      document.getElementById('wiki-page-container').classList.remove('hidden');
      await loadLevelDetail(level);
    }
    function openImageLightbox(images, index, level) {
      lightboxImages = images || []; lightboxIndex = Math.max(0, index || 0); lightboxLevel = level || '';
      renderImageLightbox(); document.getElementById('image-lightbox').classList.remove('hidden');
    }
    async function renderImageLightbox() {
      const item = lightboxImages[lightboxIndex]; if (!item) return;
      if (!item.src && item.name) {
        const result = await pywebview.api.get_local_image_data(item.name);
        if (result.src) item.src = result.src;
      }
      document.getElementById('lightbox-image').src = item.src || '';
      document.getElementById('lightbox-image').alt = item.name || '';
      document.getElementById('lightbox-caption').textContent = `${lightboxLevel || item.level_id || ''} · ${item.name || ''} (${lightboxIndex + 1}/${lightboxImages.length})`;
      document.getElementById('lightbox-open-level').classList.toggle('hidden', !(lightboxLevel || item.level_id));
    }
    function stepImageLightbox(offset) {
      if (!lightboxImages.length) return;
      lightboxIndex = (lightboxIndex + offset + lightboxImages.length) % lightboxImages.length; renderImageLightbox();
    }
    function closeImageLightbox() { document.getElementById('image-lightbox').classList.add('hidden'); }
    function openLightboxLevel() { const level = lightboxLevel || (lightboxImages[lightboxIndex] || {}).level_id; closeImageLightbox(); openGalleryLevel(level); }
    document.addEventListener('keydown', event => {
      if (document.getElementById('image-lightbox').classList.contains('hidden')) return;
      if (event.key === 'Escape') closeImageLightbox();
      else if (event.key === 'ArrowLeft') stepImageLightbox(-1);
      else if (event.key === 'ArrowRight') stepImageLightbox(1);
    });

    function renderEnvironmentSettings(settings) {
      if (settings.theme) applyTheme(settings.theme);
      activeTimezone = settings.timezone || 'Asia/Tokyo';
      document.getElementById('timezone-select').value = activeTimezone;
      updateHeaderClock();
      const toggle = document.getElementById('auto-update-catalog');
      toggle.checked = Boolean(settings.auto_update_catalog);
      const history = document.getElementById('catalog-update-history');
      const lastAttempt = settings.last_catalog_update_attempt_at
        ? new Date(settings.last_catalog_update_attempt_at).toLocaleString('ja-JP') : 'なし';
      const lastSuccess = settings.last_catalog_update_success_at
        ? new Date(settings.last_catalog_update_success_at).toLocaleString('ja-JP') : 'なし';
      const states = {never: '未実行', running: '実行中', success: '成功', failed: '失敗', cancelled: '停止'};
      const next = settings.next_catalog_update_at
        ? new Date(settings.next_catalog_update_at).toLocaleString('ja-JP') : '初回起動時';
      history.textContent = `状態: ${states[settings.last_catalog_update_status] || '不明'} / 最終試行: ${lastAttempt} / 最終成功: ${lastSuccess}`
        + (settings.auto_update_catalog ? ` / 次回自動更新: ${settings.update_due ? 'まもなく開始' : next}` : ' / 自動更新は無効');
    }

    async function initializeEnvironmentSettings() {
      const toggle = document.getElementById('auto-update-catalog');
      try {
        renderEnvironmentSettings(await pywebview.api.get_environment_settings());
      } catch (error) {
        document.getElementById('catalog-update-history').textContent = `環境設定を読み込めません: ${error}`;
      }
      toggle.addEventListener('change', async () => {
        toggle.disabled = true;
        try {
          const result = await pywebview.api.save_environment_settings({auto_update_catalog: toggle.checked});
          if (result.error) throw new Error(result.error);
          renderEnvironmentSettings(result);
        } catch (error) {
          document.getElementById('catalog-update-history').textContent = `設定を保存できません: ${error}`;
          toggle.checked = !toggle.checked;
        } finally { toggle.disabled = false; }
      });
      refreshStorageSummary();
      try {
        const result = await pywebview.api.start_auto_database_update();
        if (result.status === 'started') {
          document.getElementById('maintenance-task-status').textContent = result.message;
          document.getElementById('maintenance-log').textContent = '';
          setMaintenanceBusy(true);
          setLevelListTaskBusy(true);
        }
        renderEnvironmentSettings(await pywebview.api.get_environment_settings());
      } catch (error) {
        document.getElementById('catalog-update-history').textContent = `自動更新を開始できません: ${error}`;
      }
    }

    async function openCurrentFandomInApp() {
      try {
        const result = await pywebview.api.get_current_fandom_url();
        if (!result || !result.url) {
          document.getElementById('html-download-status').textContent =
            (result && result.error) || "FandomページのURLがありません。";
          return;
        }
        await openLevelListPage({url: result.url});
      } catch (error) {
        document.getElementById('html-download-status').textContent =
          "内蔵ブラウザーでFandomを開けませんでした: " + error;
      }
    }

    let appContextCopyText = "";
    let appContextSelectionText = "";

    function getContextSelectionText() {
      const active = document.activeElement;
      if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA") &&
          Number.isInteger(active.selectionStart) && active.selectionEnd > active.selectionStart) {
        return active.value.slice(active.selectionStart, active.selectionEnd).trim();
      }
      return String(window.getSelection ? window.getSelection() : "").trim();
    }

    function getContextTargetText(event) {
      const target = event.target instanceof Element ? event.target : event.target.parentElement;
      if (!target) return "";
      const field = target.closest("input, textarea");
      if (field) return String(field.value || "").trim();

      let textNode = null;
      if (document.caretRangeFromPoint) {
        const range = document.caretRangeFromPoint(event.clientX, event.clientY);
        if (range && range.startContainer.nodeType === Node.TEXT_NODE) {
          textNode = range.startContainer;
        }
      } else if (document.caretPositionFromPoint) {
        const position = document.caretPositionFromPoint(event.clientX, event.clientY);
        if (position && position.offsetNode.nodeType === Node.TEXT_NODE) {
          textNode = position.offsetNode;
        }
      }
      const textElement = textNode ? textNode.parentElement : target;
      const block = textElement.closest(
        "p, li, td, th, blockquote, h1, h2, h3, h4, h5, h6, button, a, label, pre, figcaption"
      ) || textElement;
      return String(block.innerText || block.textContent || "").trim();
    }

    function hideAppContextMenu() {
      document.getElementById('app-context-menu').classList.add("hidden");
    }

    function showAppContextMenu(event) {
      const selectedText = getContextSelectionText();
      const target = event.target instanceof Element ? event.target : event.target.parentElement;
      if (target && target.closest("input, textarea, [contenteditable='true']") && !selectedText) {
        return;
      }
      const clickedText = selectedText || getContextTargetText(event);
      if (!clickedText) return;
      event.preventDefault();
      appContextCopyText = clickedText;
      appContextSelectionText = selectedText;
      const menu = document.getElementById('app-context-menu');
      document.getElementById('app-context-ask-ai').classList.toggle("hidden", !selectedText);
      menu.classList.remove("hidden");
      const bounds = menu.getBoundingClientRect();
      menu.style.left = `${Math.max(4, Math.min(event.clientX, window.innerWidth - bounds.width - 4))}px`;
      menu.style.top = `${Math.max(4, Math.min(event.clientY, window.innerHeight - bounds.height - 4))}px`;
    }

    async function copyContextText() {
      const text = appContextCopyText;
      hideAppContextMenu();
      if (!text) return;
      try {
        await navigator.clipboard.writeText(text);
      } catch (error) {
        const area = document.createElement('textarea');
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        const copied = document.execCommand('copy');
        area.remove();
        if (!copied) console.error("クリップボードへのコピーに失敗しました。", error);
      }
    }

    async function askAIAboutSelection() {
      const text = appContextSelectionText;
      hideAppContextMenu();
      if (!text) return;
      const prompt = "次の選択テキストについて説明してください。必要なら重要な点を要約してください。\n\n" + text;
      if (prompt.length > 12000) {
        switchTab('ai');
        aiSetStatus("選択範囲が長すぎます。12,000文字以内にしてください。", true);
        return;
      }
      switchTab('ai');
      await initializeAI();
      if (!await aiNewConversation()) return;
      document.getElementById('ai-message-input').value = prompt;
      await sendAIMessage();
    }

    document.addEventListener('contextmenu', showAppContextMenu);
    document.addEventListener('pointerdown', event => {
      if (!event.target.closest || !event.target.closest('#app-context-menu')) hideAppContextMenu();
    });
    document.addEventListener('keydown', event => {
      if (event.key === "Escape") hideAppContextMenu();
    });
    document.getElementById('app-context-menu').addEventListener('mousedown', event => {
      event.preventDefault();
    });
    document.getElementById('app-context-copy').addEventListener('click', copyContextText);
    document.getElementById('app-context-ask-ai').addEventListener('click', askAIAboutSelection);

    let levelSearchResults = [];
    let levelSearchRequestId = 0;
    let levelSearchActiveIndex = -1;
    let explorerIndexPages = [];
    let explorerIndexCategory = null;

    let explorerCatalogPromise = null;

    async function getExplorerCatalog() {
      if (!explorerCatalogPromise) {
        explorerCatalogPromise = pywebview.api.search_candidates('').then(result => {
          if (result && result.error) throw new Error(result.error);
          return Array.isArray(result) ? result : [];
        }).catch(error => {
          explorerCatalogPromise = null;
          throw error;
        });
      }
      return explorerCatalogPromise;
    }

    async function onSearchInput() {
      const requestId = ++levelSearchRequestId;
      const query = document.getElementById('search-input').value;
      const candidates = query.trim()
        ? await pywebview.api.search_candidates(query)
        : await getExplorerCatalog();
      if (requestId !== levelSearchRequestId) return;
      levelSearchResults = candidates || [];
      if (!explorerIndexPages.length) explorerIndexPages = levelSearchResults.map(candidate => ({...candidate}));
      renderCandidates(levelSearchResults);
      renderExplorerCategoryTiles();
      renderLevelSearchSuggestions();
    }

    function selectLevelCandidate(item) {
      document.getElementById('search-input').value = item.title;
      hideLevelSearchSuggestions();
      try {
        const recent = JSON.parse(localStorage.getItem('brnavi-recent-levels') || '[]');
        localStorage.setItem('brnavi-recent-levels', JSON.stringify([item.title, ...recent.filter(title => title !== item.title)].slice(0, 8)));
      } catch (_) {}
      openExplorerCandidate(item);
    }

    function renderCandidates(list) {
      const host = document.getElementById('candidate-list');
      host.replaceChildren();
      document.getElementById('candidate-count').textContent = list.length;
      const query = document.getElementById('search-input').value.trim();
      if (!query) {
        host.innerHTML = '<p class="p-2 text-[11px] leading-relaxed text-gray-500">検索すると候補が表示されます。画像付きカテゴリ一覧から番号帯をたどることもできます。</p>';
        return;
      }
      if (!list.length) {
        host.innerHTML = '<p class="p-2 text-gray-500">一致する階層がありません。</p>';
        return;
      }
      list.slice(0, 80).forEach(item => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'mb-1 block w-full rounded border border-transparent px-2 py-1.5 text-left hover:border-[#dfd3a8] hover:bg-white';
        button.innerHTML = `<span class="mr-1">${item.saved ? '📖' : '↗'}</span><span class="font-semibold text-[#1558a6]">${escapeHTML(item.title)}</span>${item.subtitle ? `<span class="block pl-5 text-[10px] text-gray-500">${escapeHTML(item.subtitle)}</span>` : ''}`;
        button.onclick = () => selectLevelCandidate(item);
        host.appendChild(button);
      });
    }

    function explorerTitleKey(title) {
      return String(title || '').replace(/[_\s]+/g, ' ').trim().toLocaleLowerCase();
    }

    function explorerPagesForCategory(categoryId) {
      const source = explorerIndexPages.length ? explorerIndexPages : levelSearchResults;
      let pages;
      if (categoryId === "saved_local") {
        pages = uniqueLevelListPages(source.filter(page => Boolean(page.saved)));
      } else if (categoryId === "all") {
        // 「階層の一覧」はMaster DB全体ではなく、hierarchy.htmlに直接掲載された記事だけを表示する。
        pages = uniqueLevelListPages(source.filter(page =>
          Boolean(getWikiMembership(page, "hierarchy")) || pageHasIndexMembership(page, "hierarchy")
        )).sort((left, right) => compareWikiMembershipOrder(left, right, "hierarchy"));
      } else {
        pages = levelListPagesForCategory(categoryId, source);
      }
      const metadataByTitle = new Map(levelSearchResults.map(item => [explorerTitleKey(item.title), item]));
      const mergedPages = pages.map(page => {
        const metadata = metadataByTitle.get(explorerTitleKey(page.title)) || {};
        return {...metadata, ...page, saved: Boolean(metadata.saved || page.saved),
          subtitle: metadata.subtitle || page.subtitle || page.meta_title || ''};
      });
      return mergedPages.sort((left, right) => categoryId === "all"
        ? compareWikiMembershipOrder(left, right, "hierarchy")
        : compareWikiIndexOrder(left, right));
    }

    async function loadExplorerIndexPages() {
      try {
        explorerIndexPages = await getExplorerCatalog();
      } catch (_) {
        explorerIndexPages = levelSearchResults.map(candidate => ({...candidate}));
      }
      // Derive saved articles locally; avoid sending the full catalog back through
      // the WebView bridge only to filter it.
      const savedSeen = new Set();
      localSavedLevels = explorerIndexPages.filter(page => {
        if (!page.saved) return false;
        const key = explorerTitleKey(page.title);
        if (!key || savedSeen.has(key)) return false;
        savedSeen.add(key);
        return true;
      });
      {
        const localCandidates = localSavedLevels;
        const known = new Set(explorerIndexPages.map(page => explorerTitleKey(page.title)));
        (localCandidates || []).filter(page => page.saved).forEach(page => {
          const key = explorerTitleKey(page.title);
          if (known.has(key)) {
            const existing = explorerIndexPages.find(item => explorerTitleKey(item.title) === key);
            if (existing) Object.assign(existing, {...page, saved: true, url: existing.url || page.url});
            return;
          }
          const category = page.category || (/η\s*$/i.test(page.title) ? 'normal_eta' : 'normal');
          explorerIndexPages.push({...page, category, category_label: page.category_label || (category === 'normal_eta' ? '通常階層 η' : '通常階層')});
          known.add(key);
        });
      }
      renderExplorerCategoryTiles();
      if (!document.getElementById('explorer-index-local-saved').classList.contains('hidden')) renderLocalSavedLevels();
      if (explorerIndexCategory !== null) renderExplorerCategorySections(explorerIndexCategory);
    }

    function renderExplorerCategoryTiles() {
      const host = document.getElementById('explorer-category-tiles');
      if (!host || !Array.isArray(levelListCategoryDefinitions)) return;
      host.replaceChildren();
      const sourcePages = explorerIndexPages.length ? explorerIndexPages : levelSearchResults;
      // 図鑑トップは固定の基本9タイルのみ。支部別の入口はHTML取得タブ側に限定する。
      const categories = levelListCategoryDefinitions.map(category => ({
        ...category,
        pages: category.id === "all"
          ? explorerPagesForCategory("all")
          : levelListPagesForCategory(category.id, sourcePages),
      }));
      categories.forEach((category, index) => {
        const card = document.createElement('div');
        card.className = 'overflow-hidden rounded-md border border-[#dfd09f] bg-[#fffdf5] shadow-sm transition hover:-translate-y-0.5 hover:border-[#b98a43] hover:shadow-md';
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'block w-full text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#b45309]';
        button.onclick = () => openExplorerCategory(category.id);
        const art = document.createElement('div');
        art.className = 'relative h-24 w-full overflow-hidden bg-[#e4d9a8]';
        const palette = [['#77754b','#cec899'],['#45463e','#b7b5a5'],['#39745f','#b7d8aa'],['#6d3f24','#d39b53'],['#6c7557','#d7c982'],['#61797e','#bed0c8'],['#657b80','#c7d5cd'],['#83785c','#d9cea9']][index % 8];
        art.style.background = `linear-gradient(145deg,${palette[0]},${palette[1]})`;
        if (category.imageUrl) {
          const image = document.createElement('img');
          image.src = category.imageUrl;
          image.alt = `${category.title}の参考画像`;
          image.loading = 'lazy';
          image.className = 'absolute inset-0 h-full w-full object-cover transition duration-200 group-hover:scale-[1.03]';
          image.onerror = () => image.remove();
          art.appendChild(image);
        }
        const title = document.createElement('div');
        title.className = 'min-w-0 flex-1 px-2 py-2 text-left text-xs font-bold leading-snug text-[#3f3828]';
        title.textContent = category.title;
        const count = document.createElement('div');
        count.className = 'shrink-0 rounded-full bg-[#f3ead2] px-2 py-1 text-[10px] font-semibold tabular-nums text-[#72552d]';
        count.textContent = category.pages.length.toLocaleString('ja-JP') + '件';
        const info = document.createElement('div');
        info.className = 'flex min-h-[48px] items-center justify-between gap-1 px-2 py-1';
        info.append(title, count);
        button.append(art, info);
        card.appendChild(button);
        if (category.imagePageUrl) {
          const credit = document.createElement('a');
          credit.href = category.imagePageUrl;
          credit.target = '_blank';
          credit.rel = 'noopener noreferrer';
          credit.className = 'block border-t border-[#eee5ce] px-2 py-1 text-right text-[9px] text-[#806a43] hover:bg-[#faf5e7] hover:underline';
          credit.textContent = '画像出典';
          card.appendChild(credit);
        }
        host.appendChild(card);
      });
      const localCard = document.createElement('div');
      localCard.className = 'overflow-hidden rounded-md border border-[#dfd09f] bg-[#fffdf5] shadow-sm transition hover:-translate-y-0.5 hover:border-[#b98a43] hover:shadow-md';
      if (categories.length === 8) localCard.classList.add('lg:col-start-1', 'lg:row-start-3');
      const localButton = document.createElement('button');
      localButton.type = 'button';
      localButton.className = 'block w-full text-left focus:outline-none focus:ring-2 focus:ring-inset focus:ring-[#b45309]';
      localButton.onclick = openLocalSavedLevels;
      const localArt = document.createElement('div');
      localArt.className = 'relative flex h-24 w-full items-center justify-center overflow-hidden bg-[#e4d9a8] text-4xl';
      localArt.style.background = 'linear-gradient(145deg,#806b42,#ddc99a)';
      localArt.textContent = '📚';
      const localTitle = document.createElement('div');
      localTitle.className = 'min-w-0 flex-1 px-2 py-2 text-left text-xs font-bold leading-snug text-[#3f3828]';
      localTitle.textContent = 'ローカル保存済み階層の一覧';
      const localCount = document.createElement('div');
      localCount.className = 'shrink-0 rounded-full bg-[#f3ead2] px-2 py-1 text-[10px] font-semibold tabular-nums text-[#72552d]';
      localCount.textContent = `${localSavedLevels.length}件`;
      const localInfo = document.createElement('div');
      localInfo.className = 'flex min-h-[48px] items-center justify-between gap-1 px-2 py-1';
      localInfo.append(localTitle, localCount);
      localButton.append(localArt, localInfo);
      localCard.appendChild(localButton);
      host.appendChild(localCard);
    }

    function openLocalSavedLevels() {
      switchExplorerSubtab('article');
      explorerIndexCategory = null;
      document.getElementById('explorer-index-home').classList.add('hidden');
      document.getElementById('explorer-index-category').classList.add('hidden');
      document.getElementById('wiki-page-container').classList.add('hidden');
      document.getElementById('explorer-index-local-saved').classList.remove('hidden');
      renderLocalSavedLevels();
    }

    function renderLocalSavedLevels() {
      const host = document.getElementById('local-saved-level-list');
      if (!host) return;
      document.getElementById('local-saved-level-count').textContent = `${localSavedLevels.length}件`;
      host.replaceChildren();
      if (!localSavedLevels.length) {
        const empty = document.createElement('p');
        empty.className = 'col-span-full rounded border border-[#e2d8b5] bg-[#fffdf5] p-5 text-sm text-gray-600';
        empty.textContent = 'ローカルに保存・抽出済みの階層はありません。Wiki閲覧・保存またはHTML取得・データ抽出から記事を保存してください。';
        host.appendChild(empty);
        return;
      }
      localSavedLevels.forEach(page => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'rounded border border-[#e2d8b5] bg-[#fffdf5] p-3 text-left shadow-sm transition hover:bg-[#f7f0d4]';
        button.onclick = () => openExplorerCandidate(page);
        button.onauxclick = event => { if (event.button === 1) { event.preventDefault(); createExplorerTab(page); } };
        const title = document.createElement('span');
        title.className = 'block font-semibold text-[#a3420b]';
        title.appendChild(document.createTextNode('📖 '));
        let strikeSpans = [];
        try {
          const parsedSpans = JSON.parse(page.strikethrough_spans || "[]");
          if (Array.isArray(parsedSpans)) strikeSpans = parsedSpans;
        } catch (_) {}
        appendDecoratedListText(title, page.title, strikeSpans, "title");
        const subtitle = document.createElement('span');
        subtitle.className = 'mt-1 block text-xs text-gray-600';
        if (page.meta_title || page.subtitle) {
          appendDecoratedListText(subtitle, page.meta_title || page.subtitle, strikeSpans, "meta_title");
        }
        if (page.meta_title_ja) {
          if (subtitle.childNodes.length) subtitle.appendChild(document.createTextNode(' · '));
          appendDecoratedListText(subtitle, page.meta_title_ja, strikeSpans, "meta_title_ja");
        }
        const categoryLabel = page.category_label || page.category;
        if (categoryLabel) {
          if (subtitle.childNodes.length) subtitle.appendChild(document.createTextNode(' · '));
          subtitle.appendChild(document.createTextNode(categoryLabel));
        }
        if (!subtitle.childNodes.length) subtitle.textContent = '保存済み';
        button.append(title, subtitle);
        host.appendChild(button);
      });
    }

    function openExplorerCategory(categoryId) {
      explorerIndexCategory = categoryId;
      document.getElementById('explorer-index-home').classList.add('hidden');
      document.getElementById('explorer-index-local-saved').classList.add('hidden');
      document.getElementById('wiki-page-container').classList.add('hidden');
      document.getElementById('explorer-index-category').classList.remove('hidden');
      const category = categoryId === "saved_local"
        ? {title: "ローカル保存済み階層の一覧"}
        : getLevelListCategories(explorerIndexPages.length ? explorerIndexPages : levelSearchResults)
            .find(item => item.id === categoryId);
      document.getElementById('explorer-index-category-title').textContent = category?.title || '階層の一覧';
      renderExplorerCategorySections(categoryId);
    }

    function showExplorerIndexHome() {
      explorerIndexCategory = null;
      document.getElementById('wiki-page-container').classList.add('hidden');
      document.getElementById('explorer-index-category').classList.add('hidden');
      document.getElementById('explorer-index-local-saved').classList.add('hidden');
      document.getElementById('explorer-index-home').classList.remove('hidden');
    }

    function renderExplorerCategorySections(categoryId) {
      const host = document.getElementById('explorer-index-sections');
      const toc = document.getElementById('explorer-index-toc');
      host.replaceChildren();
      toc.replaceChildren();
      const pages = explorerPagesForCategory(categoryId);
      const sections = getLevelListSections(pages, categoryId);
      document.getElementById('explorer-index-total').textContent = `${pages.length}件`;
      if (!pages.length) {
        const message = document.createElement('p');
        message.className = 'py-5 text-sm text-gray-600';
        message.textContent = 'このカテゴリのローカル一覧データはありません。';
        host.appendChild(message);
        const category = getLevelListCategories(explorerIndexPages).find(item => item.id === categoryId);
        if (category?.wikiTitle) {
          const link = document.createElement('button');
          link.className = 'mb-4 rounded bg-[#91520f] px-3 py-2 text-xs font-semibold text-white';
          link.textContent = 'Fandom Wikiで一覧を開く';
          link.onclick = () => openFandomLevelList(category.wikiTitle);
          host.appendChild(link);
        }
        return;
      }
      const appendHeading = (title, key, level = 3) => {
        const heading = document.createElement(level === 3 ? 'h3' : 'h4');
        heading.id = `explorer-section-${key}`;
        heading.className = level === 3
          ? 'sticky top-0 z-10 -mx-5 mb-2 border-b border-[#d8c99e] bg-[#fff8dc] px-4 py-2 text-xl font-semibold text-[#3f3828]'
          : 'mb-1 mt-3 border-b border-[#eee5ce] px-2 py-1 text-sm font-semibold text-gray-700';
        heading.textContent = title;
        host.appendChild(heading);
        const link = document.createElement('a');
        link.href = `#${heading.id}`;
        link.className = level === 3 ? 'mb-1 block rounded px-2 py-1 text-gray-700 hover:bg-[#fff7dd] hover:text-[#9b3d06]' : 'mb-1 ml-3 block rounded px-2 py-1 text-gray-600 hover:bg-[#fff7dd]';
        link.textContent = title;
        link.onclick = event => { event.preventDefault(); heading.scrollIntoView({block: 'start'}); };
        toc.appendChild(link);
        return heading;
      };
      const appendPages = (items, indent = false) => items.forEach(page => {
        const row = document.createElement('div');
        row.className = `py-1.5 text-sm leading-relaxed ${indent ? 'pl-4' : ''}`;
        const link = document.createElement('a');
        link.href = page.url || '#';
        link.className = 'text-[#a3420b] hover:underline';
        let strikeSpans = [];
        try {
          const parsedSpans = JSON.parse(page.strikethrough_spans || "[]");
          if (Array.isArray(parsedSpans)) strikeSpans = parsedSpans;
        } catch (_) {}
        appendDecoratedListText(link, page.title || '無題', strikeSpans, "title");
        link.onclick = event => { event.preventDefault(); openExplorerCandidate(page); };
        link.onauxclick = event => { if (event.button === 1) { event.preventDefault(); createExplorerTab(page); } };
        row.appendChild(link);
        const subtitleValue = page.meta_title || page.subtitle || "";
        if (subtitleValue || page.meta_title_ja) {
          const subtitle = document.createElement('span');
          subtitle.className = 'ml-1 text-gray-700';
          subtitle.appendChild(document.createTextNode(': "'));
          appendDecoratedListText(subtitle, subtitleValue, strikeSpans, "meta_title");
          subtitle.appendChild(document.createTextNode('"'));
          if (page.meta_title_ja) {
            subtitle.appendChild(document.createTextNode(' ('));
            appendDecoratedListText(subtitle, page.meta_title_ja, strikeSpans, "meta_title_ja");
            subtitle.appendChild(document.createTextNode(')'));
          }
          row.appendChild(subtitle);
        }
        const badge = document.createElement('span');
        badge.className = `ml-2 rounded px-1.5 py-0.5 text-[10px] ${page.saved ? 'bg-emerald-100 text-emerald-800' : 'bg-gray-100 text-gray-600'}`;
        badge.textContent = page.saved ? '保存済み' : '未保存';
        row.appendChild(badge);
        host.appendChild(row);
      });
      sections.forEach(section => {
        appendHeading(section.title, section.key, section.level === 4 ? 4 : 3);
        if (section.wikiCategory) {
          const dedicatedLink = document.createElement("button");
          dedicatedLink.type = "button";
          dedicatedLink.className = section.level === 4
            ? "mb-2 ml-4 rounded border border-[#d8c99e] bg-[#fff8dc] px-3 py-1.5 text-xs font-semibold text-[#91520f] hover:bg-[#f7edcf]"
            : "mb-3 rounded border border-[#d8c99e] bg-[#fff8dc] px-3 py-1.5 text-xs font-semibold text-[#91520f] hover:bg-[#f7edcf]";
          dedicatedLink.textContent = "専用一覧を開く";
          dedicatedLink.onclick = () => openExplorerCategory(section.wikiCategory);
          host.appendChild(dedicatedLink);
        }
        if (section.key === '100-999' && categoryId === 'normal_eta') {
          const buckets = new Map();
          section.pages.forEach(page => {
            const number = getIntegerLevelNumber(page);
            const start = Math.floor(number / 100) * 100;
            const key = `${start}-${start + 99}`;
            if (!buckets.has(key)) buckets.set(key, []);
            buckets.get(key).push(page);
          });
          Array.from(buckets.entries()).sort((left, right) => Number(left[0].split('-')[0]) - Number(right[0].split('-')[0]))
            .forEach(([range, items]) => {
              const [start, end] = range.split('-');
              const label = `${start} η から ${end} η まで`;
              appendHeading(label, `eta-${range}`, 4);
              appendPages(items, true);
            });
        } else {
          appendPages(section.pages, section.level === 4);
        }
      });
    }

    let explorerTabs = [{id: 1, title: 'New tab', page: null, history: [], historyIndex: -1}];
    let activeExplorerTabId = 1;
    let nextExplorerTabId = 2;
    let currentExplorerPage = null;
    let explorerNavigationToken = 0;

    function activeExplorerTab() {
      return explorerTabs.find(tab => tab.id === activeExplorerTabId) || explorerTabs[0];
    }

    function renderExplorerTabs() {
      const host = document.getElementById('explorer-tab-strip');
      if (!host) return;
      host.replaceChildren();
      explorerTabs.forEach(tab => {
        const active = tab.id === activeExplorerTabId;
        const shell = document.createElement('div');
        shell.className = 'group flex h-9 max-w-56 min-w-0 shrink-0 items-center gap-1 rounded-t-lg border px-2 ' +
          (active ? 'border-[#c9b98d] border-b-[#f7f0dc] bg-[#f7f0dc] text-[#49371f] shadow-sm' : 'border-transparent bg-[#e8d9b2] text-[#6c5735] hover:bg-[#f0e4c5]');
        shell.setAttribute('role', 'presentation');

        const select = document.createElement('button');
        select.type = 'button';
        select.className = 'min-w-0 flex-1 truncate text-left text-xs font-medium';
        select.setAttribute('role', 'tab');
        select.setAttribute('aria-selected', String(active));
        select.textContent = tab.title || 'New tab';
        select.title = tab.page ? tab.page.title : (tab.title || 'New tab');
        select.onclick = () => activateExplorerTab(tab.id);
        select.onauxclick = event => {
          if (event.button === 1) { event.preventDefault(); closeExplorerTab(tab.id); }
        };

        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-sm text-[#806b49] hover:bg-[#ead8b0] hover:text-[#8b2f20]';
        close.textContent = '\u00d7';
        close.title = 'Close tab';
        close.setAttribute('aria-label', 'Close ' + (tab.title || 'tab'));
        close.onclick = event => { event.stopPropagation(); closeExplorerTab(tab.id); };

        shell.append(select, close);
        host.appendChild(shell);
      });
      const active = activeExplorerTab();
      const back = document.getElementById('explorer-history-back');
      if (back) back.disabled = active.historyIndex <= 0;
      const forward = document.getElementById('explorer-history-forward');
      if (forward) forward.disabled = active.historyIndex >= active.history.length - 1;
      const address = document.getElementById('explorer-address');
      if (address) address.textContent = active.page && active.page.title ? active.page.title : 'Backrooms Wiki';
    }

    function createExplorerTab(item = null) {
      const tab = {id: nextExplorerTabId++, title: item ? item.title : 'New tab', page: null, history: [], historyIndex: -1};
      explorerTabs.push(tab);
      activeExplorerTabId = tab.id;
      renderExplorerTabs();
      if (item) openExplorerCandidate(item);
      else showExplorerIndexHome();
    }

    function closeExplorerTab(id) {
      if (explorerTabs.length === 1) {
        explorerTabs[0] = {id: 1, title: 'New tab', page: null, history: [], historyIndex: -1};
        activeExplorerTabId = 1;
        currentExplorerPage = null;
        showExplorerIndexHome();
        renderExplorerTabs();
        return;
      }
      const index = explorerTabs.findIndex(tab => tab.id === id);
      if (index < 0) return;
      explorerTabs.splice(index, 1);
      if (activeExplorerTabId === id) {
        activeExplorerTabId = explorerTabs[Math.max(0, index - 1)].id;
        activateExplorerTab(activeExplorerTabId);
      } else renderExplorerTabs();
    }

    async function activateExplorerTab(id) {
      activeExplorerTabId = id;
      const tab = activeExplorerTab();
      renderExplorerTabs();
      if (tab.page) await displayExplorerPage(tab.page);
      else { currentExplorerPage = null; showExplorerIndexHome(); }
    }

    async function goExplorerTabBack() {
      const tab = activeExplorerTab();
      if (!tab || tab.historyIndex <= 0) return;
      tab.historyIndex--;
      tab.page = tab.history[tab.historyIndex];
      await displayExplorerPage(tab.page);
      renderExplorerTabs();
    }

    async function goExplorerTabForward() {
      const tab = activeExplorerTab();
      if (!tab || tab.historyIndex >= tab.history.length - 1) return;
      tab.historyIndex++;
      tab.page = tab.history[tab.historyIndex];
      await displayExplorerPage(tab.page);
      renderExplorerTabs();
    }

    async function reloadExplorerTab() {
      const tab = activeExplorerTab();
      if (!tab || !tab.page) return;
      await displayExplorerPage(tab.page);
      renderExplorerTabs();
    }

    async function openExplorerCandidate(item) {
      const tab = activeExplorerTab();
      const page = {
        title: item.title,
        url: item.url || '',
        html_path: item.html_path || '',
        remote_html: item.remote_html || '',
        saved: Boolean(item.saved)
      };
      tab.history = tab.history.slice(0, tab.historyIndex + 1);
      tab.history.push(page);
      tab.historyIndex = tab.history.length - 1;
      tab.page = page;
      tab.title = item.title || 'Untitled';
      renderExplorerTabs();
      await displayExplorerPage(page);
    }

    async function displayExplorerPage(page) {
      const token = ++explorerNavigationToken;
      currentExplorerPage = page;
      activeExplorerTab().page = page;
      document.getElementById('explorer-index-home').classList.add('hidden');
      document.getElementById('explorer-index-category').classList.add('hidden');
      document.getElementById('explorer-index-local-saved').classList.add('hidden');
      document.getElementById('wiki-page-container').classList.remove('hidden');
      if (!page.saved) {
        const article = document.getElementById('view-article-content');
        document.getElementById('view-level-id').textContent = page.title || 'Wiki記事';
        document.getElementById('view-meta-title').textContent = '';
        const attribution = document.getElementById('article-attribution');
        attribution.textContent = '';
        attribution.classList.add('hidden');
        document.getElementById('translation-article-banner').classList.add('hidden');
        article.dataset.articleKind = 'unsaved';
        article.classList.remove('article-kind-item');
        article.textContent = 'ローカルデータなし：この記事はまだ保存されていません。Wiki本文を閲覧するには、HTML取得・データ抽出から保存してください。';

        // Clear every article-specific field before showing an unsaved page.
        // This also prevents the previous page's image/license metadata lingering.
        explorerImages = [];
        document.getElementById('print-source-title').textContent = page.title || '';
        const printSourceLink = document.getElementById('print-source-link');
        printSourceLink.removeAttribute('href');
        printSourceLink.textContent = '';
        printSourceLink.classList.add('hidden');
        document.getElementById('view-category').textContent = '未保存';
        document.getElementById('view-danger-tag').textContent = '情報なし';
        document.getElementById('infobox-title').textContent = page.title || '';
        document.getElementById('infobox-danger').textContent = '情報なし';
        document.getElementById('infobox-comprehension').textContent = '情報なし';
        document.getElementById('infobox-category').textContent = '未保存';
        document.getElementById('infobox-lang').textContent = '-';
        document.getElementById('infobox-special-fields').replaceChildren();
        document.getElementById('infobox-special-fields').classList.add('hidden');
        document.getElementById('infobox-danger-row').classList.remove('hidden');
        document.getElementById('infobox-comprehension-row').classList.remove('hidden');
        const mainImg = document.getElementById('infobox-main-img');
        mainImg.removeAttribute('src');
        mainImg.classList.add('hidden');
        document.getElementById('infobox-no-img').classList.remove('hidden');
        document.getElementById('infobox-caption').textContent = '';
        document.getElementById('view-image-gallery').replaceChildren();
        document.getElementById('gallery-container').classList.add('hidden');
        renderExplorerGallery();
        const infobox = document.querySelector('#wiki-page-container aside');
        if (infobox) infobox.classList.add('hidden');
      } else if (page.remote_html) {
        const article = document.getElementById('view-article-content');
        document.getElementById('view-level-id').textContent = page.title || 'Wiki記事';
        document.getElementById('view-meta-title').textContent = '';
        document.getElementById('article-attribution').classList.add('hidden');
        document.getElementById('translation-article-banner').classList.add('hidden');
        article.dataset.articleKind = 'remote';
        article.classList.remove('article-kind-item');
        article.innerHTML = page.remote_html;
        bindExplorerArticleLinks(article);
        const infobox = document.querySelector('#wiki-page-container aside');
        if (infobox) infobox.classList.add('hidden');
        document.getElementById('gallery-container').classList.add('hidden');
      } else {
        const infobox = document.querySelector('#wiki-page-container aside');
        if (infobox) infobox.classList.remove('hidden');
        await loadLevelDetail(page.title, page.url || "", token);
      }
      if (token !== explorerNavigationToken) return;
      const articleMissing = (document.getElementById('view-article-content').textContent || '').includes('本文データがありません');
      document.getElementById('explorer-unsaved-actions').classList.toggle('hidden', !articleMissing);
      renderExplorerTabs();
    }

    function showLevelSearchSuggestions() {
      renderLevelSearchSuggestions();
    }

    function hideLevelSearchSuggestions() {
      const panel = document.getElementById('level-search-suggestions');
      panel.classList.add('hidden');
      document.getElementById('search-input').setAttribute('aria-expanded', 'false');
      levelSearchActiveIndex = -1;
    }

    function renderLevelSearchSuggestions() {
      const input = document.getElementById('search-input');
      const panel = document.getElementById('level-search-suggestions');
      if (document.activeElement !== input) return;
      const query = input.value.trim().toLocaleLowerCase();
      const items = levelSuggestionItems(query);
      panel.replaceChildren();
      if (!items.length) {
        panel.innerHTML = `<div class="px-3 py-2 text-xs text-gray-500">${query ? '一致する階層はありません' : '階層名を入力して検索'}</div>`;
      } else {
        items.forEach((item, index) => {
          const button = document.createElement('button');
          button.type = 'button';
          button.role = 'option';
          button.setAttribute('aria-selected', String(index === levelSearchActiveIndex));
          button.className = `block w-full border-b border-[#eee5ce] px-3 py-2 text-left hover:bg-[#fff8df] ${index === levelSearchActiveIndex ? 'bg-[#fff2c4]' : ''}`;
          button.innerHTML = `<span class="mr-2">${item.saved ? '📖' : '🌐'}</span><strong class="text-sm text-gray-800">${escapeHTML(item.title)}</strong><span class="ml-2 text-[10px] text-gray-500">${item.saved ? '保存済み' : '未保存'} · ${escapeHTML(item.category || '階層')}</span>${item.subtitle ? `<span class="block pl-7 text-[11px] text-gray-600">${escapeHTML(item.subtitle)}</span>` : ''}`;
          button.onmousedown = event => event.preventDefault();
          button.onclick = () => selectLevelCandidate(item);
          panel.appendChild(button);
        });
      }
      panel.classList.remove('hidden');
      input.setAttribute('aria-expanded', 'true');
    }

    function levelSuggestionItems(query = document.getElementById('search-input').value.trim().toLocaleLowerCase()) {
      if (query) return levelSearchResults.slice(0, 10);
      let recent = [];
      try { recent = JSON.parse(localStorage.getItem('brnavi-recent-levels') || '[]'); } catch (_) {}
      return [...recent.map(title => levelSearchResults.find(item => item.title === title)).filter(Boolean), ...levelSearchResults]
        .filter((item, index, all) => all.findIndex(other => other.title === item.title) === index)
        .slice(0, 8);
    }

    function onLevelSearchKeydown(event) {
      const panel = document.getElementById('level-search-suggestions');
      if (event.key === 'Escape') { hideLevelSearchSuggestions(); return; }
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp' && event.key !== 'Enter') return;
      const items = levelSuggestionItems();
      if (!items.length) return;
      event.preventDefault();
      if (event.key === 'Enter') {
        selectLevelCandidate(items[Math.max(0, levelSearchActiveIndex)]);
        return;
      }
      levelSearchActiveIndex = event.key === 'ArrowDown'
        ? (levelSearchActiveIndex + 1) % items.length
        : (levelSearchActiveIndex <= 0 ? items.length - 1 : levelSearchActiveIndex - 1);
      renderLevelSearchSuggestions();
    }

    document.addEventListener('pointerdown', event => {
      if (!event.target.closest('#search-input') && !event.target.closest('#level-search-suggestions')) hideLevelSearchSuggestions();
    });

    // 本文TXTのマークダウン・見出し整形
    function escapeHTML(value) {
      return String(value).replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      })[ch]);
    }

    function dangerBadgeClass(value) {
      const text = String(value || '').trim();
      const number = text.match(/^([0-6])\s*\/\s*5/);
      if (number && Number(number[1]) <= 1) return 'inline-flex rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-bold text-emerald-800';
      if (number && Number(number[1]) <= 3) return 'inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-xs font-bold text-amber-800';
      if (number) return 'inline-flex rounded-full bg-rose-100 px-2 py-0.5 text-xs font-bold text-rose-800';
      if (/可変|条件付き|N\s*\/\s*A/i.test(text)) return 'inline-flex rounded-full bg-violet-100 px-2 py-0.5 text-xs font-bold text-violet-800';
      return 'inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-xs font-bold text-slate-700';
    }

    let routeLastResults = [];

    function showRouteResults() {
      document.getElementById('route-detail-view').classList.add('hidden');
      document.getElementById('route-results-list').classList.remove('hidden');
    }

    function showRouteDetail(routeIndex) {
      const route = routeLastResults[routeIndex];
      if (!route) return;
      const edgeInfo = routeLastResults.edge_info || {};
      const levelBlock = node => `<article class="rounded-lg border border-[#d6c79a] bg-white p-4 shadow-sm">
        <div class="flex flex-wrap items-center justify-between gap-3"><div><div class="text-lg font-bold">${escapeHTML(node.id)}</div><div class="text-sm text-[#91520f]">${escapeHTML(node.title || '題名なし')}</div></div>
        <div class="flex flex-wrap items-center gap-2"><span class="${dangerBadgeClass(node.danger)}">危険度 ${escapeHTML(node.danger || '未登録')}</span><span class="rounded-full bg-sky-100 px-2 py-0.5 text-xs font-bold text-sky-800">理解度 ${escapeHTML(node.comprehension || '未登録')}</span><span class="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-bold text-amber-800">データ状態 ${escapeHTML(node.data_status || 'Unknown')}</span>
        <button type="button" data-route-level="${escapeHTML(node.id)}" class="rounded bg-[#91520f] px-3 py-1.5 text-xs font-semibold text-white">Level 図鑑で開く</button></div></div></article>`;
      let timeline = levelBlock(route[0]);
      for (let index = 0; index < route.length - 1; index++) {
        const source = route[index], target = route[index + 1];
        const transitions = edgeInfo[`${source.id}\n${target.id}`] || [];
        const details = transitions.length ? transitions.map(info => `<div class="rounded border border-[#e4d6b2] bg-[#fbf6de] p-3">
          <div class="text-xs font-bold text-[#78440f]">${[info.category, info.type].filter(Boolean).map(escapeHTML).join(' · ') || '移動条件'}</div>
          <div class="mt-1 flex flex-wrap gap-1 text-[11px]">確実性: ${escapeHTML(info.certainty || 'Unknown')} · 利用可否: ${escapeHTML(info.availability || 'Unknown')} · 証拠: ${escapeHTML(info.evidence_type || 'Unknown')} · 頻度: ${escapeHTML(info.probability || 'Unknown')} · 審査: ${escapeHTML(info.review_status || '—')}</div>
          <div class="mt-1 whitespace-pre-wrap break-words text-base font-semibold leading-relaxed">${escapeHTML(info.condition_display !== undefined ? info.condition_display : (info.condition || '条件の記載なし'))}</div>
          ${(info.evidence || []).map(ev => `<div class="mt-2 rounded border border-[#e4d6b2] bg-white p-2 text-xs"><div class="font-semibold">記事証拠: ${escapeHTML(ev.article_title || ev.article_id || '不明')} · ${escapeHTML(ev.relation_type || '区分不明')}</div><div class="mt-1 whitespace-pre-wrap">${escapeHTML(ev.condition_display !== undefined ? ev.condition_display : (ev.condition || ''))}</div><div class="mt-1 text-gray-500">抽出: ${escapeHTML(ev.extract_method || '不明')} · 信頼度: ${escapeHTML(ev.confidence || '未設定')}</div></div>`).join('')}</div>`).join('') : '<div class="text-sm text-gray-600">移動条件の記載はありません。</div>';
        timeline += `<div class="ml-6 border-l-2 border-[#c68a2b] py-3 pl-5"><div class="mb-2 text-xs font-bold text-[#91520f]">↓ ${escapeHTML(source.id)} から ${escapeHTML(target.id)} へ</div><div class="space-y-2">${details}</div></div>${levelBlock(target)}`;
      }
      const content = document.getElementById('route-detail-content');
      content.innerHTML = `<h3 class="mb-4 text-lg font-bold">ルート ${routeIndex + 1} · ${route.length - 1} 接続</h3><div class="space-y-0">${timeline}</div>`;
      content.querySelectorAll('[data-route-level]').forEach(button => button.addEventListener('click', () => openRouteLevel(button.dataset.routeLevel)));
      document.getElementById('route-results-list').classList.add('hidden');
      document.getElementById('route-detail-view').classList.remove('hidden');
    }

    async function openRouteLevel(levelId) {
      switchTab('explorer');
      try {
        const candidate = levelSearchResults.find(item => explorerTitleKey(item.title) === explorerTitleKey(levelId));
        await openExplorerCandidate(candidate || {title: levelId, saved: false});
        const article = document.getElementById('view-article-content').textContent || '';
        if (!article.includes('本文データがありません')) return;
      } catch (_) {}
      try {
        const result = await pywebview.api.search_online_wiki(levelId);
        const page = (result.pages || []).find(item => String(item.title || '').toLowerCase().includes(levelId.toLowerCase()));
        if (page && page.url) {
          await openAIAnswerLink(page.url);
          document.getElementById('html-download-status').textContent = `${levelId} は未保存のため内蔵ブラウザーで開きました。保存する場合は「表示中を保存」を押してください。`;
          return;
        }
      } catch (_) {}
      switchTab('wiki');
      document.getElementById('html-download-search').value = levelId;
      searchOnlineWiki();
    }

    async function searchRoutes() {
      const start = document.getElementById('route-start').value.trim();
      const goal = document.getElementById('route-goal').value.trim();
      const status = document.getElementById('route-status');
      const output = document.getElementById('route-results');
      output.innerHTML = '';
      routeLastResults = [];
      showRouteResults();
      if (!start || !goal) { status.textContent = '出発地と目的地を入力してください。'; return; }
      status.textContent = '経路を検索しています…';
      try {
        const result = await pywebview.api.search_routes(start, goal, {
          include_uncertain: document.getElementById('route-include-uncertain').checked,
          include_unknown: document.getElementById('route-include-unknown').checked,
          include_rumor: document.getElementById('route-include-rumor').checked,
          include_closed: document.getElementById('route-include-closed').checked,
          include_needs_review: document.getElementById('route-include-review').checked
        });
        if (result.error) {
          const groups = [['出発地の候補', result.start_candidates], ['目的地の候補', result.goal_candidates]];
          const candidates = groups.filter(([, values]) => values && values.length).map(([label, values]) => `<div class="mt-2"><b>${label}</b><div class="flex flex-wrap gap-2 mt-1">${values.map(n => `<button type="button" data-route-field="${label.startsWith('出発') ? 'route-start' : 'route-goal'}" data-route-id="${escapeHTML(n.id)}" class="px-2 py-1 border rounded text-xs hover:bg-amber-50">${escapeHTML(n.id)}${n.title ? ` — ${escapeHTML(n.title)}` : ''}</button>`).join('')}</div></div>`).join('');
          output.innerHTML = `<div class="rounded border border-amber-300 bg-amber-50 p-3 text-sm">${escapeHTML(result.error)}${candidates}</div>`;
          output.querySelectorAll('[data-route-field]').forEach(button => button.addEventListener('click', () => { document.getElementById(button.dataset.routeField).value = button.dataset.routeId; }));
          status.textContent = ''; return;
        }
        status.textContent = result.message || `${result.routes.length}件の経路`;
        routeLastResults = result.routes || [];
        routeLastResults.edge_info = result.edge_info || {};
        output.innerHTML = routeLastResults.map((route, routeIndex) => {
          const cards = route.map((node, index) => {
            const badge = `<span class="${dangerBadgeClass(node.danger)}">危険度 ${escapeHTML(node.danger || '未登録')}</span>`;
            return `<div class="flex items-center gap-2 min-w-max"><article class="w-48 rounded-lg border-2 border-[#d6c79a] bg-[#fffdf5] p-3 shadow-sm"><div class="font-bold text-sm">${escapeHTML(node.id)}</div><div class="text-xs text-[#91520f] mt-1">${escapeHTML(node.title || '題名なし')}</div><div class="mt-2">${badge}</div><div class="text-xs text-gray-600 mt-1">理解度 ${escapeHTML(node.comprehension || '未登録')}</div></article>${index < route.length - 1 ? '<span class="text-xl font-bold text-[#b45309]">→</span>' : ''}</div>`;
          }).join('');
          return `<button type="button" data-route-index="${routeIndex}" class="w-full rounded-lg border border-[#e2d8b5] bg-[#fffdf5] p-3 text-left hover:border-[#b45309] hover:shadow"><span class="mb-2 block text-sm font-bold">ルート ${routeIndex + 1} · ${route.length - 1} 接続　詳細を見る →</span><span class="flex items-center gap-2 overflow-x-auto pb-1">${cards}</span></button>`;
        }).join('') || '<div class="p-3 text-sm text-gray-600">到達可能な経路がありません。</div>';
        output.querySelectorAll('[data-route-index]').forEach(button => button.addEventListener('click', () => showRouteDetail(Number(button.dataset.routeIndex))));
      } catch (error) { status.textContent = `検索に失敗しました: ${error}`; }
    }

    function explorerWikiTargetHref(target) {
      const value = String(target || '').trim();
      if (/^https?:\/\//i.test(value)) return value;
      if (value.startsWith('/ja/wiki/')) return 'https://backrooms.fandom.com' + value;
      const title = value.replace(/_/g, ' ').trim();
      const slug = title.split('/').map(part => encodeURIComponent(part.replace(/ /g, '_'))).join('/');
      return 'https://backrooms.fandom.com/ja/wiki/' + slug;
    }

    function bindExplorerArticleLinks(root) {
      if (!root) return;
      root.querySelectorAll('a[href]').forEach(link => {
        if (!link.dataset.wikiTarget) {
          try {
            const parsed = new URL(link.href, window.location.href);
            if (parsed.hostname === 'backrooms.fandom.com' && parsed.pathname.startsWith('/ja/wiki/')) {
              link.dataset.wikiTarget = parsed.href;
            }
          } catch (_) {}
        }
        if (!link.dataset.wikiTarget) return;
        link.addEventListener('click', event => {
          if (event.button !== 0) return;
          event.preventDefault();
          openExplorerWikiTarget(link.dataset.wikiTarget, event.ctrlKey || event.metaKey || event.shiftKey);
        });
        link.addEventListener('auxclick', event => {
          if (event.button !== 1) return;
          event.preventDefault();
          openExplorerWikiTarget(link.dataset.wikiTarget, true);
        });
      });
    }

    function formatWikiContent(text) {
      if (!text) return "";
      const lines = text.split('\n');
      let html = '';
      let paragraph = [];
      function makeWikiAnchor(target, label) {
        const value = String(target || '').trim();
        return '<a href="' + escapeHTML(explorerWikiTargetHref(value)) +
          '" data-wiki-target="' + escapeHTML(value) +
          '" class="text-[#a3420b] underline hover:text-[#78350f]">' +
          escapeHTML(label || value) + '</a>';
      }
      function flushParagraph() {
        if (!paragraph.length) return;
        const linked = paragraph.map(line => {
          let safe = escapeHTML(line);
          safe = safe.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, target, label) =>
            makeWikiAnchor(target, (label || target).trim()));
          safe = safe.replace(/\[([^\]]+)\]\((https?:\/\/backrooms\.fandom\.com\/ja\/wiki\/[^\s)]+)\)/g,
            (_, label, url) => makeWikiAnchor(url, label));
          safe = safe.replace(/(<a\b[^>]*>[\s\S]*?<\/a>)|([^<]+)/g, (chunk, anchor, plain) => {
            if (anchor) return anchor;
            return (plain || '').replace(/(?<![\w])Level\s+(-?\d+(?:\.\d+)?)(?:\s*(\u03b7))?/gi, (label, number, eta) => {
              const target = 'Level ' + number + (eta ? ' η' : '');
              return makeWikiAnchor(target, label);
            });
          });
          return safe;
        }).join('<br>');
        html += '<p class="my-2.5 text-gray-800 leading-relaxed">' + linked + '</p>';
        paragraph = [];
      }
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) { flushParagraph(); continue; }
        const heading = trimmed.match(/^\[([^\]]+)\]$/);
        if (heading) {
          flushParagraph();
          html += '<div class="wiki-heading"><span>' + escapeHTML(heading[1]) + '</span></div>';
        } else paragraph.push(trimmed);
      }
      flushParagraph();
      return html;
    }

    // 印刷オプションの表示切り替え
    function togglePrintOptions(forceState) {
      const panel = document.getElementById('print-options-panel');
      if (!panel) return;
      const shouldShow = typeof forceState === 'boolean' ? forceState : panel.classList.contains('hidden');
      panel.classList.toggle('hidden', !shouldShow);
    }

    // レベル詳細の印刷
    function printLevelArticle() {
      const article = document.getElementById('wiki-page-container');

      if (!article || article.classList.contains('hidden')) {
        alert('印刷するLevel記事を開いてください。');
        return;
      }

      const enabled = document.getElementById('print-staple-enabled')?.checked ?? false;
      const edge = document.getElementById('print-staple-edge')?.value || 'left';
      document.body.dataset.printStaple = enabled ? 'true' : 'false';
      document.body.dataset.stapleEdge = edge;
      togglePrintOptions(false);
      window.print();
    }

    async function openExplorerWikiTarget(target, openInNewTab = false) {
      const value = String(target || '').trim();
      if (!value) return;
      const targetUrl = explorerWikiTargetHref(value);
      const parsedTarget = new URL(targetUrl);
      const fragment = parsedTarget.hash;
      let title = decodeURIComponent(parsedTarget.pathname.split('/ja/wiki/').pop() || '').replace(/_/g, ' ');
      const lookupUrl = parsedTarget.origin + parsedTarget.pathname;
      const normalizeArticleUrl = url => String(url || '').replace(/\/$/, '').toLocaleLowerCase();
      const existing = [...explorerIndexPages, ...levelSearchResults, ...localSavedLevels]
        .find(page => page.url && normalizeArticleUrl(page.url) === normalizeArticleUrl(lookupUrl));
      let candidate = existing;
      // Article links in the local encyclopedia are strictly local.
      // Resolve only by canonical URL or exact title; never use a partial
      // search result and never fetch an unsaved page implicitly.
      if (!candidate || !candidate.saved) {
        const result = await pywebview.api.search_candidates('');
        const pages = result || [];
        candidate = pages.find(page =>
          page.url && normalizeArticleUrl(page.url) === normalizeArticleUrl(lookupUrl)
        ) || null;
      }
      const resolvedPage = candidate || {title, url: lookupUrl, saved: false};
      if (openInNewTab) createExplorerTab(resolvedPage);
      else await openExplorerCandidate(resolvedPage);
      if (fragment) {
        const anchorId = decodeURIComponent(fragment.slice(1));
        const anchor = document.getElementById(anchorId) ||
          document.querySelector('[name="' + CSS.escape(anchorId) + '"]');
        if (anchor) anchor.scrollIntoView({block: 'start'});
      }
    }

    async function loadLevelDetail(levelId, pageUrl = "", navigationToken = explorerNavigationToken) {
      const requestedPage = currentExplorerPage;
      const d = await pywebview.api.get_level_detail(levelId, pageUrl);
      // A slower response from the previous article must never overwrite the
      // currently selected tab/page's article, infobox, images or attribution.
      if (navigationToken !== explorerNavigationToken || currentExplorerPage !== requestedPage) return;
      if (currentExplorerPage) currentExplorerPage.html_path = d.html_path || '';
      explorerImages = d.images || [];

      let articleStrikeSpans = [];
      try {
        const parsed = JSON.parse(d.strikethrough_spans || "[]");
        if (Array.isArray(parsed)) articleStrikeSpans = parsed;
      } catch (_) {}
      const levelHeading = document.getElementById('view-level-id');
      levelHeading.replaceChildren();
      appendDecoratedListText(levelHeading, d.level_id, articleStrikeSpans, "title");
      const metaHeading = document.getElementById('view-meta-title');
      metaHeading.replaceChildren();
      if (d.title && d.title !== d.level_id) {
        metaHeading.appendChild(document.createTextNode('"'));
        appendDecoratedListText(metaHeading, d.title, articleStrikeSpans, "meta_title");
        metaHeading.appendChild(document.createTextNode('"'));
        if (d.meta_title_ja) {
          metaHeading.appendChild(document.createTextNode(' (' ));
          appendDecoratedListText(metaHeading, d.meta_title_ja, articleStrikeSpans, "meta_title_ja");
          metaHeading.appendChild(document.createTextNode(')'));
        }
      }
      document.getElementById('print-source-title').textContent = d.level_id || '';
      const printSourceLink = document.getElementById('print-source-link');
      printSourceLink.removeAttribute('href');
      printSourceLink.textContent = '';
      printSourceLink.classList.add('hidden');
      try {
        const sourceUrl = new URL(String(d.url || ''));
        if (sourceUrl.protocol === 'https:' || sourceUrl.protocol === 'http:') {
          printSourceLink.href = sourceUrl.href;
          printSourceLink.textContent = sourceUrl.href;
          printSourceLink.classList.remove('hidden');
        }
      } catch (_) {
        // 元記事URLがない場合は出典リンクを表示しない。
      }
      const categoryNames = {
        normal: '無印通常階層',
        normal_eta: '通常階層 η',
        sublevel: '無印通常亜階層',
        sublevel_eta: '通常亜階層 η',
        enigmatic: '無印番外階層',
        enigmatic_eta: '番外階層 η',
        enigmatic_sublevel_eta: '番外亜階層 η',
        alias: '別名', terms: '用語', contest: 'コンテスト',
        item: '物品', entity: 'エンティティ', phenomenon: '現象',
        person: '人物', dimension: '次元', foreign: '国外階層',
        canon: 'カノン', joke_level: 'ジョーク階層',
        joke_article: 'ジョーク記事', foreign_joke: '国外ジョーク記事'
      };
      const isItemArticle = d.category === 'item';
      const isTranslation = d.source_type === 'translation';
      const articleContent = document.getElementById('view-article-content');
      articleContent.dataset.articleKind = isItemArticle ? 'item' : (isTranslation ? 'translation' : 'level');
      articleContent.classList.toggle('article-kind-item', isItemArticle);
      document.getElementById('view-category').innerText = categoryNames[d.category] || d.category || '未分類';
      document.getElementById('infobox-danger-row').classList.toggle('hidden', isItemArticle);
      document.getElementById('infobox-comprehension-row').classList.toggle('hidden', isItemArticle);
      const translationBanner = document.getElementById('translation-article-banner');
      translationBanner.classList.toggle('hidden', !isTranslation);
      translationBanner.textContent = isTranslation
        ? '翻訳記事 · 原文言語: ' + (d.language || '不明') + (d.translation_section ? ' · 翻訳元分類: ' + d.translation_section : '') + (d.section_number ? ' · 一覧番号: ' + d.section_number : '')
        : '';
      const specialFields = document.getElementById('infobox-special-fields');
      const rows = [];
      if (isItemArticle) rows.push(['記事種別', 'アイテム']);
      if (isTranslation) {
        rows.push(['記事種別', '翻訳記事']);
        if (d.translation_section) rows.push(['翻訳元分類', d.translation_section]);
        if (d.section_number) rows.push(['一覧番号', d.section_number]);
        if (d.language) rows.push(['原文言語', d.language]);
      }
      specialFields.innerHTML = rows.map(([label, value]) =>
        '<div class="grid grid-cols-[38%_1fr] border-b border-[#e5dcbe] text-xs last:border-b-0"><div class="bg-[#f0e7cf] px-2.5 py-1.5 font-semibold">' + escapeHTML(label) + '</div><div class="px-2.5 py-1.5">' + escapeHTML(value) + '</div></div>'
      ).join('');
      specialFields.classList.toggle('hidden', rows.length === 0);
      document.getElementById('view-danger-tag').innerText = isItemArticle ? 'アイテム' : d.danger;
      document.getElementById('view-danger-tag').className = isItemArticle
        ? 'rounded-full bg-[#f3ead2] px-2 py-0.5 text-xs font-semibold text-[#72552d]'
        : dangerBadgeClass(d.danger);

      // 本文の整形描画
      const attribution = document.getElementById('article-attribution');
      attribution.textContent = d.attribution || '';
      attribution.classList.toggle('hidden', !d.attribution);
      articleContent.innerHTML = formatWikiContent(d.text);
      bindExplorerArticleLinks(articleContent);

      // Infobox
      document.getElementById('infobox-title').innerText = isItemArticle ? 'アイテム · ' + d.level_id : d.level_id;
      document.getElementById('infobox-danger').innerText = d.danger;
      document.getElementById('infobox-danger').className = dangerBadgeClass(d.danger);
      document.getElementById('infobox-comprehension').innerText = d.comprehension;
      document.getElementById('infobox-category').innerText = categoryNames[d.category] || d.category || '未分類';
      document.getElementById('infobox-lang').innerText = d.language;

      // メイン画像
      const mainImg = document.getElementById('infobox-main-img');
      const noImg = document.getElementById('infobox-no-img');
      const caption = document.getElementById('infobox-caption');

      if (d.images && d.images.length > 0) {
        mainImg.src = d.images[0].src;
        mainImg.classList.toggle('object-contain', isItemArticle);
        mainImg.classList.toggle('object-cover', !isItemArticle);
        mainImg.classList.remove('hidden');
        mainImg.classList.add('cursor-zoom-in');
        mainImg.onclick = () => openImageLightbox(explorerImages, 0, d.level_id);
        noImg.classList.add('hidden');
        caption.innerText = d.images[0].name;
      } else {
        mainImg.classList.add('hidden');
        noImg.classList.remove('hidden');
        caption.innerText = "";
      }

      // 下部写真ギャラリー
      const galleryContainer = document.getElementById('gallery-container');
      const gallery = document.getElementById('view-image-gallery');
      gallery.innerHTML = '';

      if (d.images && d.images.length > 1) {
        galleryContainer.classList.remove('hidden');
        d.images.slice(1).forEach((img, index) => {
          const card = document.createElement('div');
          card.className = "bg-[#fdfaf0] border border-[#d6c99c] rounded p-2 text-center shadow-sm";
          card.innerHTML = `
            <img src="${img.src}" class="w-full h-32 object-cover rounded border border-[#dfd3a8] mb-1 cursor-zoom-in" alt="${img.name}">
            <span class="text-[10px] text-gray-500 truncate block" title="${img.name}">${img.name}</span>
          `;
          card.querySelector('img').onclick = () => openImageLightbox(explorerImages, index + 1, d.level_id);
          gallery.appendChild(card);
        });
      } else {
        galleryContainer.classList.add('hidden');
      }
    }

    let levelListLanguagesLoaded = false;
    let levelListPages = [];
    let visibleLevelListPages = [];
    let selectedLevelPages = new Map();
    let levelListAnchorUrl = null;
    let levelListTaskBusy = false;
    let currentLevelListCategory = null;

    async function loadLevelListLanguages() {
      const status = document.getElementById('level-list-status');
      status.textContent = "level_listsフォルダーを確認しています...";
      try {
        const result = await pywebview.api.get_level_list_languages();
        if (result.error) throw new Error(result.error);
        const select = document.getElementById('level-list-language');
        select.replaceChildren();
        const allOption = document.createElement('option');
        allOption.value = "ALL";
        allOption.textContent = "ALL";
        select.appendChild(allOption);
        (result.languages || []).forEach(language => {
          const option = document.createElement('option');
          option.value = language;
          option.textContent = language;
          select.appendChild(option);
        });
        if (!result.languages || result.languages.length === 0) {
          status.textContent = "言語別レベルリストがありません。環境設定でLevel一覧データを更新してください。";
          return;
        }
        levelListLanguagesLoaded = true;
        select.value = "ALL";
        await loadLevelListPages();
      } catch (error) {
        status.textContent = "レベルリストを読み込めません: " + error;
      }
    }

    async function loadLevelListPages() {
      const language = document.getElementById('level-list-language').value;
      if (!language) return;
      const status = document.getElementById('level-list-status');
      selectedLevelPages.clear();
      levelListAnchorUrl = null;
      currentLevelListCategory = null;
      updateLevelListSelectionControls();
      status.textContent = language === "ALL"
        ? "全言語のレベルリストを読み込み中..."
        : `${language}.txtを読み込み中...`;
      try {
        const result = await pywebview.api.get_level_list_pages(language);
        if (result.error) throw new Error(result.error);
        levelListPages = result.pages || [];
        filterLevelListTiles();
        status.textContent = language === "ALL"
          ? `全言語から${levelListPages.length}件を読み込みました。`
          : `${language}.txtから${levelListPages.length}件を読み込みました。`;
      } catch (error) {
        levelListPages = [];
        filterLevelListTiles();
        status.textContent = "レベルリストを読み込めません: " + error;
      }
    }

    const levelListCategoryDefinitions = [
      {id: "all", title: "階層の一覧", visual: "階層", wikiTitle: "階層の一覧",
        imageUrl: "https://static.wikia.nocookie.net/backrooms/images/f/f4/1524371231850.jpg/revision/latest/scale-to-width-down/360?cb=20220223141840&path-prefix=ja",
        imagePageUrl: "https://backrooms.fandom.com/ja/wiki/ファイル:1524371231850.jpg"},
      {id: "normal", title: "無印通常階層の一覧", visual: "0", wikiTitle: "無印通常階層の一覧",
        imageUrl: "https://static.wikia.nocookie.net/backrooms/images/7/75/14070865988_8d84e32323_o.jpg/revision/latest/scale-to-width-down/360?cb=20220224133310&path-prefix=ja",
        imagePageUrl: "https://backrooms.fandom.com/ja/wiki/ファイル:14070865988_8d84e32323_o.jpg"},
      {id: "normal_eta", title: "通常階層 η の一覧", visual: "η", wikiTitle: "通常階層 η の一覧",
        imageUrl: "https://static.wikia.nocookie.net/backrooms/images/7/7b/Playroom_of_West_Hikari_train.jpg/revision/latest/scale-to-width-down/360?cb=20220606092655&path-prefix=ja",
        imagePageUrl: "https://backrooms.fandom.com/ja/wiki/ファイル:Playroom_of_West_Hikari_train.jpg"},
      {id: "enigmatic", title: "番外階層の一覧", visual: "!", wikiTitle: "番外階層の一覧",
        imageUrl: "https://static.wikia.nocookie.net/backrooms/images/8/87/Ventilation.jpg/revision/latest/scale-to-width-down/360?cb=20220301165212&path-prefix=ja",
        imagePageUrl: "https://backrooms.fandom.com/ja/wiki/ファイル:Ventilation.jpg"},
      {id: "sublevel", title: "亜階層の一覧", visual: "1.1", wikiTitle: "亜階層の一覧",
        imageUrl: "https://static.wikia.nocookie.net/backrooms/images/5/5b/Level_4.1_%CE%B7.jpeg/revision/latest?cb=20230130123700&path-prefix=ja",
        imagePageUrl: "https://backrooms.fandom.com/ja/wiki/ファイル:Level_4.1_%CE%B7.jpeg"},
      {id: "alias", title: "別名の一覧", visual: "別名", wikiTitle: "別名の一覧",
        imageUrl: "https://static.wikia.nocookie.net/backrooms/images/1/10/%E9%9D%92%E3%81%8D%E6%B0%BE%E6%BF%AB.png/revision/latest/scale-to-width-down/360?cb=20230204154413&path-prefix=ja",
        imagePageUrl: "https://backrooms.fandom.com/ja/wiki/ファイル:青き氾濫.png"},
      {id: "terms", title: "用語の一覧", visual: "用語", wikiTitle: "用語の一覧",
        imageUrl: "", imagePageUrl: ""},
      {id: "translation", title: "翻訳の一覧", visual: "翻訳", wikiTitle: "翻訳の一覧",
        imageUrl: "https://static.wikia.nocookie.net/backrooms/images/4/4d/Wilshire_Boulevard_at_Hope_Street%2C_downtown_Los_Angeles%2C_California.jpg/revision/latest/scale-to-width-down/360?cb=20220411112616&path-prefix=ja",
        imagePageUrl: "https://backrooms.fandom.com/ja/wiki/ファイル:Wilshire_Boulevard_at_Hope_Street,_downtown_Los_Angeles,_California.jpg"},
      {id: "contest", title: "コンテストの一覧", visual: "祭", wikiTitle: "コンテストの一覧",
        imageUrl: "https://static.wikia.nocookie.net/backrooms/images/a/a4/Scattered_smoke02.jpg/revision/latest/scale-to-width-down/360?cb=20240210091314&path-prefix=ja",
        imagePageUrl: "https://backrooms.fandom.com/ja/wiki/ファイル:Scattered_smoke02.jpg"},
    ];

    const levelListCategoryLabels = {
      enigmatic_sublevel_eta: "番外亜階層 η",
      alias: "別名",
      terms: "用語",
      contest: "コンテスト",
      foreign: "国外階層",
      dimension: "次元",
      entity: "エンティティ",
      item: "物品",
      phenomenon: "現象",
      canon: "カノン",
      joke_level: "ジョーク階層",
      joke_article: "ジョーク記事",
      foreign_joke: "国外ジョーク記事",
      person: "人物",
      unknown: "その他",
    };

    function uniqueLevelListPages(pages) {
      const byUrl = new Map();
      pages.forEach(page => {
        if (page.url && !byUrl.has(page.url)) byUrl.set(page.url, page);
      });
      return Array.from(byUrl.values());
    }

    function pageHasIndexMembership(page, indexName) {
      return String(page.indexes || "").split("|").map(value => value.trim()).includes(indexName);
    }

    function normalizeExplorerCategory(value) {
      const category = String(value || '').trim().toLocaleLowerCase();
      if (/番外.*亜階層|enigmatic_sublevel/.test(category)) return 'enigmatic_sublevel_eta';
      if (/通常.*亜階層|sublevel/.test(category)) return /η|eta/.test(category) ? 'sublevel_eta' : 'sublevel';
      if (/番外階層|enigmatic/.test(category)) return /η|eta/.test(category) ? 'enigmatic_eta' : 'enigmatic';
      if (/通常階層|normal/.test(category)) return /η|eta/.test(category) ? 'normal_eta' : 'normal';
      if (/別名|alias/.test(category)) return 'alias';
      if (/翻訳|translation/.test(category)) return 'translation';
      if (/コンテスト|contest/.test(category)) return 'contest';
      return category;
    }

    function isInternationalBranchPage(page) {
      const language = String(page.language || "").trim().toUpperCase();
      if (["RU", "PL", "IT", "PT", "UA", "TR", "ZH"].includes(language)) return true;

      // hierarchy.html の支部見出しを言語列の欠落・旧データに対する補助判定に使う。
      const hierarchyMembership = getWikiMembership(page, "hierarchy");
      const sectionPath = hierarchyMembership?.section_path || [];
      if (Array.isArray(sectionPath) && sectionPath.some(section =>
        ["A", "PL", "IT", "PT", "UA", "TR", "ZH"].includes(
          String(section?.heading || "").replace(/\s*\[[ xX✓✔]?\]\s*$/, "").trim().toUpperCase()
        )
      )) return true;

      // 支部別タイトル形式も補助判定に使い、language が欠落した記事の混入を防ぐ。
      const title = String(page.title || "").trim();
      return /^Level\s+(?:A|PL|UA|ZH)\s*-?\s*\(?-?\d/i.test(title) ||
        /^Level\s+-?\d+(?:\.\d+)*(?:\s*-\s*|\s+)(?:IT|PT|TR)\b/i.test(title);
    }

    function levelListPagesForCategory(categoryId, sourcePages = levelListPages) {
      const branchMatch = String(categoryId || "").match(/^branch:([A-Z]+):([a-z_]+)$/);
      const pages = sourcePages.filter(page => {
        const category = normalizeExplorerCategory(page.category);
        const language = String(page.language || "unknown").toUpperCase();
        const source = String(page.source_type || "").toLowerCase();
        const sources = source.split("|").map(value => value.trim());
        const defaultLanguage = ["EN", "JP", "UNKNOWN"].includes(language);
        const internationalBranch = isInternationalBranchPage(page);
        if (branchMatch) {
          return language === branchMatch[1] && category === branchMatch[2];
        }
        if (categoryId === "all") return true;
        if (categoryId === "normal") {
          // 無印通常階層はEN記事、または normal.html への明示的な掲載所属だけを採用する。
          // hierarchy.html 上の国際支部は、カテゴリが normal でも基本タイルへ混ぜない。
          const belongsToNormalIndex = Boolean(getWikiMembership(page, "normal")) ||
            pageHasIndexMembership(page, "normal");
          return category === "normal" && !internationalBranch &&
            (language === "EN" || belongsToNormalIndex) && !sources.includes("translation");
        }
        if (categoryId === "normal_eta") {
          return category === "normal_eta" && defaultLanguage && !internationalBranch &&
            !sources.includes("translation");
        }
        if (categoryId === "enigmatic") {
          return defaultLanguage && !internationalBranch && !sources.includes("translation") &&
            ["enigmatic", "enigmatic_eta"].includes(category);
        }
        if (categoryId === "sublevel") {
          return defaultLanguage && !internationalBranch && !sources.includes("translation") &&
            ["sublevel", "sublevel_eta", "enigmatic_sublevel_eta"].includes(category);
        }
        if (categoryId === "translation") {
          return sources.includes("translation") || category === "translation";
        }
        if (categoryId === "alias") {
          return category === "alias" || source === "alias" || pageHasIndexMembership(page, "alias");
        }
        if (categoryId === "terms") {
          return category === "terms" || source === "terms" || pageHasIndexMembership(page, "terms");
        }
        if (categoryId === "contest") {
          return category === "contest" || category === "contest_level" || pageHasIndexMembership(page, "contest");
        }
        return category === categoryId && defaultLanguage && !internationalBranch;
      });
      return uniqueLevelListPages(pages);
    }

    function getLevelListCategories(sourcePages = levelListPages) {
      const categories = levelListCategoryDefinitions
        .map(category => ({...category, pages: levelListPagesForCategory(category.id, sourcePages)}))
      const knownIds = new Set(levelListCategoryDefinitions.map(category => category.id));
      const additionalIds = Array.from(new Set(sourcePages.map(page =>
        normalizeExplorerCategory(page.category)
      )))
        .filter(id => id && !knownIds.has(id) &&
          !["normal_eta", "enigmatic_eta", "sublevel_eta"].includes(id) &&
          !id.startsWith("normal") && !id.startsWith("enigmatic") && !id.startsWith("sublevel"))
        .sort((left, right) => left.localeCompare(right));
      additionalIds.forEach(id => {
        const pages = levelListPagesForCategory(id, sourcePages);
        if (!pages.length) return;
        const label = pages[0].category_label || levelListCategoryLabels[id] || id;
        categories.push({
          id,
          title: `${label}の一覧`,
          visual: label.slice(0, 3),
          wikiTitle: "",
          pages,
        });
      });

      // 国際支部はカテゴリと支部言語の組み合わせで個別の入口を生成する。
      const branchLabels = {RU: "A", PL: "PL", IT: "IT", PT: "PT", UA: "UA", TR: "TR", ZH: "ZH"};
      const categoryLabels = {
        normal: "通常階層", normal_eta: "通常階層 η",
        enigmatic: "番外階層", enigmatic_eta: "番外階層 η",
        sublevel: "亜階層", sublevel_eta: "亜階層 η",
        enigmatic_sublevel_eta: "番外亜階層 η",
      };
      const branchKeys = new Set();
      sourcePages.forEach(page => {
        const language = String(page.language || "").toUpperCase();
        const category = normalizeExplorerCategory(page.category);
        if (!branchLabels[language] || !categoryLabels[category]) return;
        const key = `${language}:${category}`;
        if (branchKeys.has(key)) return;
        branchKeys.add(key);
        const pages = levelListPagesForCategory(`branch:${language}:${category}`, sourcePages);
        if (!pages.length) return;
        const branchCode = branchLabels[language];
        const label = categoryLabels[category];
        categories.push({
          id: `branch:${language}:${category}`,
          title: `${label} ${branchCode}（${language}）の一覧`,
          visual: branchCode,
          wikiTitle: "",
          pages,
        });
      });
      return categories;
    }

    function renderLevelListCategoryTiles() {
      const host = document.getElementById('level-list-categories');
      const query = document.getElementById('level-list-filter').value.trim().toLocaleLowerCase();
      host.replaceChildren();
      const categories = getLevelListCategories().filter(category =>
        !query || category.title.toLocaleLowerCase().includes(query)
      );
      categories.forEach((category, index) => {
        const card = document.createElement('div');
        card.className = "overflow-hidden rounded border border-[#d6c79a] bg-[#fff9df] shadow-sm transition hover:-translate-y-0.5 hover:shadow-md";
        const tile = document.createElement('button');
        tile.type = "button";
        tile.className = "block w-full text-left focus:outline-none focus:ring-2 focus:ring-inset focus:ring-[#b45309]";
        tile.dataset.category = category.id;
        tile.onclick = () => openLevelListCategory(category.id);
        const art = document.createElement('div');
        art.className = "relative flex h-36 items-center justify-center overflow-hidden border-b border-[#d6c79a]";
        const colorSets = [
          ["#7b7745", "#d0c990"], ["#4a4b42", "#a9a89a"], ["#468f78", "#c5e1b5"],
          ["#784325", "#dfa251"], ["#6e7353", "#d6c57f"], ["#68808a", "#c1d5ce"],
          ["#687e82", "#c4d2cc"], ["#81785e", "#d9d0ad"],
        ];
        const colors = colorSets[index % colorSets.length];
        art.style.background = `linear-gradient(145deg, ${colors[0]}, ${colors[1]})`;
        if (category.imageUrl) {
          const image = document.createElement('img');
          image.src = category.imageUrl;
          image.alt = `${category.title}の画像`;
          image.loading = "lazy";
          image.className = "absolute inset-0 h-full w-full object-cover";
          image.onerror = () => image.remove();
          art.appendChild(image);
        }
        const visual = document.createElement('span');
        visual.className = "relative select-none rounded bg-black/25 px-3 py-1 font-serif text-4xl font-bold text-white/90 drop-shadow";
        visual.textContent = category.visual;
        art.appendChild(visual);
        const caption = document.createElement('div');
        caption.className = "flex items-center justify-between gap-2 px-3 py-3";
        const title = document.createElement('span');
        title.className = "text-sm font-bold text-gray-800";
        title.textContent = category.title;
        const count = document.createElement('span');
        count.className = "shrink-0 text-[11px] text-gray-500";
        count.textContent = `${category.pages.length}件`;
        caption.append(title, count);
        tile.append(art, caption);
        card.appendChild(tile);
        if (category.imagePageUrl) {
          const attribution = document.createElement('a');
          attribution.href = category.imagePageUrl;
          attribution.target = "_blank";
          attribution.rel = "noopener noreferrer";
          attribution.className = "block border-t border-[#eee2bd] px-3 py-1 text-right text-[10px] text-[#765b32] hover:underline";
          attribution.textContent = "画像の出典・情報";
          card.appendChild(attribution);
        }
        host.appendChild(card);
      });
      document.getElementById('level-list-status').textContent =
        `${categories.length}件の一覧から選択できます。`;
    }

    function openLevelListCategory(categoryId) {
      currentLevelListCategory = categoryId;
      selectedLevelPages.clear();
      levelListAnchorUrl = null;
      document.getElementById('level-list-categories').style.display = "none";
      document.getElementById('level-list-browser').style.display = "flex";
      document.getElementById('level-list-filter').style.display = "block";
      const category = getLevelListCategories().find(item => item.id === categoryId);
      document.getElementById('level-list-category-title').textContent =
        category ? category.title : "階層の一覧";
      document.getElementById('level-list-filter').value = "";
      filterLevelListTiles();
      updateLevelListSelectionControls();
    }

    function showLevelListCategories() {
      currentLevelListCategory = null;
      selectedLevelPages.clear();
      levelListAnchorUrl = null;
      document.getElementById('level-list-browser').style.display = "none";
      document.getElementById('level-list-categories').style.display = "grid";
      document.getElementById('level-list-filter').style.display = "none";
      document.getElementById('level-list-filter').value = "";
      document.getElementById('level-list-toc').classList.add("hidden");
      renderLevelListCategoryTiles();
      updateLevelListSelectionControls();
    }

    function getIntegerLevelNumber(page) {
      // Accept translated IDs and nested sublevels while grouping by the main integer.
      const match = String(page.title || "").match(
        /^Level\s+(?:[A-Z]{1,3}-|[A-Z]{1,3}\s+)?\(?(-?\d+)(?:\.\d+)*\)?(?:\s*\u03B7)?(?:[-\s].*)?$/i
      );
      return match ? Number(match[1]) : null;
    }

    function getLevelSortNumber(page) {
      const match = String(page.title || "").match(
        /^Level\s+(?:[A-Z]{1,3}-|[A-Z]{1,3}\s+)?\(?(-?\d+(?:\.\d+)*)\)?(?:\s*\u03B7)?(?:[-\s].*)?$/i
      );
      return match ? match[1].split(".").map(Number) : null;
    }

    function getLevelNumberRange(number, categoryId = currentLevelListCategory) {
      const suffix = /(?:^|:)(?:normal_eta|enigmatic_eta|sublevel_eta|enigmatic_sublevel_eta)$/.test(categoryId) ? "η" : "";
      if (number === 0) return {key: "zero", title: `0${suffix}`};
      if (number >= 1 && number <= 9) return {key: "1-9", title: `1${suffix} から 9${suffix} まで`};
      if (number >= 10 && number <= 99) return {key: "10-99", title: `10${suffix} から 99${suffix} まで`};
      if (number >= 100 && number <= 999) return {key: "100-999", title: `100${suffix} から 999${suffix} まで`};
      if (number >= 1000) return {key: "1000-plus", title: `1000${suffix} から`};
      if (number <= -1 && number >= -9) return {key: "minus-1-9", title: `-1${suffix} から -9${suffix} まで`};
      if (number <= -10 && number >= -99) return {key: "minus-10-99", title: `-10${suffix} から -99${suffix} まで`};
      if (number <= -100 && number >= -999) return {key: "minus-100-999", title: `-100${suffix} から -999${suffix} まで`};
      return {key: "minus-1000-plus", title: `-1000${suffix} から`};
    }

    function compareLevelListPages(left, right) {
      const leftParts = getLevelSortNumber(left);
      const rightParts = getLevelSortNumber(right);
      if (leftParts && rightParts) {
        const length = Math.max(leftParts.length, rightParts.length);
        for (let index = 0; index < length; index += 1) {
          const leftValue = leftParts[index] ?? -1;
          const rightValue = rightParts[index] ?? -1;
          if (leftValue !== rightValue) return leftValue - rightValue;
        }
      } else if (leftParts) {
        return -1;
      } else if (rightParts) {
        return 1;
      }
      return String(left.title).localeCompare(String(right.title), "ja", {numeric: true});
    }

    function compareWikiIndexOrder(left, right) {
      const sectionOrder = Number(left.section_order || 0) - Number(right.section_order || 0);
      if (sectionOrder) return sectionOrder;
      const pageOrder = Number(left.page_order || 0) - Number(right.page_order || 0);
      if (pageOrder) return pageOrder;
      return compareLevelListPages(left, right);
    }

    function getWikiMembership(page, indexName = "") {
      if (!indexName) return null;
      try {
        const memberships = JSON.parse(page.index_memberships || "[]");
        return Array.isArray(memberships)
          ? memberships.find(item => item && item.index_name === indexName) || null
          : null;
      } catch (_) {
        return null;
      }
    }

    function getWikiSectionHeading(page, indexName = "") {
      try {
        const membership = getWikiMembership(page, indexName);
        const path = membership?.section_path || JSON.parse(page.section_path || "[]");
        if (Array.isArray(path) && path.length) {
          const heading = path[path.length - 1]?.heading;
          if (heading) return String(heading).replace(/\s*\[[ xX✓✔]?\]\s*$/, "").trim();
        }
      } catch (_) {}
      return page.category_label || page.category || "その他";
    }

    function compareWikiMembershipOrder(left, right, indexName = "") {
      const leftMembership = getWikiMembership(left, indexName) || left;
      const rightMembership = getWikiMembership(right, indexName) || right;
      const sectionOrder = Number(leftMembership.section_order || 0) - Number(rightMembership.section_order || 0);
      if (sectionOrder) return sectionOrder;
      const pageOrder = Number(leftMembership.page_order || 0) - Number(rightMembership.page_order || 0);
      if (pageOrder) return pageOrder;
      return compareLevelListPages(left, right);
    }

    function getLevelListSections(pages, categoryId = currentLevelListCategory) {
      if (["enigmatic", "sublevel", "alias", "terms", "contest"].includes(categoryId)) {
        const groups = new Map();
        const sectionIndex = {alias: "alias", terms: "terms", contest: "contest"}[categoryId] || "";
        pages.forEach(page => {
          const title = getWikiSectionHeading(page, sectionIndex);
          const key = title || normalizeExplorerCategory(page.category);
          if (!groups.has(key)) {
            groups.set(key, {
              key,
              title,
              order: Number((getWikiMembership(page, sectionIndex) || page).section_order || 0),
              pages: [],
            });
          }
          groups.get(key).pages.push(page);
        });
        return Array.from(groups.values())
          .map(group => ({...group, pages: group.pages.sort((left, right) => compareWikiMembershipOrder(left, right, sectionIndex))}))
          .sort((left, right) => left.order - right.order || left.key.localeCompare(right.key, "ja"));
      }

      if (categoryId === "all") {
        const roots = new Map();
        const dedicatedCategoryByHeading = {
          "無印通常階層": "normal",
          "通常階層 η": "normal_eta",
          "無印番外階層": "enigmatic",
          "番外階層 η": "enigmatic",
          "無印通常亜階層": "sublevel",
          "通常亜階層 η": "sublevel",
          "番外亜階層 η": "sublevel",
          "番外階層": "enigmatic",
          "亜階層": "sublevel",
        };
        pages.forEach(page => {
          const membership = getWikiMembership(page, "hierarchy");
          if (!membership && !pageHasIndexMembership(page, "hierarchy")) return;
          let path = membership?.section_path;
          if (!Array.isArray(path)) {
            try { path = JSON.parse(page.section_path || "[]"); } catch (_) { path = []; }
          }
          const headings = (Array.isArray(path) ? path : [])
            .filter(item => item && item.heading && String(item.heading).trim() !== "目次")
            .map(item => ({
              level: Number(item.level) || 2,
              heading: String(item.heading).replace(/\s*\[[ xX✓✔]?\]\s*$/, "").trim(),
            }));
          if (!headings.length) return;
          const rootHeading = headings[0];
          const rootKey = `hierarchy:${rootHeading.heading}`;
          if (!roots.has(rootKey)) {
            roots.set(rootKey, {
              key: rootKey, title: rootHeading.heading, level: 3,
              order: Number(membership?.section_order || page.section_order || 0),
              pages: [], children: new Map(),
              wikiCategory: dedicatedCategoryByHeading[rootHeading.heading] || "",
            });
          }
          const root = roots.get(rootKey);
          const sectionOrder = Number(membership?.section_order || page.section_order || 0);
          root.order = Math.min(root.order || sectionOrder, sectionOrder);
          if (headings.length === 1) {
            root.pages.push(page);
            return;
          }
          const childPath = headings.slice(1);
          const childHeading = childPath[childPath.length - 1];
          const childKey = `${rootKey}/${childPath.map(item => item.heading).join("/")}`;
          if (!root.children.has(childKey)) {
            root.children.set(childKey, {
              key: childKey, title: childHeading.heading, level: 4,
              order: sectionOrder, pages: [],
              wikiCategory: dedicatedCategoryByHeading[childHeading.heading] || "",
            });
          }
          const child = root.children.get(childKey);
          child.order = Math.min(child.order, sectionOrder);
          child.pages.push(page);
        });

        const normalRoot = Array.from(roots.values()).find(item => item.title === "通常階層");
        if (normalRoot) {
          const childOrders = Array.from(normalRoot.children.values()).map(item => item.order);
          const firstBranchOrder = childOrders.length ? Math.min(...childOrders) : normalRoot.order + 3;
          [
            {title: "無印通常階層", wikiCategory: "normal", order: firstBranchOrder - 2},
            {title: "通常階層 η", wikiCategory: "normal_eta", order: firstBranchOrder - 1},
          ].forEach(item => {
            const key = `hierarchy:${normalRoot.title}/${item.title}`;
            if (!normalRoot.children.has(key)) {
              normalRoot.children.set(key, {
                key, title: item.title, level: 4, order: item.order,
                pages: [], wikiCategory: item.wikiCategory,
              });
            }
          });
          normalRoot.order = Math.min(normalRoot.order, firstBranchOrder - 3);
        }

        if (!Array.from(roots.values()).some(item => item.title === "番外階層")) {
          const lastOrder = Math.max(0, ...Array.from(roots.values()).map(item =>
            Math.max(item.order, ...Array.from(item.children.values()).map(child => child.order))
          ));
          roots.set("hierarchy:番外階層", {
            key: "hierarchy:番外階層", title: "番外階層", level: 3,
            order: lastOrder + 1, pages: [], children: new Map(),
            wikiCategory: "enigmatic",
          });
        }

        return Array.from(roots.values())
          .sort((left, right) => left.order - right.order)
          .flatMap(root => [
            {...root, pages: root.pages.sort((left, right) => compareWikiMembershipOrder(left, right, "hierarchy"))},
            ...Array.from(root.children.values())
              .sort((left, right) => left.order - right.order)
              .map(child => ({
                ...child,
                pages: child.pages.sort((left, right) => compareWikiMembershipOrder(left, right, "hierarchy")),
              })),
          ]);
      }

      const groups = new Map();
      pages.forEach(page => {
        const number = getIntegerLevelNumber(page);
        const range = number === null
          ? {key: "other", title: "その他の階層"}
          : getLevelNumberRange(number, categoryId);
        if (!groups.has(range.key)) groups.set(range.key, {...range, pages: []});
        groups.get(range.key).pages.push(page);
      });
      const order = ["zero", "1-9", "10-99", "100-999", "1000-plus",
        "minus-1-9", "minus-10-99", "minus-100-999", "minus-1000-plus", "other"];
      return Array.from(groups.values()).sort((left, right) =>
        order.indexOf(left.key) - order.indexOf(right.key)
      );
    }

    function appendDecoratedListText(host, value, spans, field) {
      const text = String(value || "");
      const ranges = (Array.isArray(spans) ? spans : [])
        .filter(item => item && item.field === field && item.text)
        .map(item => {
          const start = text.indexOf(String(item.text));
          return start >= 0 ? {start, end: start + String(item.text).length} : null;
        })
        .filter(Boolean)
        .sort((left, right) => left.start - right.start);
      let cursor = 0;
      ranges.forEach(range => {
        if (range.start < cursor) return;
        if (range.start > cursor) host.appendChild(document.createTextNode(text.slice(cursor, range.start)));
        const struck = document.createElement("span");
        struck.className = "line-through";
        struck.textContent = text.slice(range.start, range.end);
        host.appendChild(struck);
        cursor = range.end;
      });
      if (cursor < text.length) host.appendChild(document.createTextNode(text.slice(cursor)));
    }

    function appendLevelListPageRow(host, page) {
      const selected = selectedLevelPages.has(page.url);
      const row = document.createElement('div');
      row.className = "flex items-center gap-3 min-h-10 px-3 py-2 border-b border-[#eee5ce] cursor-pointer select-none transition " +
        (selected ? "bg-[#e7dfca] text-[#5b5343]" : "bg-white text-gray-800 hover:bg-[#fffaf0]");
      row.style.cssText = "box-sizing: border-box; flex: 0 0 auto; width: 100%;";
      row.dataset.url = page.url;
      row.setAttribute("role", "checkbox");
      row.setAttribute("aria-checked", String(selected));
      row.setAttribute("aria-label", `${page.title}をオフライン保存対象に選択`);
      row.tabIndex = 0;
      row.onclick = event => selectLevelListTile(page, event.shiftKey);
      row.oncontextmenu = event => {
        event.preventDefault();
        deselectLevelListPage(page);
      };
      row.onkeydown = event => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectLevelListTile(page, event.shiftKey);
        }
      };
      const mark = document.createElement('span');
      mark.className = "flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px] font-black transition " +
        (selected ? "border-[#713b09] bg-[#91520f] text-white shadow-sm" : "border-[#a99a78] bg-white text-transparent");
      mark.textContent = "✓";
      const link = document.createElement('a');
      link.href = page.url;
      link.className = "min-w-0 flex-1 break-words text-sm text-[#9b3d06] hover:underline";
      let strikeSpans = [];
      try {
        const parsedSpans = JSON.parse(page.strikethrough_spans || "[]");
        if (Array.isArray(parsedSpans)) strikeSpans = parsedSpans;
      } catch (_) {}
      const titleLine = document.createElement("span");
      titleLine.className = "block";
      appendDecoratedListText(titleLine, page.title, strikeSpans, "title");
      link.appendChild(titleLine);
      const metaParts = [
        ["meta_title", page.meta_title],
        ["meta_title_ja", page.meta_title_ja],
      ].filter(([, value]) => String(value || "").trim());
      if (metaParts.length) {
        const subtitle = document.createElement("span");
        subtitle.className = "mt-0.5 block text-[11px] text-gray-500";
        metaParts.forEach(([field, value], index) => {
          if (index) subtitle.appendChild(document.createTextNode(" · "));
          appendDecoratedListText(subtitle, value, strikeSpans, field);
        });
        link.appendChild(subtitle);
      }
      link.onclick = event => {
        event.preventDefault();
        event.stopPropagation();
        openLevelListPage(page);
      };
      row.append(mark, link);
      host.appendChild(row);
    }

    function renderLevelListSections() {
      const grid = document.getElementById('level-list-grid');
      const toc = document.getElementById('level-list-toc');
      grid.replaceChildren();
      toc.replaceChildren();
      const query = document.getElementById('level-list-filter').value.trim().toLocaleLowerCase();
      const categoryPages = levelListPagesForCategory(currentLevelListCategory);
      visibleLevelListPages = categoryPages
        .filter(page => !query || page.title.toLocaleLowerCase().includes(query))
        .sort(compareWikiIndexOrder);
      const sections = getLevelListSections(visibleLevelListPages);
      if (!sections.length) {
        const message = document.createElement('p');
        message.className = "px-4 py-6 text-sm text-gray-600";
        message.textContent = visibleLevelListPages.length
          ? "この一覧に表示できる階層はありません。"
          : currentLevelListCategory === "alias"
            ? "別名の一覧は、階層カテゴリごとに親記事と別名をまとめた本家Wikiの構成で表示できます。"
            : "このカテゴリのローカル一覧データはありません。Wikiで一覧を開けます。";
        grid.appendChild(message);
        const category = getLevelListCategories().find(item => item.id === currentLevelListCategory);
        if (category && category.wikiTitle && !query) {
          const openWiki = document.createElement('button');
          openWiki.type = "button";
          openWiki.className = "mx-4 mb-4 w-fit rounded bg-[#91520f] px-3 py-2 text-xs font-semibold text-white hover:bg-[#713b09]";
          openWiki.textContent = "Fandom Wikiで一覧を開く";
          openWiki.onclick = () => openFandomLevelList(category.wikiTitle);
          grid.appendChild(openWiki);
        }
        document.getElementById('level-list-total').textContent =
          `${visibleLevelListPages.length}件`;
        toc.classList.add("hidden");
        updateLevelListSelectionControls();
        return;
      }
      sections.forEach(section => {
        const sectionId = `level-range-${section.key}`;
        const heading = document.createElement('h4');
        heading.id = sectionId;
        heading.className = "sticky top-0 z-10 border-b border-[#d8c99e] bg-[#fff8dc] px-3 py-2 text-base font-semibold text-gray-800";
        heading.textContent = section.title;
        grid.appendChild(heading);
        const tocLink = document.createElement('a');
        tocLink.href = `#${sectionId}`;
        tocLink.className = "block rounded px-2 py-1 text-gray-700 hover:bg-[#fff7dd] hover:text-[#9b3d06]";
        tocLink.textContent = section.title;
        tocLink.onclick = event => {
          event.preventDefault();
          heading.scrollIntoView({block: "start"});
        };
        toc.appendChild(tocLink);

        if (section.key === "100-999" && currentLevelListCategory === "normal_eta") {
          const hundredGroups = new Map();
          section.pages.forEach(page => {
            const number = getIntegerLevelNumber(page);
            const start = Math.floor(number / 100) * 100;
            const key = `${start}-${start + 99}`;
            if (!hundredGroups.has(key)) hundredGroups.set(key, []);
            hundredGroups.get(key).push(page);
          });
          Array.from(hundredGroups.entries()).sort((left, right) =>
            Number(left[0].split("-")[0]) - Number(right[0].split("-")[0])
          ).forEach(([range, pages]) => {
            const subheading = document.createElement('h5');
            subheading.id = `level-range-${range}`;
            subheading.className = "border-b border-[#eee5ce] px-4 py-1.5 text-sm font-semibold text-gray-700";
            const suffix = currentLevelListCategory === "normal_eta" ? "η" : "";
            subheading.textContent = `${range.replace("-", `${suffix} から `)}${suffix} まで`;
            grid.appendChild(subheading);
            const subLink = document.createElement('a');
            subLink.href = `#level-range-${range}`;
            subLink.className = "block rounded py-1 pl-5 text-gray-600 hover:bg-[#fff7dd]";
            subLink.textContent = subheading.textContent;
            subLink.onclick = event => {
              event.preventDefault();
              subheading.scrollIntoView({block: "start"});
            };
            toc.appendChild(subLink);
            pages.forEach(page => appendLevelListPageRow(grid, page));
          });
        } else {
          section.pages.forEach(page => appendLevelListPageRow(grid, page));
        }
      });
      document.getElementById('level-list-total').textContent =
        `${visibleLevelListPages.length}件`;
      toc.classList.toggle("hidden", sections.length === 0);
      updateLevelListSelectionControls();
    }

    function filterLevelListTiles() {
      if (currentLevelListCategory === null) {
        renderLevelListCategoryTiles();
        return;
      }
      renderLevelListSections();
    }

    async function openLevelListPage(page) {
      document.getElementById('online-wiki-url').value = page.url;
      onlineWikiDefaultOpened = true;
      switchTab('wiki');
      await openDirectWikiUrl();
    }

    async function openFandomLevelList(wikiTitle) {
      const pageTitle = String(wikiTitle || "").replace(/ /g, "_");
      await openLevelListPage({
        url: `https://backrooms.fandom.com/ja/wiki/${encodeURIComponent(pageTitle)}`,
      });
    }

    function selectLevelListTile(page, extendRange) {
      if (extendRange && levelListAnchorUrl) {
        const anchorIndex = visibleLevelListPages.findIndex(item => item.url === levelListAnchorUrl);
        const targetIndex = visibleLevelListPages.findIndex(item => item.url === page.url);
        if (anchorIndex >= 0 && targetIndex >= 0) {
          const range = visibleLevelListPages.slice(
            Math.min(anchorIndex, targetIndex),
            Math.max(anchorIndex, targetIndex) + 1
          );
          selectedLevelPages = new Map(range.map(item => [item.url, item]));
        } else {
          selectedLevelPages = new Map([[page.url, page]]);
          levelListAnchorUrl = page.url;
        }
      } else {
        selectedLevelPages = new Map([[page.url, page]]);
        levelListAnchorUrl = page.url;
      }
      updateLevelListSelectionControls();
    }

    function deselectLevelListPage(page) {
      if (!selectedLevelPages.delete(page.url)) return;
      if (levelListAnchorUrl === page.url) levelListAnchorUrl = null;
      updateLevelListSelectionControls();
    }

    function updateLevelListSelectionControls() {
      const count = selectedLevelPages.size;
      document.getElementById('level-list-selection-count').textContent = `${count}件選択中`;
      document.getElementById('level-list-clear-selection').disabled = count === 0;
      document.getElementById('level-list-download-button').disabled =
        count === 0 || levelListTaskBusy;
      document.querySelectorAll('#level-list-grid [role="checkbox"]').forEach(tile => {
        const selected = selectedLevelPages.has(tile.dataset.url);
        tile.setAttribute("aria-checked", String(selected));
        tile.classList.toggle("bg-[#e7dfca]", selected);
        tile.classList.toggle("text-[#5b5343]", selected);
        tile.classList.toggle("bg-white", !selected);
        tile.classList.toggle("text-gray-800", !selected);
        tile.classList.toggle("hover:bg-[#fffaf0]", !selected);
        const mark = tile.querySelector("span");
        if (mark) {
          mark.classList.toggle("border-[#713b09]", selected);
          mark.classList.toggle("bg-[#91520f]", selected);
          mark.classList.toggle("text-white", selected);
          mark.classList.toggle("shadow-sm", selected);
          mark.classList.toggle("border-[#a99a78]", !selected);
          mark.classList.toggle("bg-white", !selected);
          mark.classList.toggle("text-transparent", !selected);
        }
      });
    }

    function clearLevelListSelection() {
      selectedLevelPages.clear();
      levelListAnchorUrl = null;
      updateLevelListSelectionControls();
    }

    function setLevelListTaskBusy(busy) {
      levelListTaskBusy = busy;
      document.getElementById('level-list-cancel-button').disabled = !busy;
      document.getElementById('level-list-download-button').disabled =
        busy || selectedLevelPages.size === 0;
      document.getElementById('level-list-language').disabled = busy;
      document.getElementById('level-list-filter').disabled = busy;
      document.getElementById('level-list-overwrite').disabled = busy;
      document.querySelectorAll('#view-data-tools button').forEach(button => {
        if (button.id !== "level-list-cancel-button" &&
            button.id !== "level-list-clear-selection" &&
            button.id !== "level-list-download-button") {
          button.disabled = busy;
        }
      });
    }

    async function runLevelListOfflineSave() {
      if (levelListTaskBusy || selectedLevelPages.size === 0) return;
      const pages = Array.from(selectedLevelPages.values());
      setLevelListTaskBusy(true);
      document.getElementById('level-list-log').textContent = "";
      document.getElementById('level-list-task-status').textContent =
        `選択した${pages.length}件のLevel情報をオフライン保存しています...`;
      try {
        const result = await pywebview.api.start_level_list_offline_save(
          pages,
          document.getElementById('level-list-overwrite').checked
        );
        if (result.status !== "started") {
          setLevelListTaskBusy(false);
          document.getElementById('level-list-task-status').textContent =
            result.message || "オフライン保存を開始できませんでした。";
        }
      } catch (error) {
        setLevelListTaskBusy(false);
        document.getElementById('level-list-task-status').textContent =
          "オフライン保存開始エラー: " + error;
      }
    }

    function appendLevelListLog(entry) {
      const log = document.getElementById('level-list-log');
      log.textContent += (entry.message || "") + "\n";
      const lines = log.textContent.split("\n");
      if (lines.length > 300) log.textContent = lines.slice(-300).join("\n");
      log.scrollTop = log.scrollHeight;
    }

    let htmlDownloadPages = [];
    let onlineWikiBrowserBusy = false;
    let onlineWikiNavigationPending = false;
    let onlineWikiNavigationTimeout = null;
    let onlineWikiSearchBusy = false;
    let onlineWikiCurrentUrl = "";
    let onlineWikiCurrentIsWiki = false;

    async function searchOnlineWiki() {
      if (onlineWikiSearchBusy || onlineWikiBrowserBusy) return;
      const query = document.getElementById('html-download-search').value.trim();
      const status = document.getElementById('html-download-status');
      const grid = document.getElementById('html-download-grid');
      if (!query) {
        status.textContent = "検索語を入力してください。";
        return;
      }
      onlineWikiSearchBusy = true;
      document.getElementById('html-download-search-button').disabled = true;
      status.textContent = "Fandomから検索しています...";
      grid.replaceChildren();
      document.getElementById('html-download-count').textContent = "検索中...";
      document.getElementById('html-download-results').classList.remove('hidden');
      try {
        const result = await pywebview.api.search_online_wiki(query);
        if (result.error) throw new Error(result.error);
        htmlDownloadPages = result.pages || [];
        filterOnlineWikiResults();
        status.textContent = htmlDownloadPages.length
          ? "検索結果からページを選択してください。"
          : "該当するページは見つかりませんでした。";
      } catch (error) {
        htmlDownloadPages = [];
        status.textContent = "オンライン検索に失敗しました: " + error;
        grid.textContent = "インターネット接続とFandomの応答を確認してください。";
        document.getElementById('html-download-count').textContent = "0件";
        document.getElementById('html-download-results').classList.remove('hidden');
      } finally {
        onlineWikiSearchBusy = false;
        document.getElementById('html-download-search-button').disabled = false;
      }
    }

    function filterOnlineWikiResults() {
      const query = document.getElementById('html-download-tag-search').value.trim().toLocaleLowerCase();
      const pages = htmlDownloadPages.filter(page =>
        !query || [page.title, ...(page.categories || [])].join(" ").toLocaleLowerCase().includes(query)
      );
      const grid = document.getElementById('html-download-grid');
      grid.replaceChildren();
      document.getElementById('html-download-results').classList.remove('hidden');
      pages.forEach(page => {
        const tile = document.createElement('button');
        tile.type = "button";
        tile.className = "w-full text-left p-2 rounded bg-white border border-[#dfd3a8] hover:border-[#b45309] text-xs";
        tile.disabled = onlineWikiBrowserBusy;
        tile.onclick = () => openOnlineWikiPage(page);
        const title = document.createElement('span');
        title.className = "block font-semibold text-gray-800 break-words";
        title.textContent = page.title || page.url;
        const tags = document.createElement('span');
        tags.className = "flex flex-wrap gap-1 mt-1";
        (page.categories || []).slice(0, 6).forEach(value => {
          const chip = document.createElement('span');
          chip.className = "px-1.5 py-0.5 rounded bg-[#f5eed3] text-[10px] text-gray-600";
          chip.textContent = value;
          tags.appendChild(chip);
        });
        tile.append(title, tags);
        grid.appendChild(tile);
      });
      document.getElementById('html-download-count').textContent =
        `${pages.length}件 / 検索結果 ${htmlDownloadPages.length}件`;
    }

    function clearOnlineWikiResults() {
      htmlDownloadPages = [];
      document.getElementById('html-download-grid').replaceChildren();
      document.getElementById('html-download-results').classList.add('hidden');
    }

    async function openOnlineWikiPage(page) {
      document.getElementById('online-wiki-url').value = page.url;
      await openDirectWikiUrl();
    }

    async function openDirectWikiUrl() {
      if (onlineWikiBrowserBusy) return;
      const url = document.getElementById('online-wiki-url').value.trim();
      const status = document.getElementById('html-download-status');
      onlineWikiBrowserBusy = true;
      onlineWikiNavigationPending = true;
      if (onlineWikiNavigationTimeout) clearTimeout(onlineWikiNavigationTimeout);
      status.textContent = "埋め込みブラウザーでFandomを開いています...";
      onlineWikiCurrentUrl = "";
      onlineWikiCurrentIsWiki = false;
      document.getElementById('online-wiki-save-current').disabled = true;
      try {
        const result = await pywebview.api.navigate_online_wiki_frame(url);
        if (result.status !== "ok") throw new Error(result.message || "Fandomを開けませんでした。");
        onlineWikiDefaultOpened = true;
        status.textContent = "Wikiページをアプリ内に表示しています。";
        document.getElementById('online-wiki-frame').src = result.url;
        onlineWikiNavigationTimeout = setTimeout(() => {
          if (!onlineWikiNavigationPending) return;
          onlineWikiNavigationPending = false;
          onlineWikiBrowserBusy = false;
          onlineWikiCurrentIsWiki = false;
          document.getElementById('online-wiki-current-url').textContent =
            "ページの読み込みを確認できませんでした。URLを再確認してください。";
          document.getElementById('online-wiki-save-current').disabled = true;
          status.textContent = "ページの読み込みがタイムアウトしました。";
        }, 45000);
      } catch (error) {
        onlineWikiNavigationPending = false;
        onlineWikiBrowserBusy = false;
        status.textContent = "Fandomを開けませんでした: " + error;
        updateOnlineWikiSelectionControls();
      }
    }

    function navigateOnlineWikiHistory(direction) {
      const frame = document.getElementById('online-wiki-frame');
      try {
        if (direction < 0) frame.contentWindow.history.back();
        else frame.contentWindow.history.forward();
      } catch (error) {
        document.getElementById('html-download-status').textContent = String(error);
      }
    }

    function reloadOnlineWikiFrame() {
      const frame = document.getElementById('online-wiki-frame');
      try { frame.contentWindow.location.reload(); }
      catch (_) { if (onlineWikiCurrentUrl) frame.src = onlineWikiCurrentUrl; }
    }

    async function resetOnlineWikiHome() {
      document.getElementById('online-wiki-url').value = onlineWikiHomeUrl;
      await openDirectWikiUrl();
    }

    function onOnlineWikiBrowserUrlChanged(result) {
      onlineWikiCurrentUrl = result.url || "";
      onlineWikiCurrentIsWiki = Boolean(result.is_wiki_page);
      if (onlineWikiCurrentUrl) {
        document.getElementById('online-wiki-url').value = onlineWikiCurrentUrl;
      }
      document.getElementById('online-wiki-current-url').textContent =
        onlineWikiCurrentUrl || "Fandomページはまだ開かれていません。";
      document.getElementById('online-wiki-current-url').title = onlineWikiCurrentUrl;
      document.getElementById('online-wiki-save-current').disabled =
        !result.is_wiki_page || onlineWikiBrowserBusy;
      document.getElementById('online-wiki-redownload-current').disabled =
        !result.is_wiki_page || onlineWikiBrowserBusy;
    }

    async function onOnlineWikiFrameLoaded() {
      if (onlineWikiNavigationPending) {
        onlineWikiNavigationPending = false;
        if (onlineWikiNavigationTimeout) clearTimeout(onlineWikiNavigationTimeout);
        onlineWikiNavigationTimeout = null;
        onlineWikiBrowserBusy = false;
      }
      await updateOnlineWikiCurrentUrl();
    }

    async function updateOnlineWikiCurrentUrl() {
      try {
        const result = await pywebview.api.get_current_online_wiki_url();
        onOnlineWikiBrowserUrlChanged(result);
      } catch (error) {
        document.getElementById('online-wiki-current-url').textContent =
          "ブラウザーの状態を取得できません: " + error;
      }
    }

    async function refreshCurrentSavedHtml() {
      const path = currentExplorerPage && currentExplorerPage.html_path;
      if (!path) {
        alert('保存済みHTMLが見つかりません。先にHTMLを保存してください。');
        return;
      }
      try {
        const result = await pywebview.api.run_maintenance_task(
          'refresh_saved_wiki_page', null, {html_path: path}
        );
        if (result.status !== 'started') throw new Error(result.message || '開始できません');
        document.getElementById('online-wiki-save-status').textContent = '保存済みHTMLを再抽出中です。';
      } catch (error) { alert('HTML再確認エラー: ' + error); }
    }

    async function redownloadCurrentOnlineWikiPage() {
      if (!onlineWikiCurrentIsWiki || onlineWikiBrowserBusy) return;
      if (!confirm('現在のページを上書き再ダウンロードし、本文・画像・抽出データを更新しますか？')) return;
      onlineWikiBrowserBusy = true;
      try {
        const page = {title: onlineWikiCurrentUrl.split('/').pop().replace(/_/g, ' '), url: onlineWikiCurrentUrl};
        const result = await pywebview.api.start_level_list_offline_save([page], true);
        if (result.status !== 'started') throw new Error(result.message || '開始できません');
        document.getElementById('online-wiki-save-status').textContent = '上書き再取得を開始しました。';
      } catch (error) {
        onlineWikiBrowserBusy = false;
        document.getElementById('online-wiki-save-status').textContent = '再取得エラー: ' + error;
      }
    }

    async function saveCurrentOnlineWikiPage() {
      if (onlineWikiBrowserBusy || !onlineWikiCurrentUrl) return;
      onlineWikiBrowserBusy = true;
      document.getElementById('online-wiki-save-current').disabled = true;
      document.getElementById('online-wiki-save-status').textContent =
        "表示中の記事を保存し、本文・図鑑・経路データへ反映しています...";
      try {
        const result = await pywebview.api.save_current_online_wiki_page();
        if (result.status !== "started") {
          document.getElementById('online-wiki-save-status').textContent =
            result.message || "ダウンロードを開始できませんでした。";
          onlineWikiBrowserBusy = false;
          await updateOnlineWikiCurrentUrl();
        } else {
          document.getElementById('online-wiki-save-status').textContent =
            "保存中です。詳細ログはCLIに表示されます。";
        }
      } catch (error) {
        document.getElementById('online-wiki-save-status').textContent =
          "保存開始エラー: " + error;
        onlineWikiBrowserBusy = false;
        await updateOnlineWikiCurrentUrl();
      }
    }

    async function onOnlineWikiSaveCompleted(result) {
      onlineWikiBrowserBusy = false;
      if (result.ok) {
        const pipelineStatus = result.pipeline_ok
          ? 'テキスト・図鑑・経路反映済'
          : `反映に一部失敗: ${result.pipeline_error || '詳細ログを確認してください'}`;
        document.getElementById('online-wiki-save-status').textContent =
          `保存＆抽出${result.pipeline_ok ? '完了' : '済'}: ${result.title} / 画像 ${result.images_saved || 0}件` +
          (result.images_failed ? ` (失敗 ${result.images_failed}件)` : "") +
          ` / ${pipelineStatus}`;
        try {
          // The backend catalog was invalidated after a successful save.
          explorerCatalogPromise = null;
          await Promise.all([onSearchInput(), loadExplorerIndexPages()]);
        } catch (error) { console.error('図鑑一覧の更新に失敗:', error); }
      } else {
        document.getElementById('online-wiki-save-status').textContent =
          "保存エラー: " + (result.error || "不明なエラー");
      }
      updateOnlineWikiCurrentUrl();
    }

    function setMaintenanceBusy(busy) {
      document.getElementById('maintenance-cancel').disabled = !busy;
      document.getElementById('level-list-cancel-button').disabled = !busy;
      const cleanupPreviewButton = document.getElementById('cleanup-preview-button');
      if (cleanupPreviewButton) cleanupPreviewButton.disabled = Boolean(busy);
      document.querySelectorAll('[data-cleanup-category]').forEach(input => {
        input.disabled = Boolean(busy);
      });
    }

    async function runTask(taskName, options = {}) {
      const status = document.getElementById('maintenance-task-status');
      status.textContent = "保守タスクを開始しています...";
      status.className = "min-h-10 rounded border border-[#dfd3a8] bg-[#fffdf5] px-3 py-2 text-xs text-gray-600";
      document.getElementById('maintenance-log').textContent = "";
      const progressBar = document.getElementById('maintenance-progress');
      progressBar.value = 0;
      progressBar.classList.add('hidden');
      document.getElementById('maintenance-progress-label').textContent = "開始中";
      setMaintenanceBusy(true);
      setLevelListTaskBusy(true);
      try {
        const result = await pywebview.api.run_maintenance_task(taskName, null, options);
        if (result.status !== "started") {
          setMaintenanceBusy(false);
          setLevelListTaskBusy(false);
          status.textContent = result.message || "タスクを開始できませんでした。";
          status.className = "min-h-10 rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800";
        }
      } catch (error) {
        setMaintenanceBusy(false);
        setLevelListTaskBusy(false);
        status.textContent = "タスク開始エラー: " + error;
        status.className = "min-h-10 rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800";
      }
    }

    async function cancelMaintenanceTask() {
      const button = document.getElementById('maintenance-cancel');
      button.disabled = true;
      try {
        const result = await pywebview.api.cancel_maintenance_task();
        document.getElementById('maintenance-progress-label').textContent =
          result.message || "安全な停止位置を待っています...";
        if (result.status === "idle") setMaintenanceBusy(false);
      } catch (error) {
        button.disabled = false;
        document.getElementById('level-list-cancel-button').disabled = false;
        document.getElementById('maintenance-task-status').textContent =
          "停止要求エラー: " + error;
        document.getElementById('level-list-task-status').textContent =
          "停止要求エラー: " + error;
      }
    }

    function onMaintenanceLog(entry) {
      const log = document.getElementById('maintenance-log');
      log.textContent += (entry.message || "") + "\n";
      const lines = log.textContent.split("\n");
      if (lines.length > 300) log.textContent = lines.slice(-300).join("\n");
      log.scrollTop = log.scrollHeight;
      appendLevelListLog(entry);
    }

    function onMaintenanceProgress(progress) {
      const bar = document.getElementById('maintenance-progress');
      const label = document.getElementById('maintenance-progress-label');
      if (Number.isFinite(progress.current) && Number.isFinite(progress.total) && progress.total > 0) {
        bar.classList.remove('hidden');
        bar.value = Math.min(100, Math.round(progress.current * 100 / progress.total));
        label.textContent = `${progress.current}/${progress.total} ${progress.message || ""}`;
      } else {
        bar.classList.add('hidden');
        label.textContent = progress.message || "処理中";
      }
      const listBar = document.getElementById('level-list-progress');
      const listLabel = document.getElementById('level-list-progress-label');
      if (Number.isFinite(progress.current) && Number.isFinite(progress.total) && progress.total > 0) {
        listBar.classList.remove('hidden');
        listBar.value = Math.min(100, Math.round(progress.current * 100 / progress.total));
        listLabel.textContent = `${progress.current}/${progress.total} ${progress.message || ""}`;
      } else {
        listBar.classList.add('hidden');
        listLabel.textContent = progress.message || "処理中";
      }
    }

    function onMaintenanceTaskCompleted(result) {
      const status = document.getElementById('maintenance-task-status');
      status.textContent = result.message || (result.ok ? "処理が完了しました。" : "処理に失敗しました。");
      status.className = result.cancelled
        ? "min-h-10 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800"
        : result.ok
        ? "min-h-10 rounded border border-green-300 bg-green-50 px-3 py-2 text-xs text-green-800"
        : "min-h-10 rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800";
      const listStatus = document.getElementById('level-list-task-status');
      listStatus.textContent = status.textContent;
      document.getElementById('maintenance-progress-label').textContent =
        result.cancelled ? "停止しました" : (result.ok ? "完了" : "失敗");
      document.getElementById('maintenance-progress').classList.add('hidden');
      document.getElementById('level-list-progress-label').textContent =
        result.cancelled ? "停止しました" : (result.ok ? "完了" : "失敗");
      document.getElementById('level-list-progress').classList.add('hidden');
      setMaintenanceBusy(false);
      setLevelListTaskBusy(false);
      onlineWikiBrowserBusy = false;
      updateOnlineWikiCurrentUrl();
      if (result.ok && currentExplorerPage) {
        displayExplorerPage(currentExplorerPage);
      }
      pywebview.api.get_environment_settings().then(renderEnvironmentSettings).catch(() => {});
      onSearchInput();
      renderExplorerTabs();
      loadExplorerIndexPages();
      if (result.details && result.details.renamed !== undefined) {
        document.getElementById('legacy-image-rename-status').textContent =
          `画像名の移行完了: ${result.details.renamed} 件 / 判定保留 ${result.details.skipped} 件 / 失敗 ${result.details.failed} 件`;
        localImages = [];
        loadLocalImageGallery();
      }
    }

    // --- BrNavi AI Assistant ---
    let aiConversationId = null;
    let aiBusy = false;
    let aiInitialized = false;
    let aiViewToken = 0;
    const aiActiveRequests = new Map();
    const aiPendingMessages = new Map();

    function aiSetStatus(message, isError = false) {
      const el = document.getElementById('ai-status');
      el.textContent = message;
      el.className = isError ? "text-[10px] text-red-700 mt-0.5" : "text-[10px] text-gray-500 mt-0.5";
    }

    function aiAddMessage(role, content, sources = []) {
      const container = document.getElementById('ai-chat-messages');
      container.innerHTML = '';
      const wrap = document.createElement('div');
      wrap.className = "max-w-4xl mx-auto flex " + (role === 'user' ? "justify-end" : "justify-start");
      const bubble = document.createElement('div');
      bubble.className = role === 'user'
        ? "max-w-[85%] rounded-lg bg-[#ead9ad] border border-[#d6c79a] px-4 py-3 text-sm text-gray-800 whitespace-pre-wrap break-words"
        : "max-w-[90%] rounded-lg bg-white border border-[#e2d8b5] shadow-sm px-4 py-3 text-sm text-gray-800 whitespace-pre-wrap break-words";
      bubble.textContent = content;
      wrap.appendChild(bubble);
      container.appendChild(wrap);

      if (role === 'assistant' && sources.length) {
        const sourceWrap = document.createElement('div');
        sourceWrap.className = "max-w-4xl mx-auto text-[10px] text-gray-500";
        sourceWrap.textContent = "📚 参照したローカルWiki資料: " + sources.map(s => s.name).join(" / ");
        container.appendChild(sourceWrap);
      }
      container.scrollTop = container.scrollHeight;
    }

    function aiRefreshBusy() {
      const request = aiConversationId && aiActiveRequests.get(aiConversationId);
      aiBusy = !!request;
      document.getElementById('ai-message-input').disabled = aiBusy;
      const button = document.getElementById('ai-send-button');
      button.disabled = false;
      button.textContent = aiBusy ? "停止" : "送信";
      button.classList.toggle('bg-red-700', aiBusy);
      button.classList.toggle('hover:bg-red-800', aiBusy);
    }

    function aiRequestId() {
      if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
      return Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
    }

    function aiSetActiveRequest(conversationId, request) {
      if (request) aiActiveRequests.set(conversationId, request);
      else aiActiveRequests.delete(conversationId);
      if (conversationId === aiConversationId) aiRefreshBusy();
    }

    async function initializeAI() {
      if (aiInitialized) return;
      aiInitialized = true;
      try {
        const status = await pywebview.api.ai_get_status();
        if (!status || !status.available) {
          aiSetStatus("利用不可: " + ((status && status.error) || "初期化に失敗しました"), true);
          return;
        }
        aiSetStatus("接続先: " + status.provider + " / モデル: " + status.model
          + (status.local_wiki ? " / ローカルWiki検索: 有効" : " / ローカルWikiフォルダなし"));
        await aiNewConversation();
        await aiLoadConversationList();
      } catch (e) {
        aiSetStatus("初期化エラー: " + e, true);
      }
    }

    async function aiNewConversation() {
      try {
        const result = await pywebview.api.ai_new_conversation();
        if (!result || !result.conversation_id) {
          aiSetStatus("新しい会話を作成できません: " + ((result && result.error) || ""), true);
          return false;
        }
        aiConversationId = result.conversation_id;
        aiViewToken++;
        aiRefreshBusy();
        const container = document.getElementById('ai-chat-messages');
        container.innerHTML = '';
        const welcome = document.createElement('div');
        welcome.className = "max-w-3xl mx-auto text-center text-xs text-gray-500 py-12";
        welcome.textContent = "新しい会話です。Backroomsについて質問してみましょう。";
        container.appendChild(welcome);
        await aiLoadConversationList();
        return true;
      } catch (e) {
        aiSetStatus("会話作成エラー: " + e, true);
        return false;
      }
    }

    async function aiLoadConversationList() {
      try {
        const list = await pywebview.api.ai_list_conversations();
        const host = document.getElementById('ai-conversation-list');
        host.innerHTML = '';
        if (!list || !list.length) {
          const empty = document.createElement('div');
          empty.className = "text-gray-400 p-2";
          empty.textContent = "履歴はありません";
          host.appendChild(empty);
          return;
        }
        list.forEach(item => {
          const row = document.createElement('div');
          row.className = "flex items-center gap-1";
          const button = document.createElement('button');
          button.className = "flex-1 min-w-0 text-left p-2 rounded border border-transparent hover:bg-white hover:border-[#dfd3a8] truncate";
          button.textContent = item.title || "新しい会話";
          button.title = item.title || "";
          if (item.id === aiConversationId) button.classList.add('bg-white', 'border-[#dfd3a8]');
          button.onclick = () => aiOpenConversation(item.id);
          const rename = document.createElement('button');
          rename.className = "px-1 text-gray-500 hover:text-amber-800";
          rename.textContent = "✎";
          rename.title = "会話名を変更";
          rename.onclick = event => { event.stopPropagation(); aiRenameConversation(item); };
          const remove = document.createElement('button');
          remove.className = "px-1 text-gray-500 hover:text-red-700";
          remove.textContent = "×";
          remove.title = "会話を削除";
          remove.onclick = event => { event.stopPropagation(); aiDeleteConversation(item); };
          row.append(button, rename, remove);
          host.appendChild(row);
        });
      } catch (e) { console.error("AI履歴一覧エラー", e); }
    }

    async function aiOpenConversation(cid) {
      const token = ++aiViewToken;
      aiConversationId = cid;
      aiRefreshBusy();
      const container = document.getElementById('ai-chat-messages');
      container.innerHTML = '<div class="text-center text-xs text-gray-500 py-12">履歴を読み込み中...</div>';
      try {
        const history = await pywebview.api.ai_get_history(cid);
        if (token !== aiViewToken || cid !== aiConversationId) return;
        container.innerHTML = '';
        if (!history || !history.length) {
          const empty = document.createElement('div');
          empty.className = "text-center text-xs text-gray-500 py-12";
          empty.textContent = "この会話にはまだメッセージがありません。";
          container.appendChild(empty);
        } else {
          history.forEach(item => aiAppendHistoryMessage(item));
        }
        await aiLoadConversationList();
        const active = await pywebview.api.ai_get_active_request(cid);
        if (token === aiViewToken && cid === aiConversationId) {
          aiSetActiveRequest(cid, active);
          if (active) aiSetStatus(active.status === 'cancel_requested' ? "生成を停止中..." : "回答を生成中...");
        }
      } catch (e) { aiSetStatus("履歴読み込みエラー: " + e, true); }
    }

    function aiAppendHistoryMessage(item) {
      const role = item.role;
      const content = item.content;
      const container = document.getElementById('ai-chat-messages');
      const wrap = document.createElement('div');
      wrap.className = "ai-message max-w-4xl mx-auto flex " + (role === 'user' ? "justify-end" : "justify-start");
      if (role === 'assistant' && item.id !== undefined) wrap.dataset.messageId = String(item.id);
      const column = document.createElement('div');
      column.className = "max-w-[92%]";
      const bubble = document.createElement('div');
      bubble.className = role === 'user'
        ? "rounded-lg bg-[#ead9ad] border border-[#d6c79a] px-4 py-3 text-sm whitespace-pre-wrap break-words"
        : "ai-answer-content rounded-lg bg-white border border-[#e2d8b5] px-4 py-3 text-sm break-words select-text";
      if (role === 'assistant') {
        bubble.tabIndex = 0;
        const version = (item.versions || []).find(v => v.version_number === item.current_version_number);
        aiSetRenderedContent(bubble, version ? version.html : item.html);
      } else {
        bubble.textContent = content;
      }
      column.appendChild(bubble);
      if (role === 'assistant') {
        const actions = document.createElement('div');
        actions.className = "flex flex-wrap items-center gap-2 mt-1 text-[10px] text-gray-500";
        const versions = item.versions || [];
        let currentIndex = versions.findIndex(v => v.version_number === item.current_version_number);
        if (currentIndex < 0 && versions.length) currentIndex = versions.length - 1;
        const versionLabel = document.createElement('span');
        const previous = document.createElement('button');
        previous.textContent = "‹";
        previous.title = "前の回答";
        previous.className = "hover:text-amber-800 disabled:opacity-40";
        const next = document.createElement('button');
        next.textContent = "›";
        next.title = "次の回答";
        next.className = "hover:text-amber-800 disabled:opacity-40";
        const updateVersionButtons = () => {
          versionLabel.textContent = versions.length ? `${currentIndex + 1} / ${versions.length}` : "";
          previous.disabled = currentIndex <= 0;
          next.disabled = currentIndex < 0 || currentIndex >= versions.length - 1;
        };
        const renderRating = version => {
          good.classList.toggle('font-bold', version && version.rating === 'good');
          bad.classList.toggle('font-bold', version && version.rating === 'bad');
        };
        const renderSources = version => {
          const names = version && version.sources ? version.sources.map(source => source.name) : [];
          sources.textContent = names.length ? "📚 参照したローカルWiki資料: " + names.join(" / ") : "";
        };
        const switchVersion = async direction => {
          const index = currentIndex + direction;
          if (index < 0 || index >= versions.length) return;
          const selected = versions[index];
          const result = await pywebview.api.ai_set_current_version(item.id, selected.version_number);
          if (result.error) { aiSetStatus(result.error, true); return; }
          currentIndex = index;
          item.current_version_number = selected.version_number;
          item.content = selected.content;
          aiSetRenderedContent(bubble, selected.html);
          renderRating(selected);
          renderSources(selected);
          updateVersionButtons();
        };
        previous.onclick = () => switchVersion(-1);
        next.onclick = () => switchVersion(1);
        actions.append(previous, versionLabel, next);
        const copy = document.createElement('button');
        copy.textContent = "回答をコピー";
        copy.className = "hover:text-amber-800";
        copy.onclick = () => aiCopyText(item.content, copy);
        actions.appendChild(copy);
        const regenerate = document.createElement('button');
        regenerate.textContent = "再生成";
        regenerate.className = "hover:text-amber-800";
        regenerate.onclick = () => aiRegenerateAnswer(item);
        actions.appendChild(regenerate);
        const good = document.createElement('button');
        good.textContent = "Good";
        good.className = "hover:text-green-700";
        good.onclick = () => aiRateAnswer(item, versions[currentIndex], 'good', renderRating);
        const bad = document.createElement('button');
        bad.textContent = "Bad";
        bad.className = "hover:text-red-700";
        bad.onclick = () => aiRateAnswer(item, versions[currentIndex], 'bad', renderRating);
        actions.append(good, bad);
        const sources = document.createElement('div');
        sources.className = "text-[10px] text-gray-500 mt-1";
        const selectedVersion = versions[currentIndex];
        renderRating(selectedVersion);
        renderSources(selectedVersion);
        updateVersionButtons();
        column.append(actions, sources);
      }
      wrap.appendChild(column); container.appendChild(wrap); container.scrollTop = container.scrollHeight;
      return wrap;
    }

    function aiSetRenderedContent(bubble, html) {
      bubble.innerHTML = html || "";
      bubble.querySelectorAll('pre').forEach(pre => {
        const copy = document.createElement('button');
        copy.textContent = "コードをコピー";
        copy.className = "ai-code-copy px-2 py-1 text-[10px] bg-gray-700 text-white rounded hover:bg-gray-600";
        copy.onclick = () => aiCopyText(pre.innerText, copy);
        pre.parentNode.insertBefore(copy, pre);
      });
    }

    async function aiRateAnswer(item, version, rating, renderRating) {
      if (!version) return;
      const result = await pywebview.api.ai_set_rating(item.id, version.version_number, rating);
      if (result.error) { aiSetStatus(result.error, true); return; }
      version.rating = rating;
      renderRating(version);
    }

    async function aiRenameConversation(item) {
      const title = window.prompt("新しい会話名（1～80文字）", item.title || "");
      if (title === null) return;
      const result = await pywebview.api.ai_rename_conversation(item.id, title);
      if (result.error) { aiSetStatus(result.error, true); return; }
      await aiLoadConversationList();
    }

    async function aiDeleteConversation(item) {
      if (!window.confirm(`「${item.title || '新しい会話'}」と関連する履歴を削除しますか？`)) return;
      const result = await pywebview.api.ai_delete_conversation(item.id);
      if (!result.ok) { aiSetStatus(result.error || "会話を削除できませんでした。", true); return; }
      aiActiveRequests.delete(item.id);
      if (aiConversationId === item.id) await aiNewConversation();
      await aiLoadConversationList();
    }

    async function aiCopyText(text, button) {
      try {
        await navigator.clipboard.writeText(text);
        if (button) { const old = button.textContent; button.textContent = "コピー済み"; setTimeout(() => button.textContent = old, 1000); }
      } catch (e) {
        const area = document.createElement('textarea'); area.value = text; document.body.appendChild(area); area.select();
        document.execCommand('copy'); area.remove();
      }
    }

    async function aiRegenerateAnswer(item) {
      if (!aiConversationId || !item.id || aiActiveRequests.has(aiConversationId)) return;
      const lastAssistant = [...document.querySelectorAll('#ai-chat-messages .ai-message')]
        .reverse().find(el => el.dataset.messageId);
      if (!lastAssistant || lastAssistant.dataset.messageId !== String(item.id)) {
        aiSetStatus("再生成は会話の最新の回答に対してのみ実行できます。", true);
        return;
      }
      const cid = aiConversationId;
      const requestId = aiRequestId();
      aiSetActiveRequest(cid, {request_id: requestId, operation: "regenerate"});
      aiSetStatus("回答を再生成中...");
      try {
        const result = await pywebview.api.ai_regenerate(cid, item.id, requestId);
        if (!result.accepted) {
          aiSetActiveRequest(cid, null);
          aiSetStatus("再生成を開始できません: " + (result.error || "不明なエラー"), true);
        }
      } catch (e) {
        aiSetActiveRequest(cid, null);
        aiSetStatus("再生成エラー: " + e, true);
      }
    }

    async function aiToggleProperties() {
      const panel = document.getElementById('ai-properties');
      panel.classList.toggle('hidden');
      if (!panel.classList.contains('hidden')) {
        try {
          const s = await pywebview.api.ai_get_settings();
          if (s.error) throw new Error(s.error);
          document.getElementById('ai-prop-model').value = s.model;
          document.getElementById('ai-prop-tokens').value = s.max_completion_tokens;
          document.getElementById('ai-prop-history').value = s.history_messages;
          document.getElementById('ai-prop-key-status').textContent = s.api_key_configured ? "GROQ_API_KEY: 設定済み" : "GROQ_API_KEY: 未設定";
        } catch(e) { document.getElementById('ai-prop-key-status').textContent = e.message; }
      }
    }

    async function aiSaveProperties() {
      const values = {model: document.getElementById('ai-prop-model').value,
        max_completion_tokens: Number(document.getElementById('ai-prop-tokens').value),
        history_messages: Number(document.getElementById('ai-prop-history').value)};
      const result = await pywebview.api.ai_update_settings(values);
      if (result.error) { aiSetStatus("設定エラー: " + result.error, true); return; }
      aiSetStatus("Groq / " + result.model + " / 最大出力 " + result.max_completion_tokens + " tokens");
    }

    document.addEventListener('keydown', event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c') {
        const selection = window.getSelection();
        if (selection && selection.toString()) return; // 選択範囲は標準コピーに任せる
        const active = document.activeElement;
        const answer = active && active.closest ? active.closest('.ai-answer-content') : null;
        if (answer) { event.preventDefault(); aiCopyText(answer.textContent); }
      }
    });

    function aiAppendPending(requestId, operation) {
      if (operation === "regenerate") return;
      const wrap = document.createElement('div');
      wrap.className = "ai-message max-w-4xl mx-auto flex justify-start";
      const bubble = document.createElement('div');
      bubble.className = "ai-pending-answer max-w-[90%] rounded-lg bg-white border border-[#e2d8b5] shadow-sm px-4 py-3 text-sm whitespace-pre-wrap break-words";
      bubble.textContent = "回答を生成中...";
      wrap.appendChild(bubble);
      document.getElementById('ai-chat-messages').appendChild(wrap);
      aiPendingMessages.set(requestId, wrap);
      document.getElementById('ai-chat-messages').scrollTop =
        document.getElementById('ai-chat-messages').scrollHeight;
    }

    function aiRemovePending(requestId) {
      const pending = aiPendingMessages.get(requestId);
      if (pending) pending.remove();
      aiPendingMessages.delete(requestId);
    }

    function aiAppendError(message) {
      const wrap = document.createElement('div');
      wrap.className = "max-w-4xl mx-auto text-sm text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2";
      wrap.textContent = message;
      document.getElementById('ai-chat-messages').appendChild(wrap);
    }

    function aiAppendMissingSourceActions(prompt, conversationId, options = {}) {
      const wrap = document.createElement('div');
      wrap.className = "ai-message max-w-4xl mx-auto flex justify-start";
      const panel = document.createElement('div');
      panel.className = "max-w-[90%] rounded border border-[#dfd3a8] bg-[#fffdf5] p-3 text-xs space-y-2";
      const explanation = document.createElement('p');
      explanation.textContent = options.missingLevel
        ? `${options.missingLevel} のローカル本文は未保存です。入口・出口が片側にだけ記載されている可能性があるため、対象ページを取得して経路CSVを再解析しますか？`
        : "対象の日本語版Wiki記事を検索できます。保存前に記事名と内容を確認し、保存操作ごとに承諾を求めます。";
      const searchButton = document.createElement('button');
      searchButton.type = "button";
      searchButton.className = "rounded bg-[#91520f] px-3 py-1.5 text-white font-semibold";
      searchButton.textContent = "Wiki資料を検索";
      const status = document.createElement('div');
      status.className = "text-gray-600 whitespace-pre-wrap break-words";
      panel.append(explanation, searchButton, status);
      wrap.appendChild(panel);
      document.getElementById('ai-chat-messages').appendChild(wrap);
      searchButton.onclick = async () => {
        searchButton.disabled = true;
        status.textContent = "日本語版Backrooms Wikiを検索中...";
        try {
          const result = await pywebview.api.search_online_wiki(options.searchQuery || prompt);
          if (result.error) throw new Error(result.error);
          const pages = (result.pages || []).slice(0, 8);
          if (!pages.length) {
            status.textContent = "記事候補が見つかりませんでした。Level番号や記事名を具体的に指定してください。";
            searchButton.disabled = false;
            return;
          }
          status.textContent = "";
          pages.forEach(page => {
            const card = document.createElement('div');
            card.className = "rounded border border-[#e2d8b5] bg-white p-2 space-y-1";
            const title = document.createElement('div');
            title.className = "font-semibold";
            title.textContent = page.title || page.url;
            const link = document.createElement('a');
            link.href = page.url;
            link.textContent = page.url;
            link.className = "block break-all text-[#9b3d06] underline";
            link.onclick = event => { event.preventDefault(); openAIAnswerLink(page.url); };
            const snippet = document.createElement('div');
            snippet.className = "text-gray-600";
            snippet.textContent = page.snippet || "検索抜粋はありません。";
            const save = document.createElement('button');
            save.type = "button";
            save.className = "rounded border border-[#d6c79a] px-2 py-1 font-semibold";
            save.textContent = "この記事を保存して回答に使う";
            save.onclick = async () => {
              const approved = window.confirm(
                `「${page.title}」の記事本文と画像をダウンロードしてローカル保存します${options.rebuildNetwork ? "。接続CSVも再解析します" : ""}。よろしいですか？\n${page.url}`
              );
              if (!approved) return;
              save.disabled = true;
              status.textContent = `${page.title} を保存中です...`;
              const flowId = aiRequestId();
              aiMissingSourceFlows.set(flowId, {prompt, conversationId, status, rebuildNetwork: Boolean(options.rebuildNetwork)});
              try {
                const started = await pywebview.api.start_ai_source_save(
                  page, prompt, true, flowId, Boolean(options.rebuildNetwork)
                );
                if (started.status !== "started") {
                  aiMissingSourceFlows.delete(flowId);
                  status.textContent = started.message || "保存を開始できませんでした。";
                  save.disabled = false;
                }
              } catch (error) {
                aiMissingSourceFlows.delete(flowId);
                status.textContent = `保存を開始できません: ${error}`;
                save.disabled = false;
              }
            };
            card.append(title, link, snippet, save);
            panel.appendChild(card);
          });
        } catch (error) {
          status.textContent = `Wiki検索に失敗しました: ${error}`;
          searchButton.disabled = false;
        }
      };
    }

    const aiMissingSourceFlows = new Map();

    function onAIMissingSourceSaveCompleted(result) {
      const flow = aiMissingSourceFlows.get(result.flow_id);
      if (!flow) return;
      aiMissingSourceFlows.delete(result.flow_id);
      if (!result.ok) {
        flow.status.textContent = `資料を保存できませんでした: ${result.error || '詳細不明'}`;
        return;
      }
      if (flow.rebuildNetwork && !result.network_updated) {
        flow.status.textContent = `${result.title} は保存しましたが、経路CSVを更新できませんでした: ${result.network_update_error || '記事から接続情報を抽出できませんでした。環境設定からネットワーク更新を実行してください。'}`;
        return;
      }
      flow.status.textContent = `${result.title} を保存しました。質問を再確認します。`;
      if (flow.conversationId === aiConversationId && !aiBusy) {
        sendAIMessage(flow.prompt);
      } else {
        flow.status.textContent += " 同じ会話を開いて、もう一度質問してください。";
      }
    }

    async function openAIAnswerLink(url) {
      try {
        const result = await pywebview.api.navigate_ai_answer_link(url);
        if (result.status !== "ok") throw new Error(result.message || "リンクを開けません。");
        switchTab('wiki');
        onlineWikiBrowserBusy = true;
        onlineWikiNavigationPending = true;
        if (onlineWikiNavigationTimeout) clearTimeout(onlineWikiNavigationTimeout);
        document.getElementById('online-wiki-url').value = result.url;
        document.getElementById('html-download-status').textContent = "AI回答のリンクを内蔵ブラウザーで開いています...";
        document.getElementById('online-wiki-current-url').textContent = result.url;
        document.getElementById('online-wiki-save-current').disabled = true;
        document.getElementById('online-wiki-frame').src = result.url;
        onlineWikiNavigationTimeout = setTimeout(() => {
          if (!onlineWikiNavigationPending) return;
          onlineWikiNavigationPending = false;
          onlineWikiBrowserBusy = false;
          document.getElementById('html-download-status').textContent = "ページの読み込みを確認できませんでした。";
        }, 45000);
      } catch (error) {
        aiSetStatus(`リンクを開けませんでした: ${error}`, true);
      }
    }

    function openAIAnswerLinkFromEvent(event) {
      const anchor = event.target.closest && event.target.closest('.ai-answer-content a[href]');
      if (!anchor) return;
      event.preventDefault();
      openAIAnswerLink(anchor.href);
    }

    async function sendAIMessage(forcedMessage = null) {
      if (aiBusy) return;
      const input = document.getElementById('ai-message-input');
      const message = String(forcedMessage === null ? input.value : forcedMessage).trim();
      if (!message) return;
      if (!aiConversationId) {
        await aiNewConversation();
        if (!aiConversationId) return;
      }
      const cid = aiConversationId;
      const requestId = aiRequestId();
      aiAppendHistoryMessage({role: 'user', content: message});
      aiAppendPending(requestId, "send");
      input.value = '';
      aiSetActiveRequest(cid, {request_id: requestId, operation: "send"});
      aiSetStatus("回答を生成中...");
      try {
        const result = await pywebview.api.ai_send_message(message, cid, requestId);
        if (!result.accepted) {
          aiSetActiveRequest(cid, null);
          aiRemovePending(requestId);
          aiSetStatus("送信を開始できません: " + (result.error || "不明なエラー"), true);
          if (cid === aiConversationId) aiAppendError(result.error || "送信を開始できませんでした。");
        }
      } catch (e) {
        aiSetActiveRequest(cid, null);
        aiRemovePending(requestId);
        if (cid === aiConversationId) {
          aiSetStatus("送信エラー: " + e, true);
          aiAppendError("送信エラー: " + e);
        }
      }
    }

    async function aiSendOrCancel() {
      const active = aiConversationId && aiActiveRequests.get(aiConversationId);
      if (!active) return sendAIMessage();
      aiSetStatus("生成を停止しています...");
      try {
        const result = await pywebview.api.ai_cancel_generation(active.request_id);
        if (!result.accepted) aiSetStatus(result.error || "生成を停止できませんでした。", true);
      } catch (e) { aiSetStatus("キャンセルエラー: " + e, true); }
    }

    function onAIStream(payload) {
      const data = typeof payload === 'string' ? JSON.parse(payload) : payload;
      if (data.conversation_id !== aiConversationId) return;
      const active = aiActiveRequests.get(data.conversation_id);
      if (!active || active.request_id !== data.request_id || active.operation !== "send") return;
      const pending = aiPendingMessages.get(data.request_id);
      if (!pending) return;
      const bubble = pending.querySelector('.ai-pending-answer');
      if (bubble.textContent === "回答を生成中...") bubble.textContent = "";
      bubble.textContent += data.text;
      const container = document.getElementById('ai-chat-messages');
      container.scrollTop = container.scrollHeight;
    }

    function onAITitleUpdated() {
      aiLoadConversationList();
    }

    function onAIResponse(payload) {
      try {
        const data = typeof payload === 'string' ? JSON.parse(payload) : payload;
        const cid = data.conversation_id;
        const active = cid && aiActiveRequests.get(cid);
        if (!active || active.request_id === data.request_id) {
          aiSetActiveRequest(cid, null);
        }
        aiRemovePending(data.request_id);
        if (cid === aiConversationId) {
          if (data.ok) {
            if (data.operation === "regenerate") {
              aiOpenConversation(cid).then(() => {
                if (data.missing_level && data.prompt) {
                  aiAppendMissingSourceActions(data.prompt, cid, {
                    missingLevel: data.missing_level.level_id,
                    searchQuery: data.missing_level.query,
                    rebuildNetwork: true,
                  });
                }
              });
            } else {
              aiAppendHistoryMessage({
                id: data.message_id,
                role: 'assistant',
                content: data.response || "",
                current_version_number: data.version_number,
                html: data.rendered_html,
                versions: [{
                  version_number: data.version_number,
                  content: data.response || "",
                  html: data.rendered_html,
                  rating: null,
                  sources: data.sources || [],
                }],
              });
              if (data.missing_level && data.prompt) {
                aiAppendMissingSourceActions(data.prompt, cid, {
                  missingLevel: data.missing_level.level_id,
                  searchQuery: data.missing_level.query,
                  rebuildNetwork: true,
                });
              } else if (data.finish_reason === "no_verified_sources" && data.prompt) {
                aiAppendMissingSourceActions(data.prompt, cid);
              }
            }
            const searchErrors = data.search_status && data.search_status.online_errors || [];
            if (data.finish_reason === "no_verified_sources" && searchErrors.length) {
              aiSetStatus("資料検索エラー: " + searchErrors.map(item =>
                `${item.source || '検索'} ${item.title ? '(' + item.title + ') ' : ''}${item.error || ''}`
              ).join(" / "), true);
            } else if (data.finish_reason === "no_verified_sources") {
              aiSetStatus("出典資料がありません。必要な記事を選び、承諾後に保存できます。");
            } else {
              aiSetStatus("接続中: Groq / " + (data.model || "モデル不明")
                + (data.finish_reason === "length" ? " / 出力上限に到達（回答が途中の可能性があります）" : ""));
            }
          } else if (!data.cancelled) {
            aiSetStatus("APIエラー: " + (data.error || "不明なエラー"), true);
            aiAppendError("回答を生成できませんでした: " + (data.error || "不明なエラー"));
          } else {
            aiSetStatus("生成を停止しました。以前の回答は保持されています。");
          }
          aiRefreshBusy();
        }
        aiLoadConversationList();
      } catch (e) {
        aiSetStatus("応答処理エラー: " + e, true);
      }
    }

    function handleAIKeydown(event) {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        aiSendOrCancel();
      }
    }

    window.addEventListener('pywebviewready', () => {
      onSearchInput();
      loadExplorerIndexPages();
      initializeAI();
      initializeEnvironmentSettings();
      updateHeaderClock();
      refreshSystemStatus();
      setInterval(updateHeaderClock, 1000);
      setInterval(refreshSystemStatus, 7000);
      updateOnlineWikiCurrentUrl();
    });
  </script>
</body>
</html>
"""


def main():
    if getattr(sys, "frozen", False):
        os.chdir(BASE_DIR)
    api = BackroomsAPI()
    html = HTML_TEMPLATE.replace("__VERSION__", VERSION)
    if syntax_highlight_css:
        html = html.replace(
            "</style>",
            syntax_highlight_css() + "\n</style>",
            1,
        )
    api._main_window = webview.create_window(
        title=f"Backrooms Navigation System - Fandom JP v{VERSION} (Wiki Explorer)",
        html=html,
        js_api=api,
        width=1320,
        height=850,
        min_size=(1000, 650)
    )
    api._main_window.events.response_received += (
        api._on_main_window_response_received
    )
    fallback_window = webview.create_window(
        title="Backrooms Wiki取得補助",
        html="<!doctype html><html><head><meta charset='utf-8'></head><body></body></html>",
        width=960,
        height=720,
        hidden=True,
    )
    set_browser_window(fallback_window)
    api._main_window.events.closed += lambda *_args: fallback_window.destroy()
    if os.name == "nt":
        webview.start(debug=False, gui="edgechromium")
    else:
        webview.start(debug=False)


if __name__ == "__main__":
    main()
