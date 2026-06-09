"""
경기도의회 상임위원회별 공모사업 자동 수집 스크래퍼 (Playwright 기반)

GitHub Actions에서 매일 실행 → '공모사업 일정/projects.json' 갱신.

Playwright(헤드리스 Chromium)로 각 기관 게시판을 실제 렌더링한 뒤
공모/모집/공고 게시글의 제목·접수기간·개별 게시글 URL을 수집한다.
JS로 목록을 불러오는 사이트(경기도 통합공모 포털 등)도 처리 가능.
"""

import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

OUTPUT = Path(__file__).parent / "공모사업 일정" / "projects.json"
TODAY_STR = date.today().strftime("%Y-%m-%d")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 공모 제목으로 인정할 키워드
KEYWORDS = ("공모", "모집", "공고", "지원사업", "선정", "참가자", "참여기업", "입주")
# 제외할 잡음 (메뉴/안내성)
EXCLUDE = ("개인정보", "이용약관", "저작권", "찾아오시는", "사이트맵",
           "로그인", "회원가입", "바로가기", "메뉴", "닫기", "전체보기")


# ──────────────────────────────────────────────────────────────
# 수집 대상 정의
#   id     : 상임위 id (projects.json 키)
#   org    : 기관명
#   dept   : 기본 부서
#   url    : 게시판 URL
#   link   : 게시글 링크를 식별하는 href 부분 문자열 (None이면 키워드 기반)
# ──────────────────────────────────────────────────────────────
TARGETS = [
    # ── 경기도 통합공모 포털 (전 도청 실국) ──
    {"id": "_portal", "org": "경기도청", "dept": "통합공모",
     "url": "https://www.gg.go.kr/gongmo/bbs/board.do?bsIdx=923&menuId=4433",
     "link": "boardView.do"},
    {"id": "_portal", "org": "경기도청", "dept": "통합공모",
     "url": "https://www.gg.go.kr/gongmo/bbs/board.do?bsIdx=924&menuId=4434",
     "link": "boardView.do"},

    # ── 산하기관 (상임위 직접 매핑) ──
    {"id": "munhwa", "org": "경기문화재단", "dept": "문화예술본부",
     "url": "https://www.ggcf.kr/boards/businessNotices/articles", "link": "/articles/"},
    {"id": "munhwa", "org": "경기콘텐츠진흥원", "dept": "콘텐츠지원팀",
     "url": "https://www.gcon.or.kr/user/board/list/8", "link": None},
    {"id": "munhwa", "org": "경기관광공사", "dept": "관광마케팅팀",
     "url": "https://www.gto.or.kr", "link": None},

    {"id": "bokji", "org": "경기도사회서비스원", "dept": "서비스지원팀",
     "url": "https://www.ggss.or.kr/bbs/?bid=notice", "link": "subAct=view"},
    {"id": "bokji", "org": "경기복지재단", "dept": "복지정책본부",
     "url": "https://www.ggwf.or.kr", "link": None},

    {"id": "gihoek", "org": "경기연구원", "dept": "연구기획실",
     "url": "https://www.gri.re.kr/web/contents/notice.do", "link": None},

    {"id": "gyoyuk1", "org": "경기도교육연구원", "dept": "연구기획부",
     "url": "https://www.gie.re.kr/board/noticeList.do", "link": None},

    {"id": "mirae", "org": "경기도경제과학진흥원", "dept": "기업성장팀",
     "url": "https://egbiz.or.kr/sp/supportPrjCatList.do", "link": None},
    {"id": "mirae", "org": "경기테크노파크", "dept": "기업지원팀",
     "url": "https://www.gtp.or.kr", "link": None},

    {"id": "gyeongje", "org": "경기도일자리재단", "dept": "일자리정책실",
     "url": "https://www.jobaba.net", "link": None},
    {"id": "gyeongje", "org": "경기도사회적경제원", "dept": "사회적경제지원팀",
     "url": "https://www.gsea.or.kr", "link": None},

    {"id": "nonghae", "org": "경기도농업기술원", "dept": "기술보급과",
     "url": "https://www.nongup.gg.go.kr", "link": None},
    {"id": "nonghae", "org": "경기도농수산진흥원", "dept": "유통지원팀",
     "url": "https://www.ggaf.or.kr", "link": None},

    {"id": "dosi", "org": "경기주택도시공사(GH)", "dept": "주거복지본부",
     "url": "https://www.gh.or.kr", "link": None},

    {"id": "yeoseong", "org": "경기도평생교육진흥원", "dept": "평생교육지원팀",
     "url": "https://www.gill.or.kr", "link": None},
    {"id": "yeoseong", "org": "경기도여성가족재단", "dept": "정책연구팀",
     "url": "https://www.gfwf.or.kr", "link": None},

    {"id": "anjeun", "org": "경기자원봉사센터", "dept": "사업팀",
     "url": "https://www.ggvol.or.kr", "link": None},

    {"id": "gunseol", "org": "경기교통공사", "dept": "교통서비스팀",
     "url": "https://www.gbus.or.kr", "link": None},

    # ── 경기도교육청 (JS 렌더링) ──
    {"id": "gyoyuk2", "org": "경기도교육청", "dept": "행정국",
     "url": "https://www.goe.go.kr/home/main.do", "link": None},
]

