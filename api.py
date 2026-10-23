"""계산 로직 (화면과 무관): 로컬 서버(server.py)와 브라우저용 정적 사이트(site/, Pyodide)가 같이 씀"""
import json
import math
import time
from collections import defaultdict
from pathlib import Path

import autorecipe
import optimize
import plan
import vendor

ROOT = Path(__file__).resolve().parent
D = plan.load()


def item_name(i):
    return D["items"].get(i, str(i))


def slot_label(m):
    return m.get("group") or item_name(m["id"])


def build_meta():
    from_nodes = plan.node_items(D)
    recipes = [{
        "pid": r["product_id"], "name": r["name"], "kind": r["kind"],
        "variants": [[{"id": m["id"], "qty": m["qty"], "label": slot_label(m), "alt": "members" in m}
                      for m in v] for v in r["variants"]],
    } for r in D["recipes"] if r["variants"]]
    # 직접 입력용: 노드에서 나오는 재료 + 가공으로 거슬러 갈 수 있는 재료
    manual = sorted(from_nodes | set(D["processing"]), key=item_name)
    nodes, edges = [], set()
    for k, n in D["graph"].items():
        if not n["pos"]:
            continue
        kind = "zone" if k in D["zones"] else ("town" if any(t["waypoint"] == k for t in D["towns"].values()) else "node")
        nodes.append({"k": k, "name": plan.zone_label(D, k), "cp": n["cp"], "x": n["pos"][0], "z": n["pos"][1], "kind": kind})
        for v in n["links"]:
            if v in D["graph"] and D["graph"][v]["pos"]:
                edges.add((min(k, v), max(k, v)))
    return {
        "recipes": recipes,
        "manual_items": [{"id": i, "name": item_name(i), "node": i in from_nodes,
                          "method": D["processing"][i][0]["method"] if i in D["processing"] else None} for i in manual],
        "towns": [{"tk": tk, "name": t["name"], "wp": t["waypoint"]} for tk, t in sorted(D["towns"].items(), key=lambda x: x[1]["name"])],
        # 보유 집 입력용
        "houses": [{"k": k, "name": h["name"], "town": t["name"], "cp": h["cp"], "beds": h["beds"], "slots": h["slots"],
                    "eff": h.get("eff", 0), "need": h["need"]}
                   for tk, t in D["towns"].items() for k, h in t["houses"].items()],
        "map": {"nodes": nodes, "edges": sorted(edges)},
        "regions": [*_REG, "기타"],  # 지역 표시 순서
        "town_order": [D["towns"][tk]["name"] for tk in sorted(D["towns"], key=town_key)],  # 마을 표시 순서 (인게임)
    }



def meta():
    return META


_max_daily = {}
BOTTLENECK_SLACK = 0.95
STOCK_DAYS = 180  # 보유 재료를 다 쓰는 기간 기본값 (사용자: 약 6개월)


def max_daily(accept, kinds):
    """허용 아이템을 노드로 하루 최대 얼마나 얻을 수 있는지: 생산 노드마다 (마을·일꾼 중 최대) 하루 생산량의 합.
    결정류처럼 확률로 나오는 재료의 '속도 상한'을 보는 용도 (경로·공헌도는 무시한 낙관적 값)"""
    key = (accept, kinds, D.get("_resource_pct"))
    if key not in _max_daily:
        total = 0.0
        for z in D["zones"].values():
            if not plan.zone_has(z, accept):
                continue
            best = 0.0
            for tk, dist in z["dist"].items():
                if tk not in D["towns"]:
                    continue
                for w in plan.town_workers(D, tk, kinds):
                    drops = plan.worker_drops(z, w)
                    per = sum(drops.get(i, 0) for i in accept)
                    best = max(best, plan.cycles_daily(plan.active_workload(D, z["workload"]), w["wspd"] + plan.WSPD_SKILL_BONUS, dist, w["mspd"]) * per)
            total += best
        _max_daily[key] = total
    return _max_daily[key]


