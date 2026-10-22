"""데이터 전체 갱신 (패치 후 실행): update.bat 이 이 스크립트를 부른다.

Workerman(노드·집·일꾼) / 인벤·코덱스(레시피·가공·아이템 이름)를 다시 받아 data/build/planner.json 을 새로 만든다.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STEPS = [
    ("Workerman 노드·집·일꾼 데이터", ["scripts/fetch_workerman.py"]),
    ("인벤 레시피", ["scripts/scrape_inven_recipes.py", "--refresh"]),
    ("코덱스 레시피·가공·아이템 이름 (1~2분)", ["scripts/scrape_codex_recipes.py", "--refresh"]),
    ("대체 재료 그룹 (새 그룹만 받음)", ["scripts/scrape_codex_groups.py"]),
    ("집 구매 가이드 시트 (비고)", ["scripts/fetch_house_guide.py"]),
    ("데이터 합치기", ["scripts/build_data.py"]),
    ("자동 조합식 '못 찾음' 기록 초기화", ["-c", "import autorecipe; autorecipe.prune_misses()"]),
]


def main():
    for n, (title, args) in enumerate(STEPS, 1):
        print(f"\n[{n}/{len(STEPS)}] {title}", flush=True)
        r = subprocess.run([sys.executable, "-X", "utf8", *args], cwd=ROOT)
        if r.returncode:
            print("\n갱신 중 오류가 났습니다. 위 메시지를 확인하세요. data/build/planner.json 은 마지막 성공 상태 그대로입니다.")
            return 1
    print("\n완료. 서버가 켜져 있으면 껐다가 run.bat 으로 다시 켜세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
