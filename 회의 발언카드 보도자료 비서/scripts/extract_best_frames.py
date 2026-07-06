# -*- coding: utf-8 -*-
"""
발언 장면 캡처 후보 추출
- 영상에서 일정 간격으로 프레임을 뽑아 '얼굴이 크고, 눈을 뜨고, 선명한' 장면을
  점수화하여 상위 후보를 저장합니다. (눈 감은/옆모습/흐린 컷은 제외)
- 한글 경로에서도 동작하도록 모든 opencv 작업은 영문 임시폴더에서 수행하고,
  최종 결과만 프로젝트(한글) 폴더로 복사합니다.

사용 예:
  python scripts/extract_best_frames.py "work/meeting.mp4" --topk 24
  python scripts/extract_best_frames.py "work/meeting.mp4" --start 00:12:30 --end 00:25:00 --fps 2 --topk 30
"""
import argparse
import os
import re
import sys
import json
import shutil
import tempfile
import subprocess

import cv2
import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(BASE, "work")
FRAMES = os.path.join(WORK, "frames")

CASCADE_FILES = {
    "face": "haarcascade_frontalface_default.xml",
    "eye": "haarcascade_eye.xml",
}


def hms_to_seconds(t):
    if t is None:
        return None
    t = str(t).strip()
    if ":" in t:
        parts = [float(p) for p in t.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        return parts[-3] * 3600 + parts[-2] * 60 + parts[-1]
    return float(t)


def prepare_cascades(tmpdir):
    """cascade xml을 영문 임시경로로 복사 후 로드(한글경로 회피)."""
    face_dst = os.path.join(tmpdir, "face.xml")
    eye_dst = os.path.join(tmpdir, "eye.xml")
    shutil.copy(os.path.join(cv2.data.haarcascades, CASCADE_FILES["face"]), face_dst)
    shutil.copy(os.path.join(cv2.data.haarcascades, CASCADE_FILES["eye"]), eye_dst)
    face_c = cv2.CascadeClassifier(face_dst)
    eye_c = cv2.CascadeClassifier(eye_dst)
    if face_c.empty() or eye_c.empty():
        raise RuntimeError("cascade 로드 실패")
    return face_c, eye_c


def run_ffmpeg_extract(video, outdir, fps, start_s, end_s):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start_s is not None:
        cmd += ["-ss", str(start_s)]
    cmd += ["-i", video]
    if end_s is not None:
        cmd += ["-t", str(end_s - (start_s or 0))]
    cmd += ["-vf", f"fps={fps}", "-q:v", "2", os.path.join(outdir, "f_%06d.jpg")]
    subprocess.run(cmd, check=False)
    return sorted(f for f in os.listdir(outdir) if f.startswith("f_") and f.endswith(".jpg"))


def analyze(img, face_c, eye_c):
    """프레임 1장 분석 → 지표 딕셔너리 (얼굴 없으면 None)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape
    faces = face_c.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                    minSize=(int(W * 0.06), int(H * 0.06)))
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])  # 가장 큰 얼굴 = 주 발언자로 가정
    face_area = (w * h) / float(W * H)
    upper = gray[y:y + int(h * 0.6), x:x + w]
    eyes = []
    if upper.size > 0:
        eyes = eye_c.detectMultiScale(upper, scaleFactor=1.1, minNeighbors=6,
                                      minSize=(int(w * 0.08), int(h * 0.08)))
    n_eyes = int(min(len(eyes), 2))
    face_roi = gray[y:y + h, x:x + w]
    sharp = float(cv2.Laplacian(face_roi, cv2.CV_64F).var()) if face_roi.size > 0 else 0.0
    cx = (x + w / 2.0) / W
    centered = 1.0 - min(abs(cx - 0.5) * 2, 1.0)
    return dict(face_area=face_area, n_eyes=n_eyes, sharp=sharp,
                centered=centered, box=[int(x), int(y), int(w), int(h)])


def make_contact_sheet(picked, frames_dir, tmp, outdir):
    """후보들을 격자 한 장으로 묶어 미리보기 시트 생성."""
    cols = 4
    cw, ch, pad, label_h = 320, 180, 8, 22
    rows = (len(picked) + cols - 1) // cols
    sheet = np.full((rows * (ch + label_h) + pad, cols * (cw + pad) + pad, 3), 30, np.uint8)
    for idx, c in enumerate(picked):
        r, cc = divmod(idx, cols)
        img = cv2.imread(os.path.join(frames_dir, c["file"]))
        if img is None:
            continue
        th = cv2.resize(img, (cw, ch))
        y0, x0 = r * (ch + label_h) + pad, cc * (cw + pad) + pad
        sheet[y0:y0 + ch, x0:x0 + cw] = th
        label = f"#{idx + 1}  t{c['t']:.1f}s  score{int(c['score'])}"
        cv2.putText(sheet, label, (x0 + 4, y0 + ch + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    out_tmp = os.path.join(tmp, "contact_sheet.jpg")
    cv2.imwrite(out_tmp, sheet)
    shutil.copy(out_tmp, os.path.join(outdir, "contact_sheet.jpg"))


def main():
    ap = argparse.ArgumentParser(description="발언 장면 캡처 후보 추출")
    ap.add_argument("video", help="영상 파일 경로")
    ap.add_argument("--start", default=None, help="시작 시각 (예: 00:12:30)")
    ap.add_argument("--end", default=None, help="끝 시각 (예: 00:25:00)")
    ap.add_argument("--fps", type=float, default=2.0, help="초당 분석 프레임 수(기본 2=0.5초마다)")
    ap.add_argument("--topk", type=int, default=24, help="저장할 후보 개수")
    ap.add_argument("--min-gap", type=float, default=2.0, help="후보 간 최소 시간 간격(초)")
    ap.add_argument("--tag", default=None,
                    help="결과를 저장할 하위 폴더 이름(예: 의원명). 미지정 시 영상 파일명으로 자동 지정 "
                         "— 다른 건의 캡처 결과를 덮어쓰지 않기 위함")
    args = ap.parse_args()

    video = args.video if os.path.isabs(args.video) else os.path.join(BASE, args.video)
    if not os.path.exists(video):
        print("[오류] 영상 파일을 찾을 수 없습니다:", video)
        sys.exit(1)

    tag = args.tag or re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", os.path.splitext(os.path.basename(video))[0])
    outdir = os.path.join(FRAMES, tag)
    os.makedirs(outdir, exist_ok=True)
    start_s, end_s = hms_to_seconds(args.start), hms_to_seconds(args.end)

    tmp = tempfile.mkdtemp(prefix="speechcard_")
    frames_dir = os.path.join(tmp, "frames")
    os.makedirs(frames_dir)
    try:
        print("[1/4] 프레임 추출 중...")
        files = run_ffmpeg_extract(video, frames_dir, args.fps, start_s, end_s)
        if not files:  # 한글경로 직접 입력 실패 시 → temp로 복사 후 재시도
            print("    (직접 처리 실패 → 임시폴더로 복사 후 재시도)")
            vtmp = os.path.join(tmp, "video" + os.path.splitext(video)[1])
            shutil.copy(video, vtmp)
            files = run_ffmpeg_extract(vtmp, frames_dir, args.fps, start_s, end_s)
        if not files:
            print("[오류] 프레임을 추출하지 못했습니다. 영상 형식/경로를 확인하세요.")
            sys.exit(2)
        print(f"    추출 프레임: {len(files)}장")

        face_c, eye_c = prepare_cascades(tmp)

        print("[2/4] 장면 분석 중 (얼굴/눈/선명도)...")
        cands = []
        for i, fn in enumerate(files):
            img = cv2.imread(os.path.join(frames_dir, fn))
            if img is None:
                continue
            m = analyze(img, face_c, eye_c)
            if m is None or m["n_eyes"] == 0:  # 얼굴없음/눈감음/옆모습 제외
                continue
            m["t"] = (start_s or 0) + i / args.fps
            m["file"] = fn
            cands.append(m)
        if not cands:
            print("[안내] 얼굴+눈이 감지된 장면이 없습니다. --fps를 높이거나 구간을 조정해 보세요.")
            sys.exit(0)

        # 점수화 (선명도는 후보 내 상대 정규화)
        sharps = [c["sharp"] for c in cands]
        smin, smax = min(sharps), max(sharps)
        def norm(v):
            return (v - smin) / (smax - smin) if smax > smin else 0.5
        for c in cands:
            eye_score = {2: 1.0, 1: 0.45}.get(c["n_eyes"], 0.0)
            c["score"] = round(100 * (0.42 * eye_score + 0.30 * norm(c["sharp"])
                                      + 0.16 * min(c["face_area"] / 0.15, 1.0)
                                      + 0.12 * c["centered"]), 1)

        # 점수순 + 시간 간격 확보(다양한 장면)
        cands.sort(key=lambda c: c["score"], reverse=True)
        picked = []
        for c in cands:
            if all(abs(c["t"] - p["t"]) >= args.min_gap for p in picked):
                picked.append(c)
            if len(picked) >= args.topk:
                break

        print(f"[3/4] 후보 {len(picked)}장 저장 중...")
        for old in os.listdir(outdir):
            if old.startswith("cand_") or old in ("index.json", "contact_sheet.jpg"):
                try:
                    os.remove(os.path.join(outdir, old))
                except OSError:
                    pass
        index = []
        for rank, c in enumerate(picked, 1):
            mm = int(c["t"] // 60)
            ss = c["t"] - mm * 60
            name = f"cand_{rank:02d}_t{mm:02d}m{ss:04.1f}s_s{int(c['score'])}.jpg"
            shutil.copy(os.path.join(frames_dir, c["file"]), os.path.join(outdir, name))
            index.append(dict(rank=rank, time_sec=round(c["t"], 1),
                              time_label=f"{mm:02d}:{ss:04.1f}", score=c["score"],
                              n_eyes=c["n_eyes"], face_area=round(c["face_area"], 3),
                              sharp=round(c["sharp"], 1), file=name))
        with open(os.path.join(outdir, "index.json"), "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

        print("[4/4] 미리보기 시트 생성 중...")
        make_contact_sheet(picked, frames_dir, tmp, outdir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n[완료] 후보 저장 위치:", outdir)
    print("       - 개별 후보 : cand_*.jpg")
    print("       - 미리보기  : contact_sheet.jpg  (한눈에 보기)")
    print("       - 정보표    : index.json")
    print("\n가장 좋은 컷의 번호(#rank)를 알려주시면 그 장면으로 카드를 만들겠습니다.")


if __name__ == "__main__":
    main()
