# interview-coach-agent · 多智能体求职陪练 Agent（本地版）

上传/指定一份 JD 和一份简历 → 结构化对齐岗位要求 → 由"出题 → 追问 → 评分 → 弱点追踪"的状态机跑一场模拟面试
→ 产出可复现的复盘报告与指标（全部落在 `results/`，不是形容词）。

面向岗位：大模型应用 / Agent / RAG 方向（AI 应用开发实习生）。

## 为什么做它

秋招复盘时我发现两件事：一是面试反馈很散，没人告诉我"哪类问题反复答不好"；二是我原来写的面试练习脚本是个线性流水线，
没有工具契约、没有状态、也没有评测——出了问题只能靠肉眼猜。
所以这个版本是我按工程标准重写的：**工具契约（Pydantic）+ 显式状态机 + 可回放 trace + 可复现指标**。

## 现状（本地已跑通，非设计稿）

- 题库 100 条 / 7 类，全部带参考要点（rubric keywords），可计算要点覆盖率
- 6 个工具全部可独立调用、可单测：`parse_jd` `match_resume` `plan_questions` `score_answer` `track_weakness` `export_report`
- 编排：显式状态机（`coach/graph/engine.py`）+ LangGraph 版（`coach/graph/langgraph_flow.py`，缺依赖自动降级）
- 降级设计：模型不可用 / 结构化输出解析失败 → 自动退回确定性规则打分，链路不中断，降级写进 trace
- 可观测：每步写 `runs/<run_id>/trace.jsonl`（脱敏 + 耗时 + 成败），可回放
- 弱点追踪：SQLite 持久化，支持**跨场次**聚合（同一弱点在多场里反复出现会被标为 high）
- 服务化：FastAPI（`/health` `/plan` `/score` `/report/{run_id}` `/stream` SSE 流式）
- 单测：`python -m unittest discover -s tests` → 16 项全过（工具 / 编排 / LangGraph / API SSE）

最近一次真实运行（真实模型 qwen-plus，7 题；输入 = 本机真实简历 + 示例 JD；命令见下）：

| 指标 | 值 | 口径 |
| --- | --- | --- |
| 出题覆盖率 coverage@k | **1.00** (6/6 硬性要求) | 有对口题的硬性要求数 / 硬性要求总数 |
| 工具调用成功率 | 1.00 (29 次 trace，0 失败) | trace 里 ok=True 的占比 |
| 要点覆盖率 point_coverage | 0.462 (18/39) | 评分命中的参考要点 / 全部参考要点 |
| 综合得分 overall | 0.50 | 各题加权总分均值 |
| 与人评一致性 | pending（待你填 `results/human_scores.csv`） | 自动分与人评分差值 ≤0.2 视为一致，同时报 MAE |

同一套流程的 LangGraph 版（3 题、示例简历）：overall 0.56、工具成功率 1.00。
注意：coverage@k 的分母是硬性要求数，所以 k 小于要求数时覆盖率天然偏低（3 题跑法为 0.17），这是口径而非 bug。

指标产物：`results/eval_<run_id>.json`、报告 `results/report_<run_id>.md`、原始 `results/run_<run_id>.json`。

## 架构

```
run_demo.py / api.py            ← 入口（CLI / HTTP+SSE）
coach/
├── config.py                   路径、模型接入（OpenAI 兼容）、脱敏、离线开关
├── schemas.py                  Pydantic 契约：JDProfile / MatchResult / Question / Rubric / ScoreCard / Turn / SessionReport
├── llm.py                      LLM 客户端：JSON 强约束 + 重试 + 模型降级链 + MockLLM（离线确定性）
├── trace.py                    JSONL trace（脱敏、耗时、可回放）
├── tools/                      6 个工具（每个都有确定性兜底，可单独测试）
├── graph/                      state.py + engine.py（状态机）+ langgraph_flow.py（同流程的 LangGraph 表达）
└── eval/                       gold_bank.jsonl（100 条）+ harness.py（指标）
data/                           示例 JD / 示例简历 / 演示用回答（均为合成或标注过的演示内容）
runs/                           运行产物（gitignore：含真人输入与 trace）
results/                        指标与报告（可提交，注意别把个人简历片段带上公网）
```

## 快速开始（Windows / 本机）

```powershell
# 1) 用你已有的 llm 环境（已含 pydantic/openai/fastapi/langgraph/python-docx），零新装
C:\Users\hexin\.conda\envs\llm\python.exe run_demo.py ^
  --jd data/jd_sample.md --resume data/resume_sample.md ^
  --answers data/answers_by_category.json --questions 7 --eval

# 2) 真人练习模式（逐题作答，输入 END 结束一题）
python run_demo.py --jd <JD.md> --resume <简历.docx> --interactive --questions 5

# 3) 离线确定性模式（不调模型，验证链路与单测）
$env:COACH_OFFLINE=1 ; python run_demo.py --jd data/jd_sample.md --resume data/resume_sample.md --questions 2 --eval

# 4) LangGraph 编排
python run_demo.py --jd data/jd_sample.md --resume data/resume_sample.md --answers data/answers_by_category.json --questions 3 --engine langgraph

# 5) API + SSE
uvicorn api:app --port 8000          # GET /health、POST /plan、POST /score、GET /stream?jd=&resume=&answers=
python -m unittest discover -s tests  # 单测
```

