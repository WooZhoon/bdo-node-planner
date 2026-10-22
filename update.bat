@echo off
rem BDO node planner - refresh all data after a game patch
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
.venv\Scripts\python -X utf8 scripts\update_all.py
pause
