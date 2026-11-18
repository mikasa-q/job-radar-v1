# JobRadar：采集、SQLite 与 FastAPI 岗位查询

项目支持公开页面采集、JSON 配置筛选、SQLite 入库和只读 FastAPI 查询。

## 第三阶段：先启动 HTTP 服务

当前数据库有 102 条历史记录（BOSS 81 + Python.org 21）。本阶段保留现有数据库，不重新采集网站。

```bash
cd /Users/thc/Documents/Codex/2026-10-21/build-ios-apps-plugin-build-ios-3/outputs/job-radar
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn job_radar.api:app --app-dir src --host 127.0.0.1 --port 8000 --reload
```

打开 [交互文档](http://127.0.0.1:8000/docs)，在 `GET /jobs` 点击 **Try it out**，填写 `city=深圳` 后点击 **Execute**。`Ctrl+C` 停止服务器。

接口：`GET /health`、`GET /jobs`、`GET /jobs/stats`、`GET /jobs/{job_id}`。列表支持 `city`、`keyword`、`skill`（完整技能名）、`source`、`limit=1..100`、`offset>=0`。返回 `total/limit/offset/items`，并非裸列表。API 查询不触发采集，不读取 CLI 配置文件；技能匹配规则与旧 CLI 的子串匹配不同。

默认数据库由项目绝对位置推导，环境变量 `JOBRADAR_DB` 可更换文件。缺库返回 503，不自动创建空库；`/health` 仅检查进程响应。

完整教学、岗位 JD/面经调研、原理、练习与参考资料见 `docs/STAGE3_FASTAPI.md`。本次依赖的精确版本记录在 `requirements.lock.txt`。

## 已实测的结果（2026-10-26）

| 来源 | 实测结果 | 数据含义 |
| --- | --- | --- |
| BOSS 直聘公开首页 | 本次最初取得的公开 HTML 中解析出 81 条岗位，已保存快照 | 首页推荐，包含各类职位；不是 Python 搜索结果，也不保证是实习 |
| BOSS 后续实时请求 | 返回验证页，程序以状态码 2 结束，保留原有岗位 JSON | 没有把验证页误当成“成功采集 0 条” |
| Python.org 招聘页 | 新版 CLI 实时联网采集 21 条岗位 | 全球 Python 相关岗位，不限国内、不限实习 |
| 本地练习 HTML | 保持原有 3 条解析、按 URL 去重后 2 条的行为 | 合成示例，不是真实岗位 |

列表页只提供标题、公司、地点、链接和部分标签；`description_kind=listing_summary` 明确标记 `description` 是列表摘要，不是完整 JD。页面没有薪资时，`salary` 保留为空字符串。`skills` 只从标题和列表摘要匹配，不补造技能。

## 先运行哪一个？

新版本目录：`/Users/thc/Documents/Codex/2026-10-21/build-ios-apps-plugin-build-ios-3/outputs/job-radar`

已配置好项目虚拟环境。打开终端，先运行真实联网来源：

```bash
cd /Users/thc/Documents/Codex/2026-10-21/build-ios-apps-plugin-build-ios-3/outputs/job-radar
PYTHONPATH=src .venv/bin/python -m job_radar.cli https://www.python.org/jobs/ --site python-org --output data/python-org.jobs.json --save-html data/python-org.latest.html
```

本次字面输出：

```text
采集 21 条岗位，已保存到 data/python-org.jobs.json
```

以后网站内容会变化，岗位数量也可能变化。

复现本次 BOSS 公开首页采集结果（读取真实网页快照，不再请求网站）：

```bash
PYTHONPATH=src .venv/bin/python -m job_radar.cli sample_data/boss-home.html --site boss --output data/boss-home.jobs.json
```

本次字面输出：

```text
采集 81 条岗位，已保存到 data/boss-home.jobs.json
```

请求当前 BOSS 首页：

```bash
PYTHONPATH=src .venv/bin/python -m job_radar.cli https://www.zhipin.com/ --site boss --output data/boss-live.jobs.json
```

本次后续实时请求的实际结果，退出状态为 2：

```text
采集失败：Boss 返回验证页面；本次停止，原 JSON 保持不变。
```

当前 Boss 适配器只支持本次观察到的首页 `.sub-li` 卡片。搜索页原始响应只有 JavaScript 外壳时，程序会明确报错；它不会自动执行浏览器脚本。程序一次只请求输入的一页，不做分页、详情页追踪或循环重试。

## 新电脑安装

Python 3.10 或以上：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## 文件各自负责什么？

```text
src/job_radar/
  cli.py       接收命令参数；成功后写 JSON；失败时报告原因
  api.py       FastAPI 路由、参数校验和 HTTP 异常映射
  schemas.py   Pydantic 响应模型
  crawler.py   用 requests 获取一页 HTML，或读取本地 HTML
  config.py    读取并校验保存的 JSON 筛选配置
  storage.py   SQLite 入库；只读筛选、分页、详情与统计
  parser.py    旧示例解析器、技能提取、去重；选择页面适配器
  sources.py   新增：按 Boss / Python.org 的真实 HTML 结构提取字段
sample_data/   原示例，以及经过精简的真实列表卡片快照
data/          已生成的 JSON、HTML 快照和 SQLite 数据库
tests/         67 项离线测试，不会请求网站
docs/          第三阶段教程
NOTES.md       当前项目状态与下一步
verification/  原始文件、原始哈希、补丁和验证记录
CHECK.sh       本阶段基线 / 修改版 / 副本回滚检查
ROLLBACK.sh    早期版本恢复工具（非阶段三回滚）
```

采集并保存到 SQLite 的流程是：

```text
命令 → requests / 本地读取 → 选择网站适配器 → 岗位字典列表
     → 技能提取 → URL 去重 → 可选筛选 → JSON + SQLite
```

可选参数：

- `--site auto|demo|boss|python-org`：选择适配器，默认自动识别。
- `--timeout 15`：请求连接/读取超时参数，不是整项任务的总时限。
- `--save-html 文件路径`：保存实际响应，方便排错和离线复现。
- `--base-url 网页地址`：读取本地快照时，指定相对链接的基础地址。
- `--config 配置文件`：从 JSON 文件读取筛选条件；同名命令行参数会覆盖配置文件。
- `--keyword Python`：对已经获取的标题和摘要做本地筛选，不等于站内搜索。
- `--title Python`：标题包含 Python；可重复，重复条件是 OR。
- `--city Remote`：地点包含 Remote；可重复，重复条件是 OR。
- `--company Evaboot`：公司包含文字。
- `--skill Python`：岗位已经提取出的技能列表包含 Python。
- `--exclude-keyword Senior`：标题或摘要不包含 Senior；可重复。
- `--remote-only`：只保留地点文字中包含 Remote 的岗位。
- `--database 文件`：把本次采集结果写入 SQLite；相同 URL 会更新而不会重复插入。
- `--query-city 深圳`：查询 SQLite 中地点包含“深圳”的岗位；查询模式不需要 source。
- `--query-keyword Python`：查询 SQLite 中标题或摘要包含 Python 的岗位。
- `--limit 10`：限制 SQLite 查询返回条数。

自定义筛选的组合规则：不同选项之间是 AND，同一个选项重复多次是 OR。例如 `--title Python --city Remote --skill FastAPI` 表示标题含 Python，并且地点含 Remote，并且技能有 FastAPI。

## 保存筛选条件

筛选条件可以写进 JSON 文件，例如 `config/shenzhen.json`：

```json
{
  "title": ["Python"],
  "city": ["深圳"],
  "exclude_keyword": ["Senior"],
  "remote_only": false
}
```

使用配置文件时，命令不再重复写筛选条件：

```bash
PYTHONPATH=src .venv/bin/python -m job_radar.cli \
  sample_data/boss-home.html \
  --site boss \
  --config config/shenzhen.json \
  --output data/boss-shenzhen-config.jobs.json
```

配置文件只保存“怎么筛”，不负责从 BOSS 全站搜索；输入数据仍然由 `source` 决定。命令行同名参数优先于配置文件，例如 `--config config/shenzhen.json --city 北京` 会临时改为北京。

## 第二阶段：SQLite

先把一次采集结果写入数据库：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m job_radar.cli \
  sample_data/boss-home.html \
  --site boss \
  --database data/jobs.db \
  --output data/boss-all.jobs.json
```

查询数据库中的深圳岗位：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m job_radar.cli \
  --database data/jobs.db \
  --query-city 深圳 \
  --output data/query-shenzhen.jobs.json
```

查询标题或摘要中含 Python 的前 10 条：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m job_radar.cli \
  --database data/jobs.db \
  --query-keyword Python \
  --limit 10 \
  --output data/query-python.jobs.json
```

`data/jobs.db` 是一个 SQLite 文件，不需要启动数据库服务。岗位 URL 是唯一键：重复采集同一个岗位时更新记录，避免重复数据。当前查询支持城市、关键词和数量限制；更复杂的技能查询留到后续阶段。

## 测试与回滚

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
./CHECK.sh baseline
./CHECK.sh modified
./CHECK.sh rollback
```

本阶段基线为 SQLite 阶段的 39 项通过，当前 67 项通过。`pytest.ini` 将默认测试目录固定为 tests，避免把归档基线重复收集。

`./CHECK.sh rollback` 在独立副本验证阶段二恢复；工作 API 和数据库保持原状。手动恢复阶段二代码时使用：

```bash
./verification/stage3/ROLLBACK.sh
```

此脚本保留数据库与虚拟环境，只撤销本次已记录的代码/文档变更。根目录旧 `ROLLBACK.sh` 是更早版本的恢复工具。新验证记录在 `verification/stage3/VERIFICATION.txt`。

## 自定义筛选练习

下面命令都读取已经保存的 Python.org HTML 快照，因此可以重复练习，不会覆盖原始岗位 JSON：

```bash
# 标题含 Python，且地点含 Remote
PYTHONPATH=src .venv/bin/python -m job_radar.cli data/python-org.latest.html --site python-org --title Python --remote-only --output data/python-remote.jobs.json

# 标题含 Python，地点含 Remote，并且已提取技能包含 Python
PYTHONPATH=src .venv/bin/python -m job_radar.cli data/python-org.latest.html --site python-org --title Python --city Remote --skill Python --output data/python-remote-skilled.jobs.json

# 标题含 Python，但排除 Senior
PYTHONPATH=src .venv/bin/python -m job_radar.cli data/python-org.latest.html --title Python --exclude-keyword Senior --output data/python-junior-or-mid.jobs.json

# 城市在 London 或 Remote，两个 --city 是 OR
PYTHONPATH=src .venv/bin/python -m job_radar.cli data/python-org.latest.html --site python-org --city London --city Remote --output data/london-or-remote.jobs.json
```

先看命令输出的数量，再打开对应 JSON 检查 `title`、`company`、`city`、`skills`。

筛选是本地文字匹配：不会替你阅读完整 JD，也不是网站搜索；列表摘要中没有的条件无法被筛出来。当前已接 SQLite 和 FastAPI；异步和定时采集留待后续明确需求。
