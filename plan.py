"""완성품 → 필요 원재료 → 공헌도 최소 노드·숙소 조합 (HiGHS 최적화)

사용 예:
  python plan.py 맥주 "새고기 튀김" --crafts 20
  python plan.py 맥주 --budget 10 --owned "바탈리 농장,핀토 농장" --beds 벨리아=3
  python plan.py 맥주 --list            # 최적화 없이 재료별 후보 노드 순위만
"""
import argparse
import heapq
import json
import math
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "build" / "planner.json"
MANUAL = DATA.parent.parent / "manual_recipes.json"
AUTO = DATA.parent.parent / "auto_recipes.json"
SOURCING = DATA.parent.parent / "sourcing_defaults.json"
SOURCES = DATA.parent.parent / "item_sources.json"      # 코덱스 판별 {id: {sold, farm}}
VENDOR_MANUAL = DATA.parent.parent / "vendor_manual.json"  # 사용자가 알려준 상점 품목
# 분해·변환 같은 가공법은 거슬러 올라가지 않음 (한 번에 20개 넘게 나오거나, 결과 1개에 입력 100개 넘게 드는 것)
MAX_PROC_OUT = 20
MAX_INPUT_PER_OUT = 100
WSPD_SKILL_BONUS = 5  # workerman profitPzTownArtisans 와 동일하게 작업속도 +5 가정
# 마을별 일꾼 목록 순서 (build_data.py: 고블린, 거인, 인간)
WORKER_KINDS = ("goblin", "giant", "human")
KIND_KO = {"goblin": "고블린", "giant": "거인", "human": "인간"}
# 기본은 노드마다 하루 생산량이 가장 많은 일꾼 (패치 후 거인 트립당 산출이 고블린의 약 1.65배)
DEFAULT_KINDS = WORKER_KINDS


def load():
    d = json.loads(DATA.read_text(encoding="utf-8"))
    d["items"] = {int(k): v for k, v in d["items"].items()}
    d["towns"] = {int(k): {**v, "houses": {int(h): x for h, x in v["houses"].items()}}
                  for k, v in d["towns"].items()}
    for t in d["towns"].values():
        for w, kind in zip(t["workers"], WORKER_KINDS):
            w["kind"] = kind
    d["zones"] = {int(k): {**v, **{f: {int(i): q for i, q in v[f].items()}
                                   for f in ("unlucky", "unlucky_gi", "lucky", "dist")}}
                  for k, v in d["zones"].items()}
    d["graph"] = {int(k): v for k, v in d["graph"].items()}
    d["processing"] = {int(k): v for k, v in d["processing"].items()}
    d["_base_processing"] = dict(d["processing"])
    apply_overrides(d)
    load_sourcing(d)
    return d


def load_sourcing(d):
    """기본 조달 방법 데이터: 사용자 상점 목록, 코덱스 판매·재배 판별, 병목 키워드"""
    manual = set()
    if VENDOR_MANUAL.exists():
        for shop in json.loads(VENDOR_MANUAL.read_text(encoding="utf-8"))["buy"].values():
            manual |= set(shop.values())
    d["_vendor_manual"] = manual
    d["_sources"] = {int(k): v for k, v in json.loads(SOURCES.read_text(encoding="utf-8")).items()} if SOURCES.exists() else {}
    d["_self_kw"] = json.loads(SOURCING.read_text(encoding="utf-8")).get("self_keywords", []) if SOURCING.exists() else []
    d.pop("_reach", None)
    order_variants(d)


