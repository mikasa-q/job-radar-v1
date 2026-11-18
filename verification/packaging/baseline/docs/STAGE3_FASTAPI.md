# JobRadar 第三阶段：从 SQLite 脚本到 FastAPI 查询服务

更新时间：2026-11-01。目标岗位：Python 后端实习，兼顾 AI/LLM 应用开发。目标技术：HTTP、FastAPI、Pydantic、SQLite 查询与接口测试。

## 0. 你这次得到了什么

原有 CLI、配置筛选、JSON 和 SQLite 入库方式保持。新增只读 HTTP 查询：

| 请求 | 用途 | 成功返回 |
| --- | --- | --- |
| `GET /health` | 查看服务进程是否响应 | `{"status":"ok"}` |
| `GET /jobs` | 筛选和分页 | `total / limit / offset / items` |
| `GET /jobs/{job_id}` | 查看一条岗位 | 岗位对象 |
| `GET /jobs/stats` | 按城市、来源统计 | 总数、分组计数 |
| `GET /docs` | 交互式接口说明 | Swagger UI 页面 |
| `GET /openapi.json` | 机器可读的接口契约 | OpenAPI JSON |

这次不扩大 BOSS 采集范围。接口查询已经入库的记录，绝不在 GET 请求里爬网页；`description` 仍是列表摘要，空薪资仍为空。

## 1. 岗位调研：为什么现在学接口，而不是继续堆采集功能

以下网页在 2026-11-01 核对。它们是具体样本，不代表全市场统计；页面可访问也不等于岗位仍开放。面经是作者自述，题目真实性与发生过程未经企业确认。参考答案由本教程编写，不是面经原作者或企业标准答案。

