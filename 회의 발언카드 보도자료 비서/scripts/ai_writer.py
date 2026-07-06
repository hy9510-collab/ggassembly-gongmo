# -*- coding: utf-8 -*-
"""
발언 텍스트 → 카드/보도자료/보고서 데이터(JSON)를 자동 작성.

작성 엔진 우선순위 (비용 없는 쪽 우선):
 1. Claude Code CLI (이미 쓰는 구독으로 처리 — 추가 비용 없음. 1회 로그인 필요)
 2. Claude API (.secrets/api_key.txt — 종량 과금)
 3. 둘 다 없으면 MissingKeyError → 서버가 '요청서'를 만들어 채팅으로 안내
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS_DIR = os.path.join(BASE, ".secrets")
KEY_PATH = os.path.join(SECRETS_DIR, "api_key.txt")
CONF_PATH = os.path.join(SECRETS_DIR, "config.json")
DEFAULT_MODEL = "claude-opus-4-8"


class MissingKeyError(RuntimeError):
    """자동 작성 수단이 없음 — 서버는 이 예외를 받아 '요청서 모드'로 안내한다."""


def find_claude_cli():
    """Claude Code CLI 실행 파일 탐색.

    데스크톱 앱이 스토어 앱(MSIX)으로 설치된 경우 %APPDATA%\\Claude 는 앱 내부에서만
    보이는 가상 경로라서, 실제 저장소인 Packages 백킹 경로까지 함께 찾는다.
    """
    p = shutil.which("claude")
    if p:
        return p
    patterns = []
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        patterns.append(os.path.join(appdata, "Claude", "claude-code", "*", "claude.exe"))
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        patterns.append(os.path.join(localappdata, "Packages", "Claude_*",
                                     "LocalCache", "Roaming", "Claude",
                                     "claude-code", "*", "claude.exe"))
    candidates = []
    for pat in patterns:
        candidates.extend(glob.glob(pat))
    if candidates:
        def ver_key(path):
            m = re.search(r"claude-code[\\/]([\d.]+)[\\/]", path)
            return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)
        return max(candidates, key=ver_key)
    return None


def load_cli_model():
    if os.path.exists(CONF_PATH):
        try:
            return json.load(open(CONF_PATH, encoding="utf-8")).get("cli_model", "sonnet")
        except Exception:
            pass
    return "sonnet"


def load_api_key():
    if os.path.exists(KEY_PATH):
        key = open(KEY_PATH, encoding="utf-8").read().strip()
        if key:
            return key
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    raise MissingKeyError("Claude API 키가 설정되지 않았습니다.")


def load_model():
    if os.path.exists(CONF_PATH):
        try:
            return json.load(open(CONF_PATH, encoding="utf-8")).get("model", DEFAULT_MODEL)
        except Exception:
            pass
    return DEFAULT_MODEL


SCHEMA = {
    "type": "object",
    "properties": {
        "tag": {"type": "string", "description": "건 분류 짧은 태그. 예: 행정사무감사, 도정질문, 5분발언"},
        "session_label": {"type": "string", "description": "회의 세션 표기. 예: 2025년도 행정사무감사"},
        "meeting_date": {"type": "string", "description": "회의 날짜 YYYY-MM-DD. 발언록/제공된 메타에서 확인. 확인 불가면 빈 문자열"},
        "audit_org": {"type": "string", "description": "피감/소관 기관 총칭(행감일 때). 예: 문화체육관광국. 해당 없으면 빈 문자열"},
        "card_topics": {
            "type": "array", "minItems": 2,
            "description": "발언 주제별 카드 데이터. 주제 3~5개, 제목은 발언 의도가 드러나게",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "주제 헤드라인 — 의도가 분명하게. 예: 비인기 종목도 스포츠클럽 지원 대상에 포함해야"},
                    "agency": {"type": "string", "description": "해당 주제의 피감기관/담당부서. 행감 아니면 빈 문자열"},
                    "points": {"type": "array", "items": {"type": "string"},
                               "description": "개조식 발언 요점 2~4개, '~요구/~촉구/~지적/~당부' 체로"}
                },
                "required": ["title", "agency", "points"],
                "additionalProperties": False
            }
        },
        "cardnews_groups": {
            "type": "array",
            "description": "부서/기관별 카드뉴스 그룹. category는 '경기도 부서' 먼저, '소관 기관' 나중. 해당 없는 카테고리는 생략",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "'경기도 부서' 또는 '소관 기관'"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "description": "부서/기관명. 길면 띄어쓰기로 줄바꿈 유도 (예: 경기도 장애인체육회)"},
                                "points": {"type": "array", "items": {"type": "string"}}
                            },
                            "required": ["label", "points"],
                            "additionalProperties": False
                        }
                    }
                },
                "required": ["category", "items"],
                "additionalProperties": False
            }
        },
        "press_title": {"type": "string", "description": "보도자료 제목. 형식: ○○○ 의원, “핵심 주장 직접인용”"},
        "press_lead": {"type": "string", "description": "리드문단. 형식: 경기도의회 [상임위] ○○○ 의원([정당], [지역구])은 ○일(요일) [회의]에서 ~을 강조/촉구했다. 정당·지역구 미상이면 괄호 생략"},
        "press_body": {"type": "array", "items": {"type": "string"},
                       "description": "본문 문단 4~6개. 직접인용 “~” + '조 의원은 ~라고 말했다/지적했다/당부했다' 문체"},
        "report_subtitle": {"type": "string", "description": "보고서 부제. 예: 2025년도 문화체육관광위원회 행정사무감사 · 조용호 위원"},
        "report_overview": {
            "type": "object",
            "properties": {
                "회의명": {"type": "string"}, "일시": {"type": "string"},
                "의원": {"type": "string"}, "소속": {"type": "string"},
                "대상기관": {"type": "string"}
            },
            "required": ["회의명", "일시", "의원", "소속", "대상기관"],
            "additionalProperties": False
        },
        "report_topics": {
            "type": "array",
            "description": "발언 전체를 주제별로 빠짐없이 정리 (4~8개)",
            "items": {
                "type": "object",
                "properties": {
                    "no": {"type": "integer"},
                    "title": {"type": "string"},
                    "target": {"type": "string", "description": "대상 기관/부서"},
                    "summary": {"type": "string", "description": "지적·요구 요지 1~2문장"},
                    "quote": {"type": "string", "description": "실제 발언 인용 (회의록 표현 그대로)"}
                },
                "required": ["no", "title", "target", "summary", "quote"],
                "additionalProperties": False
            }
        },
        "report_conclusion": {"type": "string", "description": "종합 평가 문단. '조○○ 위원은 이번 ~에서 ①… ②… ③…을 핵심 의제로 제시하였다' 형식"}
    },
    "required": ["tag", "session_label", "meeting_date", "audit_org", "card_topics", "cardnews_groups",
                 "press_title", "press_lead", "press_body",
                 "report_subtitle", "report_overview", "report_topics", "report_conclusion"],
    "additionalProperties": False
}

SYSTEM = """너는 경기도의회 의원실의 공보 비서다. 회의록에서 추출한 의원의 실제 발언(질의·답변)을 바탕으로
발언카드·보도자료·발언요약보고서 데이터를 작성한다.

