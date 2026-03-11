#!/usr/bin/env python3
"""
note-refine Web UI  (Tornado版)
起動: python web_app.py
ブラウザで http://localhost:5000 を開く
"""

import sys
import json
import threading
import queue
import uuid
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from section_manager import (
    list_sections, read_section, write_section,
    find_section_by_name, write_all, build_all,
    split_markdown_to_sections,
    save_section_iteration, load_state, save_state, append_history,
)

DRAFTS_DIR = BASE_DIR / "drafts"
DRAFTS_DIR.mkdir(exist_ok=True)

import tornado.ioloop
import tornado.web
import tornado.gen

# ── グローバル状態 ─────────────────────────────────────────────────
jobs: dict = {}
_jobs_lock = threading.Lock()
_api_config = {
    "key":   os.environ.get("GEMINI_API_KEY", ""),
    "model": os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
}


# ── stdout キャプチャ ─────────────────────────────────────────────

class StreamCapture:
    def __init__(self, q: queue.Queue):
        self.queue = q
    def write(self, text: str):
        if text and text.strip():
            self.queue.put({"type": "log", "text": text.rstrip("\n")})
    def flush(self):
        pass


def _get_client():
    from llm_client import GeminiClient
    key = _api_config.get("key") or ""
    if not key:
        raise ValueError("GEMINI_API_KEY が設定されていません。⚙️ 設定から入力してください。")
    return GeminiClient(api_key=key, model=_api_config.get("model", "gemini-2.5-flash"))


# ── ジョブ管理 ────────────────────────────────────────────────────

def _start_job(fn) -> str:
    job_id = str(uuid.uuid4())
    q = queue.Queue()
    with _jobs_lock:
        jobs[job_id] = {"status": "running", "queue": q, "result": None, "error": None}

    def _wrapper():
        original_stdout = sys.stdout
        sys.stdout = StreamCapture(q)
        try:
            fn(q)
            jobs[job_id]["status"] = "done"
        except Exception as exc:
            import traceback
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(exc)
            q.put({"type": "error", "message": str(exc), "detail": traceback.format_exc()})
        finally:
            sys.stdout = original_stdout
            q.put(None)

    threading.Thread(target=_wrapper, daemon=True).start()
    return job_id


# ── Tornado ハンドラ基底 ──────────────────────────────────────────

class BaseHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

    def json_body(self):
        try:
            return json.loads(self.request.body)
        except Exception:
            return {}

    def ok(self, data):
        self.write(json.dumps(data, ensure_ascii=False))

    def err(self, msg, code=400):
        self.set_status(code)
        self.write(json.dumps({"error": msg}, ensure_ascii=False))


# ── ルートハンドラ ────────────────────────────────────────────────

class IndexHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.write(HTML)


class ConfigHandler(BaseHandler):
    def get(self):
        self.ok({
            "api_key_set": bool(_api_config.get("key")),
            "model": _api_config.get("model", "gemini-2.5-flash"),
        })

    def post(self):
        data = self.json_body()
        if "api_key" in data:
            _api_config["key"] = data["api_key"]
        if "model" in data:
            _api_config["model"] = data["model"]
        self.ok({"ok": True})


class ArticlesHandler(BaseHandler):
    def get(self):
        result = []
        for d in sorted(DRAFTS_DIR.iterdir()):
            if d.is_dir() and not d.name.startswith("_"):
                sections = list_sections(d)
                state = load_state(d)
                result.append({
                    "name": d.name,
                    "section_count": len(sections),
                    "iteration": state.get("iteration", 0),
                    "history_count": len(state.get("history", [])),
                })
        self.ok(result)

    def post(self):
        import time
        # ファイルアップロードか JSON テキストか
        if self.request.files:
            file_info = self.request.files.get("file", [None])[0]
            if not file_info or not file_info["filename"].endswith(".md"):
                return self.err(".md ファイルのみ対応しています")
            name = Path(file_info["filename"]).stem
            tmp = BASE_DIR / f"_upload_{name}.md"
            tmp.write_bytes(file_info["body"])
        else:
            data = self.json_body()
            content = data.get("content", "").strip()
            name = (data.get("name") or "article").strip()
            if not content:
                return self.err("コンテンツが空です")
            tmp = BASE_DIR / f"_upload_{name}.md"
            tmp.write_text(content, encoding="utf-8")

        try:
            article_dir = DRAFTS_DIR / name
            if article_dir.exists():
                name = f"{name}_{int(time.time())}"
                article_dir = DRAFTS_DIR / name
            article_dir.mkdir(parents=True, exist_ok=True)
            sections = split_markdown_to_sections(tmp, article_dir)
            self.ok({"name": article_dir.name, "section_count": len(sections)})
        finally:
            if tmp.exists():
                tmp.unlink()


class ArticleHandler(BaseHandler):
    def get(self, name):
        article_dir = DRAFTS_DIR / name
        if not article_dir.exists():
            return self.err("見つかりません", 404)
        sections = list_sections(article_dir)
        sections_data = []
        for s in sections:
            content = read_section(s)
            sections_data.append({
                "filename": s["filename"],
                "index": s["index"],
                "slug": s["slug"],
                "content": content,
                "length": len(content),
            })
        state = load_state(article_dir)
        all_path = article_dir / "all.md"
        all_content = all_path.read_text(encoding="utf-8") if all_path.exists() else build_all(article_dir)
        self.ok({"name": name, "sections": sections_data, "state": state, "all_content": all_content})