def bottleneck_days(unit_need, qty, days, src, kinds):
    """1개(1회) 기준 필요량으로 병목 재료를 찾아, 원하는 속도가 노드로 낼 수 있는 최대 속도보다 빠르면 기간을 늘림.
    반환: (실제 기간, 병목 정보 또는 None)"""
    slots, _ = plan.make_slots(D, unit_need, src)
    worst = None
    for label, accept, q1 in slots:
        cap = max_daily(tuple(accept), kinds)
        if q1 > 0 and cap > 0:
            # 이 재료로 하루 최대 몇 개(회) 만들 수 있나. 실제 배치(마을·일꾼 후보 제한)가 최대치보다 조금 느릴 수 있어 5% 여유
            rate = cap * BOTTLENECK_SLACK / q1
            if not worst or rate < worst[0]:
                worst = (rate, label, cap, q1)
    # 결정류처럼 '하루에 1개도 못 만드는' 경우에만 기간을 늘림. 달걀처럼 조금 모자란 건 기간 대신 부족으로 보여줌
    if worst and worst[0] < 1 and qty / days > worst[0]:
        rate, label, cap, q1 = worst
        need_days = qty / rate
        return need_days, {"label": label, "per_day": cap, "per_unit": q1, "days": need_days}
    return days, None


def expand_request(body, src):
    """완성품·재료마다 펼쳐서 하루 몫 필요량을 필수/여유로 나눠 합침.
    항목별 days(목표 기간)가 없으면 전체 목표 기간, priority 가 'nice' 면 여유"""
    by_pid = {r["product_id"]: r for r in D["recipes"]}
    default_days = max(1, int(body.get("days") or 1))
    must, nice = defaultdict(float), defaultdict(float)
    expansion, parts = [], []

    kinds = tuple(k for k in (body.get("worker_kinds") or plan.WORKER_KINDS) if k in plan.WORKER_KINDS) or plan.WORKER_KINDS

    def add(entry, one, qty, unit):
        """기간으로 나눠 하루 몫. 병목 재료 속도보다 빠르면 기간을 자동으로 늘림"""
        days = max(1, int(entry.get("days") or default_days))
        days, neck = bottleneck_days(unit, qty, days, src, kinds)
        target = nice if entry.get("priority") == "nice" else must
        for k, v in one.items():
            target[k] += v / days
        # 생산량 최대에서 항목마다 따로 늘리려고 필수 항목의 하루 몫을 따로 둠.
        # 수량 1개짜리(장비·유물·도구 등)는 하나면 되니 늘리지 않음
        if entry.get("priority") != "nice" and qty > 1:
            parts.append((len(expansion), {k: v / days for k, v in one.items()}))
        return days, neck

    for p in body.get("products", []):
        r = by_pid.get(int(p["pid"]))
        crafts = max(0, int(p.get("crafts", 10)))
        if not r or not crafts:
            continue
        v = min(int(p.get("variant", 0)), len(r["variants"]) - 1)
        chain, one, unit, tree = [], defaultdict(float), defaultdict(float), []
        plan.expand(D, r, crafts, one, chain, variant=v, src=src, tree=tree)
        plan.expand(D, r, 1, unit, [], variant=v, src=src)
        days, neck = add(p, one, crafts, unit)
        expansion.append({"name": r["name"], "crafts": crafts, "days": days, "priority": p.get("priority", "must"),
                          "bottleneck": neck,
                          "mats": [f"{slot_label(m)} x{m['qty']}" for m in r["variants"][v]], "chain": chain, "tree": tree})
    for m in body.get("materials", []):
        i, q = int(m["id"]), max(0, int(m.get("qty", 0)))
        if q:
            chain, one, unit, tree = [], defaultdict(float), defaultdict(float), []
            plan.expand_slot(D, item_name(i), (i,), q, one, chain, 0, src, tree=tree)
            plan.expand_slot(D, item_name(i), (i,), 1, unit, [], 0, src)
            days, neck = add(m, one, q, unit)
            expansion.append({"name": f"{item_name(i)} (직접 입력)", "crafts": q, "days": days, "bottleneck": neck,
                              "priority": m.get("priority", "must"), "mats": [], "chain": chain, "tree": tree})
    must = {k: round(v, 3) for k, v in must.items() if v > 0}
    nice = {k: round(v, 3) for k, v in nice.items() if v > 0}
    need = defaultdict(float)
    for part in (must, nice):
        for k, v in part.items():
            need[k] += v
    return dict(need), nice, expansion, parts


