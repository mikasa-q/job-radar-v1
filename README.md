# JobRadar V1：AI 求职匹配助手

面向本科在校生的 AI 求职匹配 / 决策助手（学习与求职项目，非商业产品）。项目从 JobRadar 爬虫版演进而来，保留原有的岗位采集与查询能力。

## 快速上手

### Docker 一键启动（默认离线）

启动 Docker Desktop 后，在仓库根目录运行：

```bash
./docker-start.sh
```

脚本会构建镜像并等待 `/v1/ready` 就绪。服务仅在本机 `http://127.0.0.1:8080`，打开 [API 文档（`/docs`）](http://127.0.0.1:8080/docs) 即可试用：选择 **上传 PDF，一站式生成岗位报告**，上传简历后直接下载 Markdown，无需手动串 API。

**默认不读取 API key，也不调用真实 LLM。** 离线报告包含岗位排序和规则证据覆盖，模型分析状态为 `not_run`。停止服务用 `./docker-start.sh stop`，本地状态会保留。数据路径与故障排查见 [Docker 启动说明](docs/DOCKER.md)。

### 真实 LLM（仅显式开启）

已有有效审批账本时，才使用 `api_v1_serve.py` 的 Live 入口：`python -m job_radar.api_v1_serve`，Docker 对应 `./docker-start.sh --live`。该入口挂接 `LiveBackend`（`allow_report=True`），密钥仅在运行时读取。启动本身不发请求；只有显式 `mode=llm` 且通过次数、预算及请求审批校验的任务才可调用。所需参数见 [Live 服务启动说明](docs/STAGE5_SERVICE_LIVE.md) 和 [Docker Live 模式](docs/DOCKER.md#显式-live-模式仅在已有有效审批时使用)。

## 核心流程

简历 PDF → 画像 → 检索与规则匹配 → 报告。索引内核支持 **BM25＋向量检索＋RRF**，但当前 V1 HTTP 接口（含 Live 模式）只开放 **BM25＋规则**。LLM 分析引用已有证据，不改变岗位排序，也不生成录用概率；简历没提及某技能不等于不会。见 [核心流程架构图](docs/ARCHITECTURE.md)。

## 已验证

- Docker 离线验收：本机证据库 **15 岗**可读取，合成 PDF 到离线 Markdown 报告的 HTTP 流程已跑通。
- 全量回归 **2401 项**通过。历史检索评测及 Live 验收与审批记录见 [NOTES.md](NOTES.md)。

## 离线演示

不触真实数据的可重复演示（合成 PDF、合成证据库，HTTP 走内存 TestClient）。不用 Docker 时，可用本机 Python 建立虚拟环境：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -B examples/stage5_pdf_report.py --output /absolute/new/directory
```

## API（一览，统一 /v1）

- `GET /health`、`GET /v1/ready`
- `GET /v1/jobs`（分页）、`GET /v1/jobs/{dataset_id}/{job_id}`
- `POST /v1/resumes`、`GET /v1/resumes/{resume_id}`
- `POST /v1/resumes/{resume_id}/profiles`、`GET /v1/profiles/{profile_id}`
- `POST /v1/matches`、`GET /v1/matches/{match_id}`
- `POST /v1/reports/from-pdf`：单次上传，默认直接返回 Markdown；显式 `?mode=llm` 须审批，只返回一个报告任务。
- `POST /v1/reports`、`GET /v1/reports/{report_id}`（`format=markdown`）

分步接口的上传返回 201；画像 / 匹配 / 报告创建返回 202，再 GET 轮询进度。新报告从已通过 v3 校验的结构化事实在本地渲染，结论按证据状态分组，引用可点击，完整审计信息保留在 JSON。旧报告也可离线重渲染，见 [可读报告说明](docs/READABLE_REPORT.md)。真实 LLM 调用仍全局串行、零重试；默认离线模式不发真实请求。

## 配置

以下为本机 Python 服务的配置；Docker 挂载及环境变量见 [Docker 启动说明](docs/DOCKER.md)。

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
tests/           回归与专项测试
verification/    基线、补丁与验证记录
NOTES.md         当前项目状态与下一步
```

## 已知限制

- 单实例、本地运行；真实 LLM 调用默认关闭，需显式放行，首失败即停，不自动降级冒充。
- 费用走调用方自己的 API key，账本原子预留、失败即停；本仓库不含任何 key。