class RefineHandler(BaseHandler):
    def post(self, name):
        article_dir = DRAFTS_DIR / name
        if not article_dir.exists():
            return self.err("見つかりません", 404)
        data = self.json_body()
        feedback = data.get("feedback", "").strip()
        if not feedback:
            return self.err("フィードバックを入力してください")
        section_hint = data.get("section") or None
        skip_validation = bool(data.get("skip_validation", False))
        skip_coherence  = bool(data.get("skip_coherence", False))

        def job_fn(q: queue.Queue):
            from agents import critic, editor, validator, coherence
            client = _get_client()
            state = load_state(article_dir)
            iter_num = state.get("iteration", 0) + 1

            sections = list_sections(article_dir)
            sections_content = {s["filename"]: read_section(s) for s in sections}

            target_hint = None
            target_section_dict = None
            if section_hint:
                target_section_dict = find_section_by_name(article_dir, section_hint)
                if target_section_dict:
                    target_hint = target_section_dict["filename"]

            # Critic
            q.put({"type": "phase", "phase": "critic", "label": "CriticAgent"})
            critique = critic.run(sections_content, feedback, target_hint, client)
            q.put({"type": "critique", "data": critique})

            critic_target = critique.get("target_section")
            if critic_target and not target_section_dict:
                target_section_dict = find_section_by_name(article_dir, critic_target)
            if not target_section_dict and sections:
                target_section_dict = sections[0]

            target_name = target_section_dict["filename"]
            target_content = sections_content[target_name]
            q.put({"type": "target", "section": target_name})

            # Editor
            q.put({"type": "phase", "phase": "editor", "label": "EditorAgent"})
            improved = editor.run(target_name, target_content, sections_content, critique, client)
            q.put({"type": "editor_result", "original": target_content, "improved": improved, "section": target_name})

            # Validator
            validation_result = {"verdict": "pass", "score": 100, "coherence_risk": "low", "recommendation": ""}
            if not skip_validation:
                q.put({"type": "phase", "phase": "validator", "label": "ValidatorAgent"})
                validation_result = validator.run(target_name, target_content, feedback, improved, critique, client)
                q.put({"type": "validation", "data": validation_result})
            else:
                q.put({"type": "log", "text": "⏭️  ValidatorAgent をスキップ"})

            write_section(target_section_dict, improved)
            save_section_iteration(article_dir, target_section_dict, improved, iter_num)
            q.put({"type": "log", "text": f"💾 {target_name} を保存しました"})

            # Coherence
            updated = {**sections_content, target_name: improved}
            coherence_applied = False
            if skip_coherence:
                write_all(article_dir)
                q.put({"type": "log", "text": "⏭️  CoherenceAgent をスキップ"})
            else:
                q.put({"type": "phase", "phase": "coherence", "label": "CoherenceAgent"})
                all_text, coherence_report = coherence.run(updated, target_name, client)
                (article_dir / "all.md").write_text(all_text, encoding="utf-8")
                coherence_applied = True
                q.put({"type": "coherence_result", "report": coherence_report})

            verdict = validation_result.get("verdict", "pass")
            score   = validation_result.get("score", 100)
            append_history(state, iter_num, target_name, feedback, critique.get("summary",""), score, verdict, coherence_applied)
            save_state(article_dir, state)

            result = {"iter": iter_num, "target": target_name, "score": score, "verdict": verdict,
                      "original": target_content, "improved": improved}
            q.put({"type": "complete", **result})
            jobs[_current_job_id]["result"] = result

        _current_job_id = _start_job(job_fn)
        # Hack: re-assign job_id inside closure via jobs dict
        # Actually the job_id is captured before fn is called; let's just return it
        self.ok({"job_id": _current_job_id})


class CoherenceOnlyHandler(BaseHandler):
    def post(self, name):
        article_dir = DRAFTS_DIR / name
        if not article_dir.exists():
            return self.err("見つかりません", 404)

        def job_fn(q: queue.Queue):
            from agents import coherence
            client = _get_client()
            sections = list_sections(article_dir)
            sections_content = {s["filename"]: read_section(s) for s in sections}
            q.put({"type": "phase", "phase": "coherence", "label": "CoherenceAgent"})
            all_text, report = coherence.run(sections_content, "", client)
            (article_dir / "all.md").write_text(all_text, encoding="utf-8")
            q.put({"type": "coherence_result", "report": report})
            q.put({"type": "complete", "message": "整合性調整完了"})

        job_id = _start_job(job_fn)
        self.ok({"job_id": job_id})


class StreamHandler(tornado.web.RequestHandler):
    """SSE ストリーミング (非同期ポーリング)"""

    async def get(self, job_id):
        if job_id not in jobs:
            self.set_status(404)
            self.write('{"error":"job not found"}')
            return

        self.set_header("Content-Type", "text/event-stream")
        self.set_header("Cache-Control", "no-cache")
        self.set_header("X-Accel-Buffering", "no")
        self.set_header("Access-Control-Allow-Origin", "*")

        q = jobs[job_id]["queue"]
        while True:
            try:
                item = q.get_nowait()
            except queue.Empty:
                await tornado.gen.sleep(0.05)
                continue

            if item is None:
                self.write(f"data: {json.dumps({'type': 'stream_end'})}\n\n")
                await self.flush()
                break

            self.write(f"data: {json.dumps(item, ensure_ascii=False)}\n\n")
            try:
                await self.flush()
            except Exception:
                break


# ── 音声文字起こし ────────────────────────────────────────────────

class TranscribeHandler(BaseHandler):
    """
    ブラウザの MediaRecorder から送られた音声を Whisper で文字起こし。
    Content-Type: audio/webm (または audio/wav) のバイナリを body で受け取る。
    """

    async def post(self):
        # Whisper が使えるか確認
        try:
            import whisper as _whisper  # noqa: F401
        except ImportError:
            return self.err(
                "openai-whisper がインストールされていません。\n"
                "pip install openai-whisper を実行してください。"
            )

        audio_data = self.request.body
        if not audio_data:
            return self.err("音声データがありません")

        audio_dir = BASE_DIR / "audio"
        audio_dir.mkdir(exist_ok=True)

        content_type = self.request.headers.get("Content-Type", "audio/webm")
        ext = ".wav" if "wav" in content_type else ".webm"
        tmp_path = audio_dir / f"_tmp_{uuid.uuid4().hex}{ext}"

        try:
            tmp_path.write_bytes(audio_data)

            # バックグラウンドスレッドで実行（Tornado IOLoop をブロックしない）
            loop = tornado.ioloop.IOLoop.current()
            text = await loop.run_in_executor(None, _run_whisper, str(tmp_path))
            self.ok({"text": text})
        except Exception as exc:
            self.err(f"文字起こし失敗: {exc}")
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def _run_whisper(audio_path: str) -> str:
    """同期的に Whisper を呼ぶ（executor スレッドで実行）"""
    import whisper
    print(f"🎙️  [Whisper] 文字起こし中: {audio_path}")
    model = whisper.load_model("base")
    result = model.transcribe(audio_path, language="ja")
    text = result["text"].strip()
    print(f"  完了: {text[:80]}")
    return text


# ── アプリケーション ──────────────────────────────────────────────

def make_app():
    return tornado.web.Application([
        (r"/",                              IndexHandler),
        (r"/api/config",                    ConfigHandler),
        (r"/api/articles",                  ArticlesHandler),
        (r"/api/articles/([^/]+)",          ArticleHandler),
        (r"/api/articles/([^/]+)/refine",   RefineHandler),
        (r"/api/articles/([^/]+)/coherence",CoherenceOnlyHandler),
        (r"/api/jobs/([^/]+)/stream",       StreamHandler),
        (r"/api/transcribe",                TranscribeHandler),
    ])


# ── HTML テンプレート ─────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>note-refine</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0d1117; --surface: #161b22; --surface2: #1c2128; --surface3: #21262d;
  --border: #30363d; --border2: #3d444d;
  --text: #e6edf3; --muted: #7d8590; --muted2: #9198a1;
  --purple: #7c3aed; --purple-light: #a78bfa; --blue: #3b82f6;
  --green: #3fb950; --yellow: #d29922; --red: #f85149;
  --critic: #818cf8; --editor: #34d399; --validator: #fbbf24; --coherence: #f472b6;
  --radius: 8px; --shadow: 0 4px 16px rgba(0,0,0,.5);
}
html, body { height: 100%; font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); font-size: 14px; line-height: 1.6; overflow: hidden; }

