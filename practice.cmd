@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=C:\Users\hexin\.conda\envs\llm\python.exe"
echo ============================================================
echo   求职陪练 Agent - 真人练习一场（5 题）
echo   每题作答：直接打字换行；答完输入 END 再回车进入下一题
echo   JD 默认用 data\jd_sample.md，想换题就改本文件里的 --jd
echo ============================================================
echo.
"%PY%" run_demo.py --jd data\jd_sample.md ^
  --resume "C:\Users\hexin\Desktop\1简历\校招简历.docx" ^
  --interactive --questions 5 --env-file .env --run-id personal-01
echo.
echo ============================================================
echo   跑完了。报告在 results\report_personal-01.md
echo ============================================================
pause
