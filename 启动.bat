@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

start "" "http://127.0.0.1:8765"
"%PYTHON_EXE%" web_app.py