密钥只从环境变量读（`COACH_API_KEY` / `DASHSCOPE_API_KEY` / `QWEN_API_KEY` / `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`），
也可用 `--env-file` 指向一个 .env（本项目从不写入仓库）。没有密钥时自动进入离线 mock 模式。

## 评测口径（为什么这么定义）

| 指标 | 定义 | 为什么需要它 |
| --- | --- | --- |
| 出题覆盖率 | 有对口题的硬性要求数 / 硬性要求总数（按选题时的 `target_req_id` 映射） | 防止"随机挑热门题"，出题必须打到岗位要求上 |
| 工具调用成功率 | trace 中 ok=True 的事件占比 | Agent 的稳定性要能量化，而不是"看起来能跑" |
| 要点覆盖率 | 评分命中的参考要点 / 全部参考要点 | 让"答得好不好"有统一标尺（人评对齐的基线） |
| 与人评一致性 | human_score 与自动分差值 ≤0.2 视为一致；同时报 MAE | 自动评分必须被人工校准过才可信 |

**故意不写的东西**：没有"提升 X%"这类无法自证的宣传语；人评一致性在有人填 `results/human_scores.csv` 之前一律显示 `pending`。

## 设计取舍（面试会问）

1. **为什么用显式状态机而不是 AgentExecutor**：需要中断恢复、需要人机协同、需要 trace 能定位到具体步骤；黑盒编排在这三点上都不合格。
2. **为什么评分有确定性兜底**：LLM 输出会抖，评测与回归需要可复现；规则分（参考要点命中率 + 长度惩罚）保证同一输入同一结果，模型分只做语义增强，两者都落进 `ScoreCard.method` 便于对照。
3. **为什么选题先做覆盖再平衡类别**：项目里唯一的"业务目标"是覆盖岗位要求，所以把覆盖作为第一目标，类别平衡只做兜底（每类上限 k/3）。
4. **为什么用 SQLite + JSONL 而不是上向量库/可观测平台**：本地单机跑得动、迁移成本低；换成 Postgres/pgvector 或 Langfuse 只需替换适配层（`config.py` + `trace.py`）。

## 已知限制（诚实清单）

- 评分尚未与人工校准（`human_scores.csv` 是空模板）→ 这是当前最大缺口，也是下一步第一件事
- 题库 100 条为自建，覆盖面依赖作者经验；同一道题在不同 JD 下的权重相同，未做岗位定制
- 检索是关键词确定性匹配，未接 bge 嵌入（避免在本地再加依赖）；混合检索是下一步
- 追问只有一层（得分低且缺口 ≥2 时触发），未做多轮深挖
- 没有对话式 UI（只有 CLI + API/SSE），演示以终端为主；可当演示视频的旁白脚本

## 踩坑记录（都是真实修过的）

1. **静默降级最危险**：最早一版模型调用失败会悄悄退回规则打分，指标看起来"正常"，实际上是规则分算的。
   现在 `LLMClient.probe()` 在开跑前做一次可用性探测：不可用就把原因写进 `report.llm_status` 和 trace（`llm.unavailable`，ok=false），
   降级步骤也会记进 `degraded_steps` —— 评估时不能把降级当成成功。
2. **写入必须幂等**：同一 `run_id` 重跑会重复累计弱点，已改成先删后写（单测覆盖）。
3. **LangGraph 节点名不能与 state 键同名**：会报 `'match' is already being used as a state key`，节点统一加 `node_` 前缀。
4. **按类别回答的提示**：演示里"追问不再作答"是刻意的，避免用同一段话重复打分导致虚高。

## 安全与合规

- `runs/` 存真人输入与 trace，已在 `.gitignore`；真实简历文本不要提交到公开仓库
- 报告里可能包含简历片段（匹配证据句）：公开前请改用 `data/resume_sample.md` 跑
- 密钥永不写入仓库；`.env.example` 只有占位符

## 面试可讲的三句话

1. "我把面试陪练做成了显式状态机 + 工具契约，每个工具都能单测，模型挂了我有确定性降级，所以它不是 demo，是能回归的系统。"
2. "我定义了四个指标，并且**故意**不用无法自证的提升数字：覆盖率、工具成功率、要点覆盖率、与人评一致性（人评没填完就显示 pending）。"
3. "它解决的是我自己的问题——跨场次弱点追踪会告诉我哪类问题反复答不好，而不是每次从零开始练。"
