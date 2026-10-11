@echo off
cd /d "%~dp0"
py -3 run.py --open
if errorlevel 1 pause
