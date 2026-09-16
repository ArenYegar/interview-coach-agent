# 选型与取舍（面试用）

| 层 | 选了 | 为什么 | 备选与迁移成本 |
| --- | --- | --- | --- |
| 模型接入 | OpenAI 兼容协议 + 降级链 | 一套 SDK 接 DeepSeek/Qwen/GLM；降级与限流集中处理 | 换 vLLM 本地部署只改 base_url |
| 编排 | 显式状态机（自研）+ LangGraph 表达 | 需要中断恢复、人机协同、可回放；两套实现共用业务函数，避免漂移 | 换 LangGraph 1.x 只改节点名/API |
| 工具 | Pydantic 契约 | 可校验、可测试、可生成 JSON schema 喂给模型 | 接 MCP Server 时可原样复用函数签名 |
| 评分 | 规则确定性分 + 模型语义分（双轨，method 标注） | 评测/回归要可复现；模型分解决语义，规则分做兜底 | 接 DeepEval 时把规则分当 baseline |
| 存储 | SQLite + JSONL trace | 单机零运维；弱点追踪只需关系表 | 换 Postgres/pgvector 改 `config.py` 一处 |
| 服务 | FastAPI + SSE | 流式返回，便于演示"边跑边看" | 上 Docker/Nginx 是纯运维工作 |
| 评测 | 自建 harness + 金标准题库 | 四类指标都能从产物直接算，不依赖第三方口径 | 接 Ragas/DeepEval 做补充 |

## 三个"为什么不"

- 不上 Milvus/向量库：题库 100 条是确定性关键词匹配就够；规模真上去再换，迁移点很小。
- 不上多 Agent 框架（CrewAI/AutoGen）：这个场景的协作是**串行角色**（出题→面试→教练），显式状态机更可控、更便宜。
- 不做微调：不是本岗位方向，且本地 6GB 显存没有意义。
