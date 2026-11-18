# JobRadar 当前状态

更新时间：2026-11-01（第三阶段 FastAPI 查询）

## 当前版本

工作目录：`/Users/thc/Documents/Codex/2026-10-21/build-ios-apps-plugin-build-ios-3/outputs/job-radar`

原始版本保留在 `/Users/thc/Documents/Codex/2026-09-27/niu/outputs/job-radar`，未覆盖。

## 学习进度

- 用户希望先理解项目结构、每个文件的职责和实际运行，不再要求逐行掌握解析器。
- 当前继续使用同步 requests。第三阶段已接 SQLite 与 FastAPI，同步查询路由使用普通 def；尚未加入异步数据库、Agent。
- HTML 获取与字段解析分离；真实站点规则集中在新增的 sources.py。

## 本次已完成

- Boss 公开首页最初正常响应的 81 条岗位已解析；真实列表卡片保存在 sample_data/boss-home.html。
- Boss 后续请求进入验证页，程序明确报错，退出 2，不以空结果覆盖 JSON。
- Python.org 招聘页通过新版 CLI 实时联网采集 21 条岗位，保存 JSON 和响应 HTML。
- 说明首页推荐并非 Python 搜索结果；列表摘要并非完整 JD；字段未提供时保留为空。
- 新增 `filters.py` 和 CLI 自定义筛选：title/city/company/skill/exclude-keyword/remote-only；不同选项 AND，同一选项重复 OR。
- 新增 `config.py` 和 `--config`：筛选条件可以保存为 JSON；命令行同名参数优先。
- 新增 `storage.py` 和 `--database`：使用 SQLite 保存岗位，按 URL upsert 去重；支持 `--query-city`、`--query-keyword` 和 `--limit` 查询。
- 修正原测试中因示例数据变动导致的过期断言，新增来源适配、HTTP 异常、CLI、JSON 保留、配置和 SQLite 测试；原有 39 项加本次 28 项，共 67 项通过。
- 原始文件及 SHA-256 已保存，回滚在独立副本验证；交付目录保留修改版。

## 第三阶段已完成

- api.py：GET /health、/jobs、/jobs/stats、/jobs/{job_id}；自动 /docs 与 /openapi.json。
- schemas.py：Pydantic 响应模型；参数校验、分页、技能精确匹配、来源筛选。
- storage.py：只读连接、SQL 参数绑定、同一快照计数与分页；同步连接显式关闭。
- 保留阶段二快照与 SHA-256；独立副本回滚恢复 39 项测试，不改动岗位数据库。
- 现有 data/jobs.db 为 102 条历史岗位（boss 81 + python.org 21）；未扩大采集范围。
- docs/STAGE3_FASTAPI.md：JD/面经来源、基础、工程进阶、场景题、学习路线与运行步骤。

## 当前待办

1. 按 README 启动 Uvicorn，打开 /docs，练习 city/keyword/limit/offset。
2. 对比 requests 发请求与 FastAPI 接请求，理解返回的 items 和 total。
3. 阅读 api.py 与 storage.py 的调用链；解释 200 空结果、404、422、503。
4. 读一个 TestClient 测试，再独立新增一个组合查询测试。

## 结果文件

- data/boss-home.jobs.json：本次真实 Boss 首页快照的 81 条岗位。
- data/python-org.jobs.json：本次真实联网获取的 21 条岗位。
- data/jobs.db：SQLite 岗位数据库示例。
- sample_data/snapshots.json：精简快照的来源与 SHA-256。
- verification/stage3/VERIFICATION.txt：本阶段基线、修改版、独立回滚与真实 HTTP 验证。
- docs/STAGE3_FASTAPI.md：第三阶段完整教程。
