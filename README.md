# JobRadar V1：AI 求职匹配助手

面向本科在校生的 AI 求职匹配 / 决策助手（学习与求职项目，非商业产品）。

链路：简历 PDF → 解析画像 → 混合 RAG 检索岗位 → 匹配度 → 技能差距报告 → 求职建议。

## 它是什么

- 不是传统 RAG 问答：LLM 只当分析员不当裁判，每条结论必须引用证据 ID，无证据标 unknown，不生成录用概率。
- 检索内核：Embedding + BM25 混合检索，RRF 融合；检索取前 10（topK=10），LLM 只分析前 3（topN=3）。Reranker 与 LangGraph Agent 放在 V2。
- 本仓库由 JobRadar 爬虫版演进而来，保留原采集与查询能力，V1 在此基础上做 AI 匹配。

## 当前状态

- 阶段 1–5 已完成。阶段 5 已通过两次真实 LLM 调用验收：S1 画像 1 次、J1 报告 1 次，累计 ¥0.06684288 / ¥1.00 预算；J1 报告自动核对 10/10 通过。
- 检索评测：45 条人工标注（3 条查询 × 15 个岗位），Macro Recall@10 0.95、MRR@10 0.75、nDCG@10 0.834。dev 与 test 共用同一批 15 个岗位，指标为描述性结果。
- 全量回归 2339 项通过。

## 快速开始

Python 3.10+。

```bash
git clone https://github.com/mikasa-q/job-radar-v1.git
cd job-radar-v1
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m uvicorn job_radar.api_v1:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000/docs。

只绑定 127.0.0.1；无账户、鉴权、多用户与生产部署能力。

## 离线演示

不触真实数据的可重复演示（合成 PDF、合成证据库，HTTP 走内存 TestClient）：

```bash
.venv/bin/python -B examples/stage5_offline.py --output /absolute/new/directory
```

## API（一览，统一 /v1）

- `GET /health`、`GET /v1/ready`
- `GET /v1/jobs`（分页）、`GET /v1/jobs/{dataset_id}/{job_id}`
- `POST /v1/resumes`、`GET /v1/resumes/{resume_id}`
- `POST /v1/resumes/{resume_id}/profiles`、`GET /v1/profiles/{profile_id}`
- `POST /v1/matches`、`GET /v1/matches/{match_id}`
- `POST /v1/reports`、`GET /v1/reports/{report_id}`（`format=markdown`）

上传返回 201；画像 / 匹配 / 报告创建返回 202，再 GET 轮询进度。真实 LLM 调用全局串行、零重试；默认离线模式不发真实请求。

## 配置

仅服务端读取：

- `JOBRADAR_API_STORE`（默认 `~/.jobradar/api-v1`）
- `JOBRADAR_EVIDENCE_DB`（独立证据库）
- `JOBRADAR_DATASET_ID`（默认 `v1-evidence`）
- `JOBRADAR_INDEX_ROOT`（阶段 3 索引）

`JOBRADAR_DB` 仍只配置旧 `/jobs` 库。

## 项目结构

```text
src/job_radar/   业务引擎（解析、检索、匹配、报告、FastAPI）
docs/            阶段契约与说明（STAGE4 / STAGE5 系列）
examples/        检查点与离线演示脚本
tests/           2339 项回归与专项测试
verification/    基线、补丁与验证记录
NOTES.md         当前项目状态与下一步
```

## 已知限制

- 单实例、本地运行；真实 LLM 调用默认关闭，需显式放行，首失败即停，不自动降级冒充。
- 费用走调用方自己的 API key，账本原子预留、失败即停；本仓库不含任何 key。
