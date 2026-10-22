"""레시피의 대체 재료 그룹("곡물류" 등) 소속 아이템을 bdocodex에서 수집 → data/raw/material_groups.json

인벤 레시피의 (대표 재료 ID, 그룹명) 쌍마다 코덱스 아이템 페이지에서 group_id를 찾고,
품목 그룹 페이지에서 소속 아이템과 가치(1개당 몇 개분으로 치는지)를 읽는다.
"""
import json
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CACHE = RAW / "codex_html"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"

# 그룹 페이지 행: 아이템 링크 ... 가치 숫자
MEMBER_RE = re.compile(r'<tr><td class="p-1">.*?/kr/item/(\d+)/.*?</td>\s*<td[^>]*>\s*([\d.]+)\s*</td>', re.S)


def get(path):
    f = CACHE / (path.strip("/").replace("/", "_") + ".html")
    if f.exists():
        return f.read_text(encoding="utf-8")
    req = urllib.request.Request("https://bdocodex.com" + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", errors="replace")
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(body, encoding="utf-8")
    time.sleep(1.5)
    return body


def group_members(gid):
    s = get(f"/kr/materialgroup/{gid}/")
    return {int(i): float(v) for i, v in MEMBER_RE.findall(s)}


def main():
    # 대표 재료 ID → 인벤 그룹명 (있으면 표시용 라벨)
    labels = {}
    for r in json.loads((RAW / "inven_recipes.json").read_text(encoding="utf-8")):
        for v in r["variants"]:
            for m in v:
                if m.get("group"):
                    labels.setdefault(m["id"], m["group"])
    reps = set(labels)
    for r in json.loads((RAW / "codex_recipes.json").read_text(encoding="utf-8")):
        reps |= {s["id"] for s in r["slots"] if s["alt"]}

    groups = {}  # 대표 재료 ID -> {label, groups: {gid: {member: value}}}
    for n, iid in enumerate(sorted(reps), 1):
        s = get(f"/kr/item/{iid}/")
        gids = sorted({int(g) for g in re.findall(r"materialgroup/(\d+)/", s)})
        # 대표 재료가 실제로 들어 있는 그룹만
        cands = {g: m for g in gids if iid in (m := group_members(g))}
        print(f"[{n}/{len(reps)}] {iid} {labels.get(iid, '')} → 그룹 {list(cands)}")
        groups[str(iid)] = {"label": labels.get(iid),
                            "groups": {str(g): {str(k): v for k, v in m.items()} for g, m in cands.items()}}

    (RAW / "material_groups.json").write_text(json.dumps(groups, ensure_ascii=False, indent=1), encoding="utf-8")
    print("저장:", RAW / "material_groups.json", len(groups))


if __name__ == "__main__":
    main()