def order_variants(d):
    """조합(변형)이 여러 개인 레시피는 '노드·구매로 구할 수 있는 칸이 많은' 조합을 기본(0번)으로.
    예: 볏골 원조 국밥 — 고급 배추 1(재배뿐) 보다 배추 2(노드) 조합이 기본. 같으면 대체 가능한 칸이 많은 쪽"""
    for r in d["recipes"]:
        if len(r["variants"]) < 2:
            continue

        def score(v):
            easy = 0
            for m in v:
                acc = tuple(m.get("members", [m["id"]]))
                mode = slot_mode(d, acc)
                if mode in ("node", "buy") and (mode == "buy" or any(reachable(d, a) for a in acc)):
                    easy += 1
            return (-(len(v) - easy), sum("members" in m for m in v))  # (구하기 어려운 칸 적을수록, 대체 칸 많을수록)

        # 점수는 정렬 전에 계산 (score 가 다른 레시피의 variants[0] 을 읽으므로 정렬 중 리스트를 건드리지 않게)
        scored = [(score(v), n, v) for n, v in enumerate(r["variants"])]
        r["variants"] = [v for _, _, v in sorted(scored, key=lambda x: (x[0][0], x[0][1], -x[1]), reverse=True)]


def apply_overrides(d):
    """코덱스 설명에서 자동으로 읽은 조합식, 그다음 직접 추가한 조합식으로 가공 목록을 덮어씀"""
    if AUTO.exists():
        for iid, e in json.loads(AUTO.read_text(encoding="utf-8")).items():
            if e.get("status") == "ok" and int(iid) not in d["_base_processing"]:
                d["processing"][int(iid)] = [{"method": e["method"], "inputs": e["inputs"], "out": e["out"], "auto": True}]
                for i, n in e.get("names", {}).items():
                    d["items"].setdefault(int(i), n)
    if MANUAL.exists():
        for m in json.loads(MANUAL.read_text(encoding="utf-8"))["recipes"]:
            d["processing"][m["product"]] = [{"method": m["method"], "inputs": m["inputs"], "out": m["out"], "manual": True}]
            d["items"][m["product"]] = m["name"]
    d.pop("_by_product", None)
    d.pop("_reach", None)


def find_recipe(d, name):
    exact = [r for r in d["recipes"] if r["name"] == name]
    if exact:
        return exact[0]
    part = [r for r in d["recipes"] if name in r["name"]]
    if not part:
        raise SystemExit(f"레시피 없음: {name}")
    if len(part) > 1:
        print(f"  '{name}' 후보: {', '.join(r['name'] for r in part[:10])} → {part[0]['name']} 사용")
    return part[0]


MIN_DIRECT_YIELD = 5  # 노드에서 트립당 이보다 적게 '덤'으로만 나오는 재료는, 가공 경로가 있으면 가공 쪽으로 거슬러 감


def direct_node(d, i, seen=frozenset()):
    """재료 i 를 노드에서 직접 얻는 것으로 볼지. 주 생산(트립당 5개 이상)이면 직접,
    덤으로만 나오고(예: 녹아내린 구리 조각 — 별무덤 채광 2.4개) 노드 재료를 가공하는 길이 있으면 가공 쪽으로"""
    if i not in node_items(d):
        return False
    if "_max_yield" not in d:
        my = {}
        for z in d["zones"].values():
            for x, q in z["unlucky"].items():
                my[x] = max(my.get(x, 0), q)
        d["_max_yield"] = my
    strong = lambda x: d["_max_yield"].get(x, 0) >= MIN_DIRECT_YIELD  # noqa: E731
    if strong(i):
        return True
    # 가공 입력이 노드 주 생산품일 때만 가공 쪽으로 (오팔 원석 ← 개량석 빻기 같은 분해 경로는 X)
    bp = best_proc(d, i, seen | {i})
    return not (bp and strong(bp["inputs"][0][0]))


def node_items(d):
    if "_node_items" not in d:
        d["_node_items"] = {i for z in d["zones"].values() for i in z["unlucky"]}
    return d["_node_items"]


MAX_DEPTH = 12  # 순환은 seen 으로 막으므로 깊은 연금 체인([파티] 영약 등)도 끝까지 펼침


SOURCE_KO = {"node": "노드", "buy": "구매", "farm": "재배", "self": "따로 모음"}


