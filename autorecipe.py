"""조합식이 없는 아이템 → bdocodex 아이템 설명의 '획득처 : 아래 재료를 가공(L) - 공작 / 이름 xN …' 을 읽어 조합식 생성

결과는 data/auto_recipes.json 에 저장 (찾지 못한 아이템도 기록해 다시 요청하지 않음).
"""
import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "auto_recipes.json"
NAMES = ROOT / "data" / "raw" / "codex_item_names.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
REQUEST_GAP = 1.0  # 코덱스 요청 간격(초)
OFFLINE = False    # 브라우저(정적 사이트)에선 코덱스에 못 물어봄(CORS) → 미리 받아 둔 캐시만 씀

_lock = threading.Lock()
_last = 0.0
_name_to_ids = None

# "획득처</span> : 아래 재료를 가공(L) - <span ...>공작</span><br><span ...>마가한의 파편 x100</span>..."
SOURCE_RE = re.compile(r"획득처</span>\s*:\s*아래 재료를\s*([^(<]+)\(L\)(.*?)(?:<div|<hr)", re.S)
METHOD_RE = re.compile(r"^\s*-\s*(?:<span[^>]*>)?([^<]+)")
MAT_RE = re.compile(r">([^<>]+?)\s*x(\d+)\s*<")


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


def save_cache(cache):
    write_cache(cache)


def name_to_ids():
    global _name_to_ids
    if _name_to_ids is None:
        _name_to_ids = {}
        for iid, name in json.loads(NAMES.read_text(encoding="utf-8")).items():
            _name_to_ids.setdefault(name, []).append(int(iid))
    return _name_to_ids


def fetch_item_page(iid):
    global _last
    if OFFLINE:
        raise OSError("offline")
    with _lock:
        wait = REQUEST_GAP - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(f"https://bdocodex.com/kr/item/{iid}/", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", errors="replace")
        _last = time.time()
    return body


def parse(html, prefer):
    """설명에서 (방식, [(이름, ID 또는 None, 개수)]). 같은 이름 아이템이 여러 개면 prefer(ID) 가 참인 것 → 작은 ID"""
    m = SOURCE_RE.search(html)
    if not m:
        return None
    kind, rest = m.group(1).strip(), m.group(2)
    mm = METHOD_RE.match(rest)
    method = mm.group(1).strip() if kind == "가공" and mm else kind
    mats = []
    for name, qty in MAT_RE.findall(rest):
        name = name.strip()
        if name == method:
            continue
        ids = name_to_ids().get(name, [])
        ids = sorted(ids, key=lambda i: (not prefer(i), i))
        mats.append((name, ids[0] if ids else None, int(qty)))
    return (method, mats) if mats else None


def lookup(iid, prefer=lambda i: False):
    """캐시에 있으면 그대로, 없으면 코덱스에서 읽어 저장. 반환: 캐시 항목(dict)"""
    cache = load_cache()
    key = str(iid)
    if key in cache:
        return cache[key]
    try:
        parsed = parse(fetch_item_page(iid), prefer)
    except Exception as e:  # 네트워크 오류는 기록하지 않고 다음에 다시 시도
        return {"status": "error", "error": str(e)}
    if parsed and all(i for _, i, _ in parsed[1]):
        method, mats = parsed
        entry = {"status": "ok", "method": method, "inputs": [[i, q] for _, i, q in mats], "out": 1,
                 "names": {str(i): n for n, i, _ in mats}, "fetched": time.strftime("%Y-%m-%d")}
    elif parsed:
        entry = {"status": "unresolved", "missing": [n for n, i, _ in parsed[1] if not i],
                 "fetched": time.strftime("%Y-%m-%d")}
    else:
        entry = {"status": "none", "fetched": time.strftime("%Y-%m-%d")}
    cache = load_cache()
    cache[key] = entry
    save_cache(cache)
    return entry


def apply(d, iid, entry):
    """조합식을 플래너 데이터(가공 목록)에 반영"""
    if entry.get("status") != "ok":
        return False
    d["processing"][int(iid)] = [{"method": entry["method"], "inputs": entry["inputs"], "out": entry["out"], "auto": True}]
    for i, n in entry.get("names", {}).items():
        d["items"].setdefault(int(i), n)
    return True


def prune_misses():
    """'찾지 못함' 기록을 지워 다음 계산 때 다시 확인하게 함 (데이터 갱신 후 사용)"""
    cache = load_cache()
    kept = {k: v for k, v in cache.items() if v.get("status") == "ok"}
    save_cache(kept)
    print(f"자동 조합식 캐시: 찾은 것 {len(kept)}개 유지, 못 찾은 기록 {len(cache) - len(kept)}개 삭제")
