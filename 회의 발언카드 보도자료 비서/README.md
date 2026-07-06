# 회의 발언카드 · 보도자료 비서

경기도의회 회의 영상에서 **발언 장면을 캡처**하고, **발언 카드**와 **보도자료**를 만드는 자동화 시스템입니다.

## 전체 흐름

```
[1] 영상 링크   →  scripts/download_video.py   →  work/ 에 영상 저장
[2] 장면 추출   →  scripts/extract_best_frames.py → work/frames/ 에 캡처 후보
[3] 캡처 선택   →  사람이 후보 중 가장 좋은 컷 선택
[4] 내용 정리   →  회의록/자막/음성으로 발언 요약(개조식)
[5] 카드 제작   →  scripts/make_card.py        →  output/cards/
[6] 보도자료    →  보도자료 초안 작성          →  output/press/
```

## 폴더 구조

| 폴더 | 용도 |
|------|------|
| `input/` | 영상 링크 메모, 회의록·자막 파일을 넣는 곳 |
| `work/` | 다운로드한 영상, 추출 프레임 등 작업용 임시 파일 |
| `work/frames/` | 추출된 발언 장면 캡처 후보 |
| `output/cards/` | 완성된 발언 카드 (HTML / PNG) |
| `output/press/` | 완성된 보도자료 |
| `scripts/` | 자동화 파이썬 스크립트 |
| `templates/` | 발언 카드 디자인 템플릿 |
| `reference/` | 참고 보도자료 (문체·형식 학습용) |

## 사용법 (요약)

가상환경 파이썬: `.venv\Scripts\python.exe`

```powershell
# 1) 영상 다운로드 (구간 지정 가능)
.venv\Scripts\python.exe scripts\download_video.py "<영상주소>" --start 00:12:30 --end 00:25:00

# 2) 발언 장면 후보 추출 (눈 뜨고 발언/정면 장면 위주)
.venv\Scripts\python.exe scripts\extract_best_frames.py "work\<영상파일>.mp4" --topk 24

# 3) 발언 카드 생성 (데이터 JSON → HTML 카드)
.venv\Scripts\python.exe scripts\make_card.py output\cards\card_data.json
```

## 발언 카드에 들어가는 항목

- 회의 날짜 · 상임위원회 · 의원 이름
- 발언 주제
- 발언 내용 요약 (개조식)
- 발언 장면 캡처 사진
- 회기 정보 (예: 제○○○회 정례회 제○차)

## 카드 제작 방식 두 가지

- **방식 A (주제별)**: 한 주제를 한 장에 깊이 있게
- **방식 B (전체)**: 한 의원의 여러 발언을 한 장에 요약

---
*경기도의회 의정활동 지원용 · 생성형 AI 보조 제작*
