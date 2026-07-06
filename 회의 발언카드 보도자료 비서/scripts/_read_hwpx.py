# -*- coding: utf-8 -*-
"""hwpx 파일 본문/구조 확인용 (양식 분석)"""
import sys
import os

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from hwpx import HwpxDocument

for p in sys.argv[1:]:
    print("=" * 64)
    print("FILE:", os.path.basename(p))
    if not os.path.exists(p):
        print("[파일 없음]")
        continue
    try:
        d = HwpxDocument.open(p)
        print("문단수:", len(d.paragraphs))
        print("--- 본문(export_text) ---")
        print(d.export_text())
    except Exception as e:
        print("[오류]", repr(e))