| 类型与来源 | 可核对事实与时效 | 对本项目的启发 |
| --- | --- | --- |
| [NomadNest：Python 后端开发实习生](https://nomadnest.co.th/zh/jobs/python-backend-intern/) | 公司页面未注明发布时间；泰国清迈岗位。列出 Python API、Django/Flask/FastAPI、数据库、Git、测试、文档。当前招聘有效性需再向企业核实。 | 学会 HTTP、API、SQL、测试，比只会调用框架更重要。 |
| [上海 TOC 产品 Python 后端实习招聘帖](https://v2ex.com/t/1170458) | 2025-11-04 的历史招聘帖，不作为当前在招承诺。明确提 FastAPI、MySQL、Redis、部署与可观测性，以及能审阅 AI 生成代码。 | API + 数据库是底座；Redis、监控以后按需求添加。 |
| [Index AI：AI Native 全栈实习](https://www.shixiseng.com/intern/inn_8kdil4isjnqk) | 页面标注 2026-10-31 刷新，同时明确“当前职位已下线”，截止日期 2026-08-09；只用于分析技能要求。列出 Python/FastAPI/REST API/PostgreSQL 和完整项目交付。 | 重视能运行、能解释、能验证的接口，不追求名词数量。 |
| [佰聆数据 AI 应用实习一面自述](https://www.nowcoder.com/discuss/906897383676538880) | 正文写面试 7.14，年份在抽取正文未明确；提到 JSON 校验包、FastAPI 底层服务器组成等问题。 | 需要理解 Pydantic、Uvicorn、ASGI，而非只记 `@app.get`。 |
| [华为后端面经汇总自述](https://www.nowcoder.com/discuss/927377559333933056) | 正文将一组问题标为 2026-08-21；涉及部署、技术栈、MySQL/MongoDB、Django/Flask/FastAPI 区别。属于社区汇总，不视为企业题库。 | 要解释技术取舍和项目链路；本阶段先练习有证据的项目问答。 |

**能从这些样本得到的趋势**：Python API 与关系数据库、AI 集成、测试交付相互结合。它们不证明“FastAPI 市占第一”，也不意味着每个实习都要求全部技术。我们采用类型声明、Pydantic v2、自动 OpenAPI、参数化 SQL、分页与可重复测试；不在这个阶段直接加入分布式或复杂 ORM。

## 2. 从零开始运行

### 第一步：进入项目并安装依赖

```bash
cd /Users/thc/Documents/Codex/2026-10-21/build-ios-apps-plugin-build-ios-3/outputs/job-radar
.venv/bin/python -m pip install -r requirements.txt
```

本机依赖已安装。`requirements.lock.txt` 记录本次实际测试版本；在兼容的 Python/平台环境里可用 `pip install -r requirements.lock.txt` 复现。

### 第二步：确认数据库已有数据

```bash
.venv/bin/python - <<'PY'
import sqlite3
from contextlib import closing

with closing(sqlite3.connect("file:data/jobs.db?mode=ro", uri=True)) as db:
    print(db.execute("SELECT source, COUNT(*) FROM jobs GROUP BY source").fetchall())
PY
```

这次核对为 BOSS 81 条、Python.org 21 条，共 102 条。它们来自旧快照/之前采集，不是今天新发布的 102 条岗位。

若在新副本中没有数据库，先沿用第二阶段 CLI 入库，**不加筛选条件**，避免只存入筛后子集：

```bash
PYTHONPATH=src .venv/bin/python -m job_radar.cli sample_data/boss-home.html --site boss --database data/jobs.db --output data/boss-import.jobs.json
PYTHONPATH=src .venv/bin/python -m job_radar.cli sample_data/python-org.html --site python-org --database data/jobs.db --output data/python-import.jobs.json
```

### 第三步：启动服务

```bash
.venv/bin/python -m uvicorn job_radar.api:app --app-dir src --host 127.0.0.1 --port 8000 --reload
```

这条命令保持运行，终端不立即回到提示符是正常的。`Ctrl+C` 停止。

含义：Uvicorn 是负责监听 HTTP 的服务器；`job_radar.api` 是模块；冒号后的 `app` 是应用对象；`--app-dir src` 指定模块根目录；`--reload` 是开发时自动重载。

默认数据库通过项目文件位置确定，而不是靠当前终端目录猜测。更换数据库可在启动前指定环境变量 `JOBRADAR_DB`；不在每个 HTTP 请求里传文件路径。

### 第四步：在浏览器操作

打开 [交互文档](http://127.0.0.1:8000/docs)：

1. 展开 `GET /jobs`。
2. 点击 **Try it out**。
3. `city` 填 `深圳`，其余可先留空。
4. `limit` 填 `20`，`offset` 填 `0`。
5. 点击 **Execute**，看 `Response code` 与 `Response body`。

这份数据中深圳共 2 条；它们不是 Python 开发岗。城市填 `shenzhen` 仍不会翻译成“深圳”。

再试 `keyword=Python`，不填城市，可看到当前 14 条匹配。它只匹配标题与摘要；不会自动判定求职适合度。

### 第五步：用第二个终端或 Python 请求

```bash
curl --get 'http://127.0.0.1:8000/jobs' \
  --data-urlencode 'city=深圳' \
  --data-urlencode 'limit=10'
```

Python 版与已学的 requests 相连：

```python
import requests

response = requests.get(
    "http://127.0.0.1:8000/jobs",
    params={"keyword": "Python", "limit": 5, "offset": 0},
    timeout=10,
)
response.raise_for_status()
page = response.json()
print("符合条件总数：", page["total"])
for job in page["items"]:
    print(job["id"], job["title"], job["city"])
```

`response.text` 是 JSON 文本字符串；`response.json()` 是解析后的 Python 字典。`page["items"]` 才是岗位列表。此接口不是以前直接写到文件里的裸列表。

## 3. 技术基础：先理解这一条请求

```text
浏览器 / requests
    → Uvicorn 接收 HTTP
    → FastAPI 匹配 GET /jobs
    → 校验 city、limit、offset 等参数
    → api.py 调用 storage.py
    → SQLite 用 SQL 执行筛选、计数和分页
    → schemas.py 校验返回结构
    → JSON 响应回客户端
```

### 3.1 HTTP 与路由

`GET` 表示读取资源；本阶段不提供修改数据库的 POST/PUT/DELETE。`/jobs` 是路径，`?city=深圳&limit=10` 是查询参数。

核心装饰器与函数形态（实际代码还包含其余参数）：

```python
@app.get("/jobs", response_model=JobsPage)
def read_jobs(
    database: Database,
    city: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return query_jobs(database, city=city, limit=limit, offset=offset)
```

`@app.get` 将路径与 Python 函数注册到应用，不是立即执行函数。请求到达时框架才提取参数并调用它。

### 3.2 参数和状态码

| 请求/条件 | 响应 |
| --- | --- |
| 正常查询，有或没有匹配记录 | `200` |
| 列表无匹配 | `200`、`total=0`、`items=[]` |
| 详情 ID 存在 | `200` 岗位对象 |
| 正整数 ID 不存在 | `404` |
| `limit=0`、`limit=abc`、`offset=-1`、`job_id=0` | `422` 参数校验失败 |
| 数据库缺失、损坏或缺 jobs 表 | `503`，提示先入库或检查数据库 |

`/health` 只证明进程在响应（liveness），不证明数据库就绪。它可以 200，而 `/jobs` 因数据库缺失返回 503。这是两个不同的状态。

### 3.3 Pydantic 与响应模型

`schemas.py` 用 `BaseModel` 定义 API 契约。`JobsPage` 包含总数、分页参数和岗位数组；`JobResponse` 对外暴露 `skills: list[str]`，不泄露数据库内部 `skills_json`。

Pydantic 对输入参数/输出数据做类型校验和序列化；它不代替 SQL，也不自动判断职位描述是否真实。输出校验失败通常说明服务端程序或数据出错，不是应该用 422 责怪客户端的情况。

### 3.4 查询条件与分页

HTTP 本阶段参数都为单值，多个字段用 AND：

| 参数 | 当前规则 |
| --- | --- |
| `city` | 地点字符串包含；不翻译拼音 |
| `keyword` | 标题或摘要包含 |
| `skill` | skills 列表中的完整技能名相等，例如 Python；`Py` 不匹配 Python |
| `source` | 来源相等，例如 `boss` 或 `python.org` |
| `limit` | 每页 1–100 条，默认 20 |
| `offset` | 跳过条数，默认 0 |

英文技术词按 SQLite `lower()` 忽略 ASCII 大小写；这不是完整 Unicode casefold 或语义搜索。`%`、`_` 在新 HTTP 查询里当普通字符，不当 LIKE 通配符。

这与旧 CLI `--skill` 的子串匹配不同，HTTP 明确采用技能完整匹配；配置文件和旧 CLI 继续按原规则工作。API 当前也不读取筛选配置文件，不要把配置 JSON 直接当作 URL。

```json
{
  "total": 14,
  "limit": 5,
  "offset": 0,
  "items": []
}
```

上面仅展示结构，真实首屏 `items` 将包含至多 5 条，不是固定为空。`total` 是筛选后、分页前的总数；`len(items)` 才是本页条数。

## 4. 项目构成与工程进阶

| 文件（项目根目录下） | 职责 |
| --- | --- |
| `src/job_radar/api.py` | 应用工厂、路由、参数范围、依赖、HTTP 异常映射 |
| `src/job_radar/schemas.py` | 定义对外响应的 Pydantic 模型 |
| `src/job_radar/storage.py` | 保留入库代码，新增参数化查询、详情、统计、只读连接 |
| `tests/test_api.py` | 测 HTTP 状态、参数、结构、分页、隔离和并发读取 |
| `tests/test_storage_api.py` | 测连接关闭、事务回滚和分页边界 |
| `requirements.txt` / `requirements.lock.txt` | 依赖范围 / 本次测试精确版本 |

### 4.1 为什么先用普通 def

sqlite3 是同步库。普通 `def` 路由由 FastAPI 在外部线程池运行，适合当前同步 I/O。直接把路由改成 `async def` 再在里面执行 sqlite3 或 requests，不会让这些操作变异步，反而可能阻塞事件循环。

FastAPI 是框架；Starlette 提供 ASGI/Web 组件；Pydantic 管校验；Uvicorn 是 ASGI 服务器。ASGI 是服务器与 Python 应用之间的调用协议，不是数据库。

注意：线程池处理的是 FastAPI 调用的同步路由/依赖；任意普通辅助函数不会凭空被自动移到别的线程。

### 4.2 为什么每次查询各自打开和关闭连接

全局 SQLite 连接跨线程共享容易触发线程约束或并发状态问题。本版在同步查询函数内部打开连接、执行、关闭，连接不跨请求共享。

`with sqlite3.connect(...)` 主要管理提交/回滚，**不保证退出时关闭连接**。本次为原写入函数补上 `contextlib.closing`，让连接资源明确回收。

HTTP 查询用 `mode=ro`：地址写错不会偷偷创建一个空数据库；GET 不做建表、不写岗位。一个只读事务内取 `total` 和 `items`，避免同一响应的计数和列表来自不同读快照。

### 4.3 参数化 SQL：代码和值分离

```python
clauses.append("instr(lower(city), lower(?)) > 0")
params.append(city)

connection.execute(
    "SELECT * FROM jobs" + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
    [*params, limit, offset],
)
```

用户输入作为参数绑定，绝不拼进 SQL 语法。动态拼接的 `where` 只来自程序内写死的条件片段。输入 `' OR 1=1 --` 会被当成普通搜索词，而不是绕过筛选。

### 4.4 为什么不直接返回全部记录

分页限制了返回体和单请求工作量。当前按唯一 `id DESC` 排序；这表示 ID 顺序，不承诺是岗位发布时间、更新时间或推荐排名。

小数据用 LIMIT/OFFSET 易理解；数据很多时，大 offset 会扫描/跳过许多记录，可考虑 `WHERE id < last_id ORDER BY id DESC LIMIT ?` 的游标分页。分页也不自动保证多次翻页之间数据不变。

### 4.5 有索引就一定快吗

当前包含搜索使用 `instr(lower(...), lower(?))`，一般不会利用普通 city B-tree 索引做查找；EXISTS + JSON 技能也适合小规模。应先用 `EXPLAIN QUERY PLAN` 与实测查找瓶颈，再考虑精确匹配、规范化城市/技能表、索引、FTS5 或更适合的数据库。没有做吞吐量/延迟基准，因此不编造 QPS 提升。

### 4.6 应用工厂与依赖注入

`create_app(database_path)` 能为每个测试创建指向临时数据库的应用。`Depends(get_database_path)` 让路由拿到服务配置，而不是把路径写死在每个路由里。测试可以用 `app.dependency_overrides` 替换路径依赖，避免碰真实数据。

默认路径是项目 data/jobs.db，环境变量 `JOBRADAR_DB` 可以改变它。API 只读，入库依然单独执行 CLI，职责清楚，也避免一个查询请求被网页超时拖住。

## 5. 场景题与参考答案

下列第 1、2 题分别由上文面经的问题方向改编；第 3–8 题是围绕本项目设计的工程训练题。它们不是统计得出的“必考题”。

### 题 1：一次 FastAPI 请求经过哪些组件？

**思路**：从外到内描述职责，而不是只堆名词。

**参考答案**：Uvicorn 接收 HTTP 并按 ASGI 调用 FastAPI 应用；应用匹配路由，解析并验证参数，调用依赖和处理函数。处理函数通过 storage 查 SQLite，返回字典；Pydantic 响应模型校验、序列化数据，最终返回 HTTP JSON。FastAPI/Starlette/Pydantic/Uvicorn 不是四个数据库。

**追问**：FastAPI 为什么快？异步能重叠等待但不让 CPU 计算自动变快；端到端性能仍受 SQL、磁盘、锁、网络和序列化限制，要测量后判断。本项目未声称支持高并发生产流量。

### 题 2：为什么选 FastAPI，不选 Django 或 Flask？

**参考答案**：这个项目已有存储和 CLI，当前只需类型化查询 API、自动文档与测试。FastAPI 适配该需求；Django 更强调完整应用配套（ORM、管理后台等），Flask 提供灵活轻量核心。三者都能构建 API，选择取决于现有技术栈和团队，不应回答成“另两个过时了”。

### 题 3：数据库没岗位和数据库文件不存在，返回一样吗？

**参考答案**：不一样。合法查询无匹配是 200 空列表；指定详情 ID 不存在是 404；数据库缺失是依赖未就绪，503。不要捕获所有错误后伪装成 200 空列表，否则用户误以为条件不匹配。

**练习**：启动时把 JOBRADAR_DB 指向一个尚不存在的临时路径，请求 `/health`、`/jobs`，观察 200 与 503；确认未创建该数据库。

### 题 4：为什么不是所有路由都写 async def？

**参考答案**：异步收益依赖于可 await 的 I/O。当前 sqlite3 同步，使用同步路由在线程池执行更直观；全局共享连接和简单改函数关键字都不能解决连接生命周期问题。后续换异步驱动后，再沿调用链设计 async/await。

### 题 5：别人输入 `limit=-1` 或 SQL 片段怎么办？

**参考答案**：边界由 Query 校验，非法 limit 返回 422；数据库层也有边界检查。SQL 使用占位符绑定，输入不是语法。输出模型只暴露契约字段；错误响应不带磁盘路径和 SQL 细节。

### 题 6：分页时 total 为什么不直接取 len(items)？

**参考答案**：每页仅是切片，total 用相同 WHERE 做 COUNT，items 用 LIMIT/OFFSET。两次查询放进同一个只读事务，避免当前响应内部不一致；跨请求有新增记录时 OFFSET 页仍可能移动，规模上来再考虑游标。

### 题 7：岗位越多，城市筛选越慢，你先做什么？

**思路**：测请求耗时 → 定位 SQL → EXPLAIN → 优化 → 回归验证。

**参考答案**：先确认慢在查询还是网络/序列化；现有子串查询可能全表扫描，普通索引未必有效。若业务允许精确城市字段，就规范化并用等值匹配；技能用关系表并建合适索引，全文检索按需考虑 FTS。缓存不是第一反应，更不为 102 条数据引入分布式系统。

### 题 8：你怎么证明这次改造没有把原项目搞坏？

**参考答案**：先保留阶段二快照及 SHA-256；运行原 39 项测试；新版本跑 67 项测试，其中测试临时库覆盖空结果、错误码、分页、只读、并发连接、文档；另外启动真实 Uvicorn 发 HTTP 请求。回滚在独立副本上还原阶段二、再跑 39 项并验证 CLI。工作库哈希保持不变。

## 6. 学习顺序、目标和验收

按目标过关，不按“看完多少视频”计进度；代码已经实现，你可以先运行再逐个模块学习。

| 顺序 | 学什么 | 动手练习 | 完成标准 |
| --- | --- | --- | --- |
| A | GET、路径、query、JSON、状态码 | 在 docs 查询深圳、Python、0 条结果 | 用自己的话解释请求和响应 |
| B | 装饰器、参数类型、Query、BaseModel | 分别输入 limit=5、0、abc | 能解释 200/422 差别 |
| C | 路由与存储分工、SQL 参数绑定 | 组合 city/keyword，读 query_jobs 的 WHERE | 能画出 api→storage→SQLite 链路 |
| D | 详情、统计、分页 | 取 items[0].id 查详情，offset=0/5 对比 | total 不变、本页条数受 limit 约束 |
| E | pytest、TestClient、临时数据库 | 运行 tests/test_api.py，读一个测试 | 能独立补一个组合查询测试 |
| F | 工程取舍、并发、索引 | 口述第 4、6、7 题 | 不把 async/索引/缓存当万能提速词 |

本轮终点是“能运行并解释岗位查询 API”。下一轮再选一个明确需求，比如公司筛选或收藏功能，然后决定是否需要迁移、事务、用户身份等内容。

## 7. 验证与回滚

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
./CHECK.sh baseline
./CHECK.sh modified
./CHECK.sh rollback
```

本阶段基线指上一次 SQLite 阶段（39 通过），不是早期那个含过期断言的最初版本。回滚检查仅对副本运行，工作 API 保留。手动恢复阶段二的脚本是 `verification/stage3/ROLLBACK.sh`；根目录旧 `ROLLBACK.sh` 是更早版本的恢复工具，二者不要混淆。

阶段三回滚只恢复本次代码和文档；岗位数据库和虚拟环境安装的依赖保留。回滚脚本会先检查目标文件是否仍是已记录的版本，避免覆盖此后自己新增的修改。

## 8. 可核实的技术资料

1. [FastAPI 首步与路由](https://fastapi.tiangolo.com/tutorial/first-steps/)
2. [Query 参数校验](https://fastapi.tiangolo.com/tutorial/query-params-str-validations/)
3. [响应模型](https://fastapi.tiangolo.com/tutorial/response-model/)
4. [同步与异步的官方说明](https://fastapi.tiangolo.com/async/)
5. [依赖注入](https://fastapi.tiangolo.com/tutorial/dependencies/)
6. [FastAPI 测试](https://fastapi.tiangolo.com/tutorial/testing/)
7. [Starlette TestClient](https://www.starlette.dev/testclient/)：本次安装的 Starlette 1.7 已提示使用 httpx2；旧 HTTPX 仍可用但有弃用提示，本次加入 httpx2 并消除了该提示。
8. [Python sqlite3](https://docs.python.org/3/library/sqlite3.html)：重点看参数绑定、连接上下文管理与线程约束。

阅读顺序：先 1、2、3，再 6；遇到同步/依赖疑问再看 4、5。不必今天掌握整个框架。