/* Layout */
.app { display: grid; grid-template-rows: 52px 1fr; height: 100vh; }
header { display: flex; align-items: center; gap: 12px; padding: 0 20px; background: var(--surface); border-bottom: 1px solid var(--border); position: sticky; top: 0; z-index: 100; }
header h1 { font-size: 16px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
.header-spacer { flex: 1; }
.btn-icon { background: var(--surface3); border: 1px solid var(--border); color: var(--text); padding: 6px 12px; border-radius: var(--radius); cursor: pointer; font-size: 13px; display: flex; align-items: center; gap: 6px; transition: all .15s; }
.btn-icon:hover { background: var(--border); }
.workspace { display: grid; grid-template-columns: 260px 1fr; overflow: hidden; }

/* Sidebar */
.sidebar { background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; }
.sidebar-section { padding: 12px 14px; border-bottom: 1px solid var(--border); }
.sb-title { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin-bottom: 8px; }
.sidebar-scroll { flex: 1; overflow-y: auto; }
.sidebar-scroll::-webkit-scrollbar { width: 4px; }
.sidebar-scroll::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 4px; }
select { width: 100%; background: var(--surface3); border: 1px solid var(--border); color: var(--text); padding: 7px 10px; border-radius: var(--radius); font-size: 13px; cursor: pointer; outline: none; }
select:focus { border-color: var(--purple); }
.art-actions { display: flex; gap: 6px; margin-top: 8px; }
.btn-sm { flex: 1; padding: 6px; background: var(--surface3); border: 1px solid var(--border); color: var(--text); border-radius: var(--radius); cursor: pointer; font-size: 12px; font-weight: 500; text-align: center; transition: all .15s; }
.btn-sm:hover { background: var(--surface2); border-color: var(--purple); color: var(--purple-light); }
.btn-sm.accent { background: var(--purple); border-color: var(--purple); color: #fff; }
.btn-sm.accent:hover { background: #6d28d9; }
.btn-sm:disabled { opacity: .45; cursor: not-allowed; }

/* Section list */
.sec-item { display: flex; align-items: center; gap: 8px; padding: 8px 14px; cursor: pointer; transition: background .12s; border-left: 3px solid transparent; font-size: 13px; }
.sec-item:hover { background: var(--surface2); }
.sec-item.active { background: var(--surface3); border-left-color: var(--purple); }
.sec-num { font-size: 11px; font-family: 'JetBrains Mono', monospace; color: var(--muted); min-width: 22px; }
.sec-name { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sec-len { font-size: 11px; color: var(--muted); font-family: 'JetBrains Mono', monospace; }

/* History */
.hist-item { padding: 8px 14px; border-bottom: 1px solid var(--border); font-size: 12px; }
.hist-row { display: flex; align-items: center; gap: 6px; margin-bottom: 2px; }
.hist-iter { font-family: 'JetBrains Mono', monospace; color: var(--muted); font-size: 11px; }
.hist-score { margin-left: auto; font-family: 'JetBrains Mono', monospace; font-size: 12px; font-weight: 600; }
.hist-sec { color: var(--purple-light); font-size: 11px; }
.hist-sum { color: var(--muted2); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* Main */
.main { display: flex; flex-direction: column; overflow: hidden; }
.tabs { display: flex; border-bottom: 1px solid var(--border); background: var(--surface); padding: 0 16px; }
.tab { padding: 10px 16px; font-size: 13px; font-weight: 500; cursor: pointer; border-bottom: 2px solid transparent; color: var(--muted); transition: all .15s; }
.tab:hover { color: var(--text); }
.tab.active { color: var(--purple-light); border-bottom-color: var(--purple); }
.content-wrapper { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.tab-panel { display: none; flex: 1; overflow-y: auto; padding: 16px 20px; }
.tab-panel.active { display: flex; flex-direction: column; gap: 14px; }
.tab-panel::-webkit-scrollbar { width: 6px; }
.tab-panel::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }

/* Cards */
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; }
.card-title { font-size: 12px; color: var(--muted); margin-bottom: 10px; font-weight: 500; }

/* Markdown */
.md h1,.md h2,.md h3 { color: var(--text); margin: .8em 0 .4em; font-weight: 600; }
.md h1 { font-size: 1.35em; } .md h2 { font-size: 1.15em; } .md h3 { font-size: 1.05em; }
.md p { margin-bottom: .7em; }
.md strong { font-weight: 600; }
.md em { color: var(--muted2); }
.md code { background: var(--surface3); padding: 1px 5px; border-radius: 4px; font-family: 'JetBrains Mono', monospace; font-size: .88em; color: var(--purple-light); }
.md pre { background: var(--surface3); border: 1px solid var(--border); border-radius: 6px; padding: 10px; margin: .7em 0; overflow-x: auto; }
.md blockquote { border-left: 3px solid var(--purple); padding-left: 10px; color: var(--muted2); margin: .7em 0; }
.md ul,.md ol { padding-left: 1.5em; margin-bottom: .7em; }
.md li { margin-bottom: .25em; }
.md hr { border: none; border-top: 1px solid var(--border); margin: 1em 0; }

/* Diff */
.diff-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.diff-panel { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
.diff-hdr { padding: 8px 14px; font-size: 12px; font-weight: 600; display: flex; align-items: center; gap: 6px; border-bottom: 1px solid var(--border); }
.diff-hdr.before { color: var(--red); } .diff-hdr.after { color: var(--green); }
.diff-body { padding: 14px; font-size: 13px; line-height: 1.8; overflow-y: auto; max-height: 55vh; }

/* Pipeline */
.pipeline { display: flex; align-items: center; gap: 8px; padding: 10px 14px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); }
.pipe-agent { display: flex; flex-direction: column; align-items: center; gap: 3px; flex: 1; }
.pipe-dot { width: 34px; height: 34px; border-radius: 50%; background: var(--surface3); border: 2px solid var(--border); display: flex; align-items: center; justify-content: center; font-size: 15px; transition: all .3s; }
.pipe-dot.active { border-color: var(--pipe-color, var(--purple)); box-shadow: 0 0 0 4px color-mix(in srgb, var(--pipe-color, var(--purple)) 20%, transparent); animation: pulse .9s ease-in-out infinite; }
.pipe-dot.done   { border-color: var(--pipe-color, var(--purple)); background: color-mix(in srgb, var(--pipe-color, var(--purple)) 18%, var(--surface3)); }
.pipe-lbl { font-size: 10px; color: var(--muted); font-weight: 500; }
.pipe-arrow { color: var(--border2); font-size: 14px; flex: 0; }
@keyframes pulse { 0%,100% { box-shadow: 0 0 0 2px color-mix(in srgb, var(--pipe-color, var(--purple)) 25%, transparent); } 50% { box-shadow: 0 0 0 6px color-mix(in srgb, var(--pipe-color, var(--purple)) 8%, transparent); } }

/* Validation */
.val-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px; }
.score-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.score-num { font-size: 26px; font-weight: 700; font-family: 'JetBrains Mono', monospace; padding: 3px 10px; border-radius: 7px; }
.score-green { color: var(--green); background: color-mix(in srgb, var(--green) 12%, transparent); border: 1px solid color-mix(in srgb, var(--green) 30%, transparent); }
.score-yellow { color: var(--yellow); background: color-mix(in srgb, var(--yellow) 12%, transparent); border: 1px solid color-mix(in srgb, var(--yellow) 30%, transparent); }
.score-red { color: var(--red); background: color-mix(in srgb, var(--red) 12%, transparent); border: 1px solid color-mix(in srgb, var(--red) 30%, transparent); }
.verdict-chip { padding: 3px 9px; border-radius: 20px; font-size: 11px; font-weight: 700; }
.chip-pass { background: color-mix(in srgb, var(--green) 15%, transparent); color: var(--green); }
.chip-needs { background: color-mix(in srgb, var(--yellow) 15%, transparent); color: var(--yellow); }
.qual-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 6px; margin-top: 8px; }
.qual-item { background: var(--surface3); border-radius: 6px; padding: 7px; text-align: center; }
.qi-lbl { font-size: 10px; color: var(--muted); margin-bottom: 1px; }
.qi-val { font-size: 12px; font-weight: 600; }
.qi-g { color: var(--green); } .qi-y { color: var(--yellow); } .qi-r { color: var(--red); }

/* Coherence */
.coh-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px; }
.coh-hdr { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.coh-score { font-size: 20px; font-weight: 700; font-family: 'JetBrains Mono', monospace; color: var(--coherence); }
.issue-list { display: flex; flex-direction: column; gap: 5px; }
.issue-item { background: var(--surface3); border-radius: 6px; padding: 7px 10px; font-size: 12px; border-left: 3px solid; }
.issue-high { border-left-color: var(--red); } .issue-medium { border-left-color: var(--yellow); } .issue-low { border-left-color: var(--green); }
.issue-type { display: inline-block; padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 600; background: var(--surface2); color: var(--coherence); margin-bottom: 2px; }

/* Agent Log */
.agent-log { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
.log-hdr { display: flex; align-items: center; gap: 8px; padding: 9px 14px; border-bottom: 1px solid var(--border); cursor: pointer; user-select: none; font-size: 13px; font-weight: 500; }
.log-hdr:hover { background: var(--surface2); }
.log-body { max-height: 180px; overflow-y: auto; padding: 8px 12px; font-family: 'JetBrains Mono', monospace; font-size: 12px; line-height: 1.65; }
.log-body::-webkit-scrollbar { width: 4px; }
.log-body::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
.ll { color: var(--muted2); }
.ll.ph-critic   { color: var(--critic);    font-weight: 600; }
.ll.ph-editor   { color: var(--editor);    font-weight: 600; }
.ll.ph-validator{ color: var(--validator); font-weight: 600; }
.ll.ph-coherence{ color: var(--coherence); font-weight: 600; }
.ll.err { color: var(--red); }
.ll.ok  { color: var(--green); }

/* Feedback */
.fb-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px; }
.fg { display: flex; flex-direction: column; gap: 4px; }
.fg label { font-size: 12px; color: var(--muted); font-weight: 500; }
textarea { width: 100%; background: var(--surface3); border: 1px solid var(--border); color: var(--text); padding: 9px 11px; border-radius: var(--radius); font-size: 14px; resize: vertical; font-family: 'Inter', sans-serif; outline: none; transition: border-color .15s; min-height: 72px; }
textarea:focus { border-color: var(--purple); }
input[type=text], input[type=password] { background: var(--surface3); border: 1px solid var(--border); color: var(--text); padding: 7px 10px; border-radius: var(--radius); font-size: 13px; outline: none; transition: border-color .15s; font-family: 'Inter', sans-serif; }
input[type=text]:focus, input[type=password]:focus { border-color: var(--purple); }
.cb-row { display: flex; align-items: center; gap: 5px; font-size: 12px; color: var(--muted2); cursor: pointer; }
.cb-row input { accent-color: var(--purple); }
.opts { display: flex; gap: 12px; margin-top: 8px; }
.btn-p { padding: 8px 18px; background: var(--purple); color: #fff; border: none; border-radius: var(--radius); font-size: 14px; font-weight: 600; cursor: pointer; transition: all .15s; display: flex; align-items: center; gap: 6px; }
.btn-p:hover { background: #6d28d9; }
.btn-p:disabled { opacity: .45; cursor: not-allowed; }
.btn-s { padding: 8px 14px; background: var(--surface3); color: var(--text); border: 1px solid var(--border); border-radius: var(--radius); font-size: 13px; font-weight: 500; cursor: pointer; transition: all .15s; display: flex; align-items: center; gap: 6px; }
.btn-s:hover { border-color: var(--coherence); color: var(--coherence); }
.btn-s:disabled { opacity: .45; cursor: not-allowed; }
.form-row { display: flex; gap: 10px; align-items: flex-end; margin-top: 10px; flex-wrap: wrap; }

/* Empty */
.empty { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 50px 20px; gap: 10px; color: var(--muted); }
.empty .ico { font-size: 42px; }
.empty p { font-size: 13px; text-align: center; }

/* No article */
.no-art { flex: 1; display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 14px; color: var(--muted); }
.no-art .ico { font-size: 52px; }
.no-art h3 { font-size: 17px; font-weight: 600; color: var(--muted2); }

/* Modal */
.modal-ov { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.72); z-index: 1000; align-items: center; justify-content: center; }
.modal-ov.open { display: flex; }
.modal { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 22px; width: 510px; max-width: 95vw; box-shadow: var(--shadow); }
.modal h2 { font-size: 15px; font-weight: 600; margin-bottom: 14px; }
.modal-acts { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }

/* Badge */
.badge { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }
.badge-g { background: color-mix(in srgb,var(--green) 18%,transparent); color: var(--green); }
.badge-r { background: color-mix(in srgb,var(--red)   18%,transparent); color: var(--red);   }

/* Running bar */
.run-bar { height: 2px; background: linear-gradient(90deg,var(--purple),var(--coherence),var(--purple)); background-size: 200%; animation: shimmer 1.4s linear infinite; }
.run-bar.hidden { display: none; }
@keyframes shimmer { to { background-position: 200% 0; } }

/* Spinner */
.spin { display: inline-block; width: 13px; height: 13px; border: 2px solid rgba(255,255,255,.2); border-top-color: #fff; border-radius: 50%; animation: sp .6s linear infinite; }
@keyframes sp { to { transform: rotate(360deg); } }

/* Mic button */
.mic-wrap { display: flex; align-items: flex-start; gap: 6px; }
.mic-wrap textarea { flex: 1; }
.btn-mic {
  flex-shrink: 0; width: 36px; height: 36px; border-radius: 50%;
  background: var(--surface3); border: 1px solid var(--border); color: var(--text);
  cursor: pointer; font-size: 16px; display: flex; align-items: center; justify-content: center;
  transition: all .2s; margin-top: 0;
}
.btn-mic:hover { background: var(--surface2); border-color: var(--red); }
.btn-mic.recording {
  background: var(--red); border-color: var(--red); color: #fff;
  animation: rec-pulse .8s ease-in-out infinite;
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--red) 20%, transparent);
}
.btn-mic.transcribing { background: var(--yellow); border-color: var(--yellow); color: #000; cursor: not-allowed; }
@keyframes rec-pulse {
  0%,100% { box-shadow: 0 0 0 2px color-mix(in srgb, var(--red) 25%, transparent); }
  50%      { box-shadow: 0 0 0 7px color-mix(in srgb, var(--red) 8%, transparent); }
}
.mic-status { font-size: 11px; margin-top: 3px; min-height: 16px; }
.mic-status.ok  { color: var(--green); }
.mic-status.err { color: var(--red); }
.mic-status.rec { color: var(--red); }
.mic-status.proc { color: var(--yellow); }

::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
</style>
</head>
<body>
<div class="app">

<!-- Header -->
<header>
  <h1>📝 note-refine</h1>
  <div id="apiStatus" class="badge badge-r">⚠ API Key 未設定</div>
  <div class="header-spacer"></div>
  <button class="btn-icon" onclick="openSettings()">⚙️ 設定</button>
</header>

<div class="workspace">
<!-- Sidebar -->
<aside class="sidebar">
  <div class="sidebar-section">
    <div class="sb-title">記事</div>
    <select id="articleSelect" onchange="onArticleChange(this.value)">
      <option value="">-- 記事を選択 --</option>
    </select>
    <div class="art-actions">
      <button class="btn-sm" onclick="openNewArticle()">＋ 新規作成</button>
      <button class="btn-sm" onclick="loadArticles()">↻ 更新</button>
    </div>
  </div>
  <div class="sidebar-scroll">
    <div class="sidebar-section" id="secListWrap" style="display:none">
      <div class="sb-title">セクション</div>
      <div id="secList"></div>
    </div>
    <div class="sidebar-section" id="histWrap" style="display:none">
      <div class="sb-title">履歴</div>
      <div id="histList"></div>
    </div>
  </div>
</aside>

<!-- Main -->
<main class="main">
  <div id="runBar" class="run-bar hidden"></div>

  <div id="noArt" class="no-art">
    <div class="ico">📄</div>
    <h3>記事を選択してください</h3>
    <p style="font-size:13px">左から記事を選ぶか「＋ 新規作成」でセットアップできます</p>
    <button class="btn-p" onclick="openNewArticle()">＋ 新しい記事を作成</button>
  </div>

  <div id="artView" style="display:none;flex-direction:column;height:100%;overflow:hidden;">
    <!-- Tabs -->
    <div class="tabs">
      <div class="tab active" data-tab="current" onclick="switchTab('current')">現在のセクション</div>
      <div class="tab" data-tab="compare" onclick="switchTab('compare')">✨ 改善前後</div>
      <div class="tab" data-tab="all" onclick="switchTab('all')">全文</div>
    </div>

    <div class="content-wrapper">
      <!-- Current -->
      <div id="tabCurrent" class="tab-panel active">
        <div id="emptySecMsg" class="empty"><div class="ico">👈</div><p>左からセクションを選んでください</p></div>
        <div id="secViewer" style="display:none">
          <div class="card"><div class="card-title" id="secViewerTitle"></div><div id="secViewerMd" class="md"></div></div>
        </div>
      </div>
      <!-- Compare -->
      <div id="tabCompare" class="tab-panel">
        <div id="emptyCmp" class="empty"><div class="ico">⚡</div><p>リファインを実行すると<br>改善前後が表示されます</p></div>
        <div id="cmpView" style="display:none">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">
            <div style="font-size:13px;font-weight:600" id="cmpTitle"></div>
            <div id="cmpScore"></div>
          </div>
          <div class="diff-grid">
            <div class="diff-panel"><div class="diff-hdr before">⬅ 改善前</div><div class="diff-body md" id="diffBefore"></div></div>
            <div class="diff-panel"><div class="diff-hdr after">改善後 ➡</div><div class="diff-body md" id="diffAfter"></div></div>
          </div>
          <div id="valCard" style="display:none"></div>
          <div id="cohCard" style="display:none"></div>
        </div>
      </div>
      <!-- All -->
      <div id="tabAll" class="tab-panel">
        <div class="card"><div class="card-title">全文 (all.md)</div><div id="allMd" class="md"></div></div>
      </div>
    </div>

    <!-- Bottom bar -->
    <div style="border-top:1px solid var(--border);background:var(--bg);padding:12px 18px;display:flex;flex-direction:column;gap:10px;">
      <!-- Pipeline -->
      <div class="pipeline">
        <div class="pipe-agent" id="ppCritic"><div class="pipe-dot" style="--pipe-color:var(--critic)">🔍</div><div class="pipe-lbl">Critic</div></div>
        <div class="pipe-arrow">→</div>
        <div class="pipe-agent" id="ppEditor"><div class="pipe-dot" style="--pipe-color:var(--editor)">✍️</div><div class="pipe-lbl">Editor</div></div>
        <div class="pipe-arrow">→</div>
        <div class="pipe-agent" id="ppValidator"><div class="pipe-dot" style="--pipe-color:var(--validator)">✅</div><div class="pipe-lbl">Validator</div></div>
        <div class="pipe-arrow">→</div>
        <div class="pipe-agent" id="ppCoherence"><div class="pipe-dot" style="--pipe-color:var(--coherence)">🔗</div><div class="pipe-lbl">Coherence</div></div>
        <div style="flex:1"></div>
        <div id="ppStatus" style="font-size:12px;color:var(--muted)">待機中</div>
      </div>

      <!-- Log -->
      <div class="agent-log">
        <div class="log-hdr" onclick="toggleLog()">
          <span>🖥️ エージェントログ</span>
          <span id="logIcon" style="margin-left:auto;color:var(--muted)">▾</span>
        </div>
        <div class="log-body" id="logBody"><div class="ll" style="color:var(--muted)">ログはここに表示されます...</div></div>
      </div>

      <!-- Feedback -->
      <div class="fb-card">
        <div class="fg">
          <label>フィードバック <span id="micWhisperNote" style="font-size:10px;color:var(--muted);font-weight:400">（🎙️ 音声入力にはWhisperが必要）</span></label>
          <div class="mic-wrap">
            <textarea id="fbInput" placeholder="改善してほしい点を日本語で入力してください。例：「導入部分をもっと共感を呼ぶように改善して」"></textarea>
            <div style="display:flex;flex-direction:column;align-items:center;gap:4px;">
              <button class="btn-mic" id="btnMic" onclick="toggleRecording()" title="クリックして音声入力">🎙️</button>
              <div class="mic-status" id="micStatus"></div>
            </div>
          </div>
        </div>
        <div class="form-row">
          <div class="fg" style="flex:1;min-width:140px;">
            <label>対象セクション</label>
            <select id="secSel" style="width:100%"><option value="">自動（CriticAgentが判断）</option></select>
          </div>
          <div class="fg">
            <label style="opacity:0">.</label>
            <div class="opts">
              <label class="cb-row"><input type="checkbox" id="skipVal"> Validatorスキップ</label>
              <label class="cb-row"><input type="checkbox" id="skipCoh"> Coherenceスキップ</label>
            </div>
          </div>
        </div>
        <div style="display:flex;gap:10px;margin-top:10px;">
          <button class="btn-p" id="btnRefine" onclick="runRefine()">🚀 リファイン実行</button>
          <button class="btn-s" id="btnCoh" onclick="runCohOnly()">🔗 整合性調整のみ</button>
        </div>
      </div>
    </div>
  </div>
</main>
</div>
</div>

<!-- Settings Modal -->
<div class="modal-ov" id="settingsModal">
  <div class="modal">
    <h2>⚙️ 設定</h2>
    <div class="fg" style="margin-bottom:12px">
      <label style="font-size:13px;color:var(--muted);margin-bottom:4px;display:block">Gemini API Key</label>
      <input type="password" id="apiKeyIn" placeholder="AIza..." style="width:100%">
      <div style="font-size:11px;color:var(--muted);margin-top:4px">
        <a href="https://aistudio.google.com/app/apikey" target="_blank" style="color:var(--purple-light)">Google AI Studio</a> で取得できます
      </div>
    </div>
    <div class="fg">
      <label style="font-size:13px;color:var(--muted);margin-bottom:4px;display:block">モデル</label>
      <input type="text" id="modelIn" style="width:100%" value="gemini-2.5-flash">
    </div>
    <div class="modal-acts">
      <button class="btn-sm" onclick="closeSettings()">キャンセル</button>
      <button class="btn-sm accent" onclick="saveSettings()">保存</button>
    </div>
  </div>
</div>

<!-- New Article Modal -->
<div class="modal-ov" id="newArtModal">
  <div class="modal">
    <h2>＋ 新しい記事を作成</h2>
    <div class="fg" style="margin-bottom:12px">
      <label style="font-size:13px;color:var(--muted);margin-bottom:4px;display:block">記事名（フォルダ名）</label>
      <input type="text" id="newName" placeholder="my_article" style="width:100%">
    </div>
    <div class="fg">
      <label style="font-size:13px;color:var(--muted);margin-bottom:4px;display:block">Markdown 本文</label>
      <textarea id="newContent" placeholder="# タイトル&#10;&#10;記事の内容を貼り付けてください..." style="min-height:170px;width:100%"></textarea>
    </div>
    <div style="font-size:11px;color:var(--muted);margin-top:6px">
      ※ <code>### 見出し</code> または <code>* * *</code> でセクション分割されます
    </div>
    <div class="modal-acts">
      <button class="btn-sm" onclick="closeNewArt()">キャンセル</button>
      <button class="btn-sm accent" id="btnSetup" onclick="setupArticle()">セットアップ</button>
    </div>
  </div>
</div>

<script>
// ── State ─────────────────────────────────────────────────────────
const S = { articles:[], article:null, sections:[], activeSecIdx:null, running:false, logOpen:true };

// ── Init ──────────────────────────────────────────────────────────
async function init() {
  await loadConfig();
  await loadArticles();
}

// ── Config ────────────────────────────────────────────────────────
async function loadConfig() {
  const r = await fetch('/api/config').then(r=>r.json());
  const el = document.getElementById('apiStatus');
  if (r.api_key_set) { el.textContent='✓ API Key 設定済'; el.className='badge badge-g'; }
  else               { el.textContent='⚠ API Key 未設定'; el.className='badge badge-r'; }
  document.getElementById('modelIn').value = r.model || 'gemini-2.5-flash';
}
function openSettings() { document.getElementById('settingsModal').classList.add('open'); }
function closeSettings() { document.getElementById('settingsModal').classList.remove('open'); }
async function saveSettings() {
  const key=document.getElementById('apiKeyIn').value.trim();
  const model=document.getElementById('modelIn').value.trim();
  await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:key,model})});
  await loadConfig(); closeSettings(); addLog('✓ 設定を保存しました','ok');
}

