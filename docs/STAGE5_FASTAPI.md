# 阶段5：离线整合服务（第二步）

共享契约见 STAGE5_CONTRACTS.md；三路接口与主代理离线汇总见 STAGE5_STEP2.md。真实LLM验收仍未运行；冻结OpenAPI元数据保持原step1值。

从项目根目录启动（需可编辑安装；已锁定现有python-multipart 0.0.32，无需升级旧依赖）：

```bash
.venv/bin/python -m uvicorn job_radar.api_v1:app --host 127.0.0.1 --port 8000
```

配置仅服务端读取：JOBRADAR_API_STORE（默认~/.jobradar/api-v1，新私有目录）、JOBRADAR_EVIDENCE_DB（独立证据库）、JOBRADAR_DATASET_ID（默认v1-evidence）、JOBRADAR_INDEX_ROOT（已准备且兼容的阶段3索引）。JOBRADAR_DB仍只配置旧/jobs库。新接口不自动导入岗位、构建索引或联网下载模型。

打开/docs，从/v1/jobs选候选复合ID；上传PDF（Idempotency-Key必填）→POST rules画像→轮询→POST bm25_rules匹配→轮询→POST offline报告→下载JSON/Markdown。离线report.status=not_run是正确结果。

完全不触及真实数据的可重复演示：

```bash
.venv/bin/python -B examples/stage5_offline.py --output /absolute/new/directory
```

演示只在新目录创建合成PDF、合成临时证据库、派生索引和API资源，实际HTTP用TestClient内存传输；保存完整request/response与报告。Uvicorn真实TCP另见交付HTTP证据。

只绑定127.0.0.1；本步无账户/鉴权/多用户/生产部署能力。未启用的LLM和Hybrid显式503，无自动重试或隐式收费降级。
