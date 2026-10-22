"""집 구매 가이드 시트(창고 효율·숙소 단계 비고) → data/raw/house_guide.json {집 키: {note, sheet_name, cp, slots}}

시트: 마을별 블록(7열 간격)에 번지 이름 / 공헌도 / 창고칸수 / 효율 / 비고.
번지 표기가 코덱스 집 이름과 조금 달라("1-1번지" ↔ "벨리아 1번지 1호실") 같은 마을 안에서 변형을 맞춰 본다.
"""
import csv
import io
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
SHEET_ID = "1498of1iOc9zc_FE65oaqigsC5Gu0yFwXPr6MG6hi5IQ"
GID = "2041276958"
URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"


def town_stem(title):
    return re.sub(r"\s*\(.*\)$", "", title).replace("수도 ", "").replace(" 마을", "").replace("마을", "").strip()


def variants(name):
    out = {name, name.replace(" ", "")}
    m = re.match(r"^(\d+)-(\d+)번지(.*)$", name)
    if m:
        out.add(f"{m.group(1)}번지 {m.group(2)}호실{m.group(3)}")
    return out


def main():
    with urllib.request.urlopen(URL, timeout=60) as r:
        text = r.read().decode("utf-8")
    (RAW / "house_guide.csv").write_text(text, encoding="utf-8")
    rows = list(csv.reader(io.StringIO(text)))

    houseinfo = json.loads((RAW / "workerman" / "houseinfo.json").read_text(encoding="utf-8"))
    names = json.loads((RAW / "codex_house_names.json").read_text(encoding="utf-8"))
    regioninfo = json.loads((RAW / "workerman" / "regioninfo.json").read_text(encoding="utf-8"))
    loc_node = json.loads((RAW / "workerman" / "loc.json").read_text(encoding="utf-8"))["ko"]["node"]
    town_name = {ri["key"]: loc_node.get(str(ri["waypoint"]), "") for ri in regioninfo.values() if ri["waypoint"]}

    guide, total, matched = {}, 0, 0
    width = max(len(r) for r in rows)
    for c in range(0, width, 7):
        town = None
        for i, row in enumerate(rows):
            cell = row[c].strip() if c < len(row) else ""
            nxt = rows[i + 1] if i + 1 < len(rows) else []
            if cell and c + 1 < len(nxt) and nxt[c + 1] == "공헌도":
                town = cell
                continue
            if not (town and cell and c + 2 < len(row) and row[c + 1].isdigit()):
                continue
            total += 1
            stem = town_stem(town)
            tks = {tk for tk, n in town_name.items() if stem and stem in n}
            want = variants(cell)
            cands = [k for k, h in houseinfo.items()
                     if h["affTown"] in tks and any(names.get(k, "").endswith(v) for v in want)]
            if len(cands) == 1:
                matched += 1
                guide[cands[0]] = {"sheet_name": cell, "town": town, "cp": int(row[c + 1]),
                                   "slots": int(row[c + 2]) if row[c + 2].isdigit() else None,
                                   "eff": float(row[c + 3]) if re.match(r"^\d+(\.\d+)?$", row[c + 3].strip()) else None,
                                   "note": (row[c + 4] if c + 4 < len(row) else "").strip()}
    (RAW / "house_guide.json").write_text(json.dumps(guide, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"집 가이드 시트: {total}행 중 {matched}곳을 집 데이터와 연결")


if __name__ == "__main__":
    main()