# 통합공모 포털 게시글 → 상임위 분류용 키워드 맵 (제목/부서 텍스트 기반)
PORTAL_CLASSIFY = {
    "gyeongje": ["일자리", "소상공인", "기업", "창업", "사회적경제", "노동", "고용", "경제"],
    "munhwa":   ["문화", "예술", "관광", "체육", "콘텐츠", "공연", "축제", "도자"],
    "nonghae":  ["농업", "농수산", "축산", "어업", "산림", "해양", "친환경", "농가"],
    "bokji":    ["복지", "보건", "건강", "의료", "사회서비스", "돌봄", "장애"],
    "gunseol":  ["교통", "건설", "도로", "철도", "물류", "항만"],
    "dosi":     ["도시", "주택", "환경", "에너지", "기후", "수자원", "정원"],
    "mirae":    ["AI", "인공지능", "과학", "스타트업", "R&D", "미래", "반도체", "바이오"],
    "yeoseong": ["여성", "가족", "평생교육", "다문화", "청소년", "보육"],
    "anjeun":   ["안전", "소방", "재난", "자원봉사", "자치경찰"],
    "gihoek":   ["연구", "정책", "감사", "평화", "남북"],
    "gyoyuk1":  ["교육", "학교", "교원", "학생"],
    "uiwoon":   ["홍보", "의정", "소통"],
}


# ──────────────────────────────────────────────────────────────
def normalize_period(raw: str) -> str:
    """'YYYY.MM.DD ~ YYYY.MM.DD' 형식으로 정규화."""
    dates = re.findall(r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}", raw or "")
    if len(dates) >= 2:
        a, b = dates[0], dates[1]
        def fix(d):
            p = re.sub(r"[-/]", ".", d).split(".")
            return f"{p[0]}.{p[1].zfill(2)}.{p[2].zfill(2)}"
        return f"{fix(a)} ~ {fix(b)}"
    return ""


def classify_portal(text: str) -> str:
    """통합공모 게시글을 제목 키워드로 상임위 분류."""
    for cid, kws in PORTAL_CLASSIFY.items():
        for kw in kws:
            if kw in text:
                return cid
    return "gihoek"  # 기본값


def looks_like_title(text: str) -> bool:
    if not text or len(text) < 6 or len(text) > 120:
        return False
    if any(x in text for x in EXCLUDE):
        return False
    return any(k in text for k in KEYWORDS)


