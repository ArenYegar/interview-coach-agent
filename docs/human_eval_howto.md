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
   # 想让"机器复核一致性"也一起重算，加 --env-file 指向你的 .env（否则复核段标 reviewer_unavailable）
   C:\Users\hexin\.conda\envs\llm\python.exe -m coach.eval.harness demo-final --env-file <你的 .env>
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

## 已出结果（2026-09-20，第一场 7 题）

| 题号 | 自动分 | 人工分 | 偏差 |
| --- | --- | --- | --- |
| 技术-05 | 0.00 | 0.05 | +0.05 |
| LL-01 | 0.83 | 0.85 | +0.02 |
| 项目-05 | 0.00 | 0.05 | +0.05 |
| Ag-11 | 0.68 | 0.65 | −0.03 |
| 技术-03 | 0.83 | 0.80 | −0.03 |
| RA-09 | 1.00 | 0.95 | −0.05 |
| 项目-02 | 0.13 | 0.10 | −0.03 |

一致率 **1.00（7/7）**，MAE **0.0371**，n = 7。

读数：偏差方向一致、幅度全在 ±0.05 内，说明自动评分没有系统性偏高或偏低；
规律是机器分在两端更绝对（判不合格给 0、判扎实给 0.83~1.00），人评会留一点余量。
**但只有 7 题、同一场、题库预设作答**，所以这只能说明"同量级、无系统性偏差"，
要证"评分准确"需要 ≥15 题 —— 见上面的"想更严谨"。

## 口径说明
- 只报两个数：一致率（容差 0.2）与 MAE；不报"提升 X%"这类无法自证的指标
- 没打分之前 harness 一律显示 `awaiting_scores`，不会用任何估算值填充
- 评分表模板：`results/human_scores.csv`（列 run_id,qid,human_score,comment）；填完直接重跑 harness
- 机器复核段需要真实 Key：无 Key / 离线时返回 `reviewer_unavailable`，**并保留上一次的 reviewer_scores.csv 不覆盖**
