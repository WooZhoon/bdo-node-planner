"""HiGHS MIP: 재료 하루 필요량을 채우는 노드·일꾼 마을·경로·숙소·창고 조합 중 공헌도 최소

변수
  x_v      노드 v 투자 (이미 보유한 노드는 0 CP)
  a_{z,t}  생산 노드 z 를 마을 t 일꾼이 담당
  r_{p,k}  (노드,마을) p 의 k번째 후보 경로 사용 (경로 위 노드는 모두 투자해야 함)
  b_k      집 k 구매 (보유한 집은 0 CP), l_k / w_k 집 k 를 숙소 / 창고로 사용 (l + w ≤ b, 선행 집 필요)
  u_{t,i}  마을 t 창고에 아이템 i 가 쌓임 (창고 칸 = 아이템 종류 수 + 은화 1칸)
  s_i      재료 i 부족분 (필요량을 다 못 채워도 답이 나오도록)
목적: 투자 CP(노드+집) + 부족 벌점. --budget 이 있으면 CP 합 ≤ 예산
  goal="max" (생산량 최대, 예산 필요): 배율 T 를 더 둬서 필수 수량 × (1+T) 를 채우게 하고 T 에 큰 보상 →
  예산 안에서 레시피 비율 그대로 최대한 많이, 같은 생산량이면 CP 적게
  노드로 다 못 채우면 반대로 항목 수량을 줄임 (배율 1 − D, D 에 벌점)
"""
import time
from collections import defaultdict

import highspy
import networkx as nx
import numpy as np

import plan

SHORTAGE_PENALTY = 1000  # '필수' 재료 하나를 완전히 못 채울 때 벌점 (CP 1000 상당 → 사실상 최우선)
# 최적과 이 비율 안으로 들어오면 그만 계산 (사용자 선택 2%). 큰 조합(노드 칸 44): 2% = 25초 445 CP,
# 1% = 1.4% 근처에서 안 줄어 시간 제한까지 감(441 CP). 작은 조합은 1초 안에 끝남
MIP_GAP = 0.02
NICE_PENALTY = 10        # '여유' 재료 하나를 완전히 못 채울 때 벌점 (CP 10 상당 → 싸게 되면 채우고, 비싸면 포기)
# 생산량 최대: 항목 수량 +100% 당 보상 (CP 상당) — (배율 구간 끝, 보상). 필수 부족 벌점(1000)보다 작아 부족을 만들며 늘리진 않음
SCALE_SEGMENTS = [(1.0, 400), (3.0, 150), (float("inf"), 50)]
SCALE_MAX = 50           # 배율 상한 (×51)
# 생산량 최대의 우선순위: 노드로 얻을 수 있는 재료 부족 > 입력 수량 줄이기 > 손채집 재료 부족 > 늘리기 > 여유 > 공헌도
# 숫자를 감으로 정하지 않고, 윗순위 1단위가 아랫순위를 다 합친 것보다 크게 계산해서 정함 (scripts/check_cases.py 로 확인)
# 줄이기: (배율 구간 끝, 배수). 벌점 = (모든 항목을 끝까지 늘렸을 때의 보상 합 + 1000) × 배수 →
# 하나를 조금 줄이고 다른 걸 늘리는 거래가 생기지 않음. 많이 줄일수록 커서 한 항목만 확 줄이기보다 고르게
SCALE_DOWN = [(0.5, 1.0), (1.0, 1.5)]
# 노드로 얻을 수 있는 필수 재료 부족 (재료 하나를 완전히 못 채울 때) = 가장 큰 줄이기 벌점 × 이 값 → 부족을 남기기보다 줄임
# (10 이면 그 재료를 조금만 쓰는 항목은 줄이지 않고 부족을 남겼음 — 그 재료 비중 1% 까지 줄이게 100)
SHORT_OVER_CUT = 100


class Model:
    def __init__(self):
        self.h = highspy.Highs()
        self.h.setOptionValue("output_flag", False)
        self.n = 0

    def vars(self, n, cost, hi=1.0, integer=True):
        h = self.h
        h.addVars(n, np.zeros(n), np.full(n, hi))
        idx = np.arange(self.n, self.n + n)
        h.changeColsCost(n, idx, np.asarray(cost, dtype=float))
        if integer:
            h.changeColsIntegrality(n, idx, np.full(n, highspy.HighsVarType.kInteger))
        self.n += n
        return list(idx)

    def row(self, lo, hi, terms):
        terms = [(i, c) for i, c in terms if c != 0]
        if terms:
            self.h.addRow(lo, hi, len(terms), np.array([i for i, _ in terms], dtype=np.int32),
                          np.array([c for _, c in terms], dtype=float))


