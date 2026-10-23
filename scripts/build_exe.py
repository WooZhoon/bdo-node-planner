"""PC 실행 파일 만들기 → dist/bdo-node-planner.exe

  .venv/Scripts/python -m pip install -r requirements.txt pyinstaller
  .venv/Scripts/python -X utf8 scripts/build_exe.py

exe 안에는 실행기(desktop/launcher.py)·파이썬·라이브러리와, 처음 실행할 때 풀어 쓸 desktop.zip(현재 코드·데이터)이 들어감.
계산 코드는 exe 밖(%LOCALAPPDATA%/BDO Node Planner/app)에서 읽으므로, 코드·데이터만 바뀐 업데이트는 exe 를 다시 만들 필요 없음.
라이브러리를 바꾸거나 새로 쓰면 launcher.py 의 EXE_VERSION 을 올리고 이 스크립트로 다시 만들어 Releases 에 올릴 것.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "build_site.py")], check=True)
    sep = ";" if sys.platform == "win32" else ":"
    site = Path(importlib.util.find_spec("highspy").origin).parent.parent
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--noconsole",
        "--name", "bdo-node-planner",
        "--add-data", f"{ROOT / 'site' / 'desktop.zip'}{sep}.",
        "--add-data", f"{ROOT / 'site' / 'desktop.json'}{sep}.",
        # networkx 는 알고리즘을 import 할 때 많이 불러와서 통째로
        "--collect-submodules", "networkx",
        "--collect-all", "highspy",
        # highspy 는 DLL(msvcp140)을 site-packages/highspy.libs 에서 불러옴 (delvewheel) → 같은 자리에 넣어 줌
        "--add-binary", f"{site / 'highspy.libs'}{sep}highspy.libs",
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
        str(ROOT / "desktop" / "launcher.py"),
    ], check=True, cwd=ROOT)
    exe = ROOT / "dist" / "bdo-node-planner.exe"
    print(f"완료: {exe} ({exe.stat().st_size / 1e6:.0f}MB)")


if __name__ == "__main__":
    main()
