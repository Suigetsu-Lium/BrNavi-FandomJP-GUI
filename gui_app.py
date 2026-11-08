# -*- coding: utf-8 -*-
import asyncio
import os
import json
import base64
import re
import threading
import warnings
from pathlib import Path
from urllib.parse import unquote, urlsplit

warnings.filterwarnings("ignore", category=UserWarning)

import webview

# modules ディレクトリから既存ロジックを読み込み
from modules.database import (
    load_master_database,
    open_fandom_page,
    open_local_html
)

# 保守・更新モジュール
from modules.extractor import extract_levels
from modules.network import process_backrooms_data
from modules.update_database import main as run_update_database
from modules.online_wiki import fetch_page as fetch_online_wiki_page
from modules.online_wiki import is_supported_wiki_url
from modules.online_wiki import save_page as save_online_wiki_page
from modules.online_wiki import search_pages as search_online_wiki_pages

# AI Assistant（Groq / ローカルWiki検索）
try:
    from modules.ai.assistant import AIAssistant
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

BASE_DIR = Path(__file__).resolve().parent
IMAGE_DIR = BASE_DIR / "downloaded_images"
TEXT_DIR = BASE_DIR / "extracted_fandom_levels"

# 🌟 ご指定のカテゴリ優先順位（日本独自階層 η を最優先）
CATEGORY_PRIORITY = {
    "normal_eta": 1,     # 1. 通常階層 η
    "sublevel_eta": 2,   # 2. 亜階層 η
    "anomalous_eta": 3,  # 3. 番外階層 η
    "normal": 4,         # 4. 通常階層 (EN翻訳)
    "sublevel": 5,       # 5. 亜階層 (EN翻訳)
    "anomalous": 6,      # 6. 番外階層 (EN翻訳)
}