def pair_yields(d, slots, paths, towns_per_zone, zones_per_slot, kinds=plan.DEFAULT_KINDS, grow=False):
    """(노드, 마을) 후보와 재료별 하루 생산량. 마을은 노드마다 경로 CP가 싼 순으로 상위 몇 곳만.
    grow: 생산량 최대 — 필요량을 넘어서도 늘릴 수 있게 재료마다 후보를 최대(40)까지"""
    pairs = {}
    zones = {zk for zk, z in d["zones"].items()
             if plan.zone_has(z, [i for _, accept, _ in slots for i in accept])}
    for zk in zones:
        z = d["zones"][zk]
        # 칸별 1회 생산량은 거인 여부·행운으로만 갈림 → 마을·일꾼마다 다시 더하지 않게 미리 (브라우저판에서 특히 느렸음)
        per = {}

        def slot_per(w):
            key = (w["giant"], w["luck"])
            if key not in per:
                drops = plan.worker_drops(z, w)
                per[key] = {si: v for si, v in ((si, sum(drops.get(i, 0) for i in accept))
                                                for si, (_, accept, _) in enumerate(slots)) if v > 0}
            return per[key]
        opts = []
        for tk, p in paths.items():
            if tk not in z["dist"] or zk not in p:
                continue
            best = None
            for w in plan.town_workers(d, tk, kinds):
                cyc = plan.cycles_daily(plan.active_workload(d, z["workload"]), w["wspd"] + plan.WSPD_SKILL_BONUS, z["dist"][tk], w["mspd"])
                ys = {si: cyc * v for si, v in slot_per(w).items()}
                ys = {si: y for si, y in ys.items() if y > 0}
                score = sum(y / slots[si][2] for si, y in ys.items())
                if not best or score > best[0]:
                    best = (score, w, ys)
            opts.append((p[zk], -best[0], tk, best[1], best[2]))
        for cp, _, tk, w, ys in sorted(opts, key=lambda o: o[:2])[:towns_per_zone]:
            pairs[(zk, tk)] = {"worker": w, "yields": ys, "cp": cp}
    # 재료마다 (생산량/CP) 상위 노드만 남김. 한 노드가 여러 재료에 걸리면 어느 하나에서라도 상위면 유지
    # 다만 상위 노드들로 필요량을 못 채우면(자연의 흔적처럼 노드당 생산이 적은 재료) 채울 때까지 후보를 더 넣음 (최대 40)
    keep = set()
    for si, (_, _, q) in enumerate(slots):
        best, amount = {}, {}
        for (zk, tk), p in pairs.items():
            if si in p["yields"]:
                score = (p["yields"][si] if grow else min(p["yields"][si], q)) / max(p["cp"], 1)
                best[zk] = max(best.get(zk, 0), score)
                amount[zk] = max(amount.get(zk, 0), p["yields"][si])
        ranked = sorted(best, key=lambda z: -best[z])
        n, total = 0, 0.0
        for z in ranked:
            if n >= zones_per_slot and ((total >= q * 1.3 and not grow) or n >= 40):
                break
            keep.add(z)
            total += amount[z]
            n += 1
    return {k: v for k, v in pairs.items() if k[0] in keep}