def nice_by_slot(slots, nice, src):
    """여유 필요량(또는 항목 하나의 필요량)을 최적화 칸 번호에 맞춰 {칸 번호: 수량}"""
    nslots, _ = plan.make_slots(D, nice, src)
    key = lambda a: frozenset(i for i in a if i in plan.node_items(D))  # noqa: E731
    keys = [key(a) for _, a, _ in slots]
    out = defaultdict(float)
    for _, a, q in nslots:
        # 전체 칸은 겹치는 칸끼리 합쳐져 있으니 겹치는 칸을 찾음
        si = next((si for si, k in enumerate(keys) if k & key(a)), None)
        if si is not None:
            out[si] += q
    return {si: min(q, slots[si][2]) for si, q in out.items()}


# 황실 공방 일꾼은 육조거리에서만 보낼 수 있음 → 육조거리 숙소에 고정
# 마을 → 지역 (결과 표·보고서를 지역별로 묶음, 순서는 파일 순서 = 인게임 순서). 없는 마을은 '기타'
_REG = {k: v for k, v in json.loads((ROOT / "data" / "town_regions.json").read_text(encoding="utf-8")).items() if not k.startswith("_")}
REGION = {tk: next(((r, i) for i, (r, ts) in enumerate(_REG.items()) if t["name"] in ts), ("기타", len(_REG)))
          for tk, t in D["towns"].items()}
# 지역 안 마을도 파일 순서(인게임 순서)대로
_TOWN_ORDER = {t: i for ts in _REG.values() for i, t in enumerate(ts)}
town_key = lambda tk: (REGION[tk][1], _TOWN_ORDER.get(D["towns"][tk]["name"], 99), D["towns"][tk]["name"])  # noqa: E731

META = build_meta()
IMPERIAL_TOWN = next((tk for tk, t in D["towns"].items() if "육조거리" in t["name"]), None)
MAX_LOOKUPS = 25  # 계산 한 번에 코덱스에 새로 물어보는 아이템 수 상한 (조합식·상점/재배 합계)


# 노드 종류 (채광·벌목·채집·발굴·재배·어업·축산·작업장): 결과를 종류별로 묶어 생산물 합계를 보여줌
NODE_TYPES = ["채광", "벌목", "채집", "발굴", "재배", "건조장", "생산", "작업장"]
TYPE_KO = {"건조장": "어업 (생선 건조장)", "생산": "축산 (닭고기·벌꿀 등)", "재배": "재배 (농장)"}


def node_type(zk):
    n = D["zones"][zk]["name"].replace("채석장", "채광")
    t = next((t for t in NODE_TYPES if t in n), n)
    return TYPE_KO.get(t, t), NODE_TYPES.index(t) if t in NODE_TYPES else len(NODE_TYPES)


def daily_drops(c):
    """배치된 일꾼 하나가 하루에 가져오는 모든 아이템 (덤·행운 보따리 내용물 포함)"""
    z, w = D["zones"][c["zone"]], c["worker"]
    cyc = plan.cycles_daily(plan.active_workload(D, z["workload"]), w["wspd"] + plan.WSPD_SKILL_BONUS, z["dist"][c["town"]], w["mspd"])
    drops = plan.worker_drops(z, w)
    return {i: cyc * q for i, q in (drops.items() if isinstance(drops, dict) else drops)}


def node_sets(chosen, slots):
    """노드 종류별 일꾼 수·생산물 합계. 필요한 재료(need)를 위로, 덤은 아래로"""
    want = {i for _, a, _ in slots for i in a}
    sets = {}
    for c in chosen:
        name, order = node_type(c["zone"])
        st = sets.setdefault(name, {"type": name, "order": order, "workers": 0, "zones": [], "items": defaultdict(float)})
        st["workers"] += 1
        st["zones"].append(plan.zone_label(D, c["zone"]))
        for i, q in daily_drops(c).items():
            st["items"][i] += q
    return [{**st, "items": [{"name": item_name(i), "daily": q, "need": i in want}
                             for i, q in sorted(st["items"].items(), key=lambda x: (x[0] not in want, -x[1]))]}
            for st in sorted(sets.values(), key=lambda st: st["order"])]


