@echo off
chcp 65001 >nul
title Number Duel 服务端
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   [错误] 没有找到 python 命令。
  echo   请先安装 Python 3.10 或更高版本，安装时记得勾选 "Add python.exe to PATH"。
  echo.
  pause
  exit /b 1
)

echo.
echo   正在启动 Number Duel 服务端，请稍候...
echo   浏览器会自动打开游戏页面。
echo.
echo   ！不要关闭这个黑窗口 —— 关掉它就等于关掉服务器。
echo.
python server.py --port 8000

echo.
echo   服务端已停止。
pause
