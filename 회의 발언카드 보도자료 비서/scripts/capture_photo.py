# -*- coding: utf-8 -*-
"""
영상회의록에서 의원 발언 장면을 자동 캡처.

- listAngunXhr.do 로 발언자별 시작 위치(초)를 얻어, 가장 긴 발언 구간을 고른다.
- 원격 mp4에서 해당 구간만 프레임 추출(전체 다운로드 없음).
- 얼굴이 크고(주인공), 눈을 뜨고(정면), 선명한 컷을 자동 선정.
- 배경의 다른 인물 얼굴은 흐리게 처리해 주인공을 부각.

사용:  python capture_photo.py <midx|영상링크> <의원이름> <저장경로.jpg>
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_best_frames import prepare_cascades, analyze  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
BASE_URL = "https://kms.ggc.go.kr"

SAMPLE_FPS = 0.5          # 2초에 1장
PER_SEG_SEC = 60          # 각 발언 구간의 '앞부분'만 샘플 (질의 시작 = 의원 화면)
SKIP_HEAD_SEC = 8         # 구간 시작 직후(호명 등)는 건너뜀
MAX_SEGMENTS = 3          # 발언 구간 최대 몇 개까지 샘플할지


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _midx_of(s):
    m = re.search(r"midx=(\d+)", str(s))
    return m.group(1) if m else str(s).strip()


def find_member_segments(midx, member_name):
    """의원의 발언 구간 목록(긴 순) → [(시작초, 끝초, 안건명), …]."""
    data = json.loads(_get(f"{BASE_URL}/caster/player/listAngunXhr.do?midx={midx}").decode("utf-8"))
    segs = []
    for i, e in enumerate(data):
        if member_name in (e.get("m_angun") or ""):
            start = int(e["m_pos"])
            end = int(data[i + 1]["m_pos"]) if i + 1 < len(data) else start + 600
            segs.append((end - start, start, end, e["m_angun"]))
    if not segs:
        names = sorted({re.sub(r".*\(|\).*", "", e.get("m_angun", ""))
                        for e in data if e.get("m_mbr") == "M"})
        raise LookupError(f"'{member_name}' 의원의 발언 구간을 영상에서 찾지 못했습니다. "
                          f"(영상 목차 발언자: {', '.join(n for n in names if n)})")
    segs.sort(reverse=True)          # 긴 구간(본질의) 우선
    return [(s, e, lb) for _, s, e, lb in segs[:MAX_SEGMENTS]]


def get_mp4_url(midx):
    html = _get(f"{BASE_URL}/caster/player/vodViewer.do?midx={midx}").decode("utf-8", "replace")
    m = re.search(r'href="(/mp4/[^"]+\.mp4)"', html)
    if not m:
        raise LookupError("영상 파일 주소를 찾지 못했습니다.")
    return BASE_URL + m.group(1)


def is_split_frame(img):
    """질의답변 구간의 좌우 분할 화면 여부 감지 (중앙 세로 경계선)."""
    import numpy as np
    H, W = img.shape[:2]
    mid = W // 2
    left_col = img[:, mid - 4:mid - 1].astype(np.int32)
    right_col = img[:, mid + 1:mid + 4].astype(np.int32)
    seam = float(np.mean(np.abs(left_col.mean(axis=1) - right_col.mean(axis=1))))
    lh = img[:, :mid].astype(np.int32).mean(axis=(0, 1))
    rh = img[:, mid:].astype(np.int32).mean(axis=(0, 1))
    halves = float(np.abs(lh - rh).mean())
    return seam > 22 or halves > 28


def blur_others(img, face_c):
    """가장 큰 얼굴(주인공) 외 다른 인물 얼굴을 부드럽게(타원+페더) 흐리게."""
    import numpy as np
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape
    faces = face_c.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                    minSize=(int(W * 0.04), int(H * 0.04)))
    if len(faces) <= 1:
        return img, 0
    main = max(faces, key=lambda f: f[2] * f[3])
    main_area = main[2] * main[3]
    mask = np.zeros((H, W), dtype=np.float32)
    blurred = 0
    for (x, y, w, h) in faces:
        if (x, y, w, h) == tuple(main):
            continue
        # 주인공과 비슷한 크기의 얼굴은 건드리지 않음(오인 방지) — 확실한 배경 인물만
        if w * h >= 0.55 * main_area:
            continue
        cx, cy = int(x + w / 2), int(y + h * 0.55)
        cv2.ellipse(mask, (cx, cy), (int(w * 0.85), int(h * 1.1)), 0, 0, 360, 1.0, -1)
        blurred += 1
    if not blurred:
        return img, 0
    mask = cv2.GaussianBlur(mask, (61, 61), 0)          # 경계 페더링
    heavy = cv2.GaussianBlur(img, (71, 71), 0)
    m3 = cv2.merge([mask, mask, mask])
    out = (img.astype(np.float32) * (1 - m3) + heavy.astype(np.float32) * m3)
    return out.astype("uint8"), blurred


def capture(midx_or_url, member_name, out_path, progress=lambda msg: None):
    """의원 발언 장면 1컷을 자동 선정해 out_path(jpg)에 저장.

    질의 시작 직후에는 반드시 의원 화면이 잡히므로, 각 발언 구간의
    '앞 60초'만 샘플링해 답변자(공무원) 화면이 섞이는 것을 피한다.
    """
    midx = _midx_of(midx_or_url)
    progress("영상 목차에서 발언 구간을 찾는 중…")
    segments = find_member_segments(midx, member_name)
    mp4 = get_mp4_url(midx)
    progress("발언 구간 " + ", ".join(f"{lb}({e-s}초)" for s, e, lb in segments)
             + " — 각 구간 앞부분에서 장면 분석")

    tmp = tempfile.mkdtemp(prefix="capshot_")
    frames_dir = os.path.join(tmp, "f")
    os.makedirs(frames_dir)
    try:
        seg_kind = {}   # si → "short"(의원 단독 발언) / "long"(질의답변 교차)
        for si, (start, end, label) in enumerate(segments):
            seg_dur = end - start
            if seg_dur < 40:
                # 짧은 구간(자료요구 등): 의원 단독 화면. 끝부분(다음 발언자)은 제외
                s0 = start + 2
                dur = max(6, int(seg_dur * 0.55))
                seg_kind[si] = "short"
            else:
                # 긴 구간(질의답변): 화면이 의원/답변자 교차 — 분할화면에서 의원 쪽만 쓸 것
                s0 = start + SKIP_HEAD_SEC
                dur = min(PER_SEG_SEC, seg_dur - SKIP_HEAD_SEC)
                seg_kind[si] = "long"
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                   "-ss", str(s0), "-i", mp4, "-t", str(int(dur)),
                   "-vf", f"fps={SAMPLE_FPS}", "-q:v", "2",
                   os.path.join(frames_dir, f"s{si}_%05d.jpg")]
            subprocess.run(cmd, check=True, timeout=300)
        files = sorted(os.listdir(frames_dir))
        if not files:
            raise RuntimeError("영상에서 프레임을 추출하지 못했습니다.")
        progress(f"프레임 {len(files)}장 추출 — 좋은 장면 고르는 중 (정면·선명도 기준)…")

        face_c, eye_c = prepare_cascades(tmp)
        cands, n_crops = [], 0
        for i, fn in enumerate(files):
            img = cv2.imread(os.path.join(frames_dir, fn))
            if img is None:
                continue
            si = int(fn[1])
            split = is_split_frame(img)
            crop = False
            if seg_kind.get(si) == "long":
                if not split:
                    continue     # 긴 구간의 비분할 화면 = 답변자/전경 → 의원 아님, 제외
                img = img[:, img.shape[1] // 2:]   # 분할화면의 오른쪽 = 질의하는 의원
                crop = True
                n_crops += 1
            else:
                if split:        # 짧은 단독 구간에 분할화면이 섞이면 그것도 오른쪽만
                    img = img[:, img.shape[1] // 2:]
                    crop = True
                    n_crops += 1
            m = analyze(img, face_c, eye_c)
            if m is None or m["n_eyes"] == 0:
                continue
            m["file"], m["crop"] = fn, crop
            cands.append(m)
        if n_crops:
            progress(f"분할 화면 {n_crops}장은 의원 쪽(오른쪽)만 잘라 사용")
        if not cands:
            raise RuntimeError("눈을 뜬 정면 장면을 찾지 못했습니다. 사진을 직접 올려 주세요.")
        sharps = [c["sharp"] for c in cands]
        smin, smax = min(sharps), max(sharps)
        best, best_score = None, -1.0
        for c in cands:
            ns = (c["sharp"] - smin) / (smax - smin) if smax > smin else 0.5
            eye = {2: 1.0, 1: 0.4}.get(c["n_eyes"], 0)
            score = (0.45 * eye + 0.3 * ns
                     + 0.15 * min(c["face_area"] / 0.12, 1.0) + 0.1 * c["centered"])
            if not c["crop"]:
                score += 0.06        # 단독 풀샷(명패 노출 가능성 높음) 우대
            if score > best_score:
                best_score, best = score, c

        img = cv2.imread(os.path.join(frames_dir, best["file"]))
        if best["crop"]:
            img = img[:, img.shape[1] // 2:]
        img, blurred = blur_others(img, face_c)
        out_tmp = os.path.join(tmp, "out.jpg")
        cv2.imwrite(out_tmp, img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        shutil.copy(out_tmp, out_path)
        msg = f"장면 선정 완료 (후보 {len(cands)}장 중 최적 컷"
        if blurred:
            msg += f", 배경 인물 {blurred}명 흐림 처리"
        progress(msg + ")")
        return {"segments": [lb for _, _, lb in segments],
                "candidates": len(cands), "blurred": blurred}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("사용법: python capture_photo.py <midx|영상링크> <의원이름> <저장경로.jpg>")
        sys.exit(1)
    info = capture(sys.argv[1], sys.argv[2], sys.argv[3], progress=lambda m: print(" -", m))
    print("[완료]", json.dumps(info, ensure_ascii=False))