def solve(d, slots, owned=frozenset(), extra_beds=None, budget=None, towns_per_zone=3, zones_per_slot=6,
          paths_per_pair=5, time_limit=120, owned_houses=frozenset(), storage=True, base_storage=16,
          worker_kinds=plan.DEFAULT_KINDS, nice=None, lodge_eff_min=None, extra_workers=0, fixed_workers=None,
          goal="min", grow_parts=None, stock=None, progress=None, hand=frozenset()):
    """slots: [(라벨, 허용 아이템 tuple, 하루 필요량)]
    storage: 창고 칸까지 계산할지. base_storage: 마을 기본 창고 칸 (KR 16, 가치 패키지면 32)
    worker_kinds: 쓸 일꾼 종류 (goblin/giant/human). 여러 개면 노드마다 생산량이 좋은 쪽
    nice: 칸별 '여유' 수량 {칸 번호: 수량}. 필요량 중 이만큼은 부족해도 벌점이 작음 (남는 공헌도로 되는 만큼)
    extra_workers: 노드 말고 따로 둘 일꾼 수 (농사용 등, 지역 무관) — 숙소만 필요, 창고 계산 없음
    fixed_workers: 마을이 정해진 일꾼 {마을: 인원} (예: 황실 공방 — 육조거리) — 숙소만 필요
    stock: 칸별 보유분에서 하루에 꺼내 쓰는 양 {칸 번호: 하루 몫} — 그만큼은 노드 없이 채움 (보유 ÷ 소진 기간)
    progress: 계산 중 지금까지 찾은 최선의 답 요약을 받는 함수 (시작 해, 더 나은 해를 찾을 때마다, 1초마다 갭)
    hand: 모자란 만큼 사람이 채울 칸 번호 — 생산량 최대에서 이 칸이 모자라도 수량을 줄이지 않음"""
    fixed_workers = {tk: n for tk, n in (fixed_workers or {}).items() if n > 0}
    nice = nice or {}
    st = [float((stock or {}).get(si, 0)) for si in range(len(slots))]
    # 창고 효율이 이 값 이상인 집은 숙소로 쓰지 않음 (창고용으로 아껴둠, 창고로는 씀)
    lodge_ok = lambda h: h["beds"] and (lodge_eff_min is None or h.get("eff", 0) < lodge_eff_min)  # noqa: E731
    t0 = time.perf_counter()
    extra_beds = extra_beds or {}
    g = d["graph"]
    cost = {v: (0 if v in owned else n["cp"]) for v, n in g.items()}
    paths = {tk: plan.cp_paths(d, t["waypoint"], cost) for tk, t in d["towns"].items() if t["waypoint"] in g}
    pairs = pair_yields(d, slots, paths, towns_per_zone, zones_per_slot, worker_kinds,
                        grow=goal == "max" and budget is not None)
    zones = sorted({z for z, _ in pairs})
    used_towns = sorted({t for _, t in pairs})
    wp = {tk: d["towns"][tk]["waypoint"] for tk in used_towns}
    # 집을 고려할 마을: 노드 일꾼 마을 + (추가 일꾼이 있으면) 모든 마을
    house_towns = sorted(set(used_towns) | (set(d["towns"]) if extra_workers else set()) | set(fixed_workers))
    hcost = {k: (0 if k in owned_houses else h["cp"]) for tk in house_towns for k, h in d["towns"][tk]["houses"].items()}

    # (노드, 마을) 마다 공헌도가 싼 경로 후보 몇 개 (경로가 겹치면 겹친 노드는 한 번만 비용)
    G = nx.DiGraph()
    for u_, n in g.items():
        for v in n["links"]:
            if v in g:
                G.add_edge(u_, v, w=cost[v])
    routes = {}
    for (zk, tk) in pairs:
        gen = nx.shortest_simple_paths(G, wp[tk], zk, weight="w")
        routes[(zk, tk)] = [r for _, r in zip(range(paths_per_pair), gen)]
    nodes = sorted({v for rs in routes.values() for r in rs for v in r})

    m = Model()
    x = dict(zip(nodes, m.vars(len(nodes), [cost[v] for v in nodes])))
    pl = list(pairs)
    a = dict(zip(pl, m.vars(len(pl), [1e-3] * len(pl))))  # 동점이면 일꾼 수 적게
    rl = [(p, i) for p, rs in routes.items() for i in range(len(rs))]
    r = dict(zip(rl, m.vars(len(rl), [0] * len(rl))))
    tot = [sum(v["yields"].get(si, 0) for v in pairs.values()) for si in range(len(slots))]
    grow = goal == "max" and budget is not None
    # 생산량 최대: 항목(완성품·재료)마다 배율 T_j. 보상은 구간마다 줄어듦(×2까지 크게, ×4까지 중간, 그 뒤 작게) →
    # 덜 늘어난 항목부터 키워서 비율이 너무 한쪽으로 쏠리지 않고, 한 재료가 막혀도 다른 항목은 계속 늘어남
    base = [q - nice.get(si, 0) for si, (_, _, q) in enumerate(slots)]
    parts = [p_ for p_ in (grow_parts if grow_parts is not None else [{si: b for si, b in enumerate(base) if b > 0}]) if p_]
    T, Dn, caps, seg_len = [], [], [], {}  # 항목별 [늘리는 구간 변수], [줄이는 구간 변수], 배율 상한, 구간 변수별 길이
    top = [q for _, _, q in slots]  # 칸별로 늘어날 수 있는 최대 필요량 (생산량 계수 자르기용)
    if grow:
        for part in parts:
            cap = min([float(SCALE_MAX)] + [max(0.0, (tot[si] - slots[si][2]) / b) for si, b in part.items() if b > 0])
            seg, lo = [], 0.0
            for hi_, rew in SCALE_SEGMENTS:
                ln = min(hi_, cap) - lo
                if ln > 1e-6:
                    seg.append(m.vars(1, [-rew], hi=ln, integer=False)[0])
                    seg_len[seg[-1]] = ln
                lo = hi_
            T.append(seg)
            caps.append(cap)
            for si, b in part.items():
                top[si] += b * cap
        # 줄이기 벌점: 늘리기 보상을 다 합친 것보다 크게
        g_max = sum(rew * seg_len[t_] for seg in T for t_, (_, rew) in zip(seg, SCALE_SEGMENTS))
        down_pen = [(hi_, (g_max + 1000) * k) for hi_, k in SCALE_DOWN]
        for part in parts:
            down, lo = [], 0.0
            for hi_, pen in down_pen:
                down.append(m.vars(1, [pen], hi=hi_ - lo, integer=False)[0])
                lo = hi_
            Dn.append(down)
    # 부족분. 생산량 최대에서 노드로 얻을 수 있는 필수 재료는 줄이기보다 훨씬 큰 벌점 (손채집 재료·노드가 없는 재료는 그대로:
    # 피할 수 없는 부족에 큰 벌점을 주면 목적값만 커져 상대 갭 2% 가 공헌도엔 너무 느슨해짐)
    strong = SHORT_OVER_CUT * down_pen[-1][1] if grow else SHORTAGE_PENALTY
    s = m.vars(len(slots), [(strong if grow and tot[si] > 0 and si not in hand else SHORTAGE_PENALTY) / q
                            for si, (_, _, q) in enumerate(slots)], hi=highspy.kHighsInf, integer=False)
    # 여유 부족분: 칸의 여유 수량까지만, 벌점 작음 → 부족은 여유 몫부터 생김
    sn = {si: m.vars(1, [NICE_PENALTY / q], hi=float(nice[si]), integer=False)[0]
          for si, (_, _, q) in enumerate(slots) if nice.get(si)}
    buy, lodge, store = {}, {}, {}
    for tk in house_towns:
        hs = d["towns"][tk]["houses"]
        keys = list(hs)
        buy[tk] = dict(zip(keys, m.vars(len(keys), [hcost[k] for k in keys])))
        lk = [k for k in keys if lodge_ok(hs[k])]
        lodge[tk] = dict(zip(lk, m.vars(len(lk), [0] * len(lk))))
        sk = [k for k in keys if hs[k]["slots"]] if storage and tk in used_towns else []
        store[tk] = dict(zip(sk, m.vars(len(sk), [0] * len(sk))))
    # 추가 일꾼(농사용 등): 마을별 인원, 합계 = extra_workers
    ex = dict(zip(house_towns, m.vars(len(house_towns), [1e-4] * len(house_towns), hi=float(extra_workers)))) \
        if extra_workers else {}
    pair_items = {p: set(d["zones"][p[0]]["items"]) for p in pl}
    town_items = {tk: sorted(set().union(*[pair_items[p] for p in pl if p[1] == tk])) for tk in used_towns}
    if storage:
        u = {tk: dict(zip(town_items[tk], m.vars(len(town_items[tk]), [0] * len(town_items[tk]))))
             for tk in used_towns}
        silver = dict(zip(used_towns, m.vars(len(used_towns), [0] * len(used_towns))))

    inf = highspy.kHighsInf
    # 재료 필요량 (부족분 s 허용)
    for si, (_, _, q) in enumerate(slots):
        # 계수를 필요량으로 잘라도 해는 같고 LP 하한이 훨씬 강해짐 (생산량 최대면 늘어날 수 있는 최대 필요량으로)
        # 보유분 하루 몫(st)은 미리 채워진 것으로 → 노드가 채울 양은 그만큼 적음 (음수면 늘리기 전까진 노드 불필요)
        m.row(q - st[si], inf, [(a[p], min(v["yields"][si], max(0.0, top[si] - st[si]))) for p, v in pairs.items() if si in v["yields"]] + [(s[si], 1)]
              + ([(sn[si], 1)] if si in sn else [])
              + [(t_, -part[si]) for part, seg in zip(parts, T) if si in part for t_ in seg]
              # 손채집 칸·노드가 없는 칸은 줄여도 필요량이 안 줄어드는 것으로 → 그 부족을 덜려고 수량을 줄이지 않음
              + [(d_, part[si]) for part, down in zip(parts, Dn) if si in part and si not in hand and tot[si] > 0 for d_ in down])
    # 노드당 일꾼 1명, 노드·마을 투자 필요
    by_zone = defaultdict(list)
    for (zk, tk) in pl:
        by_zone[zk].append(tk)
        m.row(-inf, 0, [(a[(zk, tk)], 1), (x[wp[tk]], -1)])
    for zk, tks in by_zone.items():
        m.row(-inf, 0, [(a[(zk, tk)], 1) for tk in tks] + [(x[zk], -1)])
    if extra_workers:
        m.row(extra_workers, extra_workers, [(i, 1) for i in ex.values()])
    for tk in house_towns:
        t, hs, bv = d["towns"][tk], d["towns"][tk]["houses"], buy[tk]
        workers = [(a[p], 1) for p in pl if p[1] == tk] + ([(ex[tk], 1)] if tk in ex else [])
        # 집은 숙소나 창고 중 하나로만, 선행 집이 있어야 구매
        for k in hs:
            use = [(lodge[tk][k], 1)] if k in lodge[tk] else []
            use += [(store[tk][k], 1)] if k in store[tk] else []
            m.row(-inf, 0, use + [(bv[k], -1)])
            if hs[k]["need"] in bv:
                m.row(-inf, 0, [(bv[k], 1), (bv[hs[k]["need"]], -1)])
        # 숙소: 기본 침대 + 추가 침대 + 숙소로 쓰는 집 침대 ≥ 일꾼 수 (마을이 정해진 일꾼은 고정 인원으로 빼 둠)
        m.row(-inf, t["base_beds"] + extra_beds.get(tk, 0) - fixed_workers.get(tk, 0),
              workers + [(lodge[tk][k], -hs[k]["beds"]) for k in lodge[tk]])
        if storage and tk in used_towns:
            # 창고: 기본 칸 + 창고로 쓰는 집 칸 ≥ 쌓이는 아이템 종류 + 은화 1칸
            for p in pl:
                if p[1] == tk:
                    for i in pair_items[p]:
                        m.row(-inf, 0, [(a[p], 1), (u[tk][i], -1)])
                    m.row(-inf, 0, [(a[p], 1), (silver[tk], -1)])
            m.row(-inf, base_storage, [(u[tk][i], 1) for i in town_items[tk]] + [(silver[tk], 1)]
                  + [(store[tk][k], -hs[k]["slots"]) for k in store[tk]])
    # 담당하면 경로 하나를 고르고, 경로 위 노드는 모두 투자.
    # 한 생산 노드는 마을·경로를 하나만 고르므로 노드 v 를 지나는 경로 변수의 합 ≤ x_v (개별 제약보다 강함)
    through = defaultdict(list)  # (생산 노드, 경유 노드) -> 경로 변수
    for p, rs in routes.items():
        m.row(0, 0, [(r[(p, i)], 1) for i in range(len(rs))] + [(a[p], -1)])
        for i, route in enumerate(rs):
            for v in set(route):
                through[(p[0], v)].append(r[(p, i)])
    for (zk, v), rv in through.items():
        m.row(-inf, 0, [(i, 1) for i in rv] + [(x[v], -1)])
    if budget is not None:
        m.row(-inf, budget, [(x[v], cost[v]) for v in nodes if cost[v]]
              + [(bv[k], hcost[k]) for bv in buy.values() for k in bv if hcost[k]])

    start = greedy(d, slots, pairs, routes, cost, extra_beds, budget, hcost, storage, base_storage, owned_houses, lodge_ok,
                   extra_workers, house_towns, fixed_workers, grow_parts=parts if T else None, stock=st)
    vec = np.zeros(m.n)
    for v in start["invested"]:
        if v in x:
            vec[x[v]] = 1
    for v in nodes:  # 비용 0 노드(마을·보유 노드)는 켜 둔다
        if cost[v] == 0:
            vec[x[v]] = 1
    for p, k in start["chosen"].items():
        vec[a[p]] = 1
        vec[r[(p, k)]] = 1
    for tk, n in start["extra"].items():
        vec[ex[tk]] = n
    for tk, infra in start["infra"].items():
        for k in infra["bought"]:
            vec[buy[tk][k]] = 1
        for k in infra["lodge"]:
            vec[lodge[tk][k]] = 1
        for k in infra["store"]:
            vec[store[tk][k]] = 1
        if storage and infra["workers"] and tk in silver:
            vec[silver[tk]] = 1
            for i in infra["items"]:
                vec[u[tk][i]] = 1
    for si, short in enumerate(start["short"]):
        take = min(short, nice.get(si, 0)) if si in sn else 0
        if si in sn:
            vec[sn[si]] = take
        vec[s[si]] = short - take
    if T:
        # 시작 해의 배율: 칸별 남는 생산량(계수 자른 값 기준)을 항목들이 같이 나눠 쓰도록 고르게 올림
        got = [sum(min(pairs[p]["yields"][si], max(0.0, top[si] - st[si])) for p in start["chosen"] if si in pairs[p]["yields"])
               + st[si] for si in range(len(slots))]
        left = [max(0.0, g - q) for g, (_, _, q) in zip(got, slots)]
        for j, t in enumerate(start_scales(parts, left, caps)):
            for v in T[j]:  # 앞 구간부터 채움
                take = min(t, seg_len[v])
                vec[v] = take
                t -= take
    init = highspy.HighsSolution()
    init.col_value = list(vec)
    init.value_valid = True
    m.h.setSolution(init)

    m.h.setOptionValue("time_limit", float(time_limit))
    m.h.setOptionValue("mip_rel_gap", MIP_GAP)
    t1 = time.perf_counter()
    if progress:
        watch_progress(m.h, progress, vec, t1, cost, x, nodes, hcost, buy, a, pl, s, slots, T, Dn)
    m.h.run()
    sol = m.h.getSolution().col_value
    status = m.h.modelStatusToString(m.h.getModelStatus())
    on = lambda i: sol[i] > 0.5  # noqa: E731

    chosen = [p for p in pl if on(a[p])]
    invested = [v for v in nodes if on(x[v]) and cost[v] > 0]
    houses = []
    for tk in house_towns:
        for k, i in buy[tk].items():
            if not on(i):
                continue
            use = ("숙소" if k in lodge[tk] and on(lodge[tk][k])
                   else "창고" if k in store[tk] and on(store[tk][k]) else "선행")
            if k in owned_houses and use == "선행":  # 보유한 집은 숙소·창고로 쓸 때만 표시
                continue
            houses.append({"town": tk, "key": k, "use": use, "owned": k in owned_houses, "cost": hcost[k],
                           **d["towns"][tk]["houses"][k]})
    got = [sum(pairs[p]["yields"].get(si, 0) for p in chosen) for si in range(len(slots))]
    storage_use = {}
    if storage:
        for tk in used_towns:
            # 실제 배치된 일꾼이 가져오는 아이템만 셈 (u 는 위로 묶여 있지 않아 그대로 세면 과대)
            n_items = len(set().union(*[pair_items[p] for p in chosen if p[1] == tk]))
            if n_items:
                cap = base_storage + sum(d["towns"][tk]["houses"][k]["slots"] for k in store[tk] if on(store[tk][k]))
                storage_use[tk] = {"need": n_items + 1, "cap": cap}
    return {
        "status": status, "gap": m.h.getInfo().mip_gap,
        "time_build": t1 - t0, "time_solve": time.perf_counter() - t1,
        "size": (len(zones), len(pl), m.n),
        "chosen": [{"zone": zk, "town": tk, **pairs[(zk, tk)],
                    "route": next((rt for i, rt in enumerate(routes[(zk, tk)]) if on(r[((zk, tk), i)])), [])}
                   for zk, tk in chosen],
        "invested": invested, "node_cp": sum(cost[v] for v in invested),
        "houses": houses, "house_cp": sum(h["cost"] for h in houses),
        "storage": storage_use, "got": got, "greedy_cp": start["cp"],
        "extra": {tk: round(sol[i]) for tk, i in ex.items() if sol[i] > 0.5},
        "fixed": fixed_workers,
        "scales": [1 + sum(sol[t_] for t_ in seg) - sum(sol[d_] for d_ in down) for seg, down in zip(T, Dn)]
        if T else [1.0] * len(parts),
        # 칸별로 수량을 줄여 덜 필요해진 하루 몫
        "cut": [sum(part.get(si, 0) * sum(sol[d_] for d_ in down) for part, down in zip(parts, Dn)) for si in range(len(slots))],
        "goal": "max" if T else "min",
    }