def sourcing(d, i, src=None):
    """아이템 조달 방법: 'node'(노드·가공으로 거슬러 감) / 'buy'(상점 구매) / 'farm'(텃밭 재배) / 'self'(따로 모음).
    화면에서 바꾼 값(src) > 사용자 상점 목록 > 코덱스 판매 > 병목 키워드 > 재배(노드에 없을 때만) > node"""
    if src and i in src:
        return src[i]
    info = d.get("_sources", {}).get(i, {})
    # 노드에서 나오는 재료는 사용자가 알려준 상점 목록에 있을 때만 구매 (코덱스 '판매' 표시엔 특수 상점도 섞여 있음)
    if i in d.get("_vendor_manual", ()) or (info.get("sold") and i not in node_items(d)):
        return "buy"
    name = d["items"].get(i, "")
    if any(k in name for k in d.get("_self_kw", [])):
        return "self"
    # 재배: 노드에서도 안 나오고, 레시피·가공으로도 못 만드는 재료만 (곡물가루처럼 코덱스가 '농산물'로 표시해도 빻기로 만들 수 있으면 노드 쪽)
    if info.get("farm") and i not in node_items(d) and i not in by_product(d)             and not any(usable_proc(p) for p in d["processing"].get(i, [])):
        return "farm"
    return "node"


def by_product(d):
    if "_by_product" not in d:
        d["_by_product"] = {r["product_id"]: r for r in d["recipes"]}
    return d["_by_product"]


def reachable(d, i, stack=frozenset(), strict=False):
    """아이템 i 를 레시피·가공으로 거슬러 올라가면 구할 수 있는 재료(노드·구매·재배·따로 모음)에 닿는지 (순환 경로는 제외)"""
    """strict=True 면 노드 생산품에 닿는 경로만 인정 (가공법 고를 때 노드 쪽 경로를 우선하기 위해)"""
    memo = d.setdefault("_reach", {})
    if (i, strict) in memo:
        return memo[(i, strict)]
    # 노드 생산품, 그리고 (strict 가 아니면) 구매·재배·따로 모음으로 정한 재료는 정상적인 끝점
    if i in node_items(d) or (not strict and sourcing(d, i) != "node"):
        return True
    if i in stack or len(stack) >= MAX_DEPTH:
        return False
    stack = stack | {i}
    r = by_product(d).get(i)
    ok = False
    if r:
        ok = any(reachable(d, a, stack, strict) for m in r["variants"][0] for a in m.get("members", [m["id"]]))
    if not ok:
        ok = any(reachable(d, x, stack, strict) for p in d["processing"].get(i, []) if usable_proc(p) for x, _ in p["inputs"])
    if len(stack) == 1:  # 최상위 호출 결과만 저장 (중간 결과는 경로에 따라 달라질 수 있음)
        memo[(i, strict)] = ok
    return ok


def usable_proc(p):
    if p.get("manual") or p.get("auto") or p.get("workshop"):  # 작업장 설계는 재료 200개도 정상
        return True
    return p["out"] <= MAX_PROC_OUT and all(q / p["out"] <= MAX_INPUT_PER_OUT for _, q in p["inputs"])


def best_proc(d, i, seen):
    """아이템 i 의 가공법 중: 입력이 경로에 없는(순환 아님) 것 → 노드까지 닿는 입력이 많은 것 → 입력 수량이 적은 것"""
    best = None
    for p in d["processing"].get(i, []):
        ins = [x for x, _ in p["inputs"]]
        if any(x in seen for x in ins) or not usable_proc(p):
            continue
        reach = sum(reachable(d, x, frozenset(seen)) for x in ins)
        if not reach:
            continue
        # 노드 생산품까지 닿는 입력 비율을 먼저 (분해·개량석 같은 경로보다 원석·농산물 가공을 우선)
        node_reach = sum(reachable(d, x, frozenset(seen), strict=True) for x in ins)
        direct = sum(x in node_items(d) for x in ins)  # 입력이 바로 노드 생산품인 것 (예: 세공된 오팔 ← 오팔 원석)
        score = (p.get("manual", False), direct / len(ins), node_reach / len(ins), reach / len(ins),
                 -sum(q for _, q in p["inputs"]) / p["out"])
        if not best or score > best[0]:
            best = (score, p)
    return best[1] if best else None


