"""아이템 조달 정보: 코덱스 아이템 페이지 탭으로 판별 → data/item_sources.json {아이템 ID: {"sold": bool, "farm": bool}}

- sold: 'NPC가 판매 한' 탭 (상점에서 살 수 있음). 사용자가 알려준 과일 7종·버섯 8종과 전부 일치 확인 (2026-10-21)
- farm: '농산물' 탭 (텃밭 재배로 얻을 수 있음)
사용자가 직접 정한 기본값은 data/vendor_manual.json, 계산마다 화면에서 바꾼 값은 요청으로 들어와 그쪽이 우선 (plan.sourcing).
"""
import json
import os
from pathlib import Path

import autorecipe

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "item_sources.json"


def load_cache():
    # 서버와 미리 확인 스크립트가 같은 파일을 쓰므로, 읽기 실패(쓰는 중)는 빈 캐시로 보고 넘어감
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_cache(cache):
    """임시 파일에 쓰고 바꿔치기 (쓰는 도중 다른 프로세스가 반쯤 쓴 파일을 읽지 않도록)"""
    tmp = CACHE.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CACHE)


def lookup(iid):
    """캐시에 있으면 그대로, 없으면 코덱스 페이지 확인 후 저장. 네트워크 오류면 None (저장 안 함)"""
    cache = load_cache()
    if str(iid) in cache:
        return cache[str(iid)]
    try:
        html = autorecipe.fetch_item_page(iid)  # 코덱스 요청 간격 공유
    except Exception:
        return None
    info = {"sold": "tabs-specialsoldbynpc" in html, "farm": "tabs-farmproduct" in html}
    cache = load_cache()
    cache[str(iid)] = info
    write_cache(cache)
    return info


def prefetch(ids, log_every=50):
    todo = [i for i in ids if str(i) not in load_cache()]
    print(f"상점·재배 확인: {len(todo)}개 (이미 확인 {len(ids) - len(todo)}개)", flush=True)
    for n, i in enumerate(todo, 1):
        lookup(i)
        if n % log_every == 0:
            print(f"  {n}/{len(todo)}", flush=True)


if __name__ == "__main__":
    # 노드 생산품 + 요리·연금 재료 (레시피로 만드는 것 제외) 를 미리 확인
    import plan
    d = plan.load()
    mats = set(plan.node_items(d))
    for r in d["recipes"]:
        for v in r["variants"]:
            for m in v:
                mats |= set(m.get("members", [m["id"]]))
    prefetch(sorted(mats - set(plan.by_product(d))))