def watch_progress(h, progress, start, t1, cost, x, nodes, hcost, buy, a, pl, s, slots, T, Dn):
    """계산 중 진행 알림: 시작 해(그리디)를 먼저 보내고, HiGHS 가 더 나은 해를 찾을 때마다 + 1초마다(갭·시간) 보냄"""
    state = {"found": 0, "last": 0.0, "best": None}

    def summary(sol):
        on = lambda i: sol[i] > 0.5  # noqa: E731
        return {"node_cp": sum(cost[v] for v in nodes if cost[v] and on(x[v])),
                "house_cp": sum(hcost[k] for bv in buy.values() for k, i in bv.items() if hcost[k] and on(i)),
                "workers": sum(1 for p in pl if on(a[p])),
                "scale": min((1 + sum(sol[t_] for t_ in seg) - sum(sol[d_] for d_ in down) for seg, down in zip(T, Dn)),
                             default=1.0),
                "short": sum(1 for si, (_, _, q) in enumerate(slots) if sol[s[si]] > q * 1e-3),
                "zones": sorted({p[0] for p in pl if on(a[p])}),
                "invested": [v for v in nodes if cost[v] and on(x[v])]}

    def send(gap=None):
        state["last"] = time.perf_counter()
        progress({**state["best"], "found": state["found"], "gap": gap, "t": round(state["last"] - t1, 1)})

    def improving(e):
        state["found"] += 1
        state["best"] = summary(list(e.data_out.mip_solution))
        send(e.data_out.mip_gap)

    def tick(e):
        if state["best"] and time.perf_counter() - state["last"] >= 1.0:
            send(e.data_out.mip_gap)

    state["best"] = summary(list(start))
    send()
    h.cbMipImprovingSolution.subscribe(improving)
    h.cbMipInterrupt.subscribe(tick)


