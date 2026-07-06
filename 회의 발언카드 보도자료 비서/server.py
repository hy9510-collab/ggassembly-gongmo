# -*- coding: utf-8 -*-
"""
발언카드·보도자료 비서 — 통합 서버
- 정적 파일 서빙(index.html, output/…) + ?list 디렉터리 목록(JSON)
- POST /api/generate : 의원·회의록 정보를 받아 [회의록 수집 → AI 작성 → 카드/문서 생성] 파이프라인 실행
- GET  /api/job/<id> : 진행 상황 조회
- GET/POST /api/settings : Claude API 키·모델 설정 (.secrets/ 폴더에 저장)

실행:  .venv\\Scripts\\python.exe server.py [포트=4600]
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "scripts"))

import fetch_speech      # noqa: E402
import ai_writer         # noqa: E402
import read_transcript   # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4600
VENV_PY = sys.executable
OUTPUT = os.path.join(BASE, "output")
SECRETS_DIR = os.path.join(BASE, ".secrets")

MIME = {
    ".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".hwpx": "application/octet-stream",
}

JOBS = {}  # job_id -> dict(status, steps[], error, folder, needs_key, request_text, usage)


def job_log(job, msg, done_step=False):
    job["steps"].append({"t": time.strftime("%H:%M:%S"), "msg": msg})
    print(f"[job {job['id'][:8]}] {msg}", flush=True)


def build_request_text(form, speech_data):
    """API 키 없을 때: 발언 원문까지 담은 '요청서'를 만들어 채팅에 붙여넣게 한다."""
    lines = ["[발언카드·보도자료 요청 — 발언 원문 포함]", ""]
    lines.append(f"■ 의원: {form.get('member','')}"
                 + (f" ({form.get('committee')})" if form.get("committee") else ""))
    for k, label in (("party", "정당"), ("region", "지역구"), ("role", "직책")):
        if form.get(k):
            lines.append(f"■ {label}: {form[k]}")
    if speech_data:
        meta = speech_data.get("meta", {})
        lines.append(f"■ 회의: {meta.get('meeting_title','')} ({meta.get('date','')})")
        lines.append(f"■ 회의록: {speech_data.get('url','')}")
        lines.append("")
        lines.append("■ 발언 원문(회의록 추출):")
        for s in speech_data.get("speeches", []):
            lines.append(f"  [{s['role']}] {s['speaker']}: {s['text']}")
    lines.append("")
    lines.append("위 발언으로 발언카드(기본+주제별+카드뉴스), 보도자료, 발언 요약 보고서를 만들어 줘.")
    return "\n".join(lines)


def run_pipeline(job, form):
    try:
        member = form.get("member", "").strip()
        if not member:
            raise ValueError("의원 이름이 없습니다.")

        # ── 1. 발언 확보 ──────────────────────────────────────────
        speech_data = None
        if form.get("file_name") and form.get("file_data"):
            import base64
            fname = os.path.basename(form["file_name"])
            updir = os.path.join(BASE, "work", "uploads")
            os.makedirs(updir, exist_ok=True)
            fpath = os.path.join(updir, time.strftime("%Y%m%d_%H%M%S_") + fname)
            with open(fpath, "wb") as fh:
                fh.write(base64.b64decode(form["file_data"].split(",")[-1]))
            job_log(job, f"업로드 파일 저장: {fname} ({os.path.getsize(fpath):,} bytes)")
            job_log(job, "파일에서 텍스트를 추출하는 중…")
            text = read_transcript.extract_text(fpath)
            text, trimmed = read_transcript.focus_member(text, member)
            job_log(job, f"텍스트 추출 완료 — {len(text):,}자"
                         + (" (긴 문서라 의원 발언 중심으로 추림)" if trimmed else ""))
            date_m = re.search(r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", form.get("meeting", ""))
            date_dot = (f"{date_m.group(1)}. {int(date_m.group(2))}. {int(date_m.group(3))}."
                        if date_m else time.strftime("%Y. %m. %d."))
            date_compact = (f"{date_m.group(1)}{int(date_m.group(2)):02d}{int(date_m.group(3)):02d}"
                            if date_m else time.strftime("%Y%m%d"))
            speech_data = {
                "url": "", "member": member,
                "meta": {"meeting_title": form.get("meeting", "") or fname,
                         "date": date_dot, "date_compact": date_compact,
                         "committee": form.get("committee", "")},
                "speeches": [{"role": "발언록 원문(파일)", "speaker": "(전체 회의 내용)",
                              "text": text}],
            }
        elif form.get("memo"):
            job_log(job, "메모 입력을 발언 데이터로 사용합니다.")
            date_m = re.search(r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", form.get("meeting", ""))
            date_dot = (f"{date_m.group(1)}. {int(date_m.group(2))}. {int(date_m.group(3))}."
                        if date_m else time.strftime("%Y. %m. %d."))
            date_compact = (f"{date_m.group(1)}{int(date_m.group(2)):02d}{int(date_m.group(3)):02d}"
                            if date_m else time.strftime("%Y%m%d"))
            speech_data = {
                "url": "", "member": member,
                "meta": {"meeting_title": form.get("meeting", ""), "date": date_dot,
                         "date_compact": date_compact,
                         "committee": form.get("committee", "")},
                "speeches": [{"role": "질의", "speaker": member, "text": line.strip()}
                              for line in form["memo"].splitlines() if line.strip()],
            }
        else:
            minutes = form.get("minutes", "").strip()
            if not minutes:
                raise ValueError("회의록/영상회의록 링크, 발언 메모, 파일 업로드 중 하나는 필요합니다.")
            job_log(job, f"회의록에서 발언을 수집하는 중… ({minutes[:80]})")
            speech_data = fetch_speech.get_member_speeches(minutes, member)
            job_log(job, f"발언 수집 완료 — 질의 {speech_data['speech_count']}건, "
                         f"{speech_data['total_chars']:,}자")

        profile = {k: form.get(k, "") for k in ("party", "region", "role", "committee")}
        if not profile.get("committee"):
            profile["committee"] = speech_data["meta"].get("committee", "")

        # ── 2. AI 작성 ────────────────────────────────────────────
        try:
            job_log(job, "AI가 카드·보도자료·보고서 초안을 작성하는 중… (30초~2분)")
            content = ai_writer.generate(speech_data, profile)
            usage = content.pop("_usage", {})
            job["usage"] = usage
            job_log(job, f"AI 작성 완료 (모델 {usage.get('model','')}, "
                         f"입력 {usage.get('input_tokens',0):,} / 출력 {usage.get('output_tokens',0):,} 토큰)")
        except ai_writer.MissingKeyError as e:
            job["needs_key"] = True
            job["request_text"] = build_request_text(form, speech_data)
            job["status"] = "needs_key"
            job_log(job, f"자동 작성 불가: {e}")
            job_log(job, "우선 아래 요청서를 복사해 Claude 채팅에 붙여넣으면 결과물을 만들어 드립니다.")
            return

        # ── 회의 날짜 보정: 링크에서 못 읽었으면 AI가 발언록에서 파악한 날짜 사용 ──
        if not speech_data["meta"].get("date"):
            md = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", content.get("meeting_date", "") or "")
            if md:
                y, mo, d = md.group(1), int(md.group(2)), int(md.group(3))
                speech_data["meta"]["date"] = f"{y}. {mo}. {d}."
                speech_data["meta"]["date_compact"] = f"{y}{mo:02d}{d:02d}"
                job_log(job, f"회의 날짜를 발언 내용에서 확인: {y}. {mo}. {d}.")

        # ── 3. 파일 생성 ──────────────────────────────────────────
        folder = f"{member}_{speech_data['meta'].get('date_compact', time.strftime('%Y%m%d'))}"
        outdir = os.path.join(OUTPUT, folder)
        os.makedirs(outdir, exist_ok=True)

        # 발언 장면 사진 — ① 직접 업로드 ② 영상에서 자동 캡처
        if form.get("photo_name") and form.get("photo_data"):
            import base64
            ext = os.path.splitext(form["photo_name"])[1].lower() or ".jpg"
            photo_path = os.path.join(outdir, "발언사진" + ext)
            with open(photo_path, "wb") as fh:
                fh.write(base64.b64decode(form["photo_data"].split(",")[-1]))
            profile["image"] = f"output/{folder}/발언사진{ext}"
            job_log(job, f"발언 장면 사진 저장 — 카드에 반영됩니다 ({os.path.getsize(photo_path):,} bytes)")
        elif form.get("photo_mode") == "capture":
            minutes_link = form.get("minutes", "")
            if "midx=" not in minutes_link:
                job_log(job, "⚠ 자동 캡처는 영상회의록 링크(midx=…)가 있어야 합니다 — 사진 없이 진행합니다.")
            else:
                try:
                    job_log(job, "영상에서 발언 장면 자동 캡처 시작… (1~2분)")
                    import capture_photo
                    photo_path = os.path.join(outdir, "발언사진.jpg")
                    capture_photo.capture(minutes_link, member, photo_path,
                                          progress=lambda m: job_log(job, "  " + m))
                    profile["image"] = f"output/{folder}/발언사진.jpg"
                    job_log(job, "캡처 사진을 카드에 반영합니다.")
                except Exception as ce:
                    job_log(job, f"⚠ 자동 캡처 실패({ce}) — 사진 없이 진행합니다. 나중에 사진을 직접 올려도 됩니다.")

        job_log(job, f"데이터 파일 생성: output/{folder}/")
        paths = ai_writer.build_case_files(content, speech_data, profile, outdir)

        def run(script, *args):
            r = subprocess.run([VENV_PY, os.path.join(BASE, "scripts", script), *args],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               cwd=BASE)
            if r.returncode != 0:
                raise RuntimeError(f"{script} 실패: {r.stderr[-400:]}")
            return r.stdout

        job_log(job, "발언 카드 생성 중…")
        run("make_card.py", paths["card"], "--mode", "B", "--style", "basic")
        run("make_card.py", paths["card"], "--mode", "A", "--style", "basic")
        if "cardnews" in paths:
            run("make_card.py", paths["cardnews"], "--style", "news")
        job_log(job, "보도자료·보고서(docx/hwpx) 생성 중…")
        run("make_docs.py", "--press", paths["press"], "--report", paths["report"])

        job["folder"] = folder
        job["status"] = "done"
        job_log(job, f"완료! 결과물 탭에서 '{member} · {speech_data['meta'].get('date','')}' 항목을 확인하세요.")
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        job_log(job, f"오류: {e}")
        traceback.print_exc()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 기본 콘솔로그 억제(잡 로그만 출력)
        pass

    # ── 공통 응답 ──
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        raw = self.rfile.read(length)
        for enc in ("utf-8", "cp949"):
            try:
                return json.loads(raw.decode(enc))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        return {}

    # ── GET ──
    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/api/settings":
            has_key = os.path.exists(os.path.join(SECRETS_DIR, "api_key.txt")) and \
                bool(open(os.path.join(SECRETS_DIR, "api_key.txt"), encoding="utf-8").read().strip())
            return self.send_json({"has_key": has_key, "model": ai_writer.load_model(),
                                   "cli": bool(ai_writer.find_claude_cli()),
                                   "cli_model": ai_writer.load_cli_model()})
        m = re.match(r"^/api/job/([\w-]+)$", path)
        if m:
            job = JOBS.get(m.group(1))
            if not job:
                return self.send_json({"error": "no such job"}, 404)
            return self.send_json({k: job.get(k) for k in
                                   ("id", "status", "steps", "error", "folder",
                                    "needs_key", "request_text", "usage")})
        # 정적 파일 / 디렉터리 목록
        rel = re.sub(r"^/+", "", path)
        rel = rel.replace("%20", " ")
        try:
            from urllib.parse import unquote
            rel = unquote(rel)
        except Exception:
            pass
        fp = os.path.realpath(os.path.join(BASE, rel or "index.html"))
        if not fp.startswith(os.path.realpath(BASE)):
            return self.send_json({"error": "forbidden"}, 403)
        if "list" in query and os.path.isdir(fp):
            entries = []
            for e in os.scandir(fp):
                try:
                    mt = e.stat().st_mtime
                except OSError:
                    mt = 0
                entries.append({"name": e.name, "dir": e.is_dir(), "mtime": mt})
            return self.send_json(entries)
        if os.path.isdir(fp):
            fp = os.path.join(fp, "index.html")
        if not os.path.isfile(fp):
            return self.send_json({"error": "not found", "path": rel}, 404)
        ext = os.path.splitext(fp)[1].lower()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(os.path.getsize(fp)))
        self.end_headers()
        with open(fp, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

    # ── POST ──
    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/generate":
            form = self.read_body_json()
            job_id = uuid.uuid4().hex
            job = {"id": job_id, "status": "running", "steps": [], "error": None,
                   "folder": None, "needs_key": False, "request_text": None, "usage": None}
            JOBS[job_id] = job
            job_log(job, f"요청 접수: {form.get('member','?')} 의원")
            threading.Thread(target=run_pipeline, args=(job, form), daemon=True).start()
            return self.send_json({"job_id": job_id})
        if path == "/api/settings":
            data = self.read_body_json()
            os.makedirs(SECRETS_DIR, exist_ok=True)
            if "api_key" in data:
                open(os.path.join(SECRETS_DIR, "api_key.txt"), "w", encoding="utf-8") \
                    .write(data["api_key"].strip())
            if "model" in data and data["model"]:
                json.dump({"model": data["model"].strip()},
                          open(os.path.join(SECRETS_DIR, "config.json"), "w", encoding="utf-8"))
            return self.send_json({"ok": True})
        return self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    os.makedirs(OUTPUT, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"발언카드 비서 서버 실행 중: http://localhost:{PORT}  (Ctrl+C로 종료)", flush=True)
    server.serve_forever()