// ── Articles ──────────────────────────────────────────────────────
async function loadArticles() {
  S.articles = await fetch('/api/articles').then(r=>r.json());
  const sel=document.getElementById('articleSelect');
  const cur=sel.value;
  sel.innerHTML='<option value="">-- 記事を選択 --</option>';
  S.articles.forEach(a=>{
    const o=document.createElement('option');
    o.value=a.name;
    o.textContent=`${a.name}  (${a.section_count}s / #${a.iteration})`;
    sel.appendChild(o);
  });
  if (cur) sel.value=cur;
}

async function onArticleChange(name) {
  if (!name) { document.getElementById('noArt').style.display='flex'; document.getElementById('artView').style.display='none'; return; }
  const data=await fetch(`/api/articles/${name}`).then(r=>r.json());
  S.article=name; S.sections=data.sections; S.activeSecIdx=null;
  document.getElementById('noArt').style.display='none';
  document.getElementById('artView').style.display='flex';
  renderSecList(data.sections);
  renderHist(data.state.history||[]);
  renderAll(data.all_content);
  updateSecSel(data.sections);
  resetPipeline(); clearCmp();
}

function renderSecList(secs) {
  document.getElementById('secListWrap').style.display='block';
  const el=document.getElementById('secList'); el.innerHTML='';
  secs.forEach((s,i)=>{
    const d=document.createElement('div');
    d.className='sec-item'+(i===S.activeSecIdx?' active':'');
    d.innerHTML=`<span class="sec-num">${String(s.index).padStart(2,'0')}</span><span class="sec-name">${s.slug}</span><span class="sec-len">${s.length}字</span>`;
    d.onclick=()=>showSec(i);
    el.appendChild(d);
  });
}

