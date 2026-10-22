# 검은사막 노드 플래너

만들고 싶은 요리·연금품을 고르면, 정해진 공헌도 안에서 **어떤 노드에 투자하고 어느 마을 일꾼을 보내고 어떤 집을 살지**를 계산해 주는 도구입니다. 레시피를 원재료까지 펼친 뒤 혼합정수계획(MIP, HiGHS)으로 노드·경로·숙소·창고 조합을 한 번에 최적화합니다.

- **생산량 최대**(기본): 공헌도 예산 안에서 완성품마다 만들 수 있는 양을 최대로
- **공헌도 최소**: 입력한 수량을 채우는 가장 싼 조합
- 레시피 전체 전개(하위 재료 트리·원재료 합계), 노드 종류별 생산량, 거점별 집 구매 목록, 지도

## 쓰는 법

**웹 (설치 없음)** — GitHub Pages 주소로 접속. 계산은 브라우저 안에서 돌아갑니다 ([Pyodide](https://pyodide.org) + HiGHS). 처음 한 번은 계산 엔진(약 30MB)을 받느라 1분쯤 걸립니다. 입력한 내용은 그 브라우저에만 저장됩니다.

**로컬 서버 (더 빠름)** — PC에서 직접 돌리면 브라우저판보다 계산이 약 5배 빠릅니다.

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
run.bat                      # http://127.0.0.1:5173
```

## 구조

| 파일 | 역할 |
|---|---|
| `plan.py` | 데이터 불러오기, 레시피 전개, 재료 칸 만들기, 일꾼 생산량 |
| `optimize.py` | HiGHS MIP 모델 (노드·마을·경로·집·창고·생산 배율) + 그리디 시작 해 |
| `api.py` | 화면용 계산 함수 (서버판·브라우저판 공용) |
| `server.py` | 로컬 Flask 서버 |
| `web/index.html` | 화면 (단일 파일) |
| `static/` | 브라우저판 연결 코드 (`worker.js` Pyodide 워커, `bridge.js`, `webapi.py`) |
| `scripts/build_site.py` | 정적 사이트 `site/` 만들기 |
| `scripts/update_all.py` | 패치 후 데이터 전체 갱신 (`update.bat`) |

## 패치 후 데이터 갱신

1. `update.bat` 실행 (원본 데이터를 다시 받아 `data/build/planner.json` 생성)
2. 바뀐 `data/` 를 커밋하고 push → GitHub Actions(`.github/workflows/pages.yml`)가 사이트를 다시 만들어 올림

원본 데이터가 먼저 패치를 반영해야 합니다 (보통 며칠).

## 데이터 출처

- 노드·일꾼·집: [shrddr/workermanjs](https://github.com/shrddr/workermanjs)
- 레시피·가공·아이템: [BDO Codex](https://bdocodex.com/kr/), 인벤

비공식 팬 도구이며 Pearl Abyss와 관계없습니다.
