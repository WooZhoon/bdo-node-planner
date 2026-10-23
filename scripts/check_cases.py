"""저장해 둔 계산 요청(tests/cases/*.json)을 다시 돌려 결과가 이상하지 않은지 확인

  .venv/Scripts/python -X utf8 scripts/check_cases.py [--time 120] [케이스 이름 ...]

케이스 만들기: PLANNER_DUMP=tests/cases 로 서버를 켜고 화면에서 계산 → tests/cases/last_plan.json 을 이름 바꿔 둠
(개인 설정이 들어 있어 tests/cases 는 저장소에 올리지 않음)

확인하는 것 (생산량 최대)
  - 노드로 얻을 수 있는데 부족한 재료가 없음 (손채집 재료 제외)
  - 수량을 줄인 항목은 병목 재료가 있음 ('공헌도 모자람'인데 공헌도가 10 넘게 남으면 이상)
  - 병목 때문에 줄인 항목이 있으면, 그 병목 재료를 쓰는 다른 항목은 늘리지 않음
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import api  # noqa: E402

CASES = ROOT / "tests" / "cases"


def check(name, body, time_limit):
    body = {**body, "time": time_limit, "run_id": ""}
    t0 = time.perf_counter()
    r = api.plan_api(body)
    dt = time.perf_counter() - t0
    cp = r.get("node_cp", 0) + r.get("house_cp", 0)
    budget = body.get("budget")
    left = int(budget) - cp if budget not in (None, "") else None
    problems = []
    short = [s for s in r.get("slots", []) if not s["hand"] and s["got"] + 1e-6 < s["qty"] - s["nice"]]
    for s in short:
        problems.append(f"부족: {s['label']} {s['got']:.1f} / {s['qty'] - s['nice']:.1f}")
    for e in r.get("expansion", []):
        c = e.get("cut")
        if c and not c.get("label") and left is not None and left > 10:
            problems.append(f"줄였는데 병목 없음: {e['name']} ×{e['scale']:.2f} (공헌도 {left} 남음)")
    grown = [e for e in r.get("expansion", []) if (e.get("scale") or 1) > 1.005]
    for e in r.get("expansion", []):
        neck = (e.get("cut") or {}).get("label")
        for g in grown:
            if neck and neck in g.get("uses", []):
                problems.append(f"{e['name']} 을(를) {neck} 때문에 줄였는데 같은 재료를 쓰는 {g['name']} 은(는) ×{g['scale']:.2f} 로 늘림")
    print(f"\n== {name}  {r.get('status')}  {dt:.0f}초  공헌도 {cp}" + (f" (남음 {left})" if left is not None else "")
          + f"  갭 {100 * (r.get('gap') or 0):.1f}%")
    for e in r.get("expansion", []):
        if e.get("scale") is not None:
            why = e.get("cut", {}).get("label") if e.get("cut") else None
            print(f"   {e['name'][:28]:<28} {e['crafts']:>6} → {e['crafts'] * e['scale']:>9.1f}  ×{e['scale']:.2f}"
                  + (f"  병목 {why}" if why else ("  (공헌도)" if e.get("cut") else "")))
    for p in problems:
        print("   ⚠", p)
    return not problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--time", type=float, default=120)
    args = ap.parse_args()
    files = sorted(CASES.glob("*.json"))
    if args.names:
        files = [f for f in files if f.stem in args.names]
    if not files:
        sys.exit(f"케이스 없음: {CASES}")
    ok = [check(f.stem, json.loads(f.read_text(encoding="utf-8")), args.time) for f in files]
    print(f"\n{sum(ok)}/{len(ok)} 통과")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
