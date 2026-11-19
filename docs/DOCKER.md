# Docker 本机一键启动

Docker Desktop 已完成首次设置，或本机已有可用的 Docker 引擎与 Docker Compose v2.24.4+。在仓库根目录运行：

```sh
./docker-start.sh
```

首次构建需要联网拉取 Python 基础镜像和依赖。不需要主机 Python 或 `.venv`。脚本构建镜像并等待 `/v1/ready` 返回 HTTP 200；容器内监听 `0.0.0.0:8000`，只发布到主机 `127.0.0.1:8080`。V1 无用户鉴权，不要转发到公网。

```sh
curl -fsS http://127.0.0.1:8080/v1/ready
./docker-start.sh stop                          # 保留本地状态
# 8080 被占用时：
JOBRADAR_PORT=18081 ./docker-start.sh
```

API 页面：`http://127.0.0.1:8080/docs`。排查启动失败：`docker compose --env-file /dev/null logs --tail 50 api`。

## 默认模式：不加载 LLM

入口为 `python -m job_radar.api_v1_serve --offline`；不读取密钥、不挂载真实账本，LLM 请求在本地返回 `503 llm_disabled`。在 `/docs` 使用 `POST /v1/reports/from-pdf` 上传 PDF，服务依次运行 `rules` 画像 → `bm25_rules` 匹配 → `offline` 报告并直接返回可下载的 Markdown。离线报告的分析状态为 `not_run`，不是生成失败。

| 配置 | 默认行为 |
| --- | --- |
| `JOBRADAR_EVIDENCE_DB` | 只读挂载 `~/Desktop/jobradar-v1-data/evidence.sqlite3`；可指定其他兼容证据库的绝对路径。替换证据库后重新启动，派生词法索引存容器的临时目录，不会写回证据库。 |
| `JOBRADAR_OFFLINE_STORE` | 独立的本机持久状态目录 `~/.jobradar/docker-v1`。包含上传解析结果及画像、任务和报告，请按私有数据管理。可以指定另一个私有目录（权限 `0700`）。停止不会删除状态。 |
| `JOBRADAR_DATASET_ID` | 默认 `v1-evidence`。 |
| `JOBRADAR_MODEL_DIR` | 自动寻找已知阶段三归档里的 BGE 固定版本快照；也可指定含 `model.safetensors` 的快照绝对路径。找到后只读挂载到 `/models/bge`，不复制、不下载、不删除。V1 HTTP 的 Hybrid 门禁仍关闭，离线 BM25 不加载该模型。 |

## 显式 Live 模式：仅在已有有效审批时使用

此模式不用于本轮真实生成验收。当前历史 S1/J1 scope 的授权已用完，**不要拿旧账本强行启动或改写账本**。如未来获得新授权，沿用[阶段 5 服务启动与审批流程](STAGE5_SERVICE_LIVE.md)，并传已有审批对应的输入：

```sh
JOBRADAR_LIVE_STORE="$RUN_ROOT/api-store" \
JOBRADAR_EVIDENCE_DB="$J1_EVIDENCE/evidence.sqlite3" \
JOBRADAR_INDEX_ROOT="$J1_EVIDENCE/derived" \
JOBRADAR_DATASET_ID=synthetic-stage4-acceptance-v1 \
./docker-start.sh --live
```

- 账本必须用审批绑定的原 canonical 绝对路径；容器同路径挂载，不改写绑定。不要与宿主机服务或其他容器共用同一份运行中账本。
- 仅在 Live 模式把 `~/.claude-mem/.env` 文件只读挂载给启动入口读取，不使用 Compose `env_file` 注入密钥；不传入构建、不复制进镜像。别把密钥写入源码、构建参数或命令行。
- 入口仍使用 `allow_report=True` 并保留 scope、剩余次数、预算、wire/context 审批和零重试门禁。启动不生成；`/v1/ready` 验证证据和索引，不探测供应商，`real_llm` 字段保持保守的 `false`。
