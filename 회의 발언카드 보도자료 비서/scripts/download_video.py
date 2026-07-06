# -*- coding: utf-8 -*-
"""
회의 영상 다운로드 스크립트
- 경기도의회 인터넷의사중계시스템 등의 영상 주소를 받아 work/ 폴더에 저장합니다.
- 전체 영상이 길면 --start / --end 로 필요한 구간만 받을 수 있습니다.

사용 예:
  python scripts/download_video.py "https://...영상주소..."
  python scripts/download_video.py "https://...영상주소..." --start 00:12:30 --end 00:25:00
  python scripts/download_video.py "https://...영상주소..." --name 도시환경위_홍길동
"""
import argparse
import os
import sys
import subprocess

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(BASE, "work")


def hms_to_seconds(t):
    """'00:12:30' 또는 '750' 형태를 초로 변환."""
    if t is None:
        return None
    t = str(t).strip()
    if ":" in t:
        parts = [float(p) for p in t.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        h, m, s = parts[-3], parts[-2], parts[-1]
        return h * 3600 + m * 60 + s
    return float(t)


def main():
    ap = argparse.ArgumentParser(description="회의 영상 다운로드")
    ap.add_argument("url", help="영상 주소(URL)")
    ap.add_argument("--start", default=None, help="시작 시각 (예: 00:12:30)")
    ap.add_argument("--end", default=None, help="끝 시각 (예: 00:25:00)")
    ap.add_argument("--name", default=None, help="저장 파일 이름(확장자 제외)")
    ap.add_argument("--list-subs", action="store_true", help="자막 목록만 확인")
    args = ap.parse_args()

    os.makedirs(WORK, exist_ok=True)

    try:
        import yt_dlp  # noqa
    except ImportError:
        print("[오류] yt-dlp가 설치되어 있지 않습니다. 가상환경에서 실행했는지 확인하세요.")
        sys.exit(1)

    name = args.name or "meeting_%(id)s"
    outtmpl = os.path.join(WORK, name + ".%(ext)s")

    # 자막 목록만 확인하는 모드
    if args.list_subs:
        cmd = [sys.executable, "-m", "yt_dlp", "--list-subs", args.url]
        subprocess.run(cmd)
        return

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["ko", "ko-KR"],
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
    }

    start_s = hms_to_seconds(args.start)
    end_s = hms_to_seconds(args.end)

    # 구간 지정 시: 해당 구간만 내려받기
    if start_s is not None or end_s is not None:
        s = start_s if start_s is not None else 0
        e = end_s if end_s is not None else None

        def ranges(info_dict, ydl):
            return [{"start_time": s, "end_time": e if e is not None else info_dict.get("duration", 0)}]

        ydl_opts["download_ranges"] = ranges
        ydl_opts["force_keyframes_at_cuts"] = True
        print(f"[구간] {args.start or '처음'} ~ {args.end or '끝'} 만 받습니다.")

    import yt_dlp
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            ydl.download([args.url])
        except Exception as e:
            print(f"[오류] 다운로드 실패: {e}")
            print("\n[안내] 경기도의회 의사중계는 특수 플레이어를 쓸 수 있습니다.")
            print("       이 경우 영상 페이지 주소 대신 '실제 영상 스트림(m3u8/mp4) 주소'가 필요할 수 있어요.")
            print("       어떤 오류가 났는지 알려주시면 다른 방법으로 받아보겠습니다.")
            sys.exit(2)

    print("\n[완료] work 폴더를 확인하세요:", WORK)


if __name__ == "__main__":
    main()
