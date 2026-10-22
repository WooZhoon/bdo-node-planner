@echo off
rem BDO node planner - start server and open browser
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
start "" http://127.0.0.1:5173/
.venv\Scripts\python -X utf8 server.py
