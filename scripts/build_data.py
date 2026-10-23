"""data/raw/* → data/build/planner.json (플래너가 읽는 단일 데이터 파일)"""
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
WM = RAW / "workerman"
OUT = ROOT / "data" / "build"

# workerman game.js medianGoblin/Giant/Human 의 지역별 일꾼 (마을 waypoint → charkey)
WORKER_OVERRIDES = {
    1623: (8003, 8006, 8009), 1604: (8003, 8006, 8009), 1691: (8023, 8027, 8031),
    1750: (8035, 8039, 8043), 1781: (8050, 8058, 8054), 1785: (8050, 8058, 8054),
    1795: (8050, 8058, 8054), 1857: (8050, 8058, 8054), 1858: (8050, 8058, 8054),
    1853: (8050, 8058, 8054),
}
DEFAULT_WORKERS = (7572, 7571, 7573)  # 고블린, 거인, 인간
# 숙소 단계 → 일꾼 수, 창고 단계 → 칸 수 (houseinfo CraftList 최대 단계 기준, 집 시트와 389곳 대조 일치)
LODGING_BEDS = {1: 1, 2: 2, 3: 4, 4: 6, 5: 8}
STORAGE_SLOTS = {1: 3, 2: 5, 3: 8, 4: 12, 5: 16}
# 작업장 종류 (workerman loc 영문 → 한글)
WORKSHOP_KO = {"Horse Gear Workshop": "마구 제작소", "Refinery": "정제소", "Weapon Workshop": "무기 제작소",
               "Armor Workshop": "방어구 제작소", "Handcraft Workshop": "수공예 제작소", "Carpentry Workshop": "목공예 제작소",
               "Jeweler": "세공 제작소", "Tool Workshop": "도구 제작소", "Ceramics Workshop": "도자기 제작소",
               "Furniture Workshop": "가구 제작소", "Wagon Workshop": "마차 제작소", "Ship Parts Workshop": "선박 부품 제작소",
               "Costume Workshop": "의상 제작소", "Horse Gear Workshop ": "마구 제작소"}
PROCESSING_METHODS = {"빻기", "흔들어 섞기", "가열하기", "말리기", "장작 패기", "솎아내기", "정제하기", "잘게 썰기",
                      "공작", "간이 연금", "간이 요리"}
GIANT_SPECIES = {2, 4, 8}  # workerman game.js giantSpecies


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def median_worker(static, charkey):
    """레벨 40 기준 중간값 스탯 (workerman makeMedianChar)"""
    s = static[str(charkey)]
    wspd = s["wspd"] + 39 * (s["wspd_lo"] + s["wspd_hi"]) / 2
    mspd = s["mspd"] * (1 + 39 * (s["mspd_lo"] + s["mspd_hi"]) / 2 / 1e6)
    luck = s["luck"] + 39 * (s["luck_lo"] + s["luck_hi"]) / 2
    return {"charkey": charkey, "wspd": round(wspd / 1e6, 2), "mspd": round(mspd) / 100,
            "luck": round(luck / 1e4, 2), "giant": s["species"] in GIANT_SPECIES}