def expand(d, recipe, crafts, need, chain, depth=0, variant=0, src=None, seen=frozenset(), tree=None):
    """레시피를 원재료 칸까지 펼친다 (요리/연금 1회 제작 = 1개 가정). 하위 레시피는 기본 조합(0번).
    tree: 주면 거쳐 간 재료마다 {depth, label, qty, how} 를 순서대로 담음 (하위 재료 전체 수량 표시용)"""
    seen = seen | {recipe["product_id"]}
    for m in recipe["variants"][variant]:
        accept = tuple(m.get("members", [m["id"]]))
        label = m.get("group") or d["items"].get(m["id"], str(m["id"]))
        expand_slot(d, label, accept, crafts * m["qty"], need, chain, depth, src, seen, tree)


def expand_slot(d, label, accept, qty, need, chain, depth, src=None, seen=frozenset(), tree=None):
    """재료 칸 하나: 노드 생산품·구매·따로 모음이면 그대로 두고, 아니면 하위 레시피 → 가공(빻기 등) 순으로 거슬러 올라간다.
    순환 경로는 막고, 노드 생산품까지 닿지 못하는 재료(블랙스톤 등 병목)는 더 펼치지 않는다."""
    from_nodes = node_items(d)
    pad = "  " * depth
    def mark(how):
        if tree is not None:
            tree.append({"depth": depth, "label": label, "qty": qty, "how": how})
    if slot_mode(d, accept, src) != "node" or any(direct_node(d, i, seen) for i in accept) or depth >= MAX_DEPTH:
        mark("노드" if slot_mode(d, accept, src) == "node" and any(i in node_items(d) for i in accept) else other_reason(d, accept, src))
        need[(label, accept)] += qty
        return
    sub = by_product(d).get(accept[0])
    if sub and accept[0] not in seen and reachable(d, accept[0]):
        chain.append(f"{pad}{label} x{qty:g} ← {sub['name']} 레시피로 제작")
        mark(sub["kind"] if sub.get("kind") else "레시피")
        expand(d, sub, qty, need, chain, depth + 1, src=src, seen=seen, tree=tree)
        return
    # 칸이 허용하는 아이템마다 가장 좋은 가공법. 모두 같은 방식·입력 1종이면 (예: 곡물가루 ← 곡물 빻기) 입력을 대체 가능으로 묶음
    procs = [(a, best_proc(d, a, seen | {a})) for a in accept if a not in seen]
    procs = [(a, p) for a, p in procs if p]
    if not procs and depth == 0:
        # 직접 고른 아이템이 노드까지 안 닿아도, 무엇을 모아야 하는지 보이도록 한 단계는 펼침
        procs = [(a, p) for a in accept for p in d["processing"].get(a, [])[:1]]
    if not procs:
        mark(other_reason(d, accept, src))
        need[(label, accept)] += qty  # 병목: 노드까지 닿는 경로 없음 → 따로 모아야 하는 재료
        return
    first = procs[0][1]
    group = [p for _, p in procs if p["method"] == first["method"] and len(p["inputs"]) == len(first["inputs"])]
    out = min(p["out"] for p in group)
    main_accept = tuple(sorted({p["inputs"][0][0] for p in group}))
    names = [d["items"].get(i, str(i)) for i in main_accept]
    # 여러 개면 "밀가루 등 5종" (예전엔 '… 원료' 라서 반죽 → 가루 → 곡물이 같은 이름으로 겹쳐 보였음)
    plain = [n for n in names if not n.startswith("[")] or names
    main_label = names[0] if len(names) == 1 else f"{min(plain, key=len)} 등 {len(names)}종"
    note = " · 직접 추가한 조합식" if first.get("manual") else " · 코덱스 설명에서 자동으로 읽음" if first.get("auto") else ""
    chain.append(f"{pad}{label} x{qty:g} ← {first['method']} (평균 {out:g}개씩 나온다고 가정{note})")
    mark(first["method"])
    seen = seen | set(accept)
    expand_slot(d, main_label, main_accept, math.ceil(qty * first["inputs"][0][1] / out), need, chain, depth + 1, src, seen, tree)
    for i, q in first["inputs"][1:]:
        expand_slot(d, d["items"].get(i, str(i)), (i,), math.ceil(qty * q / out), need, chain, depth + 1, src, seen, tree)


