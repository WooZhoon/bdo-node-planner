"""bdocodex 전체 레시피(요리/연금) → data/raw/codex_recipes.json

인벤에 없는 신규 레시피 보충 + 대체 재료 그룹 판별(레시피별 허용 아이템 목록)용.
"""
import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CACHE = RAW / "codex_html" / "recipes_all.json"
URL = "https://bdocodex.com/query.php?a=recipes&l=kr"
# 가공(빻기·가열하기 등) 레시피 전체. build_data.py 가 직접 읽는다
M_CACHE = RAW / "codex_html" / "mrecipes_all.json"
M_URL = "https://bdocodex.com/query.php?a=mrecipes&l=kr"
# 전체 아이템 이름 (약 7만 개, 38MB) → 이름만 추려 codex_item_names.json
I_CACHE = RAW / "codex_html" / "items_all.json"
I_URL = "https://bdocodex.com/query.php?a=items&l=kr"
# 집 작업장 제작(설계): 마구 제작소 등에서 만드는 것 (예: 크로그달로 말 장비)
D_CACHE = RAW / "codex_html" / "designs_all.json"
D_URL = "https://bdocodex.com/query.php?a=designs&l=kr"
# 집 이름 (집 키 = workerman houseinfo 키)
H_CACHE = RAW / "codex_html" / "houses_all.json"
H_URL = "https://bdocodex.com/query.php?a=houses&l=kr"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"

SLOT_RE = re.compile(r'<div class="iconset_wrapper_medium inlinediv">(.*?)</a></div>', re.S)


def fetch(url, cache, refresh):
    if cache.exists() and not refresh:
        return cache.read_text(encoding="utf-8-sig")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://bdocodex.com/kr/recipes/"})
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read().decode("utf-8-sig", errors="replace")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(body, encoding="utf-8")
    time.sleep(2)
    return body


def slots(fragment):
    out = []
    for s in SLOT_RE.findall(fragment):
        iid = re.search(r"/kr/item/(\d+)/", s)
        qty = re.search(r'quantity_small nowrap">(\d+)<', s)
        if iid:
            out.append({"id": int(iid.group(1)), "qty": int(qty.group(1)) if qty else 1,
                        "alt": "icon-repeat" in s})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    rows = json.loads(fetch(URL, CACHE, args.refresh))["aaData"]
    fetch(M_URL, M_CACHE, args.refresh)
    items = json.loads(fetch(I_URL, I_CACHE, args.refresh))["aaData"]
    names = {r[0]: re.sub(r"<[^>]+>", "", str(r[2])).strip() for r in items}
    (RAW / "codex_item_names.json").write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")
    print(f"아이템 이름 {len(names)}개 저장")
    designs = json.loads(fetch(D_URL, D_CACHE, args.refresh))["aaData"]
    out_d = [{"design_id": r[0], "name": re.sub(r"<[^>]+>", "", str(r[2])).strip(),
              "inputs": slots(r[6]), "outputs": slots(r[8])} for r in designs]
    (RAW / "codex_designs.json").write_text(json.dumps(out_d, ensure_ascii=False), encoding="utf-8")
    print(f"작업장 설계 {len(out_d)}개 저장")
    houses = json.loads(fetch(H_URL, H_CACHE, args.refresh))["aaData"]
    hnames = {r[0]: re.sub(r"<[^>]+>", "", str(r[2])).strip() for r in houses}
    (RAW / "codex_house_names.json").write_text(json.dumps(hnames, ensure_ascii=False), encoding="utf-8")
    print(f"집 이름 {len(hnames)}개 저장")
    out = []
    for r in rows:
        if r[3] not in ("요리", "연금"):
            continue
        out.append({
            "codex_id": r[0],
            "name": re.sub(r"<[^>]+>", "", r[2]).strip(),
            "kind": r[3],
            "slots": slots(r[6]),
            "outputs": [s["id"] for s in slots(r[8])],
            "allowed": r[9] or [],
        })
    (RAW / "codex_recipes.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"코덱스 레시피 {len(out)}개 저장")


if __name__ == "__main__":
    main()