# ──────────────────────────────────────────────────────────────
# Playwright 추출
# ──────────────────────────────────────────────────────────────
def extract_from_page(page, target: dict) -> list[dict]:
    """렌더링된 페이지에서 공모 게시글을 추출."""
    base = target["url"]
    link_key = target["link"]
    items = []
    seen = set()

    # 페이지의 모든 링크 정보 + 조상 행 텍스트 추출
    anchors = page.evaluate(
        """() => {
            const out = [];
            document.querySelectorAll('a[href]').forEach(a => {
                const text = (a.innerText || a.textContent || '').trim();
                const href = a.getAttribute('href') || '';
                // 가까운 행(tr/li) 텍스트 (날짜 탐색용)
                const row = a.closest('tr,li,.item,.list-item,article,div');
                const rowText = row ? (row.innerText || '').trim().slice(0, 300) : text;
                out.push({text, href, rowText});
            });
            return out;
        }"""
    )

    for a in anchors:
        text = (a.get("text") or "").strip()
        href = a.get("href") or ""
        row_text = a.get("rowText") or text

        # 링크 식별: link_key가 있으면 href로, 없으면 키워드로
        if link_key:
            if link_key not in href:
                continue
            if not text or len(text) < 6 or any(x in text for x in EXCLUDE):
                continue
        else:
            if not looks_like_title(text):
                continue

        abs_href = urljoin(base, href) if href and not href.startswith("javascript") else base
        key = (text, abs_href)
        if key in seen:
            continue
        seen.add(key)

        period = normalize_period(row_text)

        # 상임위 결정
        cid = target["id"]
        if cid == "_portal":
            cid = classify_portal(text)

        items.append({
            "name": text,
            "period": period,
            "org": target["org"],
            "dept": target["dept"],
            "boardUrl": abs_href,
            "src": "auto",          # 자동 수집 표시 (매 실행마다 교체)
            "_committee": cid,
        })
        if len(items) >= 8:
            break

    return items


def scrape_all() -> list[dict]:
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, locale="ko-KR",
                                   viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(20000)

        for t in TARGETS:
            try:
                print(f"  [{t['org']}] {t['url']}")
                page.goto(t["url"], wait_until="domcontentloaded")
                # JS 목록 로딩 대기
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                page.wait_for_timeout(1500)
                items = extract_from_page(page, t)
                print(f"    → {len(items)}건")
                results.extend(items)
            except Exception as e:
                print(f"    [오류] {e}", file=sys.stderr)
                continue

        browser.close()
    return results


# ──────────────────────────────────────────────────────────────
# 병합 / 저장
# ──────────────────────────────────────────────────────────────
def load_existing() -> dict:
    if OUTPUT.exists():
        with open(OUTPUT, encoding="utf-8") as f:
            return json.load(f)
    return {"projects": {}}


def merge(existing: dict, scraped: list[dict]) -> dict:
    """
    자동 수집분(src='auto')은 매 실행마다 교체(self-cleaning),
    수동 입력분(src 없음)은 그대로 보존한다.
    """
    # 1) 기존에서 수동 입력분만 남김 (이전 auto 항목 제거)
    result = {}
    for cid, plist in existing.get("projects", {}).items():
        result[cid] = [p for p in plist if p.get("src") != "auto"]

    # 2) 이번에 수집한 auto 항목 추가 (중복 제거)
    for item in scraped:
        cid = item.pop("_committee", "gihoek")
        result.setdefault(cid, [])
        names = {p["name"] for p in result[cid]}
        if item.get("name") and item.get("boardUrl") and item["name"] not in names:
            result[cid].append(item)

    return result


def save(projects: dict) -> None:
    payload = {
        "updated": TODAY_STR,
        "source": "각 기관 공모 게시판 자동 수집 (Playwright)",
        "projects": projects,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[완료] {OUTPUT} 저장 ({TODAY_STR})")


def main() -> None:
    print(f"스크래핑 시작: {TODAY_STR}")
    existing = load_existing()
    scraped = scrape_all()
    print(f"수집 총 {len(scraped)}건")
    merged = merge(existing, scraped)
    save(merged)
    print(f"저장 총 {sum(len(v) for v in merged.values())}건")


if __name__ == "__main__":
    main()
