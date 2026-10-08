@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=C:\Users\hexin\.conda\envs\llm\python.exe"
if not exist "%PY%" set "PY=python"
echo ============================================================
echo   评测 / 回归标准跑法（固定口径，结果才可比较）
echo   · 确定性选题（--no-variety）：同输入必同题
echo   · 每题采样 3 次取中位数（--reps 3）：压掉单次采样抖动
echo   · 用合成样例简历，产物可入库
echo   · 打开打分缓存（COACH_CACHE=1）：重跑同一批答案不再重复花钱；换口径会自动失效
echo   用法：eval_run.cmd --run-id my-eval-01 --eval
echo ============================================================
echo.
set "COACH_CACHE=1"
"%PY%" run_demo.py --jd data\jd_sample.md --resume data\resume_sample.md ^
  --answers data\answers_by_category.json --questions 7 --reps 3 --no-variety ^
  --env-file .env %*
echo.
echo ============================================================
echo   跑完了。比较两场：python -m coach.eval.compare_runs ^<a^> ^<b^>
echo ============================================================
pause
