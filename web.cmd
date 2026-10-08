@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=C:\Users\hexin\.conda\envs\llm\python.exe"
if not exist "%PY%" set "PY=python"
echo ============================================================
echo   面试陪练 Agent - Web 界面
echo   默认地址 http://127.0.0.1:8010   （要换端口：web.cmd 8000）
echo   关闭这个黑窗口即停止服务
echo ============================================================
"%PY%" web_app.py --port %~1
pause
