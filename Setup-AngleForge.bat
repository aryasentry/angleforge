@echo off
cd /d "%~dp0"
py -3 run.py --setup --preflight
pause
