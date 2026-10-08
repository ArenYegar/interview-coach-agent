# 项目自证清单：简历上每句话对应哪个产物

> 用途：投递前把简历里关于本项目的每个数字都指到产物文件。**当场说不出口径的数字一律不要写** ——
> 被追问一层就露，杀伤力比不写大得多。
> 用法：改简历时对着这张表核一遍；面试前一天按"核对命令"把数字现场复算一次。

## 一、可写的说法 ↔ 产物 ↔ 核对命令

| 可写的说法 | 产物文件 | 核对命令 |
| --- | --- | --- |
| 6 个工具、Pydantic 契约、每工具可单测 | `coach/tools/*.py`、`coach/schemas.py` | `ls coach/tools/` |
| 显式状态机 + LangGraph 双实现，逐轮一致 | `coach/graph/engine.py`、`langgraph_flow.py` | `python -m unittest tests.test_graph_and_api` |
| 单测 106 项全过 | `tests/`（13 个测试文件） | `python -m unittest discover -s tests` |
| 提示注入对抗：作答案侧 14% → 0% | `results/injection_injection-run1.json` | `python -m coach.eval.injection_suite --side answer --levels 0,1 --env-file .env` |
| JD 侧注入 17% → 0%（判定只算"指令被执行"） | `results/injection_jd_jd2.json` | `python -m coach.eval.injection_suite --side jd --levels 0,1 --env-file .env` |
| 成本与延迟：11 次调用 / 16,680 token / 墙钟 48.0 s，LLM 等待占 99.8% | `results/cost_cost-demo-7.json`、`report_cost-demo-7.md` | `python -m coach.eval.cost_report cost-demo-7` |
| 每题 1,505.6 token / 4.42 s；首题固定开销 1,624 token | 同上（`per_turn` / `fixed` 字段） | 同上 |
| 评测稳定性：单次采样平均极差 6.0pp → 取中位数 2.5pp | `results/stability_probe-run1.json`、`stability_probe-E.json` | `python -m coach.eval.stability_probe --qids 技术-03,技术-01 --reps 3 --env-file .env` |
| 人评校准 agreement 1.00 / MAE 0.0371（7 题） | `results/human_scores.csv`、`results/eval_*.json` | `python -m coach.eval.harness <run_id> --env-file .env` |
| 题库 100 条 / 7 类，每条带参考要点 | `coach/eval/gold_bank.jsonl` | `wc -l coach/eval/gold_bank.jsonl` |
| 出题覆盖率 coverage@k 1.00（6/6 硬性要求） | `results/report_*.md` 头部 | 见报告 |
| 工具调用成功率 1.00 | trace 里 `ok=True` 占比 | `python -m coach.eval.cost_report <run_id>` |
| 跨场次弱点画像 + HITL 人工确认闸门 | `coach/review.py`、`runs/<id>/pending_review.json` | `python run_demo.py ... --hitl` |

## 二、**不要写**的东西（不能自证或已过时）

- 任何"提升 X%"式的宣传语（项目自己就拒写这类无法自证的表述）。
- "评分准确" —— 只能说"与人评无系统性偏差（7 题，同场同源）"。
- 混合检索"更优" —— 目前两个代理指标互相矛盾，**结论是未证明**，不能写更优。
- 旧的 0.37 / 0.4957 等早期场次数字（那次是规则分、不是真实模型结果）。数字要先重跑 harness 再引用。

## 三、简历条目草稿（可直接粘，风格对齐你现有简历）

```
多智能体求职陪练 Agent（LangGraph 工具编排 + 可评测系统）    github.com/ArenYegar/interview-coach-agent
项目描述：上传 JD 与简历 → 岗位要求结构化对齐 → 由"出题 → 追问 → 评分 → 弱点追踪"的状态机跑一场模拟面试，
产出可复现的复盘报告与指标；面向大模型应用 / Agent 方向的岗位考察面自建题库与评分口径。
技术栈：Python、LangGraph、FastAPI、Pydantic、SQLite、Qwen / DeepSeek、FAISS（混合检索）、unittest
• 工具编排与契约：6 个工具全部走 Pydantic 契约、每个都有确定性兜底（模型不可用时链路不中断但显式标记降级）；
  显式状态机与 LangGraph 双实现共用同一段业务逻辑，逐轮一致性由单测锁住（共 106 项单测）。
• 可靠性工程：模型降级链 + 空响应判失败 + 幂等写入；提示注入防护（7 种话术 × 3 档，作答案侧 14% → 0%，
  JD 侧 17% → 0%）；跨场次记忆加人工确认闸门（Agent 只提议、人确认后才入库，驳回的要点不进画像）。
• 评测体系：100 条金标准题库 + 4 类指标（覆盖率 / 工具成功率 / 要点覆盖率 / 追问有效率）；
  自建稳定性探针定位"单次采样抖动"并把平均极差从 6.0pp 压到 2.5pp（每题采样 3 次取中位数）。
• 成本与延迟：每场 11 次模型调用、16,680 token、墙钟 48 s，其中 LLM 等待占 99.8%（规则与落库仅 22 ms）；
  独立调用并发后 reps=3 不再增加墙钟；报告自带成本与延迟段。
```

## 四、替换建议

用这一条替掉简历里的「电商推荐系统：多路召回与 DeepFM 排序」——
理由：那条偏算法方向、与目标岗位（大模型应用 / Agent / RAG）不对口；
换上之后两个项目正好一个打 RAG 命题、一个打 Agent + 可靠性 + 评测命题，覆盖完整。

## 五、投递前要先做完的三件事

1. **推到 GitHub**（现在只有本地提交，简历上写链接就点不开）—— 推之前跑一遍密钥/PII 扫描，
   `runs/` 与含真人简历的产物必须已被 `.gitignore` 挡住。
2. **人评样本扩到 ≥15 题**（当前 7 题同场同源）—— 跑法：双击 `practice.cmd` 两场，结束后把 xlsx 交回。
3. **重跑一次 harness 再引用数字** —— `results/eval_*.json` 是静态产物会过期，
   引用前用 `python -m coach.eval.harness <run_id> --env-file .env` 现场重算。