class BackroomsAPI:
    """PythonとWebViewを繋ぐAPIブリッジ"""
    def __init__(self):
        self.current_info = None
        self.ai = None
        self.ai_error = None
        self._ai_jobs = {}
        self._ai_jobs_lock = threading.Lock()
        self._maintenance_lock = threading.Lock()
        self._current_online_wiki_url = ""
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

    # --- AI Assistant API ---
    def ai_get_status(self):
        if not self.ai:
            return {"available": False, "error": self.ai_error or "AIが初期化されていません。"}
        return self.ai.get_status()

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

    def _extract_level_id_from_filename(self, path):
        """TXTファイル名の先頭からLevel IDを抽出する。

        例:
          Level 20 η__民道駅ホーム_.txt -> Level 20 η
          Level -14.txt                 -> Level -14
          Level 13.1 η__自然体_.txt     -> Level 13.1 η
        """
        name = Path(path).stem.replace("_", " ").strip()
        match = re.match(
            r"^(Level\s+-?\d+(?:\.\d+)?(?:\s*η)?)",
            name,
            re.IGNORECASE
        )
        return re.sub(r"\s+", " ", match.group(1)).strip() if match else None

    def _normalize_level_number_query(self, query):
        """'20', 'Level 20', 'Level 20 η'を数値検索として解釈する。"""
        value = query.strip()
        value = re.sub(r"^level\s*", "", value, flags=re.IGNORECASE).strip()
        match = re.fullmatch(r"(-?\d+(?:\.\d+)?)(?:\s*η)?", value, re.IGNORECASE)
        return match.group(1) if match else None

    def search_candidates(self, query):
        """extracted_fandom_levels内のTXTファイル名を基準にLevelを検索。

        Master Databaseはカテゴリ・言語などの補助情報にのみ使用する。
        本文やURLは検索対象にしない。
        """
        if not TEXT_DIR.exists():
            return []

        query = (query or "").strip().casefold()
        number_query = self._normalize_level_number_query(query) if query else None

        # Master Databaseは表示用メタデータの取得にだけ利用
        master = load_master_database() or {}
        master_map = {
            str(page.get("title", "")).casefold(): page
            for page in master.values()
            if page.get("title")
        }

        results = []
        for text_file in TEXT_DIR.glob("*.txt"):
            level_id = self._extract_level_id_from_filename(text_file)
            if not level_id:
                continue

            if query:
                if number_query is not None:
                    # 数字検索はLevel番号を完全一致させる（20で200を拾わない）
                    number_match = re.match(
                        r"^Level\s+(-?\d+(?:\.\d+)?)",
                        level_id,
                        re.IGNORECASE
                    )
                    if not number_match or number_match.group(1) != number_query:
                        continue
                elif query not in text_file.stem.casefold():
                    # キーワード検索もTXTファイル名だけを対象にする
                    continue

            info = master_map.get(level_id.casefold(), {})
            results.append({
                "title": level_id,
                "category": info.get("category", ""),
                "language": info.get("language", "JP")
            })

        # 同じLevel IDのTXTが複数あっても一覧に重複表示しない
        unique_results = {}
        for item in results:
            unique_results[item["title"].casefold()] = item

        filtered = list(unique_results.values())
        filtered.sort(key=self._get_sort_key)
        return filtered

    def _find_images_flexible(self, level_id):
        """スペースやアンダースコアの差異を吸収して画像を確実に取得"""
        if not IMAGE_DIR.exists():
            return []

        # Level 4 η -> r"^Level[_\s]*4[_\s]*η"
        num_match = re.search(r"Level\s+(-?\d+(?:\.\d+)?)\s*(η)?", level_id, re.IGNORECASE)
        if num_match:
            num = num_match.group(1)
            eta = num_match.group(2)
            if eta:
                pattern = rf"^Level[_\s]+{re.escape(num)}[_\s]+η"
            else:
                pattern = rf"^Level[_\s]+{re.escape(num)}(?:[_\s]|$)"
        else:
            pattern = rf"^{re.escape(level_id)}"

        matched_images = []
        for p in IMAGE_DIR.iterdir():
            if not p.is_file() or p.suffix.lower() not in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
                continue
            if re.search(pattern, p.name, re.IGNORECASE):
                matched_images.append(p)

        matched_images.sort(key=lambda x: x.name)
        return matched_images

    def get_level_detail(self, level_id):
        """本文TXTからメタタイトル・危険度・理解度を直接パースし、画像を確実に連結"""
        # 本文TXTファイルの探索：ファイル名先頭のLevel IDを完全一致で照合
        text_file = None
        if TEXT_DIR.exists():
            requested_id = re.sub(r"\s+", " ", level_id.strip()).casefold()
            for p in TEXT_DIR.glob("*.txt"):
                file_level_id = self._extract_level_id_from_filename(p)
                if file_level_id and file_level_id.casefold() == requested_id:
                    text_file = p
                    break

        raw_text = ""
        if text_file and text_file.exists():
            try:
                with open(text_file, "r", encoding="utf-8", errors="ignore") as f:
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

        danger_m = re.search(r'\[危険度\]\s*\n\s*([^\n\[]+)', raw_text)
        if danger_m:
            danger = danger_m.group(1).strip()

        comp_m = re.search(r'\[理解度\]\s*\n\s*([^\n\[]+)', raw_text)
        if comp_m:
            comprehension = comp_m.group(1).strip()

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

        # 画像の取得（柔軟マッチング）
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
        master = load_master_database()
        page_info = None
        for p in master.values():
            if p["title"].casefold() == level_id.casefold():
                page_info = p
                break

        self.current_info = {
            "url": page_info.get("url", "") if page_info else "",
            "html_file": BASE_DIR / "downloaded_fandom_html" / f"{level_id.replace(' ', '_')}.html"
        }

        category = page_info.get("category", "") if page_info else "階層"
        language = page_info.get("language", "") if page_info else "JP"

        return {
            "level_id": level_id,
            "title": meta_title or (page_info.get("title", "") if page_info else ""),
            "url": self.current_info["url"],
            "category": category,
            "language": language,
            "danger": danger,
            "comprehension": comprehension,
            "text": cleaned_text or "※ 本文データがありません。",
            "images": images_base64
        }

    def open_external_fandom(self):
        if self.current_info and self.current_info.get("url"):
            open_fandom_page(self.current_info["url"])
            return {"status": "success"}
        return {"status": "error", "message": "URLがありません。"}

    def open_local_browser_html(self):
        if self.current_info and self.current_info.get("html_file") and self.current_info["html_file"].exists():
            open_local_html(self.current_info["html_file"])
            return {"status": "success"}
        return {"status": "error", "message": "ローカルHTMLがありません。"}

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
            not url.startswith("https://backrooms.fandom.com/")
            or "text/html" not in content_type.lower()
        ):
            return
        self._current_online_wiki_url = url
        self._notify_online_wiki_url(url, is_supported_wiki_url(url))

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

    def get_current_online_wiki_url(self):
        url = self._current_online_wiki_url
        return {
            "url": url,
            "is_wiki_page": is_supported_wiki_url(url),
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
        return self.run_maintenance_task("save_online_wiki_page", page)

    def run_maintenance_task(self, task_name, selected_urls=None):
        if task_name in {"download_html", "download_selected_html"}:
            return {
                "status": "error",
                "message": "オンラインWiki検索からページを選択してください。",
            }
        if not self._maintenance_lock.acquire(blocking=False):
            return {"status": "busy", "message": "別の保守タスクが実行中です。"}
        worker = threading.Thread(
            target=self._execute_task,
            args=(task_name, selected_urls),
            daemon=True,
        )
        try:
            worker.start()
        except Exception:
            self._maintenance_lock.release()
            raise
        return {"status": "started"}

    def _execute_task(self, task_name, selected_urls=None):
        if task_name == "save_online_wiki_page":
            payload = {"ok": False}
            try:
                page = selected_urls
                print(
                    f"GUIから選択されたWikiページを保存します: "
                    f"{page.get('title', '')}"
                )
                result = save_online_wiki_page(page)
                payload = {"ok": True, **result}
            except Exception as error:
                print(f"Wikiページ保存エラー: {error}")
                payload["error"] = str(error)
            finally:
                try:
                    encoded = json.dumps(payload, ensure_ascii=True)
                    main_window = getattr(self, "_main_window", None)
                    if main_window is not None:
                        main_window.evaluate_js(
                            f"onOnlineWikiSaveCompleted({encoded})"
                        )
                except Exception as error:
                    print(f"[GUI] Wiki保存完了通知に失敗: {error}")
                self._maintenance_lock.release()
            return

        task_labels = {
            "update_master": "既存データベース更新",
            "extract_info": "情報データベース更新",
            "update_network": "ネットワーク更新",
        }
        task_label = task_labels.get(task_name)
        if task_label is None:
            self._notify_maintenance_task_completed(
                False, f"未対応の保守タスクです: {task_name}"
            )
            self._maintenance_lock.release()
            return

        print(f"保守タスクを開始します: {task_label}")
        try:
            if task_name == "update_master":
                run_update_database()
            elif task_name == "extract_info":
                extract_levels(interactive=False)
            elif task_name == "update_network":
                process_backrooms_data()
            print(f"保守タスクが完了しました: {task_label}")
            self._notify_maintenance_task_completed(
                True, f"{task_label}が完了しました。"
            )
        except Exception as error:
            print(f"保守タスクに失敗しました ({task_label}): {error}")
            self._notify_maintenance_task_completed(
                False, f"{task_label}に失敗しました: {error}"
            )
        finally:
            self._maintenance_lock.release()

    def _notify_maintenance_task_completed(self, succeeded, message):
        main_window = getattr(self, "_main_window", None)
        if main_window is None:
            return
        payload = json.dumps(
            {"ok": succeeded, "message": message},
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
  </style>
</head>
<body class="flex flex-col h-screen overflow-hidden select-text">

  <!-- トップナビバー -->
  <header class="bg-[#1f1a14] text-white px-5 py-2.5 flex justify-between items-center shadow-md select-none">
    <div class="flex items-center space-x-3">
      <div class="w-8 h-8 rounded-lg bg-[#b45309] flex items-center justify-center font-bold text-lg text-white shadow">
        ♾️
      </div>
      <div>
        <h1 class="text-sm font-bold tracking-wide">Backrooms Wiki (Fandom JP)</h1>
        <p class="text-[10px] text-gray-400">Navigation & Database System</p>
      </div>
    </div>
    <div class="flex space-x-2">
      <button onclick="switchTab('explorer')" id="tab-btn-explorer" class="px-4 py-1.5 rounded text-xs font-semibold bg-[#b45309] text-white transition shadow">📖 Level 図鑑・検索</button>
      <button onclick="switchTab('wiki')" id="tab-btn-wiki" class="px-4 py-1.5 rounded text-xs font-semibold bg-gray-800 text-gray-300 hover:bg-gray-700 transition">🌐 Wiki閲覧・保存</button>
      <button onclick="switchTab('maintenance')" id="tab-btn-maintenance" class="px-4 py-1.5 rounded text-xs font-semibold bg-gray-800 text-gray-300 hover:bg-gray-700 transition">⚙️ データ保守</button>
      <button onclick="switchTab('ai')" id="tab-btn-ai" class="px-4 py-1.5 rounded text-xs font-semibold bg-gray-800 text-gray-300 hover:bg-gray-700 transition">🤖 AI Assistant</button>
    </div>
  </header>

  <main class="flex-1 flex overflow-hidden">

    <!-- 1. 📖 Level 図鑑 ＆ 検索タブ -->
    <div id="view-explorer" class="flex-1 flex overflow-hidden">

      <!-- 左ペイン：検索 ＆ 全件縦スライダースクロール一覧 -->
      <aside class="w-80 border-r border-[#e2d8b5] bg-[#f7f0d4] flex flex-col select-none">
        <div class="p-3 border-b border-[#e2d8b5] bg-[#f2e8c5]">
          <input type="text" id="search-input" placeholder="Level名・番号・キーワードで検索..."
            oninput="onSearchInput()"
            class="w-full px-3 py-2 bg-white border border-[#d6c79a] rounded text-xs focus:outline-none focus:border-[#b45309] shadow-inner">
        </div>
        <div class="px-3 py-1.5 text-[11px] font-bold text-gray-600 bg-[#eee3bc] border-b border-[#e2d8b5] flex justify-between">
          <span>階層一覧（通常η優先順）</span>
          <span><strong id="candidate-count">0</strong> 件</span>
        </div>
        <!-- 🌟 スライダー（縦スクロールバー）で全件閲覧可能なコンテナ -->
        <div id="candidate-list" class="flex-1 overflow-y-auto p-2 space-y-1.5 text-xs"></div>
      </aside>

      <!-- 右ペイン：Wiki 詳細ビュー -->
      <section class="flex-1 overflow-y-auto p-8 bg-[#fbf6de]">
        <div id="wiki-page-container" class="max-w-4xl mx-auto bg-white border border-[#e2d8b5] p-8 rounded shadow-sm relative">

          <div class="text-xs text-[#8c6b12] mb-1 font-medium">
            分類: <span id="view-category">階層</span>、危険度: <span id="view-danger-tag">-</span>
          </div>

          <div class="flex justify-between items-start border-b border-[#e2d8b5] pb-3 mb-6">
            <div>
              <h2 id="view-level-id" class="text-2xl font-bold text-[#202124]">Level 0 η</h2>
              <div id="view-meta-title" class="text-base font-semibold text-[#b45309] mt-0.5"></div>
            </div>
            <div class="flex space-x-2 select-none">
              <button onclick="pywebview.api.open_external_fandom()" class="px-2.5 py-1 text-xs bg-[#f4e7b8] hover:bg-[#e8d89f] text-[#784f00] rounded font-medium border border-[#d9c78d]">🌐 Fandomを開く</button>
              <button onclick="pywebview.api.open_local_browser_html()" class="px-2.5 py-1 text-xs bg-gray-100 hover:bg-gray-200 text-gray-700 rounded font-medium border border-gray-300">📄 ローカルHTML</button>
            </div>
          </div>

          <!-- 本文 ＆ Infobox -->
          <div class="flex space-x-6 items-start">

            <!-- 本文エリア (左) -->
            <div class="flex-1 text-sm leading-relaxed text-gray-800">
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

            <!-- 🌟 Wiki Infobox (右側情報カード) -->
            <aside class="wiki-infobox rounded overflow-hidden select-none sticky top-0">
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
                <tr><th>危険度</th><td id="infobox-danger" class="font-bold text-[#b45309]">-</td></tr>
                <tr><th>理解度</th><td id="infobox-comprehension">-</td></tr>
                <tr><th>カテゴリ</th><td id="infobox-category">-</td></tr>
                <tr><th>言語</th><td id="infobox-lang">-</td></tr>
              </table>
            </aside>

          </div>
        </div>
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
          <input id="online-wiki-url" type="url"
            onkeydown="if(event.key==='Enter') openDirectWikiUrl()"
            class="min-w-0 flex-1 border border-[#d6c79a] rounded px-3 py-1.5 text-xs"
            value="https://backrooms.fandom.com/ja/wiki/Backrooms_Wiki"
            placeholder="https://backrooms.fandom.com/ja/wiki/...">
          <button onclick="openDirectWikiUrl()"
            class="shrink-0 px-3 py-1.5 text-xs font-semibold bg-[#91520f] text-white rounded">
            移動
          </button>
          <button id="online-wiki-save-current" onclick="saveCurrentOnlineWikiPage()" disabled
            class="shrink-0 px-3 py-1.5 text-xs font-semibold bg-[#b45309] text-white rounded disabled:opacity-50">
            表示中を保存
          </button>
          <span id="online-wiki-save-status" class="shrink-0 text-[11px] text-gray-600">保存先: downloaded_fandom_html</span>
        </div>
        <div class="flex-1 min-h-0 overflow-hidden rounded border border-[#d6c79a] bg-white">
          <iframe id="online-wiki-frame"
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

    <!-- 3. ⚙️ データベース保守タブ -->
    <div id="view-maintenance" class="hidden flex-1 flex flex-col p-8 bg-[#fbf6de] overflow-y-auto">
      <div class="max-w-3xl mx-auto w-full bg-white border border-[#e2d8b5] p-6 rounded shadow-sm space-y-5">
        <h2 class="text-lg font-bold text-gray-800 border-b border-gray-200 pb-2">⚙️ データ保守</h2>
        <div class="grid grid-cols-2 gap-3 pt-2">
          <button onclick="runTask('update_master')" class="p-3 bg-[#f5eed3] hover:bg-[#eadfae] border border-[#d6c79a] rounded text-xs font-semibold text-gray-800 text-left">
            <span class="text-sm block">🔄 既存データベース更新</span>
            <span class="text-[10px] text-gray-500">図鑑・ルート機能の互換用（オンライン閲覧・保存には不要）</span>
          </button>
          <button onclick="runTask('extract_info')" class="p-3 bg-[#f5eed3] hover:bg-[#eadfae] border border-[#d6c79a] rounded text-xs font-semibold text-gray-800 text-left">
            <span class="text-sm block">📑 情報データベース更新</span>
            <span class="text-[10px] text-gray-500">ローカルHTMLから本文やタグを抽出</span>
          </button>
          <button onclick="runTask('update_network')" class="p-3 bg-[#f5eed3] hover:bg-[#eadfae] border border-[#d6c79a] rounded text-xs font-semibold text-gray-800 text-left">
            <span class="text-sm block">🕸️ ネットワーク更新</span>
            <span class="text-[10px] text-gray-500">Level間の出入口・接続グラフを生成</span>
          </button>
        </div>
        <div id="maintenance-task-status" role="status" aria-live="polite"
          class="min-h-10 rounded border border-[#dfd3a8] bg-[#fffdf5] px-3 py-2 text-xs text-gray-600">
          保守タスクを選択してください。
        </div>
      </div>
    </div>

    <!-- 4. BrNavi AI Assistant -->
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

  <script>
    let onlineWikiDefaultOpened = false;

    function switchTab(tab) {
      ['explorer', 'wiki', 'maintenance', 'ai'].forEach(t => {
        document.getElementById('view-' + t).classList.add('hidden');
        document.getElementById('tab-btn-' + t).className = "px-4 py-1.5 rounded text-xs font-semibold bg-gray-800 text-gray-300 hover:bg-gray-700 transition";
      });
      document.getElementById('view-' + tab).classList.remove('hidden');
      document.getElementById('tab-btn-' + tab).className = "px-4 py-1.5 rounded text-xs font-semibold bg-[#b45309] text-white transition shadow";
      if (tab === 'wiki' && !onlineWikiDefaultOpened) {
        openDirectWikiUrl();
      }
    }

    async function onSearchInput() {
      const q = document.getElementById('search-input').value;
      const candidates = await pywebview.api.search_candidates(q);
      renderCandidates(candidates);
    }

    // 🌟 全件リストを描画（スライダー対応）
    function renderCandidates(list) {
      const cList = document.getElementById('candidate-list');
      cList.innerHTML = '';
      document.getElementById('candidate-count').innerText = list.length;

      list.forEach(c => {
        const item = document.createElement('div');
        item.className = "p-2 rounded bg-white border border-[#dfd3a8] hover:border-[#b45309] hover:bg-[#fffdf2] cursor-pointer transition shadow-sm";
        const title = document.createElement('div');
        title.className = "font-bold text-gray-800 truncate";
        title.textContent = c.title || "";
        const meta = document.createElement('div');
        meta.className = "text-[10px] text-gray-400 mt-0.5 flex space-x-1.5";
        const lang = document.createElement('span');
        lang.className = "px-1 bg-yellow-100 text-yellow-800 rounded font-mono";
        lang.textContent = c.language || 'JP';
        const category = document.createElement('span');
        category.className = "truncate text-gray-500";
        category.textContent = c.category || '階層';
        meta.append(lang, category);
        item.append(title, meta);
        item.onclick = () => loadLevelDetail(c.title);
        cList.appendChild(item);
      });
    }

    // 本文TXTのマークダウン・見出し整形
    function escapeHTML(value) {
      return String(value).replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      })[ch]);
    }

    function formatWikiContent(text) {
      if (!text) return "";

      const lines = text.split('\n');
      let html = '';
      let paragraph = [];

      function flushParagraph() {
        if (paragraph.length > 0) {
          html += `<p class="my-2.5 text-gray-800 leading-relaxed">${paragraph.map(escapeHTML).join('<br>')}</p>`;
          paragraph = [];
        }
      }

      for (let line of lines) {
        let trimmed = line.trim();
        if (!trimmed) {
          flushParagraph();
          continue;
        }

        // [概要] や [出入口] などの見出しタグを検出して公式Wiki風見出しに変換
        const headingMatch = trimmed.match(/^\[([^\]]+)\]$/);
        if (headingMatch) {
          flushParagraph();
          const title = headingMatch[1];
          html += `
            <div class="wiki-heading">
              <span>${escapeHTML(title)}</span>
              <span class="text-xs text-gray-400 font-normal">✎</span>
            </div>
          `;
        } else {
          paragraph.push(trimmed);
        }
      }
      flushParagraph();
      return html;
    }

    // レベル詳細の描画
    async function loadLevelDetail(levelId) {
      const d = await pywebview.api.get_level_detail(levelId);

      document.getElementById('view-level-id').innerText = d.level_id;
      document.getElementById('view-meta-title').innerText = d.title && d.title !== d.level_id ? `"${d.title}"` : "";
      document.getElementById('view-category').innerText = d.category;
      document.getElementById('view-danger-tag').innerText = d.danger;

      // 本文の整形描画
      document.getElementById('view-article-content').innerHTML = formatWikiContent(d.text);

      // Infobox
      document.getElementById('infobox-title').innerText = d.level_id;
      document.getElementById('infobox-danger').innerText = d.danger;
      document.getElementById('infobox-comprehension').innerText = d.comprehension;
      document.getElementById('infobox-category').innerText = d.category;
      document.getElementById('infobox-lang').innerText = d.language;

      // メイン画像
      const mainImg = document.getElementById('infobox-main-img');
      const noImg = document.getElementById('infobox-no-img');
      const caption = document.getElementById('infobox-caption');

      if (d.images && d.images.length > 0) {
        mainImg.src = d.images[0].src;
        mainImg.classList.remove('hidden');
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
        d.images.slice(1).forEach(img => {
          const card = document.createElement('div');
          card.className = "bg-[#fdfaf0] border border-[#d6c99c] rounded p-2 text-center shadow-sm";
          card.innerHTML = `
            <img src="${img.src}" class="w-full h-32 object-cover rounded border border-[#dfd3a8] mb-1">
            <span class="text-[10px] text-gray-500 truncate block" title="${img.name}">${img.name}</span>
          `;
          gallery.appendChild(card);
        });
      } else {
        galleryContainer.classList.add('hidden');
      }
    }

    let htmlDownloadPages = [];
    let onlineWikiBrowserBusy = false;
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
        onOnlineWikiBrowserUrlChanged({url: result.url, is_wiki_page: true});
      } catch (error) {
        status.textContent = "Fandomを開けませんでした: " + error;
      } finally {
        onlineWikiBrowserBusy = false;
        if (onlineWikiCurrentUrl) {
          onOnlineWikiBrowserUrlChanged({
            url: onlineWikiCurrentUrl,
            is_wiki_page: onlineWikiCurrentIsWiki
          });
        }
      }
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

    async function saveCurrentOnlineWikiPage() {
      if (onlineWikiBrowserBusy || !onlineWikiCurrentUrl) return;
      onlineWikiBrowserBusy = true;
      document.getElementById('online-wiki-save-current').disabled = true;
      document.getElementById('online-wiki-save-status').textContent =
        "表示中のWiki本文と画像を保存しています...";
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

    function onOnlineWikiSaveCompleted(result) {
      onlineWikiBrowserBusy = false;
      if (result.ok) {
        document.getElementById('online-wiki-save-status').textContent =
          `保存完了: ${result.title} / 画像 ${result.images_saved}件` +
          (result.images_failed ? ` (失敗 ${result.images_failed}件)` : "") +
          ` / ${result.path}`;
      } else {
        document.getElementById('online-wiki-save-status').textContent =
          "保存エラー: " + (result.error || "不明なエラー");
      }
      updateOnlineWikiCurrentUrl();
    }

    async function runTask(taskName) {
      const status = document.getElementById('maintenance-task-status');
      status.textContent = "保守タスクを開始しています...";
      status.className = "min-h-10 rounded border border-[#dfd3a8] bg-[#fffdf5] px-3 py-2 text-xs text-gray-600";
      try {
        const result = await pywebview.api.run_maintenance_task(taskName);
        if (result.status !== "started") {
          status.textContent = result.message || "タスクを開始できませんでした。";
          status.className = "min-h-10 rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800";
        }
      } catch (error) {
        status.textContent = "タスク開始エラー: " + error;
        status.className = "min-h-10 rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800";
      }
    }

    function onMaintenanceTaskCompleted(result) {
      const status = document.getElementById('maintenance-task-status');
      status.textContent = result.message || (result.ok ? "処理が完了しました。" : "処理に失敗しました。");
      status.className = result.ok
        ? "min-h-10 rounded border border-green-300 bg-green-50 px-3 py-2 text-xs text-green-800"
        : "min-h-10 rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800";
      onSearchInput();
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
          return;
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
      } catch (e) {
        aiSetStatus("会話作成エラー: " + e, true);
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

    async function sendAIMessage() {
      if (aiBusy) return;
      const input = document.getElementById('ai-message-input');
      const message = input.value.trim();
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
              aiOpenConversation(cid);
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
            }
            aiSetStatus("接続中: Groq / " + (data.model || "モデル不明")
              + (data.finish_reason === "length" ? " / 出力上限に到達（回答が途中の可能性があります）" : ""));
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
      initializeAI();
      updateOnlineWikiCurrentUrl();
    });
  </script>
</body>
</html>
"""


def main():
    api = BackroomsAPI()
    html = HTML_TEMPLATE
    if syntax_highlight_css:
        html = html.replace(
            "</style>",
            syntax_highlight_css() + "\n</style>",
            1,
        )
    api._main_window = webview.create_window(
        title="Backrooms Navigation System - Fandom JP (Wiki Explorer)",
        html=html,
        js_api=api,
        width=1320,
        height=850,
        min_size=(1000, 650)
    )
    api._main_window.events.response_received += (
        api._on_main_window_response_received
    )
    if os.name == "nt":
        webview.start(debug=False, gui="edgechromium")
    else:
        webview.start(debug=False)


if __name__ == "__main__":
    main()