def slot_mode(d, accept, src=None):
    """대체 가능한 칸 전체의 조달 방법.
    화면에서 칸의 재료를 직접 바꿨으면 그대로 → 살 수 있는 재료가 하나라도 있으면 구매(예: 채소류 ← 파프리카)
    → 노드로 얻을 수 있는 재료가 하나라도 있으면 노드(예: 곡물류 — 고급·특상품은 재배뿐이어도 일반 곡물은 노드)
    → 그 외에는 따로 모음 / 재배"""
    if src:
        picked = [src[i] for i in accept if i in src]
        if picked:
            return picked[0]
    modes = {sourcing(d, i) for i in accept}
    if "buy" in modes:
        return "buy"
    # 노드 생산품이거나, 레시피·가공으로 노드 재료까지 거슬러 갈 수 있는 재료가 하나라도 있으면 노드 (예: 곡물가루 ← 빻기 ← 곡물)
    if "node" in modes and any(sourcing(d, i) == "node" and (i in node_items(d) or reachable(d, i, strict=True))
                               for i in accept):
        return "node"
    for m in ("self", "farm"):
        if m in modes:
            return m
    return "node"


def other_reason(d, accept, src=None):
    """노드 계산에서 빠지는 이유: 구매 / 재배 / 따로 모음 / 노드 없음"""
    m = slot_mode(d, accept, src)
    return SOURCE_KO[m] if m != "node" else "노드 없음"


def make_slots(d, need, src=None):
    """need {(라벨, 허용 아이템): 수량} → 최적화용 칸 [(라벨, 허용, 수량)], 노드 계산에서 빠지는 재료 [(라벨, 수량, 허용)].
    구매·따로 모음으로 정한 재료는 노드가 있어도 뺀다 (대체 그룹은 하나라도 구매면 칸 전체 구매).
    노드 아이템이 겹치는 칸은 하나로 (예: 곡물류 + 곡물가루 원료 = 곡물)"""
    from_nodes = node_items(d)
    merged, others = {}, []
    for (label, accept), q in need.items():
        if slot_mode(d, accept, src) != "node":
            others.append((label, q, accept))
            continue
        key = frozenset(i for i in accept if i in from_nodes)
        if not key:
            others.append((label, q, accept))
            continue
        # 노드 아이템이 하나라도 겹치는 칸은 합침 (예: 밀가루용 '밀 등 5종' + 맥주용 '곡물류' → 곡물류 한 칸).
        # 따로 두면 같은 곡물 생산량을 두 칸이 모두 자기 몫으로 세서 부족이 안 보였음
        for k in [k for k in merged if k & key]:
            ol, oa, oq = merged.pop(k)
            key = key | k
            accept = tuple(sorted(set(accept) | set(oa)))
            # 이름은 원래 그룹 이름 쪽을 (가공으로 거슬러 온 '… 등 N종'·'… 원료' 보다)
            if (" 등 " in label or label.endswith(" 원료")) and not (" 등 " in ol or ol.endswith(" 원료")):
                label = ol
            q += oq
        merged[key] = (label, accept, q)
    return list(merged.values()), others


def cp_paths(d, start, cost=None):
    """마을 waypoint에서 각 노드까지 최소 공헌도 (경유 노드 CP 합, 출발 마을 제외)"""
    g = d["graph"]
    cost = cost or {v: n["cp"] for v, n in g.items()}
    dist = {start: 0}
    pq = [(0, start)]
    while pq:
        c, u = heapq.heappop(pq)
        if c > dist.get(u, math.inf):
            continue
        for v in g.get(u, {}).get("links", []):
            if v not in g:
                continue
            nc = c + cost[v]
            if nc < dist.get(v, math.inf):
                dist[v] = nc
                heapq.heappush(pq, (nc, v))
    return dist


