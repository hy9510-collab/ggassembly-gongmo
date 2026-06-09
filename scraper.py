"""
경기도의회 상임위원회별 공모사업 자동 수집 스크래퍼
GitHub Actions에서 매일 실행 → 공모사업 일정/projects.json 갱신
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

OUTPUT = Path(__file__).parent / "공모사업 일정" / "projects.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

TODAY_STR = date.today().strftime("%Y-%m-%d")
THIS_YEAR = date.today().year


# ──────────────────────────────────────────────────────────────
# 날짜 유틸
# ──────────────────────────────────────────────────────────────
def normalize_period(raw: str) -> str:
    """날짜 문자열을 'YYYY.MM.DD ~ YYYY.MM.DD' 형식으로 정규화."""
    dates = re.findall(r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}", raw)
    if len(dates) >= 2:
        d1 = re.sub(r"[-/]", ".", dates[0])
        d2 = re.sub(r"[-/]", ".", dates[1])
        p1 = d1.split(".")
        p2 = d2.split(".")
        if len(p1) == 3 and len(p2) == 3:
            return (f"{p1[0]}.{p1[1].zfill(2)}.{p1[2].zfill(2)} ~ "
                    f"{p2[0]}.{p2[1].zfill(2)}.{p2[2].zfill(2)}")
    return ""


def abs_url(base: str, href: str) -> str:
    """상대 URL → 절대 URL 변환."""
    if not href:
        return ""
    if href.startswith("http"):
        return href
    from urllib.parse import urljoin
    return urljoin(base, href)


def safe_get(url: str, **kwargs) -> requests.Response | None:
    try:
        r = SESSION.get(url, timeout=15, **kwargs)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"  [오류] {url} → {e}", file=sys.stderr)
        return None


# ──────────────────────────────────────────────────────────────
# 기관별 파서 정의
# board_id : 상임위 id
# url      : 게시판 URL
# parser   : 함수(response) → list of dict
# ──────────────────────────────────────────────────────────────

# 공통 helper: 일반 테이블형 게시판 파싱
def parse_table_board(r: requests.Response, base_url: str,
                      title_sel: str, date_sel: str,
                      link_sel: str, period_in_detail: bool = False) -> list[dict]:
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    rows = soup.select("table tbody tr")
    for row in rows:
        title_el = row.select_one(title_sel) if title_sel else None
        link_el  = row.select_one(link_sel)  if link_sel  else title_el
        date_el  = row.select_one(date_sel)  if date_sel  else None

        if not title_el:
            continue
        name = title_el.get_text(strip=True)
        if not name or name in ("제목", "번호", ""):
            continue

        href = ""
        if link_el:
            href = abs_url(base_url, link_el.get("href", ""))

        period = ""
        if date_el:
            period = normalize_period(date_el.get_text(strip=True))

        items.append({"name": name, "period": period, "boardUrl": href or base_url})
    return items


# ── 경기문화재단 ──────────────────────────────────────────────
def fetch_ggcf() -> list[dict]:
    url = "https://www.ggcf.kr/boards/businessNotices/articles"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for a in soup.select("ul.board-list li a, table tbody tr td.subject a, .list-title a, a[href*='/articles/']"):
        name = a.get_text(strip=True)
        if not name or len(name) < 5:
            continue
        href = abs_url(url, a.get("href", ""))
        # 상세 페이지에서 기간 추출
        period = ""
        detail = safe_get(href) if href else None
        if detail:
            d_soup = BeautifulSoup(detail.text, "html.parser")
            text = d_soup.get_text(" ")
            period = normalize_period(text)
        items.append({
            "name": name, "period": period,
            "org": "경기문화재단", "dept": "문화예술본부",
            "boardUrl": href or url,
            "_committee": "munhwa"
        })
        if len(items) >= 10:
            break
    return items


# ── 경기도사회서비스원 ────────────────────────────────────────
def fetch_ggss() -> list[dict]:
    url = "https://www.ggss.or.kr/bbs/?bid=notice"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for row in soup.select("table tbody tr"):
        a = row.select_one("td.subject a, td a")
        if not a:
            continue
        name = a.get_text(strip=True)
        if not name or len(name) < 4:
            continue
        href = abs_url(url, a.get("href", ""))
        date_tds = row.select("td")
        period = ""
        for td in date_tds:
            p = normalize_period(td.get_text())
            if p:
                period = p
                break
        items.append({
            "name": name, "period": period,
            "org": "경기도사회서비스원", "dept": "서비스지원팀",
            "boardUrl": href or url,
            "_committee": "bokji"
        })
        if len(items) >= 5:
            break
    return items


# ── 경기연구원 ────────────────────────────────────────────────
def fetch_gri() -> list[dict]:
    url = "https://www.gri.re.kr/web/contents/notice.do"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for row in soup.select("table tbody tr"):
        a = row.select_one("td.tit a, td a")
        if not a:
            continue
        name = a.get_text(strip=True)
        if not name or len(name) < 4:
            continue
        href = abs_url(url, a.get("href", ""))
        tds = row.select("td")
        period = ""
        for td in tds:
            p = normalize_period(td.get_text())
            if p:
                period = p
                break
        items.append({
            "name": name, "period": period,
            "org": "경기연구원", "dept": "연구기획실",
            "boardUrl": href or url,
            "_committee": "gihoek"
        })
        if len(items) >= 5:
            break
    return items


# ── 경기도교육연구원 ──────────────────────────────────────────
def fetch_gie() -> list[dict]:
    url = "https://www.gie.re.kr/board/noticeList.do"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for row in soup.select("table tbody tr"):
        a = row.select_one("td a")
        if not a:
            continue
        name = a.get_text(strip=True)
        if not name or len(name) < 4:
            continue
        href = abs_url(url, a.get("href", ""))
        tds = row.select("td")
        period = ""
        for td in tds:
            p = normalize_period(td.get_text())
            if p:
                period = p
                break
        items.append({
            "name": name, "period": period,
            "org": "경기도교육연구원", "dept": "연구기획부",
            "boardUrl": href or url,
            "_committee": "gyoyuk1"
        })
        if len(items) >= 5:
            break
    return items


# ── 경기도경제과학진흥원 ──────────────────────────────────────
def fetch_egbiz() -> list[dict]:
    url = "https://egbiz.or.kr/sp/supportPrjCatList.do"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    # 목록의 각 링크 찾기
    for a in soup.select("a[href*='supportPrjDetail'], a[href*='Detail'], .list-item a, table tbody tr td a"):
        name = a.get_text(strip=True)
        if not name or len(name) < 4:
            continue
        href = abs_url(url, a.get("href", ""))
        # 상위 tr에서 기간 찾기
        tr = a.find_parent("tr")
        period = ""
        if tr:
            period = normalize_period(tr.get_text())
        items.append({
            "name": name, "period": period,
            "org": "경기도경제과학진흥원", "dept": "기업성장팀",
            "boardUrl": href or url,
            "_committee": "mirae"
        })
        if len(items) >= 5:
            break
    return items


# ── 경기도농업기술원 ──────────────────────────────────────────
def fetch_nongup() -> list[dict]:
    url = "https://www.nongup.gg.go.kr"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    # 공모/공고 링크 찾기
    for a in soup.select("a"):
        text = a.get_text(strip=True)
        if "공모" in text and len(text) > 6:
            href = abs_url(url, a.get("href", ""))
            items.append({
                "name": text, "period": "",
                "org": "경기도농업기술원", "dept": "기술보급과",
                "boardUrl": href or url,
                "_committee": "nonghae"
            })
        if len(items) >= 3:
            break
    return items


# ── 경기테크노파크 ────────────────────────────────────────────
def fetch_gtp() -> list[dict]:
    url = "https://www.gtp.or.kr"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for a in soup.select("a"):
        text = a.get_text(strip=True)
        if ("공모" in text or "모집" in text) and len(text) > 6:
            href = abs_url(url, a.get("href", ""))
            items.append({
                "name": text, "period": "",
                "org": "경기테크노파크", "dept": "기업지원팀",
                "boardUrl": href or url,
                "_committee": "mirae"
            })
        if len(items) >= 3:
            break
    return items


# ── 경기평생교육진흥원 ────────────────────────────────────────
def fetch_gill() -> list[dict]:
    url = "https://www.gill.or.kr"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for a in soup.select("a"):
        text = a.get_text(strip=True)
        if ("공모" in text or "모집" in text) and len(text) > 6:
            href = abs_url(url, a.get("href", ""))
            items.append({
                "name": text, "period": "",
                "org": "경기도평생교육진흥원", "dept": "평생교육지원팀",
                "boardUrl": href or url,
                "_committee": "yeoseong"
            })
        if len(items) >= 3:
            break
    return items


# ── 경기복지재단 ──────────────────────────────────────────────
def fetch_ggwf() -> list[dict]:
    # 여러 가능한 게시판 URL 시도
    candidates = [
        "https://www.ggwf.or.kr/board/list.do?menuId=",
        "https://www.ggwf.or.kr",
    ]
    for url in candidates:
        r = safe_get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        items = []
        for a in soup.select("a"):
            text = a.get_text(strip=True)
            if ("공모" in text or "모집" in text) and len(text) > 6:
                href = abs_url(url, a.get("href", ""))
                items.append({
                    "name": text, "period": "",
                    "org": "경기복지재단", "dept": "복지정책본부",
                    "boardUrl": href or url,
                    "_committee": "bokji"
                })
            if len(items) >= 3:
                break
        if items:
            return items
    return []


# ──────────────────────────────────────────────────────────────
# 기존 JSON 로드 / 병합 / 저장
# ──────────────────────────────────────────────────────────────
def load_existing() -> dict:
    if OUTPUT.exists():
        with open(OUTPUT, encoding="utf-8") as f:
            return json.load(f)
    return {"projects": {}}


def merge(existing: dict, scraped: list[dict]) -> dict:
    """
    스크래핑 결과를 상임위별로 분류하여 병합합니다.
    - 새 항목 추가 / 기존 항목 기간·URL 갱신
    - 수동 입력 항목은 삭제하지 않음
    """
    result = {k: list(v) for k, v in existing.get("projects", {}).items()}

    for item in scraped:
        cid = item.pop("_committee", "gihoek")
        if cid not in result:
            result[cid] = []

        existing_names = {p["name"] for p in result[cid]}
        if item["name"] not in existing_names:
            # boardUrl이 유효한 경우만 추가
            if item.get("boardUrl") and item.get("name"):
                result[cid].append(item)
        else:
            for p in result[cid]:
                if p["name"] == item["name"]:
                    if item.get("period"):
                        p["period"] = item["period"]
                    if item.get("boardUrl") and "main.do" not in item["boardUrl"]:
                        p["boardUrl"] = item["boardUrl"]
                    break

    return result


def save(projects: dict) -> None:
    payload = {
        "updated": TODAY_STR,
        "source": "각 기관 공모 게시판 자동 수집",
        "projects": projects,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[완료] {OUTPUT} 저장 완료 ({TODAY_STR})")


# ──────────────────────────────────────────────────────────────
FETCHERS = [
    ("경기문화재단",         fetch_ggcf),
    ("경기도사회서비스원",   fetch_ggss),
    ("경기연구원",           fetch_gri),
    ("경기도교육연구원",     fetch_gie),
    ("경기도경제과학진흥원", fetch_egbiz),
    ("경기도농업기술원",     fetch_nongup),
    ("경기테크노파크",       fetch_gtp),
    ("경기평생교육진흥원",   fetch_gill),
    ("경기복지재단",         fetch_ggwf),
]


def main() -> None:
    print(f"스크래핑 시작: {TODAY_STR}")
    existing = load_existing()
    scraped: list[dict] = []

    for name, fn in FETCHERS:
        print(f"  [{name}] 수집 중...")
        items = fn()
        print(f"    → {len(items)}건 수집")
        scraped.extend(items)

    merged = merge(existing, scraped)
    save(merged)
    total = sum(len(v) for v in merged.values())
    print(f"총 {total}건 유지/갱신")


if __name__ == "__main__":
    main()
