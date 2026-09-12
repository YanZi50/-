@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PY=C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python

echo [1/3] 用 PyInstaller 打包（onedir + 无控制台）...
"%PY%" -m PyInstaller --noconfirm --clean --onedir --noconsole ^
  --name "信息流素材一键拼接" ^
  --add-data "web;web" ^
  --collect-submodules imageio_ffmpeg ^
  --exclude-module faster_whisper ^
  web_app.py
if errorlevel 1 ( echo 打包失败 & pause & exit /b 1 )

echo [2/3] 复制 ffmpeg.exe 到 exe 旁 bin 目录...
for /f "delims=" %%i in ('"%PY%" -c "from imageio_ffmpeg import get_ffmpeg_exe; print(get_ffmpeg_exe())"') do set FFMPEG=%%i
if not exist "dist\信息流素材一键拼接\bin" mkdir "dist\信息流素材一键拼接\bin"
copy /y "%FFMPEG%" "dist\信息流素材一键拼接\bin\ffmpeg.exe" >nul
if errorlevel 1 ( echo 复制 ffmpeg 失败 & pause & exit /b 1 )

echo [3/3] 完成：dist\信息流素材一键拼接\
echo 双击 信息流素材一键拼接.exe 即可使用（自动打开浏览器）。
pause