def known(i):
    return i in plan.node_items(D) or i in D["processing"] or any(r["product_id"] == i for r in D["recipes"])


def finite(o):
    """JSON 에 못 넣는 inf·nan 을 None 으로 (시간이 짧으면 솔버 갭이 inf — 브라우저 JSON.parse 가 실패했음)"""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [finite(v) for v in o]
    return o


def plan_api(body):
    return finite(_plan(body))


def _expand(body):
    """완성품·재료 → 노드로 모을 칸(slots)과 노드 계산에서 빠지는 재료(others). 계산(_plan)과 재료 불러오기(expand_api) 공용"""
    # 지역 자원량(%): 실제 작업량 = 기본 × (2 − 자원량). 기본 0% (보수적)
    D["_resource_pct"] = min(100, max(0, float(body.get("resource_pct") or plan.RESOURCE_DEFAULT)))
    # 화면에서 재료별로 정한 조달 방법 {아이템 ID: node/buy/farm/self}
    src = {int(k): v for k, v in (body.get("sourcing") or {}).items() if v in plan.SOURCE_KO}
    # 전개 → (1) 상점·재배 여부를 아직 모르는 재료는 코덱스에서 확인 (2) 노드로도 조합식으로도 안 되는 재료는
    # 코덱스 설명에서 조합식을 찾아봄 → 바뀐 게 있으면 다시 전개
    looked, found = 0, []
    while True:
        need, nice_need, expansion, parts = expand_request(body, src)
        slots, others = plan.make_slots(D, need, src)
        new = False
        leaf_ids = {i for _, a, _ in slots for i in a} | {i for _, _, a in others for i in a}
        for i in sorted(leaf_ids):
            if i in D["_sources"] or looked >= MAX_LOOKUPS:
                continue
            info = vendor.lookup(i)
            looked += 1
            if info is not None:
                D["_sources"][i] = info
                D.pop("_reach", None)
                new = new or plan.sourcing(D, i, src) != "node"
        for label, q, accept in others:
            if len(accept) != 1 or looked >= MAX_LOOKUPS or plan.sourcing(D, accept[0], src) != "node":
                continue
            i = accept[0]
            cached = str(i) in autorecipe.load_cache()
            entry = autorecipe.lookup(i, prefer=known)
            looked += 0 if cached else 1
            if entry.get("status") == "ok" and i not in D["processing"]:
                autorecipe.apply(D, i, entry)
                D.pop("_reach", None)
                found.append(item_name(i))
                new = True
        if not new:
            break
    return src, need, nice_need, expansion, parts, slots, others, found


def expand_api(body):
    """계산 전에 노드로 모을 재료 목록 (보유 개수 입력용). key = 보유 입력 키(아이템 ID 쉼표), daily = 하루 필요량"""
    src, _, _, _, _, slots, others, found = _expand(body)
    return {"slots": [{"key": ",".join(map(str, a)), "label": l, "daily": q,
                       "items": [item_name(i) for i in a if i in plan.node_items(D)][:8]} for l, a, q in slots],
            "others": [{"label": l, "qty": q, "reason": plan.other_reason(D, a, src)} for l, q, a in others],
            "auto_found": found}


