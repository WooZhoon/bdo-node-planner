"""정적 사이트 만들기 → site/ (GitHub Pages 등에 그대로 올림). 서버 없이 브라우저(Pyodide)에서 계산.

  .venv/Scripts/python -X utf8 scripts/build_site.py
  python -m http.server 5180 -d site     # 로컬 확인: http://127.0.0.1:5180/

site/
  index.html   web/index.html + bridge.js 연결 (화면 코드는 로컬 서버판과 같은 파일)
  bridge.js    화면의 /api/* 요청 → 워커
  worker.js    Pyodide + HiGHS 로 api.py 실행
  bundle.zip   파이썬 코드 + 데이터 (패치 후 update.bat → 이 스크립트 다시 실행)
파일이 바뀌면 ?v=<해시> 가 바뀌어 브라우저가 옛 파일을 쓰지 않음
"""
import hashlib
import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site"
PY = ["api.py", "plan.py", "optimize.py", "autorecipe.py", "vendor.py", "static/webapi.py"]
DATA = ["data/build/planner.json", "data/auto_recipes.json", "data/item_sources.json", "data/manual_recipes.json",
        "data/sourcing_defaults.json", "data/town_regions.json", "data/vendor_manual.json"]
CREDIT = ('<p class="hint" style="text-align:center;margin:24px 0 8px">데이터: '
          '<a href="https://github.com/shrddr/workermanjs" target="_blank" rel="noopener" style="color:var(--accent)">workermanjs</a> · '
          '<a href="https://bdocodex.com/kr/" target="_blank" rel="noopener" style="color:var(--accent)">BDO Codex</a> · 인벤 — '
          '비공식 팬 도구이며 Pearl Abyss와 관계없습니다.</p>')


def bundle():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in PY + DATA:
            # 날짜를 고정해 내용이 같으면 zip 도 같게 (해시가 괜히 바뀌지 않도록)
            info = zipfile.ZipInfo(Path(f).name if f.startswith("static/") else f, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, (ROOT / f).read_bytes())
    return buf.getvalue()


def main():
    OUT.mkdir(exist_ok=True)
    files = {
        "bundle.zip": bundle(),
        "bridge.js": (ROOT / "static" / "bridge.js").read_bytes(),
        "worker.js": (ROOT / "static" / "worker.js").read_bytes(),
    }
    page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    v = hashlib.sha256(b"".join(files.values()) + page.encode() + CREDIT.encode()).hexdigest()[:10]
    main_script = "\n<script>\nconst $ = s =>"
    assert page.count(main_script) == 1, "index.html 의 본 스크립트 위치를 못 찾음"
    page = page.replace(main_script, f'\n<script src="bridge.js?v={v}" data-v="{v}"></script>{main_script}')
    assert page.count("</main>") == 1
    page = page.replace("</main>", CREDIT + "\n</main>")
    files["index.html"] = page.encode("utf-8")
    files[".nojekyll"] = b""
    for name, data in files.items():
        (OUT / name).write_bytes(data)
    print(f"site/ 완료 (버전 {v}) · bundle.zip {len(files['bundle.zip']) / 1e6:.1f}MB")


if __name__ == "__main__":
    main()
