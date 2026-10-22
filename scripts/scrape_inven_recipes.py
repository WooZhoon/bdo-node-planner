"""인벤 검은사막 레시피 DB(요리/연금) 수집 → data/raw/inven_recipes.json

원본 HTML은 data/raw/inven_html/ 에 캐시하고, --refresh 없으면 캐시를 재사용한다.
"""
import argparse
import html
import json
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CACHE = RAW / "inven_html"
URL = "https://black.inven.co.kr/dataninfo/recipe/?retype={}"
TABS = {1: "요리", 2: "연금"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"

ROW_RE = re.compile(r'<tr class="row\d (maType\d)">(.*?)</tr>', re.S)
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
UL_RE = re.compile(r"<ul class=\"itemList[^\"]*\">(.*?)</ul>", re.S)
ITEM_RE = re.compile(r'data-black-item-code="(\d+)"[^>]*>(.*?)</a>', re.S)
# "옥수수 x 5 (곡물류)" / "시원한 생맥주(확률)"
MAT_RE = re.compile(r"^(.*?)\s*x\s*(\d+)\s*(?:\((.+)\))?$")


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def fetch(retype, refresh):
    path = CACHE / f"retype_{retype}.html"
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8")
    req = urllib.request.Request(URL.format(retype), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", errors="replace")
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    time.sleep(2)
    return body


def parse_items(fragment):
    out = []
    for code, inner in ITEM_RE.findall(fragment):
        label = text(inner)
        m = MAT_RE.match(label)
        if m:
            out.append({"id": int(code), "name": m.group(1), "qty": int(m.group(2)), "group": m.group(3)})
        else:
            # 결과물 칸: "시원한 생맥주(확률)" 형태, 수량 없음
            gm = re.match(r"^(.*?)\s*\((.+)\)$", label)
            out.append({"id": int(code), "name": gm.group(1) if gm else label, "note": gm.group(2) if gm else None})
    return out


def parse(body, kind):
    recipes = []
    for matype, row in ROW_RE.findall(body):
        tds = TD_RE.findall(row)
        if len(tds) < 4:
            continue
        name_td, mat_td, type_td, out_td = tds[:4]
        icon = re.search(r"itemicon/0*(\d+)", name_td)
        recipes.append({
            "name": text(re.search(r"<strong>(.*?)</strong>", name_td, re.S).group(1)),
            "icon_id": int(icon.group(1)) if icon else None,
            "kind": kind,
            "confirmed": matype == "maType1",
            "station": text(type_td),
            # 재료 목록이 여러 개면 서로 대체 가능한 조합
            "variants": [parse_items(ul) for ul in UL_RE.findall(mat_td)],
            "outputs": parse_items(out_td),
        })
    return recipes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="캐시 무시하고 다시 받기")
    args = ap.parse_args()
    allr = []
    for retype, kind in TABS.items():
        rs = parse(fetch(retype, args.refresh), kind)
        print(f"{kind}: {len(rs)}개")
        allr += rs
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "inven_recipes.json").write_text(json.dumps(allr, ensure_ascii=False, indent=1), encoding="utf-8")
    print("저장:", RAW / "inven_recipes.json")


if __name__ == "__main__":
    main()
