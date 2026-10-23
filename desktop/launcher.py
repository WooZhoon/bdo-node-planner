"""PC용 실행 파일(exe) 진입점: 업데이트 확인 → 로컬 서버(server.py) 실행 → 브라우저 열기

exe 에는 파이썬·라이브러리·이 파일만 들어 있고, 계산 코드·데이터(desktop.zip)는
%LOCALAPPDATA%/BDO Node Planner/app 에 풀어서 씀. 시작할 때 사이트의 desktop.json 과 버전을 비교해
새 버전이면 물어보고 받아서 바꿈 (직접 추가한 조합식·코덱스 조회 캐시는 이어 붙임).
exe 자체를 바꿔야 할 때(EXE_VERSION 이 올라감)는 GitHub Releases 에서 새 exe 를 받아 바꿔치기.

  빌드: .venv/Scripts/python -X utf8 scripts/build_exe.py  → dist/bdo-node-planner.exe
"""
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import traceback
import urllib.request
import webbrowser
import zipfile
from pathlib import Path
from tkinter import messagebox, ttk

EXE_VERSION = 1  # 라이브러리·실행기 자체가 바뀔 때만 올림 (올린 뒤 Releases 에 새 exe 를 먼저 올릴 것)
SITE = "https://woozhoon.github.io/bdo-node-planner/"
EXE_URL = "https://github.com/WooZhoon/bdo-node-planner/releases/latest/download/bdo-node-planner.exe"
PORT = 5173  # run.bat 과 같은 주소 → 브라우저에 저장된 설정을 같이 씀
TITLE = "검은사막 노드 플래너"

BASE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BDO Node Planner"
APP = BASE / "app"
VERSION_FILE = APP / "desktop.json"
# 업데이트해도 사용자 것을 남길 파일: 직접 추가한 조합식, 코덱스 조회 캐시
KEEP = ["data/manual_recipes.json", "data/auto_recipes.json", "data/item_sources.json"]


def _bundle_deps():
    """호출하지 않음. PyInstaller 가 계산 코드(exe 밖)에 필요한 모듈을 exe 에 넣도록 import 만 적어 둠"""
    import argparse, heapq, math, re  # noqa: F401,E401
    from collections import defaultdict  # noqa: F401
    import flask, highspy, networkx, numpy  # noqa: F401,E401
    import networkx.algorithms.simple_paths  # noqa: F401


def resource(name):
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent / "site")) / name