function renderHist(history) {
  document.getElementById('histWrap').style.display=history.length?'block':'none';
  const el=document.getElementById('histList'); el.innerHTML='';
  [...history].reverse().forEach(h=>{
    const d=document.createElement('div'); d.className='hist-item';
    const icon=h.verdict==='pass'?'✅':'⚠️';
    const sc=h.validation_score;
    const scColor=sc>=80?'var(--green)':sc>=60?'var(--yellow)':'var(--red)';
    d.innerHTML=`<div class="hist-row"><span class="hist-iter">#${String(h.iter).padStart(2,'0')}</span><span>${icon}</span><span class="hist-score" style="color:${scColor}">${sc}</span></div><div class="hist-sec">${h.target_section||''}</div><div class="hist-sum">${h.feedback_snippet||''}</div>`;
    el.appendChild(d);
  });
}

function renderAll(content) { document.getElementById('allMd').innerHTML=marked.parse(content||''); }
function updateSecSel(secs) {
  const sel=document.getElementById('secSel');
  sel.innerHTML='<option value="">自動（CriticAgentが判断）</option>';
  secs.forEach(s=>{ const o=document.createElement('option'); o.value=s.filename; o.textContent=s.filename; sel.appendChild(o); });
}

function showSec(idx) {
  S.activeSecIdx=idx;
  const s=S.sections[idx];
  document.querySelectorAll('.sec-item').forEach((el,i)=>el.classList.toggle('active',i===idx));
  document.getElementById('emptySecMsg').style.display='none';
  document.getElementById('secViewer').style.display='block';
  document.getElementById('secViewerTitle').textContent=s.filename+`  (${s.length} 文字)`;
  document.getElementById('secViewerMd').innerHTML=marked.parse(s.content);
  switchTab('current');
}