원칙:
- 발언에 없는 내용을 지어내지 않는다. 회의록에 있는 내용만 쓴다.
- 보도자료는 핵심 메시지 중심(제일 중요한 2~3개 의제), 카드는 구체적 발언까지, 보고서는 발언 전체를 빠짐없이.
- 개조식 요점은 명사형 종결(~요구, ~촉구, ~지적, ~당부, ~제안)로 간결하게.
- 직접인용은 회의록의 실제 표현을 다듬어 쓰되 취지를 바꾸지 않는다. 구어체 군더더기(어, 뭐, 좀)는 제거.
- 보도자료 문체는 경기도의회 보도자료 표준을 따른다: 제목 '○○○ 의원, "주장"', 리드 '경기도의회 [상임위] ○○○ 의원([정당], [지역구])은 ○일 …했다', 본문은 직접인용+간접서술 교차.
- 행정사무감사 건이면 각 주제에 피감기관(agency)을 반드시 기재하고, cardnews_groups를 부서별로 구성한다. '경기도 부서'를 먼저, '소관 기관'(공사·공단·출자출연기관)을 나중에.
- 답변(공무원 발언)은 맥락 파악용이다. 카드·보도자료의 주어는 항상 의원이다.
- 발언 자료가 '발언록 원문(파일)' 형태(회의 전체 원문)로 주어지면, 그 안에서 대상 의원의 발언만 정확히 찾아 작성한다. 다른 의원의 발언을 섞지 않는다."""


def _user_payload(speech_data, profile):
    return {
        "의원": speech_data["member"],
        "프로필": {
            "정당": profile.get("party", ""),
            "지역구": profile.get("region", ""),
            "직책": profile.get("role", "위원"),
            "상임위원회": profile.get("committee") or speech_data["meta"].get("committee", ""),
        },
        "회의": speech_data["meta"],
        "발언(질의·답변)": speech_data["speeches"],
    }


REQUIRED_KEYS = ["tag", "session_label", "card_topics", "press_title", "press_lead",
                 "press_body", "report_subtitle", "report_overview", "report_topics",
                 "report_conclusion"]


def _validate(content):
    missing = [k for k in REQUIRED_KEYS if k not in content]
    if missing:
        raise RuntimeError(f"AI 응답에 필수 항목 누락: {', '.join(missing)}")
    content.setdefault("audit_org", "")
    content.setdefault("cardnews_groups", [])
    return content


def _extract_json(text):
    """응답 텍스트에서 JSON 객체 추출 (코드펜스/앞뒤 설명 제거)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("AI 응답에서 JSON을 찾지 못했습니다.")
    return json.loads(text[start:end + 1])


