# 技术栈（截至 2026-09-20，版本为运行环境实测值）

> 版本来源：本机 `C:\Users\hexin\.conda\envs\llm` 实测（`importlib.metadata.version`），不是文档里抄的。
> 复核命令：
> `C:\Users\hexin\.conda\envs\llm\python.exe -c "import importlib.metadata as m; [print(p, m.version(p)) for p in ['pydantic','openai','fastapi','uvicorn','langgraph','sentence-transformers','rank-bm25']]"`

## 一句话

**Python 3.10 + Pydantic 契约 + 自写显式状态机（同一流程另有 LangGraph 实现）+ OpenAI 兼容协议接大模型
+ FastAPI/SSE 服务化 + 单文件原生 JS 前端 + SQLite/JSONL/Markdown 落盘 + 自建评测 harness。**
没有前端框架、没有向量库、没有容器、没有可观测平台——这些是"刻意不上"，理由都写在下面。

## 分层清单（组件 → 版本 → 为什么是它 → 为什么不选另一个）

| 层 | 组件（实测版本） | 为什么是它 | 为什么不选另一个 |
| --- | --- | --- | --- |
| 运行时 | Python 3.10.19 + conda 环境 `llm` | 复用本机已装好的环境（torch/transformers 都在），**零新装**当天可跑 | 不新建 venv：本机已有重依赖，重建一遍纯浪费 |
| 契约 | Pydantic 2.12.5 | 工具输入输出都是模型：可校验、可序列化，还能直接生成 JSON schema 约束模型输出 | 不用 dataclass/dict：拿不到 schema，模型结构化输出就得手写校验 |
| 业务编排 | 自写显式状态机（engine）；LangGraph 0.4.5 为同一流程的第二实现 | 需要中断恢复、人机协同、trace 能定位到具体步骤；状态机每步都可断言 | 不用 AgentExecutor：黑盒编排，上面三点都不合格 |
| 模型接入 | openai 1.109.1（OpenAI 兼容协议） | 一套代码通吃 DeepSeek / DashScope / OpenAI，换供应商只改端点与模型名 | 不用各家原生 SDK：会把代码绑死在一家；兼容协议已是事实标准 |
| 结构化输出 | JSON mode + 解析失败自动修复 + 模型降级链 + 离线 Mock | 模型输出会抖，评测要可复现；降级后链路仍能跑完 | 不用纯提示词"请输出 JSON"：解析失败率明显更高 |
| 服务化 | FastAPI 0.128.0 + uvicorn 0.40.0（+ starlette 0.50 / anyio 4.12） | 异步 + 自动 OpenAPI 文档 + 原生支持 SSE 流式 | 不用 Flask：SSE 与异步要额外折腾；同步栈在高并发演示上没优势 |
| 文件上传 | python-multipart 0.0.21 | FastAPI 处理 multipart 的标准依赖 | — |
| 简历解析 | python-docx 1.2.0 / pypdf 6.5.0 / pdfplumber 0.11.9 / pdfminer.six 20251230 | 多后端回退：本机**没有** PyMuPDF 也能读 PDF（实测踩过这个坑） | 不硬依赖单一 PDF 库：缺一个库就报"解析失败"是不可接受的 |
| 检索（可选开关） | sentence-transformers 5.2.0 + bge-large-zh（本机已有）+ rank-bm25 0.2.2 + RRF 融合 | 复用本机模型**零下载**；BM25 补关键词、稠密补语义，RRF 融合两路 | 不默认开启：两个代理指标互相矛盾（关键词口径关键词法更好、语义口径混合法更好），缺人工标注前不下结论 |
| 前端 | 原生 HTML/CSS/JS 单文件（无框架、无构建、无 CDN） | 拖拽上传、逐题作答、报告三类交互而已；离线可开、零依赖、随时能发给别人跑 | 不上 React/Vue：为三个页面引入构建链与依赖，收益为负（面试可讲"知道什么时候不该上框架"） |
| 存储 | SQLite（内置 sqlite3）+ JSONL + JSON + Markdown | 单机自用：弱点跨场次聚合用 SQLite，trace 用 JSONL（可回放），会话状态与结果用 JSON，报告用 Markdown | 不上 Postgres/pgvector：本地无并发需求；迁移只改适配层（约 2 个文件） |
| 可观测 | 自写 JSONL trace（脱敏 + 耗时 + 成败 + 可回放） | 格式自己定，产物即证据；出问题能按 run_id 回溯每一步 | 不上 Langfuse：要跑容器；trace 已独立成层，将来替换成本很低 |
| 评测 | 自建 harness（出题覆盖率 / 工具成功率 / 要点覆盖率 / 追问有效率 / 人评一致性 / 机器复核一致性） | 指标口径必须自己能解释、能指着产物文件说出处；本项目评的是"面试问答"而不是 RAG 生成质量 | 不用 DeepEval/RAGAS 做主评：面向生成质量的指标不匹配，且要额外依赖；保留为后续回归选项 |
| 测试 | unittest（标准库），81 项 | 环境里没有 pytest，不再为新装而新装；断言全在标准库能力内 | 不引入 pytest：零收益、多一层依赖 |
| 模型（运行配置） | deepseek-flash（主）→ deepseek-chat（降级），端点 api.deepseek.com | 便宜、快、JSON 模式稳；端点感知的模型链自动匹配供应商 | 不用 deepseek-v4-pro 作默认：重推理模型可能把 token 预算全花在 reasoning 上、返回空内容 |

## 刻意不碰的清单（面试问"为什么不用 X"时的答案）

| 没用的东西 | 理由 |
| --- | --- |
| Docker / K8s | 本地单机自用，容器化只增加演示成本；要上线时再补（Dockerfile 是 20 分钟的事） |
| 向量数据库（Milvus/pgvector） | 题库 100 条，内存里直接算向量就够；规模上去了再换 |
| 全量 LangChain | 链式封装会隐藏控制流，trace 定位和中断恢复都变难；只用 LangGraph 表达同一流程 |
| Langfuse / OTel | 需要额外服务；JSONL trace 已满足"可回放" |
| 前端框架 | 三个页面不值得一条构建链 |
| 微服务拆分 | 单进程跑得动，拆了只让调试变难 |

## 本地版 → 上线版要换的三个替身

| 本地用什么 | 上线换成什么 | 换的理由 |
| --- | --- | --- |
| SQLite | Postgres / pgvector | 多用户并发 + 弱点与题库向量化检索 |
| JSONL trace | Langfuse / OpenTelemetry | 集中查询、跨服务追踪、面板化 |
| 自建 harness | DeepEval 等回归框架 + CI 门禁 | 每次提交自动跑回归，避免指标退化 |
（另外：uvicorn 单进程 → gunicorn + uvicorn worker；报告 Markdown → 加 PDF 导出。）

## 规模事实（面试可报的硬数字）

- Python：35 个文件 / 4072 行（含测试与前端后端全部逻辑）
- 题库：100 条 / 7 类，每题 2 个评分维度、带参考要点
- 单测：81 项全过（unittest）
- 文档：README + PLAN + 4 份说明（人评操作、技术决策、面试脚本、踩坑复盘、评分答法）
- 前端：1 个 HTML 文件（含拖拽上传、逐题作答、报告三类界面）