def main():
    loc_all = load(WM / "loc.json")
    loc = loc_all["ko"]
    loc_en_housetype = loc_all["en"]["housetype"]
    plantzone = load(WM / "plantzone.json")
    drops = load(WM / "plantzone_drops.json")
    links = load(WM / "links.json")  # workerman 이 실제로 쓰는 연결 (nodes_links.json 은 구버전)
    regioninfo = load(WM / "regioninfo.json")
    pzk2tk = load(WM / "distances_pzk2tk.json")
    static = load(WM / "worker_static.json")
    lodging = load(WM / "lodging_per_town.json")
    houseinfo = load(WM / "houseinfo.json")
    house_names = load(RAW / "codex_house_names.json")  # 집 키 → 코덱스 이름
    guide_file = RAW / "house_guide.json"
    house_guide = load(guide_file) if guide_file.exists() else {}  # 집 키 → 시트 비고
    inven = load(RAW / "inven_recipes.json")
    codex = load(RAW / "codex_recipes.json")
    groups_raw = load(RAW / "material_groups.json")

    items = {int(k): v for k, v in loc["item"].items()}
    for r in inven:
        for v in r["variants"] + [r["outputs"]]:
            for m in v:
                items.setdefault(m["id"], m["name"])
    for html_file in (RAW / "codex_html").glob("kr_*.html"):
        s = html_file.read_text(encoding="utf-8")
        for iid, name in re.findall(r'item_grade_\d" data-id="item--(\d+)"[^>]*>([^<]+)</a>', s):
            items.setdefault(int(iid), name.strip())
    codex_names = {}
    for r in codex:
        codex_names.setdefault(r["outputs"][0], r["name"])
    for iid, name in codex_names.items():
        items.setdefault(iid, name)

    def slot(s, allowed):
        """코덱스 재료 칸 → {id, qty[, group, members]}. 대체 그룹은 대표 재료가 속한 그룹 중
        이 레시피의 허용 목록과 가장 많이 겹치는 것 (일반/고급/특상품 모두 1:1로 취급)"""
        out = {"id": s["id"], "qty": s["qty"]}
        g = groups_raw.get(str(s["id"]))
        if s["alt"] and g and g["groups"]:
            best = max(g["groups"].values(), key=lambda m: len(set(map(int, m)) & allowed))
            members = sorted(int(k) for k in best if int(k) in allowed) or sorted(map(int, best))
            if len(members) > 1:
                rep = items.get(s["id"], str(s["id"]))
                label = g["label"]
                # 같은 그룹은 어느 레시피에서 나와도 같은 이름이 되도록, 이름이 없을 때 쓰는 대표 재료는 그룹의 가장 작은 ID
                plain = [int(k) for k in best if not str(items.get(int(k), "")).startswith("[")]  # [이벤트] 등 제외
                rep_fixed = items.get(min(plain or [int(k) for k in best]), rep)
                # 인벤 그룹명이 재료와 안 맞는 경우가 있음 (예: 양파 그룹이 '곡물가루') → 그럴 땐 '대표 재료 계열'
                if label and any((k in label) != (k in rep) for k in ("가루", "반죽")):
                    label = None
                # 인벤의 '다른 피가능'·'다른 수정가능' 같은 이름은 여러 그룹에 똑같이 붙어 구분이 안 됨
                if label and re.match(r"^다른 .*가능$", label):
                    label = None
                out["group"] = label or f"{rep_fixed} 계열"
                out["members"] = members
        return out

    # 완성품별로 코덱스 레시피(여러 조합) 묶기. 인벤에만 있는 레시피는 인벤 재료로 보충
    by_product = {}
    for r in codex:
        allowed = set(json.loads(r["allowed"]) if isinstance(r["allowed"], str) else r["allowed"])
        p = by_product.setdefault(r["outputs"][0], {
            "name": r["name"], "kind": r["kind"], "product_id": r["outputs"][0],
            "outputs": r["outputs"], "variants": [], "source": "codex"})
        p["variants"].append([slot(s, allowed) for s in r["slots"]])
    for r in inven:
        pid = r["outputs"][0]["id"] if r["outputs"] else r["icon_id"]
        if pid in by_product or not r["variants"]:
            continue
        by_product[pid] = {
            "name": r["name"], "kind": r["kind"], "product_id": pid, "source": "inven",
            "outputs": [o["id"] for o in r["outputs"]],
            "variants": [[{"id": m["id"], "qty": m["qty"]} for m in v] for v in r["variants"]],
        }
    # 같은 그룹(허용 재료가 같은 칸)은 이름 하나로: 인벤 이름이 있으면 그것, 없으면 '… 계열'
    names_by_set = defaultdict(list)
    for p in by_product.values():
        for v in p["variants"]:
            for m in v:
                if m.get("group"):
                    names_by_set[frozenset(m["members"])].append(m["group"])
    best_name = {k: next((n for n in ns if not n.endswith(" 계열")), ns[0]) for k, ns in names_by_set.items()}
    for p in by_product.values():
        for v in p["variants"]:
            for m in v:
                if m.get("group"):
                    m["group"] = best_name[frozenset(m["members"])]
    # 대체 가능한 칸이 많은 조합을 기본(첫 번째)으로
    for p in by_product.values():
        p["variants"].sort(key=lambda v: -sum("members" in m for m in v))
    recipe_out = sorted(by_product.values(), key=lambda p: p["name"])

    # 가공(빻기·흔들어 섞기·가열하기 등): 결과물 → [{입력, 결과 평균 개수}]. 결과 범위 "1~4" 는 중간값(2.5)으로
    processing = defaultdict(list)
    mrows = json.loads((RAW / "codex_html" / "mrecipes_all.json").read_text(encoding="utf-8-sig"))["aaData"]
    qty_re = re.compile(r'/kr/item/(\d+)/.*?quantity_small[^>]*>([^<]*)<', re.S)
    for r in mrows:
        if r[3] not in PROCESSING_METHODS:
            continue
        ins = [(int(i), int(q)) for i, q in qty_re.findall(r[6]) if q.isdigit()]
        outs = qty_re.findall(r[8])
        if not ins or not outs:
            continue
        oid, oq = outs[0]
        lo, _, hi = oq.partition("~")
        num = lambda s: float(s[:-1]) * 1000 if s.endswith("k") else float(s)  # noqa: E731
        try:
            avg = (num(lo) + num(hi or lo)) / 2
        except ValueError:
            continue
        processing[int(oid)].append({"method": r[3], "inputs": ins, "out": avg})
        items.setdefault(int(oid), re.sub(r"<[^>]+>", "", r[2]).strip())
    # 집 작업장 제작(코덱스 설계). 요리·연금 레시피나 가공이 이미 있는 결과물은 건드리지 않음
    design_file = RAW / "codex_designs.json"
    if design_file.exists():
        workshop_of = {}
        rec_file = WM / "houseinforeceipe.json"
        for hk, h in (load(rec_file).items() if rec_file.exists() else []):
            ws = WORKSHOP_KO.get(loc_en_housetype.get(hk, ""), loc["housetype"].get(hk) or f"작업장 {hk}")
            for grp in h.get("groups", []):
                for rk in grp:
                    workshop_of.setdefault(rk, ws)
        product_ids = {r["product_id"] for r in recipe_out}
        added = 0
        for dz in load(design_file):
            if not dz["inputs"] or not dz["outputs"]:
                continue
            oid = dz["outputs"][0]["id"]
            if oid in product_ids or oid in processing:
                continue
            ws = workshop_of.get(dz["design_id"], "작업장")
            processing[oid].append({"method": f"작업장 제작({ws})", "inputs": [(m["id"], m["qty"]) for m in dz["inputs"]],
                                    "out": float(dz["outputs"][0]["qty"] or 1), "workshop": True})
            items.setdefault(oid, dz["name"])
            added += 1
        print(f"작업장 설계 {added}개 추가")
    for p in processing.values():
        for x in p:
            x["inputs"] = [[i, q] for i, q in x["inputs"]]
    # 코덱스 전체 아이템 이름으로 빈 이름 채우기
    names_file = RAW / "codex_item_names.json"
    if names_file.exists():
        for iid, name in load(names_file).items():
            items.setdefault(int(iid), name)

    # 마을: 일꾼 거리 데이터가 있는 곳만 (= 일꾼을 둘 수 있는 마을)
    towns = {}
    tk_with_dist = {tk for lst in pzk2tk.values() for tk, _ in lst}
    for ri in regioninfo.values():
        tk, wp = ri["key"], ri["waypoint"]
        if wp and tk in tk_with_dist:
            gob, gi, hum = WORKER_OVERRIDES.get(wp, DEFAULT_WORKERS)
            # 마을의 모든 집: 숙소·창고 최대 단계 기준 침대·창고 칸 (CraftList 1=숙소, 2=창고)
            houses = {}
            for k, hi in houseinfo.items():
                if hi["affTown"] != tk:
                    continue
                lodge_lv, store_lv = hi["CraftList"].get("1", 0), hi["CraftList"].get("2", 0)
                guide = house_guide.get(k, {})
                houses[int(k)] = {
                    "cp": hi["CP"], "need": hi["needHouseKey"],
                    "lodge_lv": lodge_lv, "beds": LODGING_BEDS.get(lodge_lv, 0),
                    "store_lv": store_lv, "slots": STORAGE_SLOTS.get(store_lv, 0),
                    "name": house_names.get(k, f"집 #{k}"), "note": guide.get("note", ""),
                    "pos": [round(hi["x"]), round(hi["z"])],
                }
            # 창고 효율 = (이 집 + 선행 집들) 창고 칸 합 ÷ 공헌도 합 — 집 가이드 시트의 '효율'과 같은 방식
            for k, h in houses.items():
                slots = cp = 0
                cur, seen = k, set()
                while cur and cur in houses and cur not in seen:
                    seen.add(cur)
                    slots += houses[cur]["slots"]
                    cp += houses[cur]["cp"]
                    cur = houses[cur]["need"]
                h["eff"] = round(slots / cp, 2) if cp else 0
                sheet = house_guide.get(str(k), {}).get("eff")
                if sheet:  # 시트 값이 있으면 그걸 씀 (시트의 구매 순서 기준, 557곳 일치·69곳 차이)
                    h["eff"] = sheet
            towns[tk] = {
                "name": loc["node"].get(str(wp), str(tk)).strip(), "waypoint": wp,
                "workers": [median_worker(static, c) for c in (gob, gi, hum)],
                # 안카도는 활성화 전 기본 숙소 0 (workerman 기준), 나머지 마을 기본 1
                "base_beds": 0 if tk == 619 else 1,
                "houses": houses,
            }

    # 행운 보따리 → 내용물 기대 개수 (보따리 1개 = 내용물 1개, 여러 종류면 균등)
    bags = {int(b): {int(i): w for i, w in c.items()}
            for b, c in load(ROOT / "data" / "bag_contents.json").items() if not b.startswith("_")}

    def open_bags(lucky):
        out = defaultdict(float)
        for i, q in lucky.items():
            i = int(i)
            if i in bags:
                tot = sum(bags[i].values())
                for x, w in bags[i].items():
                    out[x] += q * w / tot
            else:
                out[i] += q
        return {i: round(q, 6) for i, q in out.items()}

    zones = {}
    for k, z in plantzone.items():
        d = drops.get(k)
        if not d:
            continue
        zones[int(k)] = {
            "name": loc["node"].get(k, k),
            "parent": loc["node"].get(str(z["parent"]), str(z["parent"])),
            "cp": z["node"]["CP"], "workload": d["workload"],
            "unlucky": {int(i): q for i, q in d["unlucky"].items()},
            # 창고 칸 계산용: 이 노드에서 나올 수 있는 아이템 종류
            "items": sorted({int(i) for i in d["unlucky"]} | {int(i) for i in d.get("lucky", {})}),
            "unlucky_gi": {int(i): q for i, q in d.get("unlucky_gi", d["unlucky"]).items()},
            "lucky": {int(i): q for i, q in d.get("lucky", {}).items()},
            # 행운 100 기준 트립당 추가 획득 (보따리는 내용물로 풂). 실제 = 일꾼 행운/100 × 이 값 (workermanjs 방식)
            "luck_drops": open_bags(d.get("lucky", {})),
            "dist": {tk: dist for tk, dist in pzk2tk.get(k, [])},
        }

    exploration = load(WM / "exploration.json")
    # 노드 CP·좌표는 exploration.json (workerman game.js 와 동일)
    graph = {int(k): {"cp": exploration[k]["CP"], "name": exploration[k]["name"].strip() or loc["node"].get(k, k).strip(),
                      "links": nbrs,
                      "pos": [round(exploration[k]["pos"]["x"]), round(exploration[k]["pos"]["z"])]}
             for k, nbrs in links.items() if k in exploration}

    OUT.mkdir(parents=True, exist_ok=True)
    data = {"items": items, "recipes": recipe_out, "processing": processing,
            "towns": towns, "zones": zones, "graph": graph}
    (OUT / "planner.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"아이템 {len(items)} / 레시피 {len(recipe_out)} / 마을 {len(towns)} / 노드 {len(zones)} / 그래프 {len(graph)}")


if __name__ == "__main__":
    main()
