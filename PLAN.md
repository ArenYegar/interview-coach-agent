# 执行清单与进度（文件级）

## 已完成（本次交付）

| 模块 | 文件 | 验收标准 | 状态 |
| --- | --- | --- | --- |
| 配置/密钥 | coach/config.py | 密钥只从环境变量或 --env-file 读；离线开关可判 | ✅ 16 项单测覆盖 |
| 契约 | coach/schemas.py | 9 个 Pydantic 模型，序列化往返正常 | ✅ |
| 模型接入 | coach/llm.py | JSON 模式 + 解析失败自动修复 + 模型降级链 + Mock 降级 | ✅ 真实调用通过 |
| 可观测 | coach/trace.py | 每步 JSONL，脱敏，异常也记录，可回放 | ✅ |
| 工具 1 | coach/tools/parse_jd.py | LLM 优先、规则兜底（source 字段标注） | ✅ |
| 工具 2 | coach/tools/match_resume.py | 硬性要求权重 2、加分项 1；给证据句与缺口 | ✅ |
| 工具 3 | coach/tools/search_bank.py | 覆盖优先选题（先满足硬性要求） | ✅ coverage 0→0.8 |
| 工具 4 | coach/tools/score_answer.py | 规则分确定性 + 模型分语义增强，method 可区分 | ✅ |
| 工具 5 | coach/tools/track_weakness.py | SQLite 持久化、幂等写入、跨场次聚合 | ✅ 修了重复累计 bug |
| 工具 6 | coach/tools/export_report.py | Markdown + JSON 双落盘 | ✅ |
| 题库 | coach/eval/gold_bank.jsonl | 100 条 / 7 类，每条带参考要点 | ✅ |
| 编排 | coach/graph/engine.py | plan→ask→score→(追问)→aggregate→report，全程 trace | ✅ |
| LangGraph | coach/graph/langgraph_flow.py | 与 engine 行为一致，缺依赖自动降级 | ✅ 单测通过 |
| 评测 | coach/eval/harness.py | 4 类指标落盘；人评缺失时明确 pending | ✅ |
| 入口 | run_demo.py / api.py | CLI（含真人模式）+ HTTP/SSE | ✅ SSE 单测通过 |
| 测试 | tests/*.py | 22 项全过（工具/编排/追问层数/检索/图/API） | ✅ |
| 多轮追问 | coach/graph/engine.py `_followup_text` | 按最低分维度深挖，最多 2 层；追问有效率可量化 | ✅ effectiveness=0.5 |
| 人评校准工具包 | results/human_scores.csv + docs/human_eval_howto.md | 模板预填 7 行，一条命令出 agreement/MAE | ✅ 待人工打分 |
| 混合检索 | coach/retrieval.py + coach/eval/select_compare.py | 稠密+BM25+RRF；零下载复用本机 bge；对比脚本落盘 | ✅ 默认仍用关键词（代理指标矛盾） |
| 版本管理 | .git 首次提交 7ccc357 | 本地 commit；个人化产物与 trace 未入库 | ✅ 未 push |

## 下一步（按优先级）

0. ~~多轮追问~~（已完成，见上表）
1. **人工校准自动评分**：跑 3 场（不同 JD），人工填 `results/human_scores.csv`，重跑 harness 得出"一致性 + MAE"，这是当前最大缺口
2. ~~检索升级为混合检索~~（已完成；下一步：人工标注 30~50 条选题质量，才能定"是否切换默认策略"）
3. ~~追问升级为多轮深挖~~（已完成；下一步：把同一能力补进 LangGraph 版，消除两版差异）
4. 会话持久化 + Web UI（Streamlit 或 FastAPI+SSE 前端），支持中断恢复演示
5. 可选：接入 Langfuse 本地容器做 trace 可视化；把 DeepEval 接进 harness 做回归

## 工程约定

- 全部产物落盘（results/、runs/），不接受"只在终端里看过"
- 每个工具都要有确定性兜底，保证模型不可用时仍能跑完
- 任何指标必须能指着产物文件说出处；无法测量的写 pending，不写形容词