// ── Tabs ──────────────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.tab===name));
  ['current','compare','all'].forEach(n=>{
    const el=document.getElementById('tab'+n.charAt(0).toUpperCase()+n.slice(1));
    if(el) el.classList.toggle('active',n===name);
  });
}

// ── New Article ───────────────────────────────────────────────────
function openNewArticle() { document.getElementById('newArtModal').classList.add('open'); }
function closeNewArt() { document.getElementById('newArtModal').classList.remove('open'); }
async function setupArticle() {
  const name=document.getElementById('newName').value.trim()||'article';
  const content=document.getElementById('newContent').value.trim();
  if (!content) { alert('記事内容を入力してください'); return; }
  const btn=document.getElementById('btnSetup');
  btn.disabled=true; btn.textContent='処理中...';
  try {
    const data=await fetch('/api/articles',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,content})}).then(r=>r.json());
    if (data.error) { alert(data.error); return; }
    closeNewArt();
    document.getElementById('newName').value=''; document.getElementById('newContent').value='';
    await loadArticles();
    document.getElementById('articleSelect').value=data.name;
    await onArticleChange(data.name);
    addLog(`✓ 記事「${data.name}」をセットアップしました（${data.section_count} セクション）`,'ok');
  } finally { btn.disabled=false; btn.textContent='セットアップ'; }
}