def start_scales(parts, left, caps):
    """항목별 시작 배율: 모든 항목을 같은 속도로 올리다가 남는 생산량이 바닥난 칸이 걸린 항목부터 멈춤"""
    t = [0.0] * len(parts)
    active = {j for j in range(len(parts)) if caps[j] > 0}
    left = list(left)
    while active:
        load = defaultdict(float)
        for j in active:
            for si, b in parts[j].items():
                load[si] += b
        step = max(0.0, min([left[si] / ld for si, ld in load.items() if ld > 0] + [caps[j] - t[j] for j in active]))
        for j in active:
            t[j] += step
            for si, b in parts[j].items():
                left[si] -= b * step
        tight = {si for si in load if left[si] <= 1e-9}
        active = {j for j in active if t[j] < caps[j] - 1e-9 and not (set(parts[j]) & tight)}
    return t


def town_infra(t, workers, n_items, hcost, extra_beds, storage, base_storage, owned, lodge_ok=lambda h: h["beds"]):
    """마을 하나에서 일꾼 workers 명 숙소 + 아이템 n_items 종 창고를 갖추는 집 조합 (그리디).
    선행 집까지 묶어 '추가 공헌도당 침대/칸' 이 좋은 집부터. 반환: cost, bought, lodge, store (불가능하면 cost=inf)"""
    hs = t["houses"]
    bought = {k for k in hs if k in owned}
    lodge, store = set(), set()
    total = 0

    def chain(k):
        out, cur = [], k
        while cur and cur in hs and cur not in bought:
            out.append(cur)
            cur = hs[cur]["need"]
        return out

    def fill(need, field, used):
        nonlocal total
        while need > 0:
            best = None
            for k, h in hs.items():
                if not h[field] or k in lodge or k in store or (field == "beds" and not lodge_ok(h)):
                    continue
                ch = chain(k)
                c = sum(hcost[y] for y in ch)
                score = (min(h[field], need) / (c + 0.01), -c)
                if not best or score > best[0]:
                    best = (score, k, ch, c)
            if not best:
                return False
            _, k, ch, c = best
            bought.update(ch)
            used.add(k)
            total += c
            need -= hs[k][field]
        return True

    ok = fill(workers - t["base_beds"] - extra_beds, "beds", lodge)
    if ok and storage and workers:
        ok = fill(n_items + 1 - base_storage, "slots", store)
    return {"cost": total if ok else float("inf"), "bought": bought, "lodge": lodge, "store": store}