RESOURCE_DEFAULT = 0  # 지역 자원량(%) 기본값. workerman 과 같은 0% = 작업량 2배 (가장 보수적)


def active_workload(d, workload):
    """실제 작업량 = 기본 작업량 × (2 − 지역 자원량). 게임 확인: 우르나스 산맥 발굴 2550 → 자원량 약 18% 에서 4633.35"""
    pct = d.get("_resource_pct", RESOURCE_DEFAULT)
    return workload * (2 - pct / 100)


def cycles_daily(workload, wspd, dist, mspd):
    """workerman user.js calcCyclesDaily. workload 는 지역 자원량을 반영한 실제 작업량 (active_workload)"""
    move = 2 * dist / mspd / 60
    return 24 * 60 / (10 * math.ceil(workload / wspd) + move)


def zone_label(d, zk):
    """노드 이름. 같은 거점에 같은 이름 노드가 여럿이면 (예: 별리숲 벌목 2개, 고두 마을 재배 4개)
    주재료가 다르면 '고두 마을 재배 (배추)', 주재료까지 같으면 '별리숲 벌목 1/2' 로 구분"""
    if "_zone_labels" not in d:
        base = {k: f"{z['parent']} {z['name']}" for k, z in d["zones"].items()}
        groups = defaultdict(list)
        for k, b in base.items():
            groups[b].append(k)
        labels = dict(base)
        for b, ks in groups.items():
            if len(ks) < 2:
                continue
            main = {k: d["items"].get(max(d["zones"][k]["unlucky"], key=d["zones"][k]["unlucky"].get), "")
                    if d["zones"][k]["unlucky"] else "" for k in ks}
            by_main = defaultdict(list)
            for k in sorted(ks):
                by_main[main[k]].append(k)
            for m, mk in by_main.items():
                for n, k in enumerate(mk, 1):
                    # 주재료가 같으면 그 노드에만 나오는 재료로 구분 (예: 도깨비숲 발굴 (조화의 결정) / (숲의 결정)), 없으면 번호
                    others = set().union(*[set(d["zones"][o]["unlucky"]) for o in mk if o != k]) if len(mk) > 1 else set()
                    own = [i for i in d["zones"][k]["unlucky"] if i not in others]
                    sub = d["items"].get(own[0], "") if (len(mk) > 1 and own) else (str(n) if len(mk) > 1 else "")
                    tag = " ".join(x for x in ((m if len(by_main) > 1 else ""), sub) if x)
                    labels[k] = f"{b} ({tag})" if (len(by_main) > 1 or own) else f"{b} {n}"
        d["_zone_labels"] = labels
    if zk in d["zones"]:
        return d["_zone_labels"][zk]
    return d["graph"][zk]["name"]


def resolve_owned(d, text):
    """'바탈리 농장,1234' → 노드 키 집합. 이름이 같은 노드가 여러 개면 전부."""
    owned = set()
    for tok in filter(None, (t.strip() for t in (text or "").split(","))):
        if tok.isdigit():
            owned.add(int(tok))
            continue
        hits = {k for k, n in d["graph"].items() if n["name"] == tok}
        hits |= {k for k in d["zones"] if zone_label(d, k) == tok}
        if not hits:
            raise SystemExit(f"노드 이름을 못 찾음: {tok}")
        owned |= hits
    return owned


def resolve_houses(d, text):
    """'벨리아 1번지 1호실,2125' → 집 키 집합"""
    keys = set()
    all_h = {k: h for t in d["towns"].values() for k, h in t["houses"].items()}
    for tok in filter(None, (t.strip() for t in (text or "").split(","))):
        if tok.isdigit():
            keys.add(int(tok))
            continue
        hits = [k for k, h in all_h.items() if h["name"] == tok] or [k for k, h in all_h.items() if tok in h["name"]]
        if len(hits) != 1:
            raise SystemExit(f"집 이름이 {'없음' if not hits else '여러 개'}: {tok}")
        keys.add(hits[0])
    return keys