// ── Pipeline ──────────────────────────────────────────────────────
const PP={critic:'ppCritic',editor:'ppEditor',validator:'ppValidator',coherence:'ppCoherence'};
function resetPipeline() {
  Object.values(PP).forEach(id=>{ const d=document.querySelector(`#${id} .pipe-dot`); if(d){d.classList.remove('active','done');} });
  document.getElementById('ppStatus').textContent='待機中';
}
function setPhase(phase,label) {
  const order=['critic','editor','validator','coherence'];
  order.slice(0,order.indexOf(phase)).forEach(p=>{ const d=document.querySelector(`#${PP[p]} .pipe-dot`); if(d){d.classList.remove('active');d.classList.add('done');} });
  const d=document.querySelector(`#${PP[phase]} .pipe-dot`); if(d){d.classList.add('active');d.classList.remove('done');}
  document.getElementById('ppStatus').textContent=`${label} 実行中...`;
}
function doneAll() {
  Object.values(PP).forEach(id=>{ const d=document.querySelector(`#${id} .pipe-dot`); if(d){d.classList.remove('active');d.classList.add('done');} });
  document.getElementById('ppStatus').textContent='完了 ✓';
}

// ── Log ───────────────────────────────────────────────────────────
function addLog(text,cls='') {
  const log=document.getElementById('logBody');
  const d=document.createElement('div'); d.className='ll'+(cls?' '+cls:''); d.textContent=text;
  log.appendChild(d); log.scrollTop=log.scrollHeight;
}
function clearLog() { document.getElementById('logBody').innerHTML=''; }
function toggleLog() {
  const b=document.getElementById('logBody'); S.logOpen=!S.logOpen;
  b.style.display=S.logOpen?'block':'none';
  document.getElementById('logIcon').textContent=S.logOpen?'▾':'▸';
}

// ── Refine ────────────────────────────────────────────────────────
async function runRefine() {
  if (S.running) return;
  const fb=document.getElementById('fbInput').value.trim();
  if (!fb) { alert('フィードバックを入力してください'); return; }
  setRunning(true); clearLog(); resetPipeline(); clearCmp();
  addLog('🚀 リファイン開始...');
  const res=await fetch(`/api/articles/${S.article}/refine`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({feedback:fb,section:document.getElementById('secSel').value||null,skip_validation:document.getElementById('skipVal').checked,skip_coherence:document.getElementById('skipCoh').checked})});
  if (!res.ok) { const e=await res.json(); addLog('❌ '+(e.error||'エラー'),'err'); setRunning(false); return; }
  const {job_id}=await res.json();
  await streamJob(job_id);
}

async function runCohOnly() {
  if (S.running) return;
  setRunning(true); clearLog(); resetPipeline();
  addLog('🔗 整合性調整を開始...');
  const res=await fetch(`/api/articles/${S.article}/coherence`,{method:'POST'});
  const {job_id}=await res.json();
  await streamJob(job_id);
}

async function streamJob(jobId) {
  const es=new EventSource(`/api/jobs/${jobId}/stream`);
  es.onmessage=evt=>{
    const msg=JSON.parse(evt.data);
    handleMsg(msg);
    if (['stream_end','complete','error'].includes(msg.type)) {
      es.close();
      setRunning(false);
      if (msg.type!=='error') { doneAll(); reloadArt(); }
      else resetPipeline();
    }
  };
  es.onerror=()=>{ addLog('⚠️ 接続エラー','err'); es.close(); setRunning(false); };
}

function handleMsg(msg) {
  switch(msg.type) {
    case 'log': addLog(msg.text); break;
    case 'phase': setPhase(msg.phase,msg.label); addLog(`▶ [${msg.label}] 開始`,`ph-${msg.phase}`); break;
    case 'critique': {
      const c=msg.data; addLog(`  対象: ${c.target_section||'未確定'} (${c.target_section_confidence||'-'})`);
      (c.issues||[]).forEach(i=>{ const sv={'high':'🔴','medium':'🟡','low':'🟢'}[i.severity]||'🟢'; addLog(`  ${sv} [${i.category}] ${i.problem}`); }); break;
    }
    case 'target': addLog(`  🎯 対象確定: ${msg.section}`); break;
    case 'editor_result': {
      const d=msg.improved.length-msg.original.length;
      addLog(`  完了: ${msg.original.length}字 → ${msg.improved.length}字 (${d>=0?'+':''}${d})`);
      showCmp(msg.section,msg.original,msg.improved); break;
    }
    case 'validation': renderVal(msg.data); break;
    case 'coherence_result': renderCoh(msg.report); break;
    case 'complete': addLog(msg.iter?`✅ #${msg.iter} 完了 (スコア:${msg.score}, ${msg.verdict})`:('✅ '+(msg.message||'完了')),'ok'); break;
    case 'error': addLog('❌ '+msg.message,'err'); break;
  }
}

