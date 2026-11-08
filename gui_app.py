# -*- coding: utf-8 -*-
import asyncio
import sys
import os
import json
import base64
import re
import threading
import io
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning)

import webview

# modules ディレクトリから既存ロジックを読み込み
from modules.database import (
    load_master_database,
    open_fandom_page,
    open_local_html
)

# 保守・更新モジュール
from modules.downloader import main as run_downloader
from modules.extractor import extract_levels
from modules.network import process_backrooms_data
from modules.update_database import main as run_update_database

# AI Assistant（Groq / ローカルWiki検索）
try:
    from modules.ai.assistant import AIAssistant
    from modules.ai.markdown_renderer import render_markdown, syntax_highlight_css
    AI_AVAILABLE = True
except Exception as e:
    AIAssistant = None
    render_markdown = None
    syntax_highlight_css = None
    AI_AVAILABLE = False
    print(f"[AI] モジュールを読み込めません: {e}")

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
            self.ai_error = "AIモジュールを読み込めません。"

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
    def run_maintenance_task(self, task_name):
        threading.Thread(target=self._execute_task, args=(task_name,), daemon=True).start()
        return {"status": "started"}

    def _execute_task(self, task_name):
        webview.windows[0].evaluate_js(f"appendLog('=== [{task_name}] を開始します ===')")
        class WebLogger(io.StringIO):
            def write(self, s):
                if s.strip():
                    safe_str = json.dumps(s.strip())
                    try:
                        webview.windows[0].evaluate_js(f"appendLog({safe_str})")
                    except Exception:
                        pass
        old_stdout = sys.stdout
        sys.stdout = WebLogger()
        try:
            if task_name == "update_master":
                run_update_database()
            elif task_name == "download_html":
                run_downloader()
            elif task_name == "extract_info":
                extract_levels(interactive=False)
            elif task_name == "update_network":
                process_backrooms_data()
            print("✔ 処理が正常に完了しました！")
        except Exception as e:
            print(f"❌ エラー: {e}")
        finally:
            sys.stdout = old_stdout
            webview.windows[0].evaluate_js("onTaskCompleted()")


HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>Backrooms Navigation System - Fandom JP</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    :root {
      --bg-fandom: #fbf6de;       /* 湿った壁紙イエロー */
      --border-fandom: #e2d8b5;   /* ベージュ枠線 */
      --header-amber: #b45309;    /* Infoboxオレンジ */
    }

    body {
      background-color: var(--bg-fandom);
      color: #202124;
      font-family: -apple-system, BlinkMacSystemFont, "BIZ UDPGothic", "Meiryo", sans-serif;
    }

    .toc-box {
      background-color: #f5eed3;
      border: 1px solid #dfd3a8;
    }

    /* Wiki Infobox（情報カード） */
    .wiki-infobox {
      width: 320px;
      background: #ffffff;
      border: 1px solid #d4c89f;
      box-shadow: 0 1px 3px rgba(0,0,0,0.06);
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
      background-color: #f7f1dc;
      border-top: 1px solid #e5dcbe;
      border-bottom: 1px solid #e5dcbe;
      padding: 6px 10px;
      text-align: left;
      width: 38%;
      font-weight: 600;
    }
    .wiki-infobox td {
      border-top: 1px solid #e5dcbe;
      border-bottom: 1px solid #e5dcbe;
      padding: 6px 10px;
    }

    /* 本文のWiki小見出し */
    .wiki-heading {
      font-size: 18px;
      font-weight: bold;
      color: #202124;
      border-bottom: 1px solid #dcd1a6;
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
    .ai-answer-content p { margin: 0.5rem 0; }
    .ai-answer-content blockquote {
      border-left: 3px solid #b45309;
      padding-left: 0.75rem;
      color: #57534e;
    }
    .ai-answer-content a { color: #92400e; text-decoration: underline; }
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
      <button onclick="switchTab('maintenance')" id="tab-btn-maintenance" class="px-4 py-1.5 rounded text-xs font-semibold bg-gray-800 text-gray-300 hover:bg-gray-700 transition">⚙️ データベース保守</button>
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

    <!-- 2. ⚙️ データベース保守タブ -->
    <div id="view-maintenance" class="hidden flex-1 flex flex-col p-8 bg-[#fbf6de] overflow-y-auto">
      <div class="max-w-3xl mx-auto w-full bg-white border border-[#e2d8b5] p-6 rounded shadow-sm space-y-5">
        <h2 class="text-lg font-bold text-gray-800 border-b border-gray-200 pb-2">⚙️ データベース・ローカルデータ保守</h2>
        <div class="grid grid-cols-2 gap-3 pt-2">
          <button onclick="runTask('update_master')" class="p-3 bg-[#f5eed3] hover:bg-[#eadfae] border border-[#d6c79a] rounded text-xs font-semibold text-gray-800 text-left">
            <span class="text-sm block">🔄 Master Database 更新</span>
            <span class="text-[10px] text-gray-500">一覧HTMLからLevelリストを更新</span>
          </button>
          <button onclick="runTask('download_html')" class="p-3 bg-[#f5eed3] hover:bg-[#eadfae] border border-[#d6c79a] rounded text-xs font-semibold text-gray-800 text-left">
            <span class="text-sm block">📥 HTML 一括ダウンロード</span>
            <span class="text-[10px] text-gray-500">未取得のLevelページを取得</span>
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
        <div class="mt-4">
          <div class="flex justify-between text-xs text-gray-500 mb-1">
            <span>実行ログ:</span>
            <button onclick="document.getElementById('task-log').innerText=''" class="hover:underline">クリア</button>
          </div>
          <div id="task-log" class="w-full h-56 bg-[#1a1714] text-green-400 font-mono text-xs p-3 rounded overflow-y-auto whitespace-pre-wrap select-text">待機中...</div>
        </div>
      </div>
    </div>

    <!-- 3. BrNavi AI Assistant -->
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
    function switchTab(tab) {
      ['explorer', 'maintenance', 'ai'].forEach(t => {
        document.getElementById('view-' + t).classList.add('hidden');
        document.getElementById('tab-btn-' + t).className = "px-4 py-1.5 rounded text-xs font-semibold bg-gray-800 text-gray-300 hover:bg-gray-700 transition";
      });
      document.getElementById('view-' + tab).classList.remove('hidden');
      document.getElementById('tab-btn-' + tab).className = "px-4 py-1.5 rounded text-xs font-semibold bg-[#b45309] text-white transition shadow";
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

    function appendLog(str) {
      const logDiv = document.getElementById('task-log');
      logDiv.innerText += "\n" + str;
      logDiv.scrollTop = logDiv.scrollHeight;
    }

    function runTask(taskName) {
      appendLog(`▶ タスクを開始します...`);
      pywebview.api.run_maintenance_task(taskName);
    }

    function onTaskCompleted() {
      appendLog(`✔ 処理が終了しました。`);
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
    webview.create_window(
        title="Backrooms Navigation System - Fandom JP (Wiki Explorer)",
        html=html,
        js_api=api,
        width=1320,
        height=850,
        min_size=(1000, 650)
    )
    webview.start(debug=False)


if __name__ == "__main__":
    main()