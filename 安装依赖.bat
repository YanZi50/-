@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

"%PYTHON_EXE%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
)
echo 依赖安装完成。
pause