// ── Compare ───────────────────────────────────────────────────────
function clearCmp() {
  document.getElementById('emptyCmp').style.display='flex';
  document.getElementById('cmpView').style.display='none';
  document.getElementById('valCard').style.display='none';
  document.getElementById('cohCard').style.display='none';
}
function showCmp(sec,orig,imp) {
  document.getElementById('emptyCmp').style.display='none';
  document.getElementById('cmpView').style.display='block';
  document.getElementById('cmpTitle').textContent='✨ '+sec;
  document.getElementById('diffBefore').innerHTML=marked.parse(orig);
  document.getElementById('diffAfter').innerHTML=marked.parse(imp);
  switchTab('compare');
}
function renderVal(d) {
  const card=document.getElementById('valCard'); card.style.display='block';
  const sc=d.score||0; const v=d.verdict||'pass';
  const scCls=sc>=80?'score-green':sc>=60?'score-yellow':'score-red';
  const q=d.quality_check||{};
  const qmap={'structure':'構成','readability':'読みやすさ','tone':'トーン','value':'価値'};
  const qi=Object.entries(q).map(([k,v])=>{const cls={'良い':'qi-g','普通':'qi-y','要改善':'qi-r'}[v]||'';return`<div class="qual-item"><div class="qi-lbl">${qmap[k]||k}</div><div class="qi-val ${cls}">${v}</div></div>`;}).join('');
  const fa=(d.feedback_addressed||[]).map(f=>{const st={'resolved':'✅','partial':'⚠️','unresolved':'❌'}[f.status]||'?';return`<div style="font-size:11px;padding:3px 0;border-bottom:1px solid var(--border)">${st} ${f.comment||f.issue_id}</div>`;}).join('');
  card.innerHTML=`<div class="val-card"><div style="font-size:12px;font-weight:600;margin-bottom:8px">✅ Validator レポート</div><div class="score-row"><div class="score-num ${scCls}">${sc}</div><div class="verdict-chip ${v==='pass'?'chip-pass':'chip-needs'}">${v==='pass'?'PASS':'NEEDS REVISION'}</div><div style="font-size:11px;color:var(--muted);margin-left:6px">coherence risk: ${d.coherence_risk||'-'}</div></div>${qi?`<div class="qual-grid">${qi}</div>`:''} ${d.recommendation?`<div style="font-size:12px;color:var(--muted2);margin-top:8px">💡 ${d.recommendation}</div>`:''} ${fa?`<div style="margin-top:8px">${fa}</div>`:''}</div>`;
}
function renderCoh(r) {
  const card=document.getElementById('cohCard'); card.style.display='block';
  const issues=(r.issues_found||[]).map(i=>`<div class="issue-item issue-${i.severity||'low'}"><div><span class="issue-type">${i.type||''}</span> <span style="font-size:10px;color:var(--muted)">${(i.sections_involved||[]).join(', ')}</span></div><div>${i.description||''}</div></div>`).join('');
  card.innerHTML=`<div class="coh-card"><div class="coh-hdr"><span style="font-size:12px;font-weight:600">🔗 Coherence レポート</span><span class="coh-score">${r.coherence_score||'?'}</span><span style="font-size:11px;color:var(--muted)">${r.summary||''}</span></div>${issues?`<div class="issue-list">${issues}</div>`:'<div style="font-size:12px;color:var(--green)">✅ 整合性の問題は見つかりませんでした</div>'}</div>`;
}

// ── Helpers ───────────────────────────────────────────────────────
function setRunning(r) {
  S.running=r;
  document.getElementById('btnRefine').disabled=r;
  document.getElementById('btnCoh').disabled=r;
  document.getElementById('runBar').classList.toggle('hidden',!r);
  document.getElementById('btnRefine').innerHTML=r?'<div class="spin"></div> 実行中...':'🚀 リファイン実行';
}
async function reloadArt() {
  if (!S.article) return;
  const data=await fetch(`/api/articles/${S.article}`).then(r=>r.json());
  S.sections=data.sections;
  renderSecList(data.sections); renderHist(data.state.history||[]); renderAll(data.all_content); updateSecSel(data.sections);
  await loadArticles();
}

init();

// ── 音声入力 (MediaRecorder + Whisper) ───────────────────────────
let _mediaRecorder = null;
let _audioChunks   = [];
let _stream        = null;

async function toggleRecording() {
  if (_mediaRecorder && _mediaRecorder.state === 'recording') {
    stopRecording();
  } else {
    await startRecording();
  }
}

async function startRecording() {
  // マイク権限を取得
  try {
    _stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    setMicStatus('❌ マイクへのアクセスが拒否されました', 'err');
    return;
  }

  _audioChunks = [];
  // ブラウザが対応している MIME を選ぶ (WebM → MP4 → フォールバック)
  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus'
    : MediaRecorder.isTypeSupported('audio/webm')
    ? 'audio/webm'
    : '';
  _mediaRecorder = mimeType ? new MediaRecorder(_stream, { mimeType }) : new MediaRecorder(_stream);

  _mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) _audioChunks.push(e.data); };
  _mediaRecorder.onstop = async () => {
    _stream.getTracks().forEach(t => t.stop());
    const blob = new Blob(_audioChunks, { type: _mediaRecorder.mimeType || 'audio/webm' });
    await sendAudioToWhisper(blob, _mediaRecorder.mimeType);
  };

  _mediaRecorder.start(100); // 100ms チャンク

  const btn = document.getElementById('btnMic');
  btn.textContent = '⏹️';
  btn.classList.add('recording');
  setMicStatus('🔴 録音中... 停止するにはもう一度押してください', 'rec');
}

function stopRecording() {
  if (_mediaRecorder && _mediaRecorder.state === 'recording') {
    _mediaRecorder.stop();
    const btn = document.getElementById('btnMic');
    btn.textContent = '⏳';
    btn.classList.remove('recording');
    btn.classList.add('transcribing');
    setMicStatus('⏳ Whisper で文字起こし中...', 'proc');
  }
}

async function sendAudioToWhisper(blob, mimeType) {
  try {
    const ct = mimeType || 'audio/webm';
    const res = await fetch('/api/transcribe', {
      method: 'POST',
      headers: { 'Content-Type': ct },
      body: blob,
    });
    const data = await res.json();

    const btn = document.getElementById('btnMic');
    btn.textContent = '🎙️';
    btn.classList.remove('transcribing');

    if (data.error) {
      setMicStatus('❌ ' + data.error, 'err');
    } else {
      // テキストエリアに追記（既存テキストがあれば末尾に改行して追加）
      const ta = document.getElementById('fbInput');
      ta.value = ta.value ? ta.value + '\n' + data.text : data.text;
      ta.focus();
      setMicStatus('✅ 文字起こし完了', 'ok');
      setTimeout(() => setMicStatus('', ''), 4000);
    }
  } catch (e) {
    const btn = document.getElementById('btnMic');
    btn.textContent = '🎙️';
    btn.classList.remove('transcribing');
    setMicStatus('❌ 通信エラー: ' + e.message, 'err');
  }
}

function setMicStatus(text, cls) {
  const el = document.getElementById('micStatus');
  el.textContent = text;
  el.className = 'mic-status' + (cls ? ' ' + cls : '');
}
</script>
</body>
</html>"""


# ── メイン ────────────────────────────────────────────────────────

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 5001))
    app = make_app()
    app.listen(PORT)
    print("=" * 60)
    print("  📝 note-refine Web UI")
    print(f"  URL: http://localhost:{PORT}")
    print("  停止: Ctrl+C")
    print("=" * 60)
    try:
        tornado.ioloop.IOLoop.current().start()
    except KeyboardInterrupt:
        print("\n停止しました。")
