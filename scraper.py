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
# (제목이 '공모'가 아니어도 신청·접수·참가 등 도민이 지원 가능한 공고 포함)
KEYWORDS = ("공모", "모집", "지원사업", "지원 사업", "선정", "참가자", "참여기업",
            "참여자", "공모전", "아이디어", "경진대회", "지원자",
            "신청", "접수", "참가", "지원 안내", "지원금")
# 행사 제목 키워드 — 공모 키워드가 없고 아래만 있으면 '행사'로 분류
EVENT_KEYWORDS = ("행사", "축제", "박람회", "설명회", "세미나", "포럼", "강연",
                  "공연", "전시", "체험", "캠페인", "대회", "교실", "아카데미")
# 제외할 잡음 (채용/입찰/분양 등 — 제목 줄에 있으면 제외)
# '안내/현황/소개/목록'은 정상 공모 제목에도 자주 쓰여 제외하지 않는다.
EXCLUDE = ("채용", "입찰", "낙찰", "계약", "보상", "분양", "임대", "합격", "임용",
           "인사발령", "발령", "승무", "사원", "운영규정", "매뉴얼", "약관",
           "개인정보", "로그인", "회원가입", "사이트맵", "더보기",
           "바로가기", "전체보기", "닫기", "오시는", "이의신청", "증명서")
# 제외할 URL 패턴 (채용/입찰/분양/보도자료 게시판) — 흔한 단어의 일부와 겹치지 않게 구체적으로
EXCLUDE_URL = ("recruit", "employ", "chaeyong", "/bid", "bid-", "reward",
               "salerental", "rental", "/sale", "sale-",
               "press", "m209147177")  # 보도자료 (gh press-release, gsic 보도 게시판 등)
# 개별 게시글로 인정할 href 패턴 (목록/메뉴 페이지 배제)
DETAIL_HINTS = ("articleno", "article=", "seq=", "idx=", "nttid", "bidx=",
                "mode=view", "subact=view", "boardview", "/view", "?no=",
                "bsidx", "ntt_id", "/articles/")

# 경기도 31개 시·군 (통합공모 포털 게시글의 경기 여부 판단용)
GG_CITIES = ("수원", "성남", "고양", "용인", "부천", "안산", "안양", "남양주",
             "화성", "평택", "의정부", "시흥", "파주", "광명", "김포", "군포",
             "이천", "양주", "오산", "구리", "안성", "포천", "의왕", "하남",
             "여주", "동두천", "과천", "양평", "가평", "연천")
# 경기 외 광역지자체·대표도시 — 제목에 있고 '경기'·경기 시군명이 없으면 제외.
# '광주'는 경기 광주시와 모호하므로 '광주광역시'만 사용.
NON_GG_REGIONS = ("서울", "부산", "대구", "대전", "울산", "인천", "광주광역시",
                  "강원", "충북", "충남", "충청", "전북", "전남", "전라",
                  "경북", "경남", "경상", "제주", "목포", "여수", "순천",
                  "청주", "천안", "아산", "전주", "익산", "포항", "경주",
                  "구미", "창원", "김해", "진주", "원주", "춘천", "강릉")
# 중앙정부·전국 단위 공모 표시어 — 타 지역명이 섞여 있어도 살린다(포함).
CENTRAL_NATIONAL = (
    "전국", "대한민국", "범정부",
    # 부(部)
    "기획재정부", "교육부", "과학기술정보통신부", "외교부", "통일부",
    "법무부", "국방부", "행정안전부", "국가보훈부", "문화체육관광부",
    "농림축산식품부", "산업통상자원부", "보건복지부", "환경부",
    "고용노동부", "여성가족부", "국토교통부", "해양수산부", "중소벤처기업부",
    # 흔한 약칭
    "행안부", "과기정통부", "문체부", "복지부", "국토부", "농식품부",
    # 청(廳)·위원회
    "국세청", "관세청", "조달청", "통계청", "검찰청", "경찰청", "소방청",
    "병무청", "방위사업청", "문화재청", "국가유산청", "농촌진흥청",
    "산림청", "특허청", "기상청", "질병관리청",
    "국민권익위", "방송통신위", "공정거래위", "금융위원회",
    "개인정보보호위", "국가인권위", "원자력안전위",
)


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
     "url": "https://ggtour.or.kr/gto/notice/notice", "link": None},
    {"id": "munhwa", "org": "경기도체육회", "dept": "사무처",
     "url": "https://ggsports.gg.go.kr/archives/category/gg_sports_notice/public",
     "link": "/archives/"},
    {"id": "munhwa", "org": "경기도체육회", "dept": "사무처",
     "url": "https://ggsports.gg.go.kr/archives/category/ggsports_ggdo_game",
     "link": "/archives/", "kind": "event"},

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
     "url": "https://gsic.or.kr", "link": None},

    {"id": "nonghae", "org": "경기도농업기술원", "dept": "기술보급과",
     "url": "https://nongup.gg.go.kr", "link": None},
    {"id": "nonghae", "org": "경기도농수산진흥원", "dept": "유통지원팀",
     "url": "https://www.gafi.or.kr/web/board/boardContentsListPage.do?board_id=42&menu_id=9d7a4fa3cd784b2ea1ab192315847444", "link": None},

    {"id": "dosi", "org": "경기주택도시공사(GH)", "dept": "주거복지본부",
     "url": "https://www.gh.or.kr/gh/notice.do", "link": None},

    {"id": "yeoseong", "org": "경기도평생교육진흥원", "dept": "평생교육지원팀",
     "url": "https://www.gill.or.kr", "link": None},
    {"id": "yeoseong", "org": "경기도여성가족재단", "dept": "정책연구팀",
     "url": "https://www.gwff.kr/base/board/list?boardManagementNo=21&menuLevel=2&menuNo=23", "link": None},

    {"id": "anjeun", "org": "경기자원봉사센터", "dept": "사업팀",
     "url": "https://www.ggvc.or.kr/cop/bbs/selectBoardArticle.do?bbsId=Business_main", "link": None},

    {"id": "gunseol", "org": "경기교통공사", "dept": "교통서비스팀",
     "url": "https://www.gbus.or.kr", "link": None},

    # ── 경기도교육청 (JS 렌더링) ──
    {"id": "gyoyuk2", "org": "경기도교육청", "dept": "행정국",
     "url": "https://www.goe.go.kr", "link": None},
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
    """'YYYY.MM.DD ~ YYYY.MM.DD' 형식으로 정규화 (시작<=종료만 인정)."""
    dates = re.findall(r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}", raw or "")
    if len(dates) >= 2:
        def fix(d):
            p = re.sub(r"[-/]", ".", d).split(".")
            return f"{p[0]}.{p[1].zfill(2)}.{p[2].zfill(2)}"
        a, b = fix(dates[0]), fix(dates[1])
        # 0 패딩된 'YYYY.MM.DD'는 문자열 비교가 곧 날짜 비교
        if a <= b:
            return f"{a} ~ {b}"
    return ""