def resolve_beds(d, text):
    beds = {}
    for tok in filter(None, (t.strip() for t in (text or "").split(","))):
        name, n = tok.split("=")
        tks = [tk for tk, t in d["towns"].items() if name in t["name"]]
        if not tks:
            raise SystemExit(f"마을 없음: {name}")
        beds[tks[0]] = int(n)
    return beds


def town_workers(d, tk, kinds):
    return [w for w in d["towns"][tk]["workers"] if w["kind"] in kinds]


def print_list(d, slots, top, kinds=DEFAULT_KINDS):
    """최적화 없이 재료별 후보 노드를 CP당 생산량 순으로"""
    paths = {tk: cp_paths(d, t["waypoint"]) for tk, t in d["towns"].items() if t["waypoint"] in d["graph"]}
    for label, accept, qty in slots:
        rows = []
        for zk, z in d["zones"].items():
            if not any(i in z["unlucky"] for i in accept):
                continue
            for tk, p in paths.items():
                if tk not in z["dist"] or zk not in p:
                    continue
                for w in town_workers(d, tk, kinds):
                    drops = z["unlucky_gi"] if w["giant"] else z["unlucky"]
                    daily = cycles_daily(active_workload(d, z["workload"]), w["wspd"] + WSPD_SKILL_BONUS, z["dist"][tk], w["mspd"]) \
                        * sum(drops.get(i, 0) for i in accept)
                    rows.append((daily / max(p[zk], 1), zk, tk, daily, p[zk], w))
        best = {}
        for r in sorted(rows, key=lambda r: -r[0]):
            best.setdefault(r[1], r)
        print(f"\n▶ {label}  하루 {qty}개 필요")
        for per, zk, tk, daily, cp, w in list(best.values())[:top]:
            print(f"    {zone_label(d, zk):<16s} ← {d['towns'][tk]['name']:<8s} CP {cp:>2} | 하루 {daily:6.0f}개 "
                  f"| CP당 {per:5.0f} | {KIND_KO[w['kind']]}")


