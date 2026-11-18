# JobRadar 当前状态

更新时间：2026-10-28（已加入 SQLite 存储）

## 当前版本

工作目录：`/Users/thc/Documents/Codex/2026-10-21/build-ios-apps-plugin-build-ios-3/outputs/job-radar`

原始版本保留在 `/Users/thc/Documents/Codex/2026-09-27/niu/outputs/job-radar`，未覆盖。

## 学习进度

- 用户希望先理解项目结构、每个文件的职责和实际运行，不再要求逐行掌握解析器。
- 当前继续使用同步 requests。没有提前引入异步、FastAPI、数据库或 Agent。
- HTML 获取与字段解析分离；真实站点规则集中在新增的 sources.py。

## 本次已完成

- Boss 公开首页最初正常响应的 81 条岗位已解析；真实列表卡片保存在 sample_data/boss-home.html。
- Boss 后续请求进入验证页，程序明确报错，退出 2，不以空结果覆盖 JSON。
- Python.org 招聘页通过新版 CLI 实时联网采集 21 条岗位，保存 JSON 和响应 HTML。
- 说明首页推荐并非 Python 搜索结果；列表摘要并非完整 JD；字段未提供时保留为空。
- 新增 `filters.py` 和 CLI 自定义筛选：title/city/company/skill/exclude-keyword/remote-only；不同选项 AND，同一选项重复 OR。
- 新增 `config.py` 和 `--config`：筛选条件可以保存为 JSON；命令行同名参数优先。
- 新增 `storage.py` 和 `--database`：使用 SQLite 保存岗位，按 URL upsert 去重；支持 `--query-city`、`--query-keyword` 和 `--limit` 查询。
- 修正原测试中因示例数据变动导致的过期断言，新增来源适配、HTTP 异常、CLI、JSON 保留、配置和 SQLite 测试；共 39 项通过。
- 原始文件及 SHA-256 已保存，回滚在独立副本验证；交付目录保留修改版。

## 当前待办

1. 使用 README 的联网命令运行一次，打开 data/python-org.jobs.json 查看字段。
2. 增加 --keyword Python，比较筛选前后岗位数量；它是本地筛选，不是网站搜索。
3. 用 `config/shenzhen.json` 练习配置化筛选，再根据目标岗位画像复制并修改配置文件。
4. 用 `data/jobs.db` 练习采集、重复写入和查询，再进入 FastAPI。

## 结果文件

- data/boss-home.jobs.json：本次真实 Boss 首页快照的 81 条岗位。
- data/python-org.jobs.json：本次真实联网获取的 21 条岗位。
- data/jobs.db：SQLite 岗位数据库示例。
- sample_data/snapshots.json：精简快照的来源与 SHA-256。
- verification/VERIFICATION.txt：基线、修改版、回滚和真实请求证据。