def _plan(body):
    src, need, nice_need, expansion, parts, slots, others, found = _expand(body)

    def mode_of(accept):
        return plan.slot_mode(D, accept, src)

    out = {"expansion": expansion, "slots": [], "auto_found": found,
           "others": [{"label": l, "qty": q, "ids": list(a), "mode": mode_of(a), "reason": plan.other_reason(D, a, src)}
                      for l, q, a in sorted(others, key=lambda o: (plan.other_reason(D, o[2], src), -o[1]))]}
    if not slots:
        out["status"] = "노드로 얻을 재료 없음"
        return out

    nice_slots = nice_by_slot(slots, nice_need, src)
    # 항목별 필수 하루 몫 → 칸 번호 (생산량 최대에서 항목마다 따로 늘림)
    grow = [(ei, nice_by_slot(slots, {k: v for k, v in part.items() if v > 0}, src)) for ei, part in parts]
    grow = [(ei, g) for ei, g in grow if g]
    # 창고 효율 기준 이상(+선행) 집: 예산에서 이미 뺐으므로 계산에선 산 것으로 (숙소로는 안 씀 — lodge_eff_min)
    owned_houses = {int(k) for k in body.get("owned_houses", [])}
    reserved = {int(k) for k in body.get("reserved_houses", [])} - owned_houses
    owned = {int(k) for k in body.get("owned", [])}
    beds = {int(tk): int(n) for tk, n in (body.get("beds") or {}).items() if int(n)}
    budget = body.get("budget")
    # 보유 재료: {아이템 ID 들(쉼표): 개수} → 소진 기간(기본 180일)에 나눠 하루 몫만큼 노드 없이 채움
    stock_days = max(1, int(body.get("stock_days") or STOCK_DAYS))
    stock_n = [sum(int(v) for k, v in (body.get("stock") or {}).items()
                   if int(v or 0) > 0 and {int(i) for i in str(k).split(",")} <= set(a)) for _, a, _ in slots]
    stock = {si: n / stock_days for si, n in enumerate(stock_n) if n}
    res = optimize.solve(D, slots, owned=owned, extra_beds=beds,
                         budget=int(budget) if budget not in (None, "") else None,
                         time_limit=float(body.get("time", 60)),
                         owned_houses=owned_houses | reserved,
                         storage=body.get("storage", True) is not False,
                         base_storage=32 if body.get("value_pack") else 16,
                         worker_kinds=tuple(k for k in (body.get("worker_kinds") or plan.WORKER_KINDS)
                                            if k in plan.WORKER_KINDS) or plan.WORKER_KINDS,
                         nice=nice_slots,
                         lodge_eff_min=float(body["lodge_eff_min"]) if body.get("lodge_eff_min") not in (None, "") else None,
                         extra_workers=max(0, int(body.get("extra_workers") or 0)),
                         fixed_workers={IMPERIAL_TOWN: min(8, max(0, int(body.get("imperial_workers") or 0)))}
                         if IMPERIAL_TOWN else None,
                         goal="min" if body.get("goal") == "min" else "max",
                         grow_parts=[g for _, g in grow], stock=stock)
    for (ei, _), sc in zip(grow, res["scales"]):
        expansion[ei]["scale"] = sc
    g = D["graph"]
    # 창고용 집은 계획에서 안 써도 전부 산다는 뜻 → 최적화가 안 고른 것도 표에 넣음 (효율 기준 집 = 창고, 선행만 걸린 집 = 선행)
    listed = {h["key"] for h in res["houses"]}
    for tk, t in D["towns"].items():
        for k, h in t["houses"].items():
            if k in reserved and k not in listed:
                use = "창고" if h.get("eff", 0) >= float(body.get("lodge_eff_min") or 0) and h["slots"] else "선행"
                res["houses"].append({"town": tk, "key": k, "use": use, "owned": False, "cost": 0, **h})
    out.update({
        "status": res["status"], "gap": res["gap"], "time": res["time_solve"],
        # 생산량 최대: 필수 항목 수량 × scale 까지 됨 (예산 없으면 goal=min 으로 계산됨)
        "scale": min(res["scales"], default=1.0), "scale_max": max(res["scales"], default=1.0), "goal": res["goal"], "goal_req": "min" if body.get("goal") == "min" else "max",
        "node_cp": res["node_cp"], "house_cp": res["house_cp"],
        # got = 노드 생산 + 보유분 하루 몫
        "slots": [{"label": l, "qty": q, "got": got + stock.get(si, 0), "ids": list(a), "mode": "node", "nice": nice_slots.get(si, 0),
                   "stock": stock_n[si], "stock_daily": stock.get(si, 0),
                   "items": [item_name(i) for i in a if i in plan.node_items(D)][:8]}
                  for si, ((l, a, q), got) in enumerate(zip(slots, res["got"]))],
        "chosen": [{
            "zone": c["zone"], "zone_name": plan.zone_label(D, c["zone"]),
            "town": c["town"], "town_name": D["towns"][c["town"]]["name"], "region": REGION[c["town"]][0],
            "giant": c["worker"]["giant"], "kind": plan.KIND_KO[c["worker"]["kind"]],
            "wspd": c["worker"]["wspd"], "mspd": c["worker"]["mspd"], "luck": c["worker"]["luck"],
            "workload": D["zones"][c["zone"]]["workload"], "dist": D["zones"][c["zone"]]["dist"][c["town"]],
            "yields": [{"label": slots[si][0], "daily": y} for si, y in c["yields"].items()],
            "route": c["route"],
        } for c in sorted(res["chosen"], key=lambda c: town_key(c["town"]))],
        "node_sets": node_sets(res["chosen"], slots),
        "invested": [{"k": v, "name": plan.zone_label(D, v), "cp": g[v]["cp"]} for v in res["invested"]],
        "houses": [{**{f: h.get(f) for f in ("key", "name", "use", "owned", "cp", "cost", "lodge_lv", "beds",
                                              "store_lv", "slots", "note", "pos", "eff")},
                    "reserved": h["key"] in reserved, "owned": h["key"] in owned_houses,
                    "town_name": D["towns"][h["town"]]["name"], "region": REGION[h["town"]][0]}
                   for h in sorted(res["houses"], key=lambda h: (town_key(h["town"]), h["use"], h["name"]))],
        "storage": [{"town_name": D["towns"][tk]["name"], "region": REGION[tk][0], **st} for tk, st in sorted(res["storage"].items(), key=lambda x: town_key(x[0]))],
        "extra": [{"town_name": D["towns"][tk]["name"], "region": REGION[tk][0], "n": n} for tk, n in sorted(res["extra"].items(), key=lambda x: town_key(x[0]))],
        "imperial": res["fixed"].get(IMPERIAL_TOWN, 0) if IMPERIAL_TOWN else 0,
    })
    return out