def main():
    ap = argparse.ArgumentParser(description="검은사막 완성품 기준 노드 추천")
    ap.add_argument("products", nargs="+", help="완성품 이름 (예: 맥주)")
    ap.add_argument("--crafts", type=int, default=10, help="완성품별 제작 횟수 (기본 10, --days 가 있으면 기간 전체 합계)")
    ap.add_argument("--days", type=int, default=1, help="목표 기간(일). 필요량을 이 일수로 나눠 하루 몫을 계산")
    ap.add_argument("--owned", help="이미 투자한 노드 (쉼표 구분, 이름 또는 키)")
    ap.add_argument("--beds", help="마을별 추가 숙소(펄 등), 예: 벨리아=2,하이델=1")
    ap.add_argument("--owned-houses", help="이미 가진 집 (쉼표 구분, 코덱스 집 이름 일부 또는 집 키)")
    ap.add_argument("--no-storage", action="store_true", help="창고 칸 계산 안 함")
    ap.add_argument("--workers", default="all",
                    help="쓸 일꾼 종류 (쉼표: goblin,giant,human 또는 all). 기본 all = 노드마다 가장 많이 생산하는 일꾼")
    ap.add_argument("--value-pack", action="store_true", help="가치 패키지 (마을 기본 창고 +16칸)")
    ap.add_argument("--budget", type=int, help="쓸 수 있는 공헌도 (노드+집)")
    ap.add_argument("--towns-per-zone", type=int, default=3, help="노드마다 고려할 일꾼 마을 수")
    ap.add_argument("--zones-per-slot", type=int, default=6, help="재료마다 고려할 후보 노드 수")
    ap.add_argument("--time", type=float, default=60, help="최적화 시간 제한(초). 넘으면 그때까지 찾은 최선의 답")
    ap.add_argument("--list", action="store_true", help="최적화 없이 재료별 후보 순위만")
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    d = load()
    need = defaultdict(int)
    for name in args.products:
        r = find_recipe(d, name)
        chain = []
        expand(d, r, args.crafts, need, chain)
        mats = " + ".join(f"{m.get('group') or d['items'].get(m['id'])} x{m['qty']}" for m in r["variants"][0])
        print(f"■ {r['name']} ({r['kind']}) x{args.crafts}/일 = {mats}")
        for line in chain:
            print("   ", line)

    need = {k: math.ceil(v / max(1, args.days)) for k, v in need.items()}
    slots, others = make_slots(d, need)
    others = [f"{label} x{q} ({other_reason(d, acc)})" for label, q, acc in others]

    kinds = WORKER_KINDS if args.workers == "all" else tuple(k.strip() for k in args.workers.split(","))
    if not set(kinds) <= set(WORKER_KINDS):
        raise SystemExit(f"--workers 는 {', '.join(WORKER_KINDS)} 또는 all")
    if args.list:
        print_list(d, slots, args.top, kinds)
    else:
        import optimize
        owned = resolve_owned(d, args.owned)
        owned_houses = resolve_houses(d, args.owned_houses)
        res = optimize.solve(d, slots, owned=owned, extra_beds=resolve_beds(d, args.beds),
                             owned_houses=owned_houses, storage=not args.no_storage, worker_kinds=kinds,
                             base_storage=32 if args.value_pack else 16,
                             budget=args.budget, towns_per_zone=args.towns_per_zone,
                             zones_per_slot=args.zones_per_slot, time_limit=args.time)
        print(f"\n{'=' * 72}")
        if res["status"] not in ("Optimal", "Time limit reached"):
            print(f"풀이 실패: {res['status']}")
            return
        total = res["node_cp"] + res["house_cp"]
        print(f"총 공헌도 {total}  (노드 {res['node_cp']} + 집 {res['house_cp']})"
              + (f" / 예산 {args.budget}" if args.budget is not None else "")
              + f"   [{res['status']}, 갭 {res['gap']:.1%}, {res['time_solve']:.1f}초]")
        print("=" * 72)
        print("\n일꾼 배치")
        for c in sorted(res["chosen"], key=lambda c: d["towns"][c["town"]]["name"]):
            made = ", ".join(f"{slots[si][0]} {y:.0f}/일" for si, y in c["yields"].items())
            print(f"  {d['towns'][c['town']]['name']:<8s} {KIND_KO[c['worker']['kind']]:<4s} → "
                  f"{zone_label(d, c['zone'])}  ({made})")
        print("\n재료 충족")
        for (label, _, q), got in zip(slots, res["got"]):
            mark = "✔" if got >= q - 1e-6 else "✘ 부족"
            print(f"  {mark} {label}: 필요 {q} / 확보 {got:.0f}")
        print("\n투자할 노드: " + (", ".join(f"{zone_label(d, v)}({d['graph'][v]['cp']})" for v in res["invested"]) or "없음"))
        if res["houses"]:
            print("\n집")
            for h in sorted(res["houses"], key=lambda h: (d["towns"][h["town"]]["name"], h["use"])):
                what = (f"숙소 {h['lodge_lv']}단계 → {h['beds']}명" if h["use"] == "숙소"
                        else f"창고 {h['store_lv']}단계 → {h['slots']}칸" if h["use"] == "창고" else "선행 조건 (다음 집 구매용)")
                tag = "보유" if h["owned"] else f"{h['cp']}CP"
                note = f"  [시트: {h['note']}]" if h["note"] else ""
                print(f"  {h['name']} ({tag}) — {what}{note}")
        for tk, st in res.get("storage", {}).items():
            print(f"  창고 {d['towns'][tk]['name']}: 필요 {st['need']}칸 / 확보 {st['cap']}칸")
        if owned:
            print(f"보유 노드 {len(owned)}개는 0 CP로 계산")

    if others:
        print(f"\n노드 계산에서 빠진 재료: {', '.join(others)}")


if __name__ == "__main__":
    main()
