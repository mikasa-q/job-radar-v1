# 阶段5第二步：三路接口与离线汇总

权威 wire 契约仍为 `STAGE5_CONTRACTS.md` / `STAGE5_OPENAPI.json`；本步不重冻字段、不改公开 schema。

## 交付边界与分工

- A：`api_v1_resumes.py` / `test_api_v1_resumes.py`，PDF multipart、独立进程解析、rules画像、查询与失败重放。
- B：`api_v1_matching.py` / `test_api_v1_matching.py`，岗位列表/复合ID、范围与formal门禁、同代索引与配置快照、原BM25匹配、任务查询。
- C：`api_v1_reports.py` / `test_api_v1_reports.py`，严格已存匹配快照、离线报告、JSON/Markdown下载、失败状态；模拟SDK不出网验证。
- 主代理：应用注册、共享生命周期/journal/预算门禁、展示包装、并发端到端与回滚。三路只写独占模块与测试，核心逻辑仍调用阶段1–4原函数。

## 关键保持与强化

1. 原所有端点、请求/响应、operationId、资源ID、状态、错误码和预算政策不变。应用标题/version仍保持冻结OpenAPI中的step1标识，实施版本以本说明区分，不借模块重构改wire元数据。
2. 上传检查实际字节与multipart边界；文件在成功/错误/取消后关闭；PDF解析进程在正常/超时/异常路径回收。CPU/墙钟/采样RSS沿用原契约，不宣称硬内存封顶。
3. 匹配捕获完整文档/画像/岗位/偏好/配置/manifest，执行时钉住索引代；formal继续复用confirmed门禁，不提升审核状态。rules/BM25算法和排序不改。
4. 报告只消费已存MatchPayload；生成前与执行时校验四字段快照hash、manifest与配置一致性，原引擎继续校验排名/引用。下载只读正文，不重新生成。
5. 新展示函数 `render_api_report` 只替换固定顶部横幅：offline明确未调用LLM，scripted明确模拟，live分支要求调用方显式声明并存在供应商audit。完成状态本身不推定真实模型；live展示分支仅合成audit测试，HTTP仍只offline。旧renderer、旧报告与历史raw字节不改。
6. 画像/报告共同调用 `OfflineService.require_live()`，无env/HTTP开关；固定llm_disabled，误接返回门禁也不会掉入rules/offline。Hybrid即使allow_dense_fallback=true仍503。
7. journal每次读取重验ID、input/snapshot/payload绑定。磁盘写入失败使本实例fail-stop：后续资源读/新任务返回store_unavailable，不宣称持久成功。重启重新校验磁盘，pending变interrupted且不补跑。executor分发失败同样持久interrupted；槽位必释放。
8. 仍是本地单用户、单实例、一个工作线程、受限队列；不是生产鉴权、多进程共享或持久分布式任务队列。journal维持既有结构，没有SQL迁移。

## 验证与运行

运行方式仍见 `STAGE5_FASTAPI.md`。新增三路专项与主代理集成测试，另保留全部旧测试；冻结OpenAPI逐对象完全相等。

```sh
.venv/bin/python -B -m pytest -q tests/test_api_v1.py tests/test_api_v1_resumes.py tests/test_api_v1_matching.py tests/test_api_v1_reports.py tests/test_api_v1_integration.py
.venv/bin/python -B examples/stage5_offline.py --output /absolute/new/synthetic-directory
```

验证先合成PDF/临时证据库/独立索引与TestClient，再只在127.0.0.1启动Uvicorn完成实际HTTP。覆盖并发同键单执行、排队满无占位、重启重放、缺失依赖后报告下载、输入/索引漂移、存储故障、错误脱敏、模拟失败零重试首停、历史版本只读、预算纯算术。最终命令/字面结果、精确差分和独立副本回滚见本批VERIFICATION.txt，不沿用代理的阶段性计数。

## 本步停止点

真实LLM请求0、真实小样本0、本轮LLM费用¥0。预算上限¥1.00，历史actual¥0.05320458、reserve¥0.6829056、保守暴露¥0.73611018不重置；纯预算投影不是已启用收费账本。后续供应商接入/全局持久收费账本/真实验收均另等用户确认。

可复用接续提示词：

```text
先读NOTES.md、docs/STAGE5_CONTRACTS.md与docs/STAGE5_STEP2.md，并核对本批VERIFICATION.txt。
保持冻结wire与阶段1–4核心、历史报告、数据库不变。不要从任务succeeded推断LLM completed。
本轮三路离线接口已汇总；不要重派或重新执行旧真实批次。
如用户授权后续真实适配，先提最新累计预算/样本/token/次数及跨请求持久账本方案；
未确认前保持llm_disabled与hybrid_not_enabled，不读取凭据或发送真实请求。
```
