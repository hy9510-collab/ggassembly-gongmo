# -*- coding: utf-8 -*-
"""
경기도의회 회의록(kms.ggc.go.kr)에서 특정 의원의 발언을 자동 추출.

- 회의록 페이지는 서버렌더링 HTML이며, 의원 발언은 <span class='PV{발언자ID}'>이름</span>
  마커로 시작하고, 공무원 답변은 <span class='bold'>○ 직함 이름</span> 마커로 시작한다.
- 특정 의원의 발언 구간(질의)과 바로 뒤따르는 답변을 함께 추출해 AI 작성 입력으로 쓴다.

사용 예:
  python scripts/fetch_speech.py "https://kms.ggc.go.kr/cms/mntsViewer.do?mntsId=15317" 조용호
"""
import io
import json
import re
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

MAX_ANSWER_CHARS = 1200  # 답변은 요지 파악용이므로 과도하게 길면 자름


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def normalize_minutes_url(url_or_id):
    """mntsViewer URL 또는 숫자 ID → 표준 회의록 URL."""
    s = str(url_or_id).strip()
    if s.isdigit():
        return f"https://kms.ggc.go.kr/cms/mntsViewer.do?mntsId={s}"
    m = re.search(r"mntsId=(\d+)", s)
    if m:
        return f"https://kms.ggc.go.kr/cms/mntsViewer.do?mntsId={m.group(1)}"
    return s


def strip_tags(fragment):
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def parse_korean_date(text):
    """여러 표기(2026.02.06 / 2026-2-6 / 2026년 2월 6일)에서 날짜 추출 → (점표기, 압축) 또는 None."""
    m = re.search(r"(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})", text or "")
    if not m:
        return None
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    return f"{y}. {mo}. {d}.", f"{y}{mo:02d}{d:02d}"


def parse_meta(html):
    """title 태그에서 회의명·날짜 추출. 예: 2025년도 문화체육관광위원회행정사무감사(2025.11.10. 월요일) | ..."""
    meta = {"meeting_title": "", "date": "", "committee": ""}
    m = re.search(r"<title>([^<|]+)", html)
    if m:
        t = m.group(1).strip()
        meta["meeting_title"] = re.sub(r"\(\d{4}\.\d{2}\.\d{2}\.[^)]*\)", "", t).strip()
        pd = parse_korean_date(t)
        if pd:
            meta["date"], meta["date_compact"] = pd
        c = re.search(r"([가-힣]+위원회)", t)
        if c:
            meta["committee"] = c.group(1)
    return meta


def speaker_map(html):
    """체크박스 메뉴에서 이름→PV발언자ID 매핑. label 예: '조용호 위원'"""
    mapping = {}
    for m in re.finditer(
            r'value="sMan(PV\d+)"[^>]*/>\s*<label[^>]*>([^<]+)</label>', html):
        pv, label = m.group(1), strip_tags(m.group(2))
        name = re.sub(r"\s*(위원장|부위원장|위원|의원)\s*$", "", label).strip()
        if name:
            mapping[name] = pv
    return mapping


def extract_speeches(html, member_name):
    """의원 발언(질의)과 바로 뒤따르는 답변을 순서대로 추출."""
    markers = []
    # 의원 발언 마커
    for m in re.finditer(r"<span class='(PV\d+)'>([^<]+)</span>", html):
        markers.append((m.start(), m.end(), "member", strip_tags(m.group(2))))
    # 공무원(답변자) 마커
    for m in re.finditer(r"<span class='bold'>\s*○([^<]+)</span>", html):
        markers.append((m.start(), m.end(), "official", strip_tags(m.group(1))))
    markers.sort()
    if not markers:
        return []

    segments = []
    for i, (s, e, kind, name) in enumerate(markers):
        seg_end = markers[i + 1][0] if i + 1 < len(markers) else min(len(html), e + 20000)
        text = strip_tags(html[e:seg_end])
        # 세그먼트 머리의 직함 잔여물과 꼬리의 다음 마커 부스러기(○) 정리
        text = re.sub(r"^(위원장|부위원장|위원|의원)\s+", "", text)
        text = re.sub(r"\s*○\s*(위원장|부위원장)?\s*$", "", text)
        segments.append({"kind": kind, "name": name, "text": text})

    # 대상 의원 발언 + 뒤따르는 답변 수집
    out = []
    for i, seg in enumerate(segments):
        if seg["kind"] == "member" and seg["name"] == member_name:
            out.append({"role": "질의", "speaker": member_name, "text": seg["text"]})
            j = i + 1
            while j < len(segments) and segments[j]["kind"] == "official":
                ans = segments[j]["text"]
                if len(ans) > MAX_ANSWER_CHARS:
                    ans = ans[:MAX_ANSWER_CHARS] + " …(중략)"
                out.append({"role": "답변", "speaker": segments[j]["name"], "text": ans})
                j += 1
    return out


# 공무원(답변자) 직함 키워드 — 발언자 구분용
ADMIN_TITLES = ("국장", "과장", "단장", "소장", "센터장", "관장", "실장", "본부장",
                "대표", "원장", "청장", "팀장", "서기관", "사무관", "주무관",
                "교육감", "교육장", "부교육감", "차장", "부장", "이사")


def _is_official(speaker):
    return any(t in speaker for t in ADMIN_TITLES)