def generate_via_cli(speech_data, profile):
    """Claude Code CLI(-p 헤드리스)로 작성 — 구독 사용량으로 처리되어 추가 비용 없음."""
    exe = find_claude_cli()
    if not exe:
        raise MissingKeyError("Claude Code CLI를 찾지 못했습니다.")
    model = load_cli_model()
    prompt = (SYSTEM
              + "\n\n[출력 규칙] 반드시 아래 JSON 스키마의 required 항목을 모두 포함하는 "
                "JSON 객체 하나만 출력한다. 코드펜스·설명 등 다른 텍스트는 절대 쓰지 않는다.\n"
              + json.dumps(SCHEMA, ensure_ascii=False)
              + "\n\n[작성 대상] 아래 회의록 발언으로 발언카드·보도자료·발언요약보고서 데이터를 작성:\n"
              + json.dumps(_user_payload(speech_data, profile or {}), ensure_ascii=False, indent=1))
    r = subprocess.run(
        [exe, "-p", "--output-format", "json", "--model", model, "--tools", ""],
        input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600)
    if r.returncode != 0 and not r.stdout.strip():
        raise RuntimeError(f"Claude Code 실행 실패: {r.stderr[-300:]}")
    try:
        envelope = json.loads(r.stdout)
        result_text = envelope.get("result", "")
        if envelope.get("is_error"):
            low = result_text.lower()
            if "logged in" in low or "/login" in low or "log in" in low:
                raise MissingKeyError(
                    "Claude Code 로그인이 필요합니다(최초 1회만). "
                    "프로젝트 폴더의 「클로드_로그인.bat」을 더블클릭 → 창에서 /login 입력 → "
                    "브라우저에서 승인하면, 이후 추가 비용 없이 자동 생성됩니다.")
            raise RuntimeError(f"Claude Code 오류: {result_text[:300]}")
    except json.JSONDecodeError:
        result_text = r.stdout
    content = _validate(_extract_json(result_text))
    content["_usage"] = {"model": f"Claude Code({model}) — 구독(추가 비용 없음)",
                         "input_tokens": 0, "output_tokens": 0}
    try:
        u = envelope.get("usage", {})
        content["_usage"]["input_tokens"] = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
        content["_usage"]["output_tokens"] = u.get("output_tokens", 0)
    except Exception:
        pass
    return content