def classify_portal(text: str) -> str:
    """통합공모 게시글을 제목 키워드로 상임위 분류."""
    for cid, kws in PORTAL_CLASSIFY.items():
        for kw in kws:
            if kw in text:
                return cid
    return "gihoek"  # 기본값


def clean_title(raw: str) -> str:
    """여러 줄 텍스트(행 전체·네비 블록)에서 제목 한 줄만 고른다.
    키워드를 포함한 줄을 우선하고, 그 중 가장 긴 줄을 택한다."""
    if not raw:
        return ""
    lines = [ln.strip() for ln in re.split(r"[\r\n\t]+", raw) if ln.strip()]
    if not lines:
        return ""
    kw_lines = [ln for ln in lines
                if any(k in ln for k in KEYWORDS + EVENT_KEYWORDS)]
    pool = kw_lines if kw_lines else lines
    name = max(pool, key=len)
    # 제목 뒤에 본문이 이어붙은 경우(…에서는/…드립니다 …) 본문 앞에서 끊는다
    for marker in ("에서는", "드립니다", "바랍니다", "하였습니다", "되었습니다", "하고 있"):
        i = name.find(marker)
        if i > 10:
            name = name[:i].strip()
            break
    return name


def looks_like_title(text: str) -> bool:
    if not text or len(text) < 6 or len(text) > 90:
        return False
    if any(x in text for x in EXCLUDE):
        return False
    return (any(k in text for k in KEYWORDS)
            or any(k in text for k in EVENT_KEYWORDS))


def classify_kind(title: str) -> str:
    """공모/행사 구분 — 공모 키워드가 있으면 공모, 행사 키워드만 있으면 행사."""
    if any(k in title for k in KEYWORDS):
        return ""          # 공모 (기본)
    if any(k in title for k in EVENT_KEYWORDS):
        return "event"     # 행사
    return ""


def is_excluded_href(href: str) -> bool:
    """채용/입찰/분양 등 잡음 게시판 URL 제외."""
    h = (href or "").lower()
    return any(bad in h for bad in EXCLUDE_URL)


def is_detail_href(href: str) -> bool:
    """개별 게시글로 보이는 URL인지 (article id 등 포함)."""
    h = (href or "").lower()
    return any(hint in h for hint in DETAIL_HINTS)


