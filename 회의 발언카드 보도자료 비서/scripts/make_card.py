# -*- coding: utf-8 -*-
"""
발언 카드 생성기
- basic 스타일: 방식 A(주제별)/B(전체) — 파란 단순 카드
- news 스타일: 카드뉴스 (사진 히어로 + 부서/기관 박스), 정당색 2버전(민주/국힘)
- 캡처 이미지는 base64로 삽입, 카드별 'PNG 저장' 버튼 포함.

사용 예:
  python scripts/make_card.py output/cards/조미자_행정사무감사_card.json            # basic
  python scripts/make_card.py output/cards/조미자_카드뉴스_card.json --style news    # 카드뉴스(민주/국힘 2버전)
"""
import argparse
import base64
import json
import mimetypes
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FONT = ('<link rel="stylesheet" '
        'href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">')

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:#eceef0;font-family:Pretendard,'Malgun Gothic',sans-serif;padding:28px;
     display:flex;flex-wrap:wrap;gap:28px;align-items:flex-start}
.card-wrap{display:flex;flex-direction:column;gap:12px}
.save-btn{padding:11px 14px;color:#fff;border:none;border-radius:10px;
          font-size:14px;font-weight:700;cursor:pointer;font-family:inherit;background:#185FA5}
.save-btn:hover{opacity:.92}
.card{width:540px;background:#fff;border-radius:20px;overflow:hidden;
      box-shadow:0 6px 24px rgba(0,0,0,.13)}
.head{background:#185FA5;color:#fff;padding:16px 24px 15px}
.head .top{display:flex;justify-content:space-between;align-items:center}
.head .brand{display:flex;align-items:center;gap:6px;font-size:12.5px;font-weight:600;color:#cfe0f3}
.head .brand b{color:#fff;font-weight:800;letter-spacing:.1px}
.head .date{font-size:12.5px;color:#cfe0f3;font-weight:500}
.head .idrow{display:flex;justify-content:space-between;align-items:baseline;margin-top:13px}
.head .idleft{display:flex;align-items:baseline;gap:9px}
.head .name{font-size:30px;font-weight:800;letter-spacing:-.7px}
.head .role{font-size:14.5px;font-weight:600;color:#eaf2fb;background:rgba(255,255,255,.16);
            padding:3px 11px;border-radius:14px}
.head .session{font-size:12px;color:#dce8f7;font-weight:500}
.shot{width:100%;display:block;aspect-ratio:4/3;object-fit:cover;object-position:50% 22%;background:#222}
.shot.ph{display:flex;align-items:center;justify-content:center;color:#9aa;
         background:#e8eaec;font-size:15px;aspect-ratio:4/3}
.body{padding:22px 24px 22px}
.agency{display:inline-block;font-size:12px;font-weight:700;color:#185FA5;background:#eaf1f8;
        padding:3px 10px;border-radius:20px;margin-bottom:9px;letter-spacing:.2px}
.agency.small{background:none;padding:0;margin-bottom:5px;font-size:11.5px}
.topic{font-size:25px;font-weight:800;line-height:1.32;color:#16314a;margin-bottom:18px;letter-spacing:-.5px;word-break:keep-all}
.points{list-style:none;display:flex;flex-direction:column;gap:12px}
.points li{display:flex;gap:10px;font-size:16px;line-height:1.55;color:#333;word-break:keep-all}
.bullet{color:#185FA5;font-weight:800;flex-shrink:0;font-size:17px}
.block{border-left:4px solid #185FA5;padding:2px 0 2px 14px;margin-bottom:18px}
.topic-b{font-size:17.5px;font-weight:700;margin-bottom:9px;color:#185FA5;line-height:1.35;letter-spacing:-.3px;word-break:keep-all}
.points.small{gap:7px}
.points.small li{font-size:14.5px;color:#555;gap:8px;line-height:1.5}
.points.small .bullet{font-size:14px;color:#aab}
@media print{body{background:#fff;padding:0;gap:0}.card{box-shadow:none;page-break-inside:avoid;margin:0 auto 12px}.save-btn{display:none}}
"""

SAVE_JS = """
function saveCard(btn){
  var card = btn.parentElement.querySelector('.card');
  var name = (card.getAttribute('data-name') || '발언카드').replace(/\\s+/g,'');
  var label = btn.textContent; btn.textContent = '이미지 만드는 중…'; btn.disabled = true;
  html2canvas(card, {scale:2, useCORS:true, backgroundColor:'#ffffff'}).then(function(c){
    var a = document.createElement('a');
    a.href = c.toDataURL('image/png'); a.download = name + '.png'; a.click();
    btn.textContent = label; btn.disabled = false;
  }).catch(function(e){ alert('이미지 생성 실패: ' + e); btn.textContent = label; btn.disabled = false; });
}
"""

# 정당색 (카드뉴스 2버전)
# main=강조(의원명·마크·헤더), accent=부서 라벨(중립 네이비), chip=소관기관 라벨, h1/bg=배경
PARTY_COLORS = {
    "더불어민주당": {"main": "#152484", "ink": "#16205e", "accent": "#33415c", "bg": "#e7edf7", "chip": "#dbe3f4",
                  "h1": "#eef2fa", "title": "#1a2340", "tag": "민주"},
    "국민의힘": {"main": "#C8203C", "ink": "#2b2b30", "accent": "#33415c", "bg": "#fbeef1", "chip": "#f6dde0",
              "h1": "#fdf1f2", "title": "#3a1518", "tag": "국힘"},
}


def img_to_data_uri(path):
    if not path:
        return None
    if not os.path.isabs(path):
        path = os.path.join(BASE, path)
    if not os.path.exists(path):
        print(f"[주의] 이미지를 찾을 수 없습니다(자리표시로 대체): {path}")
        return None
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_points(points, small=False):
    cls = "points small" if small else "points"
    lis = "\n".join(
        f'<li><span class="bullet">{"–" if small else "•"}</span><span>{esc(p)}</span></li>'
        for p in points
    )
    return f'<ul class="{cls}">{lis}</ul>'


def shot_html(image_uri):
    if image_uri:
        return f'<img class="shot" src="{image_uri}" alt="발언 장면"/>'
    return '<div class="shot ph">발언 장면 캡처가 들어갈 자리</div>'


def agency_tag(t, color=None, small=False):
    """행정사무감사 등에서 주제별 피감기관 표시(데이터에 agency가 있을 때만)."""
    ag = t.get("agency")
    if not ag:
        return ""
    cls = "agency small" if small else "agency"
    style = f' style="color:{color}"' if color else ""
    return f'<div class="{cls}"{style}>{esc(ag)}</div>'


def build_card(meta, topics, image_uri, mode):
    role = meta.get("member_role", "")
    if mode.upper() == "A":
        t = topics[0]
        # 긴 제목은 글자 크기를 줄여 한두 줄 안에 자연스럽게 들어가게
        tl = len(t["title"])
        tsize = 25 if tl <= 16 else (22 if tl <= 24 else 19)
        body = (f'{agency_tag(t)}'
                f'<h2 class="topic" style="font-size:{tsize}px">{esc(t["title"])}</h2>'
                f'{render_points(t["points"])}')
    else:
        colors = ["#185FA5", "#1f8a4c", "#7b3fb5", "#c2410c", "#0e7490"]
        body = "".join(
            f'<div class="block" style="border-left-color:{colors[i % len(colors)]}">'
            f'{agency_tag(t, colors[i % len(colors)], small=True)}'
            f'<p class="topic-b" style="color:{colors[i % len(colors)]}">{esc(t["title"])}</p>'
            f'{render_points(t["points"], small=True)}</div>'
            for i, t in enumerate(topics)
        )
    role_html = f'<span class="role">{esc(role)}</span>' if role else ""
    session_html = f'<span class="session">{esc(meta.get("session",""))}</span>' if meta.get("session") else ""
    cmte_html = f' · {esc(meta.get("committee",""))}' if meta.get("committee") else ""
    # 1줄: 의회마크+경기도의회+상임위 / 날짜   2줄: 이름+직책 / 세션
    head = (f'<div class="head">'
            f'<div class="top"><span class="brand">{mark_html(20)}'
            f'<b>{esc(meta.get("council","경기도의회"))}</b>{cmte_html}</span>'
            f'<span class="date">{esc(meta.get("date",""))}</span></div>'
            f'<div class="idrow"><span class="idleft"><span class="name">{esc(meta["member"])}</span>{role_html}</span>'
            f'{session_html}</div>'
            f'</div>')
    card = (f'<div class="card" data-name="{esc(meta["member"])}_{esc(meta.get("committee",""))}">'
            f'{head}'
            f'{shot_html(image_uri)}'
            f'<div class="body">{body}</div>'
            f'</div>')
    return (f'<div class="card-wrap">{card}'
            f'<button class="save-btn" onclick="saveCard(this)">📷 이미지로 저장 (PNG)</button></div>')


def hex_rgba(h, a):
    h = h.lstrip("#")
    return f"rgba({int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)},{a})"


MARK_PATH = os.path.join(BASE, "assets", "경기도의회_마크.png")


def mark_html(size=22):
    """경기도의회 공식 마크(홈페이지 제공 이미지, assets/에 저장해 재사용)."""
    uri = img_to_data_uri(MARK_PATH)
    if not uri:
        return ""
    return (f'<img src="{uri}" alt="경기도의회 마크" '
            f'style="width:{size}px;height:{size}px;object-fit:contain;flex-shrink:0;display:block;"/>')


def build_card_news(d, party):
    c = PARTY_COLORS.get(party, PARTY_COLORS["더불어민주당"])
    img = img_to_data_uri(d.get("default_image") or d.get("image"))
    # 기본 카드와 같은 구조: 정당색 헤더 띠 → 전체 폭 사진 → 내용
    if img:
        photo = (f'<img src="{img}" alt="발언 장면" style="width:100%;display:block;'
                 f'aspect-ratio:4/3;object-fit:cover;object-position:50% 22%;background:#222;"/>')
    else:
        photo = ('<div style="width:100%;aspect-ratio:4/3;display:flex;align-items:center;'
                 'justify-content:center;color:#9aa;background:#e8eaec;font-size:15px;">'
                 '발언 장면 캡처가 들어갈 자리</div>')

    gh = ""
    if d.get("layout") == "topic":
        # 주제별: (피감기관 있으면 태그) + 주제 헤드라인 + 발언 (왼쪽 컬러 막대)
        for t in d.get("topics", []):
            pts = "".join(
                f'<li style="font-size:13.5px;line-height:1.55;color:#1a1a1a;font-weight:500;word-break:keep-all;">{esc(p)}</li>'
                for p in t.get("points", [])
            )
            agency = t.get("agency")
            agency_html = (f'<div style="display:inline-block;font-size:11px;font-weight:700;color:#fff;'
                           f'background:{c["accent"]};padding:2px 9px;border-radius:12px;margin-bottom:7px;">'
                           f'{esc(agency)}</div>') if agency else ""
            gh += (f'<div style="background:#fff;border-radius:13px;padding:13px 16px;'
                   f'border-left:5px solid {c["accent"]};box-shadow:0 1px 5px rgba(0,0,0,.07);">'
                   f'{agency_html}'
                   f'<div style="font-size:15.5px;font-weight:800;color:{c["ink"]};margin-bottom:8px;'
                   f'letter-spacing:-.3px;">{esc(t.get("title",""))}</div>'
                   f'<ul style="margin:0;padding:0;list-style:none;display:flex;flex-direction:column;'
                   f'gap:6px;">{pts}</ul></div>')
    else:
        # 부서/기관별: 왼쪽 라벨 + 발언
        for g in d.get("groups", []):
            cat = g.get("category", "")
            is_dept = cat.replace(" ", "").startswith("경기도부") or "부서" in cat
            chip_bg = c["accent"] if is_dept else c["chip"]
            chip_fg = "#fff" if is_dept else c["accent"]
            gh += (f'<div style="font-size:12px;font-weight:700;color:{c["ink"]};'
                   f'margin:6px 2px -2px;letter-spacing:.5px;">▌{esc(cat)}</div>')
            for it in g.get("items", []):
                pts = "".join(
                    f'<li style="font-size:13.5px;line-height:1.5;color:#1a1a1a;font-weight:500;'
                    f'display:flex;gap:6px;word-break:keep-all;"><span style="color:{c["accent"]};font-weight:800;flex-shrink:0;">•</span>'
                    f'<span>{esc(p)}</span></li>'
                    for p in it.get("points", [])
                )
                gh += (f'<div style="background:#fff;border-radius:13px;padding:12px 14px;display:flex;'
                       f'gap:11px;align-items:center;box-shadow:0 1px 5px rgba(0,0,0,.07);">'
                       f'<div style="flex-shrink:0;width:84px;text-align:center;font-size:11.5px;font-weight:700;'
                       f'color:{chip_fg};background:{chip_bg};border-radius:9px;padding:8px 5px;line-height:1.25;'
                       f'word-break:keep-all;">{esc(it.get("label",""))}</div>'
                       f'<ul style="margin:0;padding:0;list-style:none;display:flex;flex-direction:column;'
                       f'gap:5px;flex:1;min-width:0;">{pts}</ul></div>')

    # 헤더 띠 (기본 카드와 동일 구성): 1줄 마크+의회·상임위/날짜, 2줄 이름+직책배지/회의명
    cmte = f' · {esc(d.get("committee",""))}' if d.get("committee") else ""
    role_html = (f'<span style="font-size:14.5px;font-weight:600;color:#f2f6fb;'
                 f'background:rgba(255,255,255,.16);padding:3px 12px;border-radius:14px;">'
                 f'{esc(d["member_role"])}</span>') if d.get("member_role") else ""
    title_r = d.get("news_title") or d.get("session") or ""
    hero = (f'<div style="background:{c["main"]};color:#fff;padding:16px 22px 15px;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;">'
            f'<span style="display:flex;align-items:center;gap:7px;font-size:12.5px;font-weight:600;color:#dbe6f5;">'
            f'{mark_html(20)}<b style="color:#fff;font-weight:800;">{esc(d.get("council","경기도의회"))}</b>{cmte}</span>'
            f'<span style="font-size:12.5px;color:#dbe6f5;font-weight:500;">{esc(d.get("date",""))}</span></div>'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:13px;">'
            f'<span style="display:flex;align-items:baseline;gap:10px;">'
            f'<span style="font-size:30px;font-weight:800;letter-spacing:-.7px;word-break:keep-all;">{esc(d["member"])}</span>{role_html}</span>'
            f'<span style="font-size:12px;color:#e2eaf6;font-weight:500;word-break:keep-all;text-align:right;">{esc(title_r)}</span></div>'
            f'</div>'
            f'{photo}')

    card = (f'<div class="card" data-name="{esc(d["member"])}_카드뉴스_{c["tag"]}" '
            f'style="width:470px;background:{c["bg"]};border-radius:14px;overflow:hidden;box-shadow:0 6px 24px rgba(0,0,0,.15);">'
            f'{hero}'
            f'<div style="padding:18px 16px 20px;display:flex;flex-direction:column;gap:12px;">{gh}</div>'
            f'</div>')
    return (f'<div class="card-wrap">{card}'
            f'<button class="save-btn" style="background:{c["main"]};" onclick="saveCard(this)">📷 이미지로 저장 (PNG)</button></div>')


def wrap_html(title, cards):
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title>'
            f'{FONT}<style>{CSS}</style></head><body>{"".join(cards)}'
            f'<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>'
            f'<script>{SAVE_JS}</script></body></html>')


def main():
    ap = argparse.ArgumentParser(description="발언 카드 생성기")
    ap.add_argument("data", help="card_data.json 경로")
    ap.add_argument("--out", default=None, help="출력 HTML 경로(basic 전용)")
    ap.add_argument("--mode", default=None, choices=["A", "B", "a", "b"],
                    help="basic 방식 (A=주제별, B=전체)")
    ap.add_argument("--style", default=None, choices=["basic", "news"],
                    help="카드 스타일 (basic=파란카드, news=카드뉴스). 미지정 시 JSON의 style 또는 basic")
    ap.add_argument("--each", action="store_true",
                    help="news 스타일에서 발언 주제마다 카드를 1장씩 따로 생성")
    args = ap.parse_args()

    data_path = args.data if os.path.isabs(args.data) else os.path.join(BASE, args.data)
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 결과물은 입력 JSON과 같은 폴더에 생성 (같은 건의 파일을 한 곳에 모으기 위함)
    outdir = os.path.dirname(data_path)
    os.makedirs(outdir, exist_ok=True)
    style = (args.style or data.get("style", "basic")).lower()

    if style == "news":
        each = args.each or data.get("each")
        # --each: 발언 주제마다 카드 1장씩 / 아니면 전체 1장
        if each and data.get("topics"):
            units = [({**data, "topics": [t]}, f"_{i+1}") for i, t in enumerate(data["topics"])]
        else:
            units = [(data, "")]
        outs = []
        for party in ("더불어민주당", "국민의힘"):
            tag = PARTY_COLORS[party]["tag"]
            for ud, idx in units:
                card = build_card_news(ud, party)
                html = wrap_html(f"발언 카드뉴스 - {ud.get('member','')} ({party})", [card])
                lay = "주제별" if ud.get("layout") == "topic" else "부서별"
                safe = f'{ud.get("member","의원")}_카드뉴스_{lay}{idx}_{tag}'.replace(" ", "")
                out = os.path.join(outdir, f"card_{safe}.html")
                with open(out, "w", encoding="utf-8") as f:
                    f.write(html)
                outs.append(os.path.basename(out))
        print(f"[완료] 카드뉴스 {len(outs)}개 생성: {', '.join(outs)}")
        return

    # basic
    mode = str(args.mode or data.get("mode", "A")).upper()
    topics = data.get("topics", [])
    if not topics:
        print("[오류] topics가 비어 있습니다.")
        sys.exit(1)
    safe_base = f'{data.get("member","의원")}_{data.get("committee","")}'.replace(" ", "")

    if mode == "A":
        # 주제별: 발언 주제마다 카드 1장씩 별도 파일로 생성
        outs = []
        for i, t in enumerate(topics, 1):
            uri = img_to_data_uri(t.get("image") or data.get("default_image"))
            html = wrap_html(f"발언 카드 - {data.get('member','')} ({t.get('title','')})",
                              [build_card(data, [t], uri, "A")])
            out = os.path.join(outdir, f"card_{safe_base}_A{i}.html")
            with open(out, "w", encoding="utf-8") as f:
                f.write(html)
            outs.append(os.path.basename(out))
        print(f"[완료] 카드(주제별) {len(outs)}장 생성: {', '.join(outs)}")
        return

    # B: 기본(전체) — 발언 주요 내용을 한 장에 모두 담은 버전
    uri = img_to_data_uri(data.get("default_image"))
    html = wrap_html(f"발언 카드 - {data.get('member','')}", [build_card(data, topics, uri, "B")])
    if args.out:
        out = args.out if os.path.isabs(args.out) else os.path.join(BASE, args.out)
    else:
        out = os.path.join(outdir, f"card_{safe_base}_B.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[완료] 카드(기본) 1장 생성: {out}")


if __name__ == "__main__":
    main()
