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
| Web 界面 | web/index.html + web_app.py + web.cmd | 拖拽上传简历/JD → 出题 → 逐题作答（含追问）→ 复盘报告；会话落盘可刷新恢复 | ✅ 09-20 端到端实测（真模型 2 题：上传→6 轮→报告），前端三步截图核对 |
| 会话持久化 | coach/session.py | 一问一答 + runs/<id>/web_state.json；刷新/换标签页可续答 | ✅ 单测覆盖（恢复后题目与分数序列一致） |
| 上传解析 | coach/ingest.py | docx/pdf/txt/md；PDF 多后端回退（本机无 PyMuPDF 也能读） | ✅ 09-20 用真实 PDF 简历经接口实测通过 |
| 选题多样性 | coach/tools/search_bank.py + engine.py | 练习模式换题 + 避开近 3 场 + seed 可复现；AI 方向 JD 方向加权 + 非方向类目配额 | ✅ 09-20 实测：多样三次不同、同 seed 一致、非方向 ≤40% |
| 追问去重 | coach/graph/engine.py + coach/session.py | 第二层换维度 + 只问未问过的要点 + 无新要点即停；已问要点随会话落盘 | ✅ 09-20 真实模型实测：两层文案不同、要点不重叠 |
| 复盘文档 | docs/bugs_and_fixes.md | 8 类 bug 的「现象/复现/根因/修法/验证/面试怎么讲」 | ✅ 09-20 |
| 测试 | tests/*.py | **81 项全过**（工具/编排/追问层数/检索/图/API/harness 复核防护/端点与模型参数兼容/模型记录与旧产物兼容） | ✅ 09-20 |
| 多轮追问 | coach/graph/engine.py `_followup_text` | 按最低分维度深挖，最多 2 层；追问有效率可量化 | ✅ effectiveness=0.5 |
| 人评校准工具包 | results/human_scores.csv + docs/human_eval_howto.md | 模板预填 7 行，一条命令出 agreement/MAE | ✅ agreement 1.00 / MAE 0.0371（7 题已打分） |
| 混合检索 | coach/retrieval.py + coach/eval/select_compare.py | 稠密+BM25+RRF；零下载复用本机 bge；对比脚本落盘 | ✅ 默认仍用关键词（代理指标矛盾） |
| harness 健壮性 | coach/eval/harness.py + tests/test_harness_reviewer.py | 无 Key/离线不发起复核；无配对时不覆盖既有 reviewer_scores.csv；CLI 补 --env-file/--out/--reviewer | ✅ 09-20 实测不覆盖（md5 一致） |
| 文档同步 | README.md / PLAN.md / .env.example | 指标与限制与实测一致（单测 38、overall 0.37、人评 1.00/0.0371）；补 DeepSeek 切换说明 | ✅ 09-20 |
| 模型供应商 | coach/config.py + coach/llm.py + run_demo.py | 端点感知模型链（**DeepSeek→deepseek-flash→deepseek-chat**，即 v4 flash；DashScope→qwen-plus/max/turbo）；`--model/--base-url` 覆盖；启动打印 `[模型] 端点+链路`；思维链模型自动摘掉 temperature/response_format | ✅ 09-20 **已用真实 Key 跑通**；默认模型已按要求切为 **deepseek-flash（v4 flash）** —— flash 场次实测 overall 0.695 / 工具成功率 1.00 / 无降级 |
| 模型记录 | schemas.py + engine.py + export_report.py + harness.py | 每场在 run json 与报告头部记录 `llm_model`，eval 同步输出；旧产物缺字段仍可加载 | ✅ 09-20 |
| 题库扩充到 202 条 | coach/eval/bank_drafts.py + bank_tools.py | 草稿池→校验→合并三步；校验查重题/rubric/关键词过泛 | ✅ 2026-10-08 100→202 条（102 条机器草稿待人工抽查） |
| 引用与拒答 | coach/tools/score_answer.py + export_report.py | 评分带原话引用（确定性抽取）；题库缺口显式声明 | ✅ 2026-10-08（含 2 个连带 bug：Mock 解析提示词说明行、选题强行对口） |
| 记忆时间衰减与置信度 | coach/tools/track_weakness.py | 21 天半衰期 + 出现在几场 | ✅ 2026-10-08 |
| 效果归因 | coach/eval/attribute.py | 整场分拆成覆盖系数 × 答题均分 + 工具耗时 | ✅ 2026-10-08 |
| 可打印 HTML / 定制权重 / 门禁 | export_html.py + search_bank.req_weights + api 中间件 | PDF 走零依赖方案；权重让高权重要求先挑题 | ✅ 2026-10-08（119 项单测） |
| 结果缓存 + 并发采样 | coach/cache.py + coach/parallel.py + llm.py/trace.py 加锁 | 缓存键含所有影响输出的输入；并发只加在独立调用上 | ✅ 2026-10-08 缓存默认关（开了会掩盖抖动）、并发默认 4（`--reps 3` 不再多花时间） |
| LangGraph 版补齐多轮追问 | coach/graph/engine.py `interview_one` + langgraph_flow.py | 两版共用同一段业务逻辑，逐轮一致 | ✅ 2026-10-08 抽出 interview_one；新增逐轮一致性单测（qid+分数完全相同） |
| 人工确认闸门（HITL） | coach/review.py + engine/session/run_demo/api.py | 弱点画像先进 pending，人确认后才入库；驳回要真生效 | ✅ 2026-10-08 实测：5 条提议只批准 1 条 → 入库 4 轮、驳回 4 条不进画像 |
| JD 侧注入评测 | coach/eval/injection_suite.py `run_jd` | 判定只算"指令被执行"，不算"被如实收录" | ✅ 2026-10-08 实测 17% → 0%（第一版口径把"被收录"算带偏得 67%，属假指标，已修正） |
| 面试背书文档 | docs/interview_backing.md | 四个高频追问各配五段答法 + 实测反例；数字与产物文件一一对应 | ✅ 2026-10-08 写成（成本与延迟 / 提示注入 / 评测抖动 / 静默失败） |
| 评测稳定性探针 | coach/eval/stability_probe.py + coach/config.py + coach/tools/score_answer.py | 分出抖动来源；给出可用修法并量化改善 | ✅ 2026-10-08：temp=0 无效(6.0pp)、seed 更糟(25pp)、中位数×3 降到 2.5pp（最大 5.0pp） |
| Web 路径可观测性缺陷 | coach/trace.py + coach/session.py | Web 会话的 trace 必须完整（不能每次请求清空）；报告成本口径与 CLI 一致 | ✅ 2026-10-08 抓到并修：原先 Web 报告 cost=0、trace 只剩 1 行；修后 calls=4 / 6,643 token / 13 行 trace |
| 注入对抗与防护 | coach/tools/score_answer.py + coach/tools/parse_jd.py + coach/eval/injection_suite.py | 7 种注入话术 × 3 档防护可复跑；防护有边界说明；产出被带偏率 | ✅ 2026-10-08 实测 14%（关）→ 0%（提示隔离）→ 0%（+自洽校验）；边界＝规则路径 1.00 vs 模型路径 0.03 |
| 成本与延迟账 | coach/llm.py + coach/trace.py + coach/eval/cost_report.py + export_report.py | 每步 trace 带 token 增量；一次问答的 token/耗时可从产物复算；报告带成本段 | ✅ 2026-10-08 真实跑出 11 次调用 / 16,680 token / 每轮 1,505.6 token / 4,420.8 ms |
| 版本管理 | 本地 5 次提交，HEAD 618d092 | 本地 commit；个人化产物与 trace 未入库 | ✅ 未 push（用户明确要求先把本地做成熟） |

## 下一步（按优先级）

0. ~~多轮追问~~（已完成，见上表）
1. **人工校准**：~~第一场 7 题~~ 已完成（agreement 1.00 / MAE 0.0371）。当前最大缺口＝**样本量**：再跑 2 场（不同 JD，`--interactive` 真人作答，`personal-*` 命名）把评分题数提到 ≥15，一致率才有统计意义
2. ~~检索升级为混合检索~~（已完成；下一步：人工标注 30~50 条选题质量，才能定"是否切换默认策略"）
3. ~~追问升级为多轮深挖~~（已完成）——**下一步也已完成**：抽出 `engine.interview_one` 让两版共用同一段业务逻辑，LangGraph 版自动获得多轮追问；新增逐轮一致性单测（qid + 分数完全相同）
4. ~~会话持久化 + Web UI~~（已完成：FastAPI + 单文件前端，拖拽上传、逐题作答、报告、可恢复）
   —— 下一步可选：报告导出 PDF、题库按 JD 定制权重、多用户鉴权
5. 评测口径稳定性（已完成，见 `docs/eval_stability.md`）—— 实测 temp=0 无效、seed 更糟，改用「每题采样 3 次取中位数」：平均极差 6.0pp → 2.5pp
   —— 下一步已完成：`eval_run.cmd`（固定口径：确定性选题 + `--reps 3`）+ `coach/eval/compare_runs.py`（差异 vs 噪声带，判定线来源会打印出来）
5. ~~提示注入对抗与防护~~（已完成，见上表）—— 下一步：把 JD 侧的注入也纳入评测（当前攻击样本集中在作答侧），并给注入口径加一条"多轮累积"的用例
5. ~~成本与延迟账~~（已完成，见上表）—— 下一步：接入**结果缓存**（同一问题不重复花钱）与**并发检索**，再复跑一遍对比每轮耗时
5. ~~题库扩充~~：已扩到 **202 条**（100 人工校过 + 102 机器草稿，`coach/eval/gold_bank_draft.jsonl` →
   `bank_tools validate` 0 错误 → `merge --yes` 合并）。**待办：人工抽查草稿池**（校验报告 `results/bank_validation.md`）
5. 机器复核复跑：**需带 `--env-file`**（harness 已支持），否则会标 `reviewer_unavailable`（这是预期行为，不再覆盖旧结果）
6. ~~换 DeepSeek 跑一场真模型~~ 已完成（代码 + 真 Key 均已验证）。下一步：**用 DeepSeek 跑一场真人 `--interactive`**，它同时满足"真人一场"和"人评样本 ≥15 题"两项目标（建议 run-id 用 `personal-*`）
7. 可选：接入 Langfuse 本地容器做 trace 可视化；把 DeepEval 接进 harness 做回归

## 工程约定

- 全部产物落盘（results/、runs/），不接受"只在终端里看过"
- 每个工具都要有确定性兜底，保证模型不可用时仍能跑完
- 任何指标必须能指着产物文件说出处；无法测量的写 pending，不写形容词
- 评测/批处理脚本**不得因为在没有 Key 的环境里跑就覆盖既有产物**：新结果无效时要保留旧文件并显式标记状态
- 两条入口（run_demo.py / harness）的参数口径保持一致（--env-file 等）