def keep_portal_item(title: str) -> bool:
    """통합공모 포털 게시글을 남길지 판단.
    1) 중앙정부·전국 단위 공모 → 지역과 무관하게 포함(살림)
    2) '경기' 또는 경기 시·군명 → 포함
    3) 타 지자체 지역명만 있으면 → 제외
    4) 지역 표기가 전혀 없으면 (경기 포털 글이므로) → 포함"""
    t = title or ""
    if any(c in t for c in CENTRAL_NATIONAL):
        return True
    if "경기" in t:
        return True
    if any(c in t for c in GG_CITIES):
        return True
    if any(r in t for r in NON_GG_REGIONS):
        return False
    return True


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
        href = (a.get("href") or "").strip()
        raw_text = (a.get("text") or "").strip()
        row_text = a.get("rowText") or raw_text

        # 유효한 링크만 (자바스크립트/앵커/빈 링크 제외)
        if not href or href.startswith("javascript") or href.startswith("#"):
            continue
        # 채용/입찰/분양 등 잡음 URL 제외
        if is_excluded_href(href):
            continue

        # 제목 정리: 여러 줄 중 키워드 포함한 가장 긴 줄
        name = clean_title(raw_text)
        if not looks_like_title(name):
            continue

        # 링크 식별
        if link_key:
            # 게시판 view 링크가 명확한 사이트
            if link_key not in href:
                continue
        else:
            # 키워드 기반 사이트: 개별 게시글 URL(article id 등)만 인정
            if not is_detail_href(href):
                continue

        abs_href = urljoin(base, href)
        key = (name, abs_href)
        if key in seen:
            continue
        seen.add(key)

        period = normalize_period(row_text)

        # 상임위 결정
        cid = target["id"]
        if cid == "_portal":
            # 통합공모 포털엔 타 시·도 공모도 올라옴 → 경기·중앙정부 건만 수집
            if not keep_portal_item(name):
                continue
            cid = classify_portal(name)

        # 공모/행사 구분 (행사 전용 게시판은 target에 kind 지정)
        kind = target.get("kind") or classify_kind(name)
        if kind == "event" and any(x in name for x in ("결과", "취소", "연기")):
            continue  # 행사 결과·취소 소식은 제외

        item = {
            "name": name,
            "period": period,
            "org": target["org"],
            "dept": target["dept"],
            "boardUrl": abs_href,
            "src": "auto",          # 자동 수집 표시 (매 실행마다 교체)
            "_committee": cid,
        }
        if kind == "event":
            item["kind"] = "event"
        items.append(item)
        if len(items) >= 8:
            break

    return items


# 지원대상/자격 줄 추출 패턴
TARGET_PAT = re.compile(
    r"(?:지원|신청|모집|응모|참가|참여|공모)\s*(?:대상|자격)\s*[:：]?\s*([^\n]{2,70})")
# 접수/공모/행사 기간이 적힌 줄 탐지
PERIOD_LINE_PAT = re.compile(r"(?:접수|신청|공모|모집|응모|참가|행사|운영)\s*기간")


def enrich_details(page, items: list[dict]) -> list[dict]:
    """개별 공고 게시글을 열어 접수기간·지원대상을 보충한다 (best-effort)."""
    n_p = n_t = 0
    for item in items:
        if item.get("period") and item.get("target"):
            continue
        url = item.get("boardUrl") or ""
        if not url.startswith("http"):
            continue
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(700)
            body = page.evaluate(
                "() => document.body ? document.body.innerText.slice(0, 9000) : ''")
        except Exception:
            continue
        lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
        if not item.get("period"):
            for ln in lines:
                if PERIOD_LINE_PAT.search(ln):
                    # '2026. 6. 1.' 처럼 점 뒤 공백이 있는 표기도 인식되게 정리
                    ln2 = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", ln)
                    p = normalize_period(ln2)
                    if p:
                        item["period"] = p
                        n_p += 1
                        break
        if not item.get("target"):
            for ln in lines:
                m = TARGET_PAT.search(ln)
                if m:
                    t = re.sub(r"\s+", " ", m.group(1)).strip(" :：·-")
                    if 2 <= len(t) <= 70:
                        item["target"] = t
                        n_t += 1
                        break
    print(f"  [상세보충] 기간 {n_p}건, 지원대상 {n_t}건 추가")
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

        # 개별 공고 게시글에서 접수기간·지원대상 보충
        try:
            results = enrich_details(page, results)
        except Exception as e:
            print(f"  [상세보충 오류] {e}", file=sys.stderr)

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
    자동 수집분(src='auto')은 매 실행마다 교체(self-cleaning).
    단, 특정 기관 게시판이 이번 실행에서 0건이면(일시 장애·타임아웃 등)
    그 기관의 기존 auto 항목은 보존한다 → 포털 장애 시 데이터 전멸 방지.
    수동 입력분(src 없음)은 항상 보존.
    """
    existing_projects = existing.get("projects", {})
    # 이번 실행에서 결과가 나온 기관(org) 집합
    fresh_orgs = {item.get("org") for item in scraped if item.get("org")}

    # 1) 기존 항목 정리:
    #    - 수동 입력분(src 없음)은 항상 보존
    #    - 이번에 결과가 없는 기관의 auto 항목도 보존(장애 대비)
    #    - 이번에 새로 받은 기관의 auto 항목은 버리고 아래서 최신본으로 교체
    result = {}
    for cid, plist in existing_projects.items():
        result[cid] = [
            p for p in plist
            if p.get("src") != "auto" or p.get("org") not in fresh_orgs
        ]

    # 2) 이번에 수집한 auto 항목 추가 (제목·URL 중복 제거)
    for item in scraped:
        cid = item.pop("_committee", "gihoek")
        result.setdefault(cid, [])
        names = {p["name"] for p in result[cid]}
        urls = {p.get("boardUrl") for p in result[cid]}
        if (item.get("name") and item.get("boardUrl")
                and item["name"] not in names
                and item["boardUrl"] not in urls):
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
