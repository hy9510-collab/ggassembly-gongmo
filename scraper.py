"""
경기도 통합공모 포털 스크래퍼
https://www.gg.go.kr/gongmo/main.do

GitHub Actions에서 매일 실행되며, 공모사업 목록을 가져와
'공모사업 일정/projects.json'을 갱신합니다.
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
    "Referer": "https://www.gg.go.kr/",
}

# ──────────────────────────────────────────────────────────────
# 소관기관 → 상임위 매핑  (기관명 포함 키워드 → committee id)
# ──────────────────────────────────────────────────────────────
ORG_MAP = {
    # 기획재정
    "기획조정실": "gihoek", "공정발전": "gihoek", "평화협력": "gihoek",
    "감사위원회": "gihoek", "경기연구원": "gihoek",
    # 경제노동
    "노동국": "gyeongje", "경제실": "gyeongje", "사회혁신경제": "gyeongje",
    "신용보증": "gyeongje", "킨텍스": "gyeongje", "경제자유구역": "gyeongje",
    "일자리재단": "gyeongje", "시장상권": "gyeongje", "사회적경제": "gyeongje",
    # 안전행정
    "안전관리실": "anjeun", "자치행정": "anjeun", "소방": "anjeun",
    "자원봉사": "anjeun", "자치경찰": "anjeun",
    # 문화체육관광
    "문화체육": "munhwa", "관광공사": "munhwa", "문화재단": "munhwa",
    "아트센터": "munhwa", "도자재단": "munhwa", "콘텐츠진흥": "munhwa",
    "체육회": "munhwa", "장애인체육": "munhwa", "DMZ": "munhwa",
    "남한산성": "munhwa",
    # 농정해양
    "농수산": "nonghae", "축산": "nonghae", "농업기술원": "nonghae",
    "산림": "nonghae", "해양수산": "nonghae", "평택항만": "nonghae",
    # 보건복지
    "복지국": "bokji", "보건건강": "bokji", "복지재단": "bokji",
    "사회서비스원": "bokji", "의료원": "bokji", "보건환경": "bokji",
    # 건설교통
    "건설국": "gunseol", "교통국": "gunseol", "철도항만물류": "gunseol",
    "교통연수원": "gunseol", "교통공사": "gunseol",
    # 도시환경
    "도시주택": "dosi", "도시개발": "dosi", "기후환경": "dosi",
    "수자원": "dosi", "주택도시공사": "dosi", "GH": "dosi",
    "환경에너지진흥": "dosi",
    # 미래과학
    "AI국": "mirae", "미래성장": "mirae", "국제협력": "mirae",
    "차세대융합": "mirae", "테크노파크": "mirae", "경제과학진흥원": "mirae",
    # 여성가족
    "여성가족국": "yeoseong", "미래평생교육": "yeoseong", "이민사회": "yeoseong",
    "여성가족재단": "yeoseong", "여성비전": "yeoseong", "미래세대재단": "yeoseong",
    "평생교육진흥원": "yeoseong", "도서관": "yeoseong",
    # 의회운영
    "의회 사무처": "uiwoon", "비서실": "uiwoon", "홍보기획관": "uiwoon",
    "중앙협력": "uiwoon",
    # 교육 (기획/행정)
    "교육청": "gyoyuk1", "교육연구원": "gyoyuk1",
    "디지털인재국": "gyoyuk2", "지역교육과": "gyoyuk2",
    "행정국": "gyoyuk2", "지방공무원인사": "gyoyuk2",
}

BOARD_URL = "https://www.gg.go.kr/gongmo/main.do"

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ──────────────────────────────────────────────────────────────
# 경기도 통합공모 포털 파싱
# ──────────────────────────────────────────────────────────────
def fetch_gg_gongmo(page: int = 1) -> list[dict]:
    """경기도 통합공모 포털에서 공모 목록을 가져옵니다."""
    url = "https://www.gg.go.kr/gongmo/main.do"
    params = {"pageIndex": page, "searchCondition": "", "searchKeyword": ""}
    try:
        r = SESSION.get(url, params=params, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"[경기도 통합공모] 요청 실패: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    items = []

    # 공모 목록 테이블 행 찾기 (실제 HTML 구조에 따라 조정 필요)
    rows = soup.select("table.board_list tbody tr") or soup.select(".list_wrap li")
    for row in rows:
        try:
            title_el = row.select_one("td.subject a, .tit a, a.title")
            if not title_el:
                continue
            name = title_el.get_text(strip=True)
            # 기간 파싱 (예: "2026.06.01 ~ 2026.07.31" 형태)
            period_el = row.select_one("td.date, .period, td:nth-child(4)")
            period_raw = period_el.get_text(strip=True) if period_el else ""
            period = normalize_period(period_raw)
            if not period:
                continue

            # 기관/부서
            org_el = row.select_one("td.org, .dept, td:nth-child(3)")
            org_raw = org_el.get_text(strip=True) if org_el else "경기도청"
            org, dept = split_org_dept(org_raw)

            # 링크
            href = title_el.get("href", "")
            if href and not href.startswith("http"):
                href = "https://www.gg.go.kr" + href

            committee_id = classify(org, dept)

            items.append({
                "name": name,
                "period": period,
                "org": org,
                "dept": dept,
                "boardUrl": href or BOARD_URL,
                "_committee": committee_id,
            })
        except Exception:
            continue

    return items


def normalize_period(raw: str) -> str:
    """날짜 문자열을 'YYYY.MM.DD ~ YYYY.MM.DD' 형식으로 정규화."""
    # 숫자+점+숫자 패턴 2개 추출
    dates = re.findall(r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}", raw)
    if len(dates) >= 2:
        d1 = re.sub(r"[-/]", ".", dates[0])
        d2 = re.sub(r"[-/]", ".", dates[1])
        # 월일 2자리 패딩
        parts1 = d1.split(".")
        parts2 = d2.split(".")
        if len(parts1) == 3 and len(parts2) == 3:
            d1 = f"{parts1[0]}.{parts1[1].zfill(2)}.{parts1[2].zfill(2)}"
            d2 = f"{parts2[0]}.{parts2[1].zfill(2)}.{parts2[2].zfill(2)}"
            return f"{d1} ~ {d2}"
    return ""


def split_org_dept(raw: str) -> tuple[str, str]:
    """'기관명 / 부서명' 또는 '기관명(부서명)' 형태를 분리."""
    for sep in [" / ", " - ", "(", "/"]:
        if sep in raw:
            parts = raw.split(sep, 1)
            org = parts[0].strip().rstrip("(")
            dept = parts[1].strip().rstrip(")")
            return org, dept
    return raw.strip(), ""


def classify(org: str, dept: str) -> str:
    """기관/부서명으로 상임위 id를 추정합니다."""
    combined = org + " " + dept
    for keyword, cid in ORG_MAP.items():
        if keyword in combined:
            return cid
    # 경기도교육청 계열
    if "교육청" in combined:
        return "gyoyuk1"
    # 기본값: 기획재정
    return "gihoek"


# ──────────────────────────────────────────────────────────────
# 경기도교육청 공모 파싱
# ──────────────────────────────────────────────────────────────
def fetch_goe_gongmo() -> list[dict]:
    """경기도교육청 공모 게시판에서 목록을 가져옵니다."""
    url = "https://www.goe.go.kr/goe/bbs/board/list.do"
    params = {"bbsId": "BBSMSTR_000000000011"}  # 공모 게시판 ID (실제 확인 필요)
    try:
        r = SESSION.get(url, params=params, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"[경기도교육청] 요청 실패: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    items = []

    rows = soup.select("table tbody tr")
    for row in rows:
        try:
            title_el = row.select_one("td.subject a, a.title")
            if not title_el:
                continue
            name = title_el.get_text(strip=True)
            period_el = row.select_one("td.date")
            period_raw = period_el.get_text(strip=True) if period_el else ""
            period = normalize_period(period_raw)
            if not period:
                continue

            dept_el = row.select_one("td.dept")
            dept = dept_el.get_text(strip=True) if dept_el else ""
            committee_id = classify("경기도교육청", dept)

            items.append({
                "name": name,
                "period": period,
                "org": "경기도교육청",
                "dept": dept,
                "boardUrl": "https://www.goe.go.kr",
                "_committee": committee_id,
            })
        except Exception:
            continue

    return items


# ──────────────────────────────────────────────────────────────
# 기존 JSON 로드 → 병합 → 저장
# ──────────────────────────────────────────────────────────────
def load_existing() -> dict:
    if OUTPUT.exists():
        with open(OUTPUT, encoding="utf-8") as f:
            return json.load(f)
    return {"projects": {}}


def merge_projects(existing: dict, scraped: list[dict]) -> dict:
    """
    스크래핑 결과를 상임위별로 분류하여 기존 데이터와 병합합니다.
    - 같은 공모사업명이 이미 있으면 기간/URL 업데이트
    - 새 항목은 추가
    - 스크래핑에 없는 기존 항목은 유지 (수동 입력분 보호)
    """
    result = {k: list(v) for k, v in existing.get("projects", {}).items()}

    for item in scraped:
        cid = item.pop("_committee", "gihoek")
        if cid not in result:
            result[cid] = []

        # 중복 확인 (공모명 동일)
        existing_names = {p["name"] for p in result[cid]}
        if item["name"] not in existing_names:
            result[cid].append(item)
        else:
            # 기간·URL 갱신
            for p in result[cid]:
                if p["name"] == item["name"]:
                    p["period"] = item["period"]
                    if item["boardUrl"] != BOARD_URL:
                        p["boardUrl"] = item["boardUrl"]
                    break

    return result


def save(projects: dict, updated: str) -> None:
    payload = {
        "updated": updated,
        "source": "경기도 통합공모 포털 (https://www.gg.go.kr/gongmo/main.do) 외",
        "projects": projects,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[완료] {OUTPUT} 저장 ({updated})")


# ──────────────────────────────────────────────────────────────
def main() -> None:
    today = date.today().strftime("%Y-%m-%d")
    print(f"스크래핑 시작: {today}")

    existing = load_existing()
    scraped: list[dict] = []

    # 경기도 통합공모 1~3페이지
    for page in range(1, 4):
        items = fetch_gg_gongmo(page)
        print(f"  경기도 통합공모 p{page}: {len(items)}건")
        scraped.extend(items)
        if len(items) == 0:
            break

    # 경기도교육청
    edu_items = fetch_goe_gongmo()
    print(f"  경기도교육청: {len(edu_items)}건")
    scraped.extend(edu_items)

    merged = merge_projects(existing, scraped)
    save(merged, today)
    print(f"총 {sum(len(v) for v in merged.values())}건 유지/갱신")


if __name__ == "__main__":
    main()