def items_api(q):
    """코덱스 전체 아이템 이름 검색 (조합식 유무 표시)"""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    by_pid = {r["product_id"] for r in D["recipes"]}
    hits = sorted((i for i, n in D["items"].items() if q in n), key=lambda i: (not D["items"][i].startswith(q), len(D["items"][i]), i))
    out = []
    for i in hits[:40]:
        how = ("노드 생산" if i in plan.node_items(D) else "요리/연금" if i in by_pid
               else D["processing"][i][0]["method"] if i in D["processing"] else None)
        out.append({"id": i, "name": D["items"][i], "how": how})
    return out


def read_manual():
    return json.loads(plan.MANUAL.read_text(encoding="utf-8"))


def write_manual(data):
    plan.MANUAL.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 지운 조합식이 원래 데이터로 돌아가도록 가공 목록을 새로 만든 뒤 다시 덮어씀
    D["processing"] = dict(D["_base_processing"])
    plan.apply_overrides(D)


def manual_list():
    return [{**m, "input_names": [item_name(i) for i, _ in m["inputs"]]} for m in read_manual()["recipes"]]


def manual_save(m):
    """잘못된 입력이면 ValueError (메시지는 화면에 그대로 보여줌)"""
    rec = {"product": int(m["product"]), "name": item_name(int(m["product"])), "method": str(m.get("method") or "기타"),
           "inputs": [[int(i), int(q)] for i, q in m["inputs"] if int(q) > 0], "out": float(m.get("out") or 1),
           "source": "웹 화면에서 직접 추가 " + time.strftime("%Y-%m-%d")}
    if not rec["inputs"]:
        raise ValueError("재료를 하나 이상 넣어주세요")
    data = read_manual()
    data["recipes"] = [r for r in data["recipes"] if r["product"] != rec["product"]] + [rec]
    write_manual(data)
    return rec


def manual_delete(pid):
    data = read_manual()
    data["recipes"] = [r for r in data["recipes"] if r["product"] != pid]
    write_manual(data)
    return {"ok": True}
