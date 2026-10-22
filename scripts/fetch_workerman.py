"""shrddr/workermanjs 의 노드/집/일꾼 데이터 → data/raw/workerman/"""
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "workerman"
BASE = "https://raw.githubusercontent.com/shrddr/workermanjs/HEAD/data/"
FILES = [
    "loc.json",
    "plantzone.json",
    "manual/plantzone_drops.json",
    "worker_static.json",
    "houseinfo.json",
    "lodging_per_town.json",
    "planttown.json",
    "links.json",
    "exploration.json",
    "distances_tk2pzk.json",
    "distances_pzk2tk.json",
    "regioninfo.json",
    "houseinforeceipe.json",  # 작업장 종류 → 제작 레시피(설계) 번호
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        dst = OUT / Path(f).name
        with urllib.request.urlopen(BASE + f, timeout=60) as r:
            dst.write_bytes(r.read())
        print(f"{dst.name}: {dst.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