def generate_via_api(speech_data, profile):
    """Claude API(종량 과금)로 작성 — 구조화 출력으로 스키마 보장."""
    import anthropic

    client = anthropic.Anthropic(api_key=load_api_key())
    model = load_model()
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{
            "role": "user",
            "content": ("아래 회의록 발언을 바탕으로 발언카드·보도자료·발언요약보고서 데이터를 작성해줘.\n\n"
                        + json.dumps(_user_payload(speech_data, profile or {}),
                                     ensure_ascii=False, indent=1)),
        }],
    )
    text = next(b.text for b in response.content if b.type == "text")
    content = _validate(json.loads(text))
    content["_usage"] = {
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return content


def generate(speech_data, profile=None):
    """발언 데이터 + 의원 프로필 → 콘텐츠 dict.
    Claude Code(무료) 우선, 실패 시 API 키, 둘 다 없으면 MissingKeyError."""
    profile = profile or {}
    cli_error = None
    if find_claude_cli():
        try:
            return generate_via_cli(speech_data, profile)
        except MissingKeyError as e:
            cli_error = e
        except Exception as e:
            cli_error = e
    try:
        load_api_key()
    except MissingKeyError:
        if cli_error:
            raise MissingKeyError(str(cli_error))
        raise
    return generate_via_api(speech_data, profile)


def build_case_files(content, speech_data, profile, outdir):
    """AI 콘텐츠 → 기존 make_card/make_docs가 읽는 3개 데이터 JSON 생성."""
    os.makedirs(outdir, exist_ok=True)
    member = speech_data["member"]
    meta = speech_data["meta"]
    profile = profile or {}
    role = profile.get("role", "위원")
    party = profile.get("party", "더불어민주당")
    committee = profile.get("committee") or meta.get("committee", "")
    date_dot = meta.get("date", "")

    card = {
        "mode": "B", "committee": committee, "date": date_dot,
        "session": content["session_label"], "member": member, "member_role": role,
        "party": party, "region": profile.get("region", ""),
        "default_image": profile.get("image") or None,
        "topics": [{"title": t["title"],
                    **({"agency": t["agency"]} if t.get("agency") else {}),
                    "points": t["points"]} for t in content["card_topics"]],
    }
    paths = {}
    p = os.path.join(outdir, f"{member}_card.json")
    json.dump(card, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    paths["card"] = p

    if content.get("cardnews_groups"):
        news = {
            "style": "news", "news_title": content["session_label"],
            "audit_org": content.get("audit_org", ""), "council": "경기도의회",
            "member": member, "member_role": role, "party": party,
            "date": date_dot.replace(" ", ""), "session": f"{committee} {content['tag']}",
            "committee": committee,
            "default_image": profile.get("image") or None,
            "groups": content["cardnews_groups"],
        }
        p = os.path.join(outdir, f"{member}_카드뉴스_card.json")
        json.dump(news, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        paths["cardnews"] = p

    press = {
        "member": member, "tag": content["tag"], "org": "경기도의회",
        "title": content["press_title"], "lead": content["press_lead"],
        "body": content["press_body"], "date_line": date_dot,
        "contact": "※ 본 보도자료는 회의 발언을 바탕으로 작성된 초안입니다.",
    }
    p = os.path.join(outdir, f"{member}_보도자료_data.json")
    json.dump(press, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    paths["press"] = p

    report = {
        "member": member, "tag": content["tag"], "title": "발언 요약 보고서",
        "subtitle": content["report_subtitle"],
        "date_line": f"작성일: {date_dot}",
        "overview": content["report_overview"],
        "topics": content["report_topics"],
        "conclusion": content["report_conclusion"],
    }
    p = os.path.join(outdir, f"{member}_보고서_data.json")
    json.dump(report, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    paths["report"] = p
    return paths
