# JobRadar

从公开招聘页面采集岗位，经过本地筛选后存入 SQLite，再通过只读 FastAPI 接口查询。

- **采集**：请求一页公开 HTML，或读取本地 HTML 快照，提取标题、公司、地点、链接和部分标签
- **筛选**：命令行参数或 JSON 配置文件，本地文字匹配
- **入库**：SQLite，岗位 URL 为唯一键，重复采集更新记录、不重复插入
- **查询**：FastAPI 只读接口，支持城市、关键词、技能、来源筛选与分页

## 快速开始

```bash
git clone https://github.com/mikasa-q/job-radar.git
cd job-radar
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Python 3.10 或以上。`pip install -e .` 将 `src/job_radar` 以可编辑方式安装到虚拟环境，
并直接读取 `requirements.lock.txt` 的精确依赖；后续命令无需额外设置模块搜索路径。
修改源码后无需重新安装；修改打包配置或锁文件后，重新执行安装命令。

### 1. 用离线样本跑通全链路

```bash
# 采集样本并写入数据库（不联网）
.venv/bin/python -m job_radar.cli \
  sample_data/boss-home.html \
  --site boss \
  --database data/jobs.db \
  --output data/boss-all.jobs.json
```

### 2. 启动 API

```bash
.venv/bin/python -m uvicorn job_radar.api:app --host 127.0.0.1 --port 8000
```

打开 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)，在 `GET /jobs` 点击 **Try it out**，
填写 `city=深圳` 后点击 **Execute**。`Ctrl+C` 停止服务器。

`data/`（生成的 JSON、HTML 快照和 SQLite 数据库）是本地运行产物，不进仓库；
缺库时接口返回 503，不会自动创建空库，可用 `JOBRADAR_DB` 环境变量更换数据库文件。

## 数据来源与说明

| 来源 | 说明 |
| --- | --- |
| BOSS 直聘公开首页 | 解析首页岗位卡片；实时请求遇到验证页时程序以状态码 2 结束，保留原有数据 |
| Python.org 招聘页 | 全球 Python 相关岗位，不限国内、不限实习 |
| 本地 HTML 快照 | `sample_data/` 下的示例与精简快照，用于离线复现和练习 |

注意：列表页只提供标题、公司、地点、链接和部分标签，`description` 是列表摘要，
不是完整 JD；`skills` 只从标题和列表摘要匹配，不补造技能；页面没有薪资时
`salary` 为空字符串。

## 接口

| 方法与路径 | 说明 |
| --- | --- |
| `GET /health` | 仅检查进程响应 |
| `GET /jobs` | 岗位列表，支持筛选与分页 |
| `GET /jobs/stats` | 统计信息 |
| `GET /jobs/{job_id}` | 单条岗位详情 |

`GET /jobs` 参数：`city`、`keyword`、`skill`（完整技能名）、`source`、
`limit=1..100`、`offset>=0`。返回 `total/limit/offset/items`，不是裸列表。
API 查询不触发采集，也不读取 CLI 配置文件。

## CLI 用法

```bash
# 实时采集 Python.org 岗位并入库
.venv/bin/python -m job_radar.cli \
  https://www.python.org/jobs/ \
  --site python-org \
  --database data/jobs.db \
  --output data/python-org.jobs.json \
  --save-html data/python-org.latest.html

# 查询库中地点含深圳的岗位
.venv/bin/python -m job_radar.cli \
  --database data/jobs.db \
  --query-city 深圳 \
  --output data/query-shenzhen.jobs.json
```

常用参数：

- `--site auto|demo|boss|python-org`：选择适配器，默认自动识别
- `--timeout 15`：单次请求连接/读取超时，不是整项任务的总时限
- `--save-html 文件路径`：保存实际响应，方便排错和离线复现
- `--base-url 网页地址`：读取本地快照时，指定相对链接的基础地址
- `--keyword Python`：对已获取的标题和摘要做本地筛选，不等于站内搜索
- `--title / --city / --company / --skill`：标题、地点、公司、已提取技能包含指定文字；可重复，重复条件是 OR
- `--exclude-keyword Senior`：标题或摘要不包含指定文字；可重复
- `--remote-only`：只保留地点文字中包含 Remote 的岗位
- `--limit 10`：限制查询返回条数

不同选项之间是 AND，同一个选项重复多次是 OR。例如
`--title Python --city Remote --skill FastAPI` 表示标题含 Python，
并且地点含 Remote，并且技能有 FastAPI。

### 用配置文件保存筛选条件

`config/shenzhen.json`：

```json
{
  "title": ["Python"],
  "city": ["深圳"],
  "exclude_keyword": ["Senior"],
  "remote_only": false
}
```

```bash
.venv/bin/python -m job_radar.cli \
  sample_data/boss-home.html \
  --site boss \
  --config config/shenzhen.json \
  --output data/boss-shenzhen-config.jobs.json
```

配置文件只保存"怎么筛"，不负责从全站搜索；输入数据仍由采集来源决定。
命令行同名参数优先于配置文件。

## 项目结构

```text
pyproject.toml 项目元数据、src 包发现与锁文件依赖
src/job_radar/
  cli.py       接收命令参数；成功后写 JSON；失败时报告原因
  api.py       FastAPI 路由、参数校验和 HTTP 异常映射
  schemas.py   Pydantic 响应模型
  crawler.py   用 requests 获取一页 HTML，或读取本地 HTML
  config.py    读取并校验保存的 JSON 筛选配置
  storage.py   SQLite 入库；只读筛选、分页、详情与统计
  parser.py    旧示例解析器、技能提取、去重；选择页面适配器
  sources.py   按 Boss / Python.org 的真实 HTML 结构提取字段
sample_data/   原示例，以及经过精简的真实列表卡片快照
config/        筛选配置示例
docs/          第三阶段教程
tests/         67 项离线测试，不会请求网站
verification/  基线、补丁与验证记录
CHECK.sh       基线 / 修改版 / 副本回滚检查
NOTES.md       当前项目状态与下一步
```

## 测试与回滚

```bash
.venv/bin/python -m pytest -q
./CHECK.sh baseline
./CHECK.sh modified
./CHECK.sh rollback
```

先完成快速开始中的可编辑安装。`pytest.ini` 仍是唯一的 pytest 配置入口，
将默认测试目录固定为 tests，避免把归档基线重复收集。
`./CHECK.sh rollback` 在独立副本验证阶段二恢复，工作 API 和数据库保持原状。

本次打包更新的验证与专用回滚见 `verification/packaging/VERIFICATION.txt` 和
`verification/packaging/ROLLBACK.sh`；回滚后需按旧方式指定模块搜索路径。

## 已知限制

- 一次只请求输入的一页，不做分页、详情页追踪或循环重试
- BOSS 适配器只支持已观察到的首页卡片结构；搜索页原始响应只有 JavaScript 外壳时，
  程序会明确报错，不会自动执行浏览器脚本
- 异步和定时采集尚未实现