def get_member_speeches_from_vod(url_or_midx, member_name):
    """영상회의록(vodViewer)의 자막 회의록(captionDoc)에서 의원 발언 추출.
    영상은 회의 직후 올라오므로 정식 회의록보다 빠르게 쓸 수 있다."""
    s = str(url_or_midx)
    m = re.search(r"midx=(\d+)", s)
    midx = m.group(1) if m else s.strip()
    vod_url = f"https://kms.ggc.go.kr/caster/player/vodViewer.do?midx={midx}"

    meta = {"meeting_title": "", "date": "", "committee": ""}
    try:
        vod_html = fetch(vod_url)
        t = re.search(r"<title>([^|<]+)", vod_html)
        if t:
            title = t.group(1).strip()  # 예: 2025년도 행정사무감사 (2025-11-10) 문화체육관광위원회
            pd = parse_korean_date(title)
            if pd:
                meta["date"], meta["date_compact"] = pd
            c = re.search(r"([가-힣]+위원회)", title)
            if c:
                meta["committee"] = c.group(1)
            meta["meeting_title"] = re.sub(r"\(\d{4}-\d{2}-\d{2}\)", "", title).strip()
    except Exception:
        pass

    cap_html = fetch(f"https://kms.ggc.go.kr/caster/player/captionDoc.do?proc=view&midx={midx}")
    markers = [(mm.start(), mm.end(), strip_tags(mm.group(1)))
               for mm in re.finditer(r"<strong>\s*○\s*([^<]+?)\s*</strong>", cap_html)]
    if not markers:
        raise LookupError(
            "이 영상의 자막 회의록이 아직 준비되지 않았습니다. "
            "회의 직후라면 몇 시간 뒤 다시 시도하거나, 발언 메모를 직접 입력해 주세요.")

    segments = []
    for i, (s0, e0, speaker) in enumerate(markers):
        seg_end = markers[i + 1][0] if i + 1 < len(markers) else len(cap_html)
        text = strip_tags(cap_html[e0:seg_end])
        segments.append({"speaker": speaker, "text": text,
                         "official": _is_official(speaker)})

    members_here = sorted({g["speaker"] for g in segments if not g["official"]})
    target = [i for i, g in enumerate(segments)
              if not g["official"] and member_name in g["speaker"]]
    if not target:
        raise LookupError(
            f"'{member_name}' 의원의 발언이 이 영상 회의록에 없습니다. "
            f"이 회의의 발언자: {', '.join(members_here) or '(없음)'}")

    out = []
    for i, g in enumerate(segments):
        if not g["official"] and member_name in g["speaker"]:
            out.append({"role": "질의", "speaker": member_name, "text": g["text"]})
            j = i + 1
            while j < len(segments) and segments[j]["official"]:
                ans = segments[j]["text"]
                if len(ans) > MAX_ANSWER_CHARS:
                    ans = ans[:MAX_ANSWER_CHARS] + " …(중략)"
                out.append({"role": "답변", "speaker": segments[j]["speaker"], "text": ans})
                j += 1
    total_chars = sum(len(x["text"]) for x in out)
    return {
        "url": vod_url, "member": member_name, "spkr_id": "",
        "meta": meta, "speeches": out,
        "speech_count": sum(1 for x in out if x["role"] == "질의"),
        "total_chars": total_chars,
    }


def get_member_speeches(minutes_url_or_id, member_name):
    """회의록(또는 영상회의록)에서 특정 의원 발언 추출 → dict(메타 + 발언 목록)."""
    s = str(minutes_url_or_id)
    # 영상회의록 링크(midx=…)면 자막 회의록 경로로
    if "midx=" in s or "vodViewer" in s or "/caster/" in s:
        return get_member_speeches_from_vod(s, member_name)

    url = normalize_minutes_url(minutes_url_or_id)
    html = fetch(url)
    meta = parse_meta(html)
    smap = speaker_map(html)
    if member_name not in smap:
        available = ", ".join(sorted(smap)) or "(발언자 목록 없음)"
        title = meta.get("meeting_title") or "제목 확인 불가"
        raise LookupError(
            f"'{member_name}' 의원의 발언이 이 회의록({title})에 없습니다. "
            f"발언 의원: {available}. "
            "링크가 회의록(mntsId=…) 또는 영상회의록(midx=…) 페이지인지 확인해 주세요.")
    speeches = extract_speeches(html, member_name)
    total_chars = sum(len(s["text"]) for s in speeches)
    return {
        "url": url, "member": member_name, "spkr_id": smap[member_name],
        "meta": meta, "speeches": speeches,
        "speech_count": sum(1 for s in speeches if s["role"] == "질의"),
        "total_chars": total_chars,
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("사용법: python fetch_speech.py <회의록URL|mntsId> <의원이름>")
        sys.exit(1)
    result = get_member_speeches(sys.argv[1], sys.argv[2])
    out_path = sys.argv[3] if len(sys.argv) > 3 else None
    dump = json.dumps(result, ensure_ascii=False, indent=2)
    if out_path:
        io.open(out_path, "w", encoding="utf-8").write(dump)
        print(f"[완료] {result['member']} 질의 {result['speech_count']}건, "
              f"{result['total_chars']:,}자 → {out_path}")
    else:
        print(dump)