def greedy(d, slots, pairs, routes, cost, extra_beds, budget, hcost, storage, base_storage, owned_houses, lodge_ok,
           extra_workers=0, house_towns=(), fixed_workers=None, grow_parts=None, grow_step=0.25, grow_levels=40, stock=None):
    """시작 해: 남은 필요량 대비 '추가 공헌도(경로 중 아직 투자 안 한 노드 + 마을 집 비용 증가분)' 효율이 좋은 노드부터 추가.
    grow_parts(생산량 최대): 필요량을 채운 뒤 남는 예산으로 항목 수량을 grow_step(+25%)씩 올려 가며 계속 추가 →
    솔버가 시간 안에 더 나은 해를 못 찾아도(브라우저판처럼 느릴 때) 늘린 결과가 나옴"""
    invested = {v for v, c in cost.items() if c == 0}
    st = stock or [0.0] * len(slots)
    # 남은 필요량. 보유분이 더 많으면 음수로 두어, 늘릴 때(2단계) 보유분 여유부터 씀
    rem = [q - st[si] for si, (_, _, q) in enumerate(slots)]
    workers, items = defaultdict(int), defaultdict(set)
    infra = {}
    chosen, used_zones, total = {}, set(), 0

    def infra_for(tk, w, its):
        return town_infra(d["towns"][tk], w, len(its), hcost, extra_beds.get(tk, 0), storage, base_storage, owned_houses, lodge_ok)

    # 마을이 정해진 일꾼(황실 공방 등)부터 숙소를 잡아 둠
    for tk, n in (fixed_workers or {}).items():
        workers[tk] += n
        infra[tk] = town_infra(d["towns"][tk], workers[tk], 0, hcost, extra_beds.get(tk, 0), False, base_storage,
                               owned_houses, lodge_ok)
        total += infra[tk]["cost"]

    def add_best():
        """남은 필요량을 가장 싸게 줄이는 (노드, 마을) 하나 추가. 추가할 게 없으면 False"""
        nonlocal total
        best = None
        for p, info in pairs.items():
            zk, tk = p
            if zk in used_zones:
                continue
            gain = sum(min(y, max(0.0, rem[si])) / slots[si][2] for si, y in info["yields"].items())
            if gain <= 1e-9:
                continue
            k, rc = min(((i, sum(cost[v] for v in set(rt) if v not in invested)) for i, rt in enumerate(routes[p])),
                        key=lambda t: t[1])
            new = infra_for(tk, workers[tk] + 1, items[tk] | set(d["zones"][zk]["items"]))
            c = rc + new["cost"] - (infra[tk]["cost"] if tk in infra else 0)
            if c == float("inf") or (budget is not None and total + c > budget):
                continue
            ratio = gain / (c + 0.01)
            if not best or ratio > best[0]:
                best = (ratio, p, k, c, new)
        if not best:
            return False
        _, p, k, c, new = best
        zk, tk = p
        chosen[p] = k
        used_zones.add(zk)
        invested.update(routes[p][k])
        workers[tk] += 1
        items[tk] |= set(d["zones"][zk]["items"])
        infra[tk] = new
        total += c
        for si, y in pairs[p]["yields"].items():
            rem[si] = min(rem[si], max(0.0, rem[si] - y))
        return True

    while any(v > 1e-9 for v in rem) and add_best():
        pass
    node_workers = {tk: n - (fixed_workers or {}).get(tk, 0) for tk, n in workers.items()}
    extra = defaultdict(int)
    for _ in range(extra_workers):
        best = None
        for tk in house_towns:
            new = town_infra(d["towns"][tk], workers[tk] + 1, len(items[tk]), hcost, extra_beds.get(tk, 0),
                             storage and node_workers.get(tk, 0) > 0, base_storage, owned_houses, lodge_ok)
            c = new["cost"] - (infra[tk]["cost"] if tk in infra else 0)
            if not best or c < best[0]:
                best = (c, tk, new)
        if not best or best[0] == float("inf"):
            break
        c, tk, new = best
        workers[tk] += 1
        extra[tk] += 1
        infra[tk] = new
        total += c
    if grow_parts and budget is not None:
        # 2단계: 항목 수량을 단계마다 올려 남는 필요량만큼 더 채움 (추가 일꾼 숙소는 위에서 이미 잡아 둠)
        for _ in range(grow_levels):
            for part in grow_parts:
                for si, b in part.items():
                    rem[si] += b * grow_step
            added = False
            while any(v > 1e-9 for v in rem) and add_best():
                added = True
            if not added:
                break
        node_workers = {tk: n - (fixed_workers or {}).get(tk, 0) - extra.get(tk, 0) for tk, n in workers.items()}
    for tk, inf_ in infra.items():
        inf_["workers"], inf_["items"] = node_workers.get(tk, 0), items[tk]
    # 부족분은 원래 필요량 기준 (2단계에서 올린 필요량 말고)
    got = [sum(pairs[p]["yields"].get(si, 0) for p in chosen) for si in range(len(slots))]
    short = [max(0.0, q - g - st[si]) for si, ((_, _, q), g) in enumerate(zip(slots, got))]
    return {"invested": invested, "chosen": chosen, "short": short, "cp": total, "infra": infra, "extra": dict(extra)}