def fetch(url, timeout=5):
    req = urllib.request.Request(url, headers={"User-Agent": "bdo-node-planner"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def merge_keep(old_dir, new_dir):
    """옛 버전의 사용자 파일을 새 버전에 이어 붙임 (조합식은 사용자 것이 우선, 캐시는 새 데이터가 우선)"""
    for rel in KEEP:
        old, new = read_json(old_dir / rel), read_json(new_dir / rel)
        if old is None:
            continue
        if new is None:
            merged = old
        elif "recipes" in old:  # manual_recipes.json: 제품 ID 기준으로 합침
            by = {r["product"]: r for r in new.get("recipes", [])}
            by.update({r["product"]: r for r in old.get("recipes", [])})
            merged = {**new, "recipes": list(by.values())}
        else:
            merged = {**old, **new}
        (new_dir / rel).write_text(json.dumps(merged, ensure_ascii=False, indent=2 if "recipes" in merged else None),
                                   encoding="utf-8")


def install(zip_bytes, meta):
    """desktop.zip → APP (새 폴더에 푼 뒤 바꿔치기. 도중에 실패하면 옛 버전 그대로)"""
    BASE.mkdir(parents=True, exist_ok=True)
    new = Path(tempfile.mkdtemp(prefix="app.", dir=BASE))
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        z.extractall(new)
    if APP.exists():
        merge_keep(APP, new)
    (new / "desktop.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    old = BASE / "app.old"
    shutil.rmtree(old, ignore_errors=True)
    if APP.exists():
        APP.rename(old)
    new.rename(APP)
    shutil.rmtree(old, ignore_errors=True)


def with_progress(root, text, work):
    """작업(work)을 스레드로 돌리는 동안 진행 창을 띄움. 예외는 그대로 다시 던짐"""
    win = tk.Toplevel(root)
    win.title(TITLE)
    win.resizable(False, False)
    ttk.Label(win, text=text, padding=(20, 16, 20, 8)).pack()
    bar = ttk.Progressbar(win, mode="indeterminate", length=280)
    bar.pack(padx=20, pady=(0, 18))
    bar.start(12)
    result = {}

    def run():
        try:
            result["value"] = work()
        except Exception as e:  # noqa: BLE001
            result["error"] = e

    # tkinter 는 다른 스레드에서 못 건드림 → 메인 스레드에서 창을 갱신하며 끝나기를 기다림
    th = threading.Thread(target=run, daemon=True)
    th.start()
    win.grab_set()
    while th.is_alive():
        root.update()
        th.join(0.03)
    win.destroy()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def update_exe(root):
    """새 exe 를 받아 두고, 이 프로그램이 끝나면 바꿔치기 후 다시 켜는 배치 파일을 실행"""
    exe = Path(sys.executable)
    data = with_progress(root, "새 실행 파일을 받는 중…", lambda: fetch(EXE_URL, timeout=120))
    new = exe.with_suffix(".new")
    new.write_bytes(data)
    bat = Path(tempfile.gettempdir()) / "bdo-node-planner-update.bat"
    bat.write_text(
        "@echo off\r\nset n=0\r\n:wait\r\ntimeout /t 1 /nobreak >nul\r\nset /a n+=1\r\n"
        f'move /y "{new}" "{exe}" >nul 2>&1 || (if %n% lss 30 goto wait)\r\n'
        f'start "" "{exe}"\r\ndel "%~f0"\r\n', encoding="cp949")
    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=subprocess.CREATE_NO_WINDOW)
    sys.exit(0)


def check_update(root):
    """사이트의 desktop.json 과 비교해 새 버전이면 물어보고 업데이트. 인터넷이 안 되면 조용히 넘어감"""
    try:
        remote = json.loads(fetch(SITE + "desktop.json", timeout=4))
    except Exception:  # noqa: BLE001
        return
    if int(remote.get("exe_version", 0)) > EXE_VERSION:
        if messagebox.askyesno(TITLE, "새로운 버전의 실행 파일이 있습니다.\n지금 받아서 바꿀까요? (다시 켜짐)", parent=root):
            try:
                update_exe(root)
            except Exception as e:  # noqa: BLE001
                messagebox.showerror(TITLE, f"실행 파일 업데이트 실패 — 지금 버전으로 실행합니다.\n{e}", parent=root)
        return
    local = read_json(VERSION_FILE) or {}
    if remote.get("version") == local.get("version"):
        return
    notes = "\n".join(f"· {n}" for n in remote.get("notes", [])[:5])
    msg = f"새로운 버전이 있습니다. ({remote.get('date', '')})\n\n" + (f"바뀐 내용:\n{notes}\n\n" if notes else "") + "업데이트할까요?"
    if not messagebox.askyesno(TITLE, msg, parent=root):
        return
    try:
        data = with_progress(root, "업데이트 받는 중…", lambda: fetch(SITE + "desktop.zip?v=" + remote["version"], timeout=60))
        install(data, remote)
        messagebox.showinfo(TITLE, "업데이트 완료!", parent=root)
    except Exception as e:  # noqa: BLE001
        messagebox.showerror(TITLE, f"업데이트 실패 — 지금 버전으로 실행합니다.\n{e}", parent=root)


def ensure_installed():
    """처음 실행이면 exe 안에 든 버전을 풀어 둠"""
    if (APP / "server.py").exists():
        return
    install(resource("desktop.zip").read_bytes(), read_json(resource("desktop.json")) or {})


def port_in_use():
    import socket
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def main():
    # 창 없는 exe 는 stdout/stderr 가 없음 → 로그 파일로 (print·werkzeug 로그·오류가 여기에 남음)
    if getattr(sys, "frozen", False) or sys.stdout is None:
        BASE.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(BASE / "log.txt", "w", encoding="utf-8", buffering=1)
    root = tk.Tk()
    root.withdraw()
    root.title(TITLE)
    url = f"http://127.0.0.1:{PORT}/"
    if port_in_use():
        # 이미 켜져 있음 (exe 또는 run.bat) → 브라우저만 열기
        webbrowser.open(url)
        return
    try:
        ensure_installed()
    except Exception as e:  # noqa: BLE001
        messagebox.showerror(TITLE, f"설치 실패\n{e}")
        return
    check_update(root)

    # 계산 코드는 APP 폴더의 것을 씀 (데이터 읽는 데 몇 초)
    sys.path.insert(0, str(APP))
    os.chdir(APP)
    from werkzeug.serving import make_server

    def load():
        # 이름을 문자열로: PyInstaller 가 server·api 를 exe 안에 넣으면 그쪽이 먼저 불려 업데이트가 안 먹음
        server = importlib.import_module("server")
        return make_server("127.0.0.1", PORT, server.app, threaded=True)

    try:
        srv = with_progress(root, "데이터 불러오는 중…", load)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        messagebox.showerror(TITLE, f"실행 실패\n{e}")
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    webbrowser.open(url)

    # 실행 중 창: 브라우저 다시 열기 / 종료 (창을 닫으면 서버도 꺼짐)
    root.deiconify()
    root.resizable(False, False)
    frm = ttk.Frame(root, padding=18)
    frm.pack()
    ver = (read_json(VERSION_FILE) or {}).get("date", "")
    ttk.Label(frm, text=f"{TITLE} 실행 중", font=("Malgun Gothic", 11, "bold")).pack(anchor="w")
    ttk.Label(frm, text=f"{url}   ·   데이터 {ver}" if ver else url, foreground="#666").pack(anchor="w", pady=(2, 12))
    ttk.Label(frm, text="이 창을 닫으면 플래너가 꺼집니다.", foreground="#666").pack(anchor="w", pady=(0, 12))
    btns = ttk.Frame(frm)
    btns.pack(anchor="e")

    def quit_():
        threading.Thread(target=srv.shutdown, daemon=True).start()
        root.after(300, root.destroy)

    ttk.Button(btns, text="브라우저 열기", command=lambda: webbrowser.open(url)).pack(side="left", padx=(0, 8))
    ttk.Button(btns, text="종료", command=quit_).pack(side="left")
    root.protocol("WM_DELETE_WINDOW", quit_)
    root.mainloop()


if __name__ == "__main__":
    main()
