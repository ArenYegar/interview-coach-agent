# 人工评分校准（10 分钟，唯一还缺的数字）

## 三步
1. 打开 `results/human_scores.csv`，里面已经预填好 demo-final 那 7 道题的 qid。
   题目原文见 `results/report_demo-final.md` 的逐题表，或 `runs/demo-final/trace.jsonl`。
2. 每行填 `human_score`（0~1，两位小数）——用你自己的标准给"当时的回答"打分，别迁就机器分：
   - 0.9~1.0 覆盖完整、有取舍与量化
   - 0.7~0.8 主线清楚、缺细节
   - 0.4~0.6 沾到边但空泛
   - 0.0~0.3 答非所问或没答到点上
   `comment` 列可写一句为什么（可选）。
3. 跑一条命令，得到一致性指标：
   ```powershell
   C:\Users\hexin\.conda\envs\llm\python.exe -m coach.eval.harness demo-final
   ```
   输出里会出现 `"human": {"agreement": ..., "mae": ..., "n": 7}`：
   - agreement = 自动分与人评分差值 ≤0.2 的比例（这是"一致率"）
   - mae = 平均绝对误差

## 想更严谨（推荐）
再跑 2 场拿到 ≥15 个题目的评分（`--interactive` 真人作答），样本量上去后一致率才有说服力：
```powershell
python run_demo.py --jd <JD.md> --resume <简历.docx> --interactive --questions 5 --run-id personal-01
```
（`personal-*` 命名的运行与产物已在 .gitignore 里，不会进仓库。）

## 口径说明
- 只报两个数：一致率（容差 0.2）与 MAE；不报"提升 X%"这类无法自证的指标
- 没打分之前 harness 一律显示 `awaiting_scores`，不会用任何估算值填充
