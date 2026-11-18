# Job Importer：HTML / TXT 纯解析与本地预览

状态：2026-11-09，三步实现与真实入库完成；只读核验独立库 15 条 full_jd、15 条 provenance、全部 unconfirmed。第 8 节单文件，第 9 节批量流程，第 10 节当前入库核验。

## 1. 本步怎么理解

文件职责：
- `src/job_radar/import_models.py`：Pydantic ImportedJob，定义一条导入岗位应该包含什么。
- `src/job_radar/importer.py`：只接收 HTML 字符串，解析已经观察到的 Boss 详情布局。
- `tests/test_importer.py`：正常样本、污染边界、缺字段、歧义与契约测试。
- `tests/fixtures/boss-detail.html`：从真实样本保留主 JD 和必要 DOM 的精简快照；加入公司介绍、推荐岗位和 logo 链接等干扰项。

数据流：
```text
HTML 字符串
 → BeautifulSoup 在内存中建树
 → 唯一标题 + 唯一主正文
 → canonical / 显式 source_url 核对
 → 可选字段与警告
 → ImportedJob
```

纯函数不读取文件，不访问网络，不获取当前时间，不写数据库。调用者负责读取文件；文件来源哈希、预览时间和入口由第二步的 import_preview.py 实现，见第 8 节。

为什么用 Pydantic 而不是普通 dict：必需字段、字段类型与未知字段能在边界处校验。它不会替我们判断 JD 是否真实，也不会理解“多种语言掌握一种”的语义。

为什么用 BeautifulSoup 而不是正则/LLM：当前 HTML 已包含正文；DOM 能限定正文区域，结果可定位、可重复，不需要模型猜测。模板改变时应新增样本测试再扩适配，不静默扩大到全页面抓文字。

## 2. 契约里的关键字段

| 字段 | 含义 |
|---|---|
| source | 来源标识，当前 HTML 解析器固定输出 boss |
| title / url / description | 必须有内容；url 必须是 HTTP(S) |
| description_kind | 固定 full_jd，表示来自详情正文；不保证岗位实时有效 |
| company / city / salary / education | 来源原文，缺失留空，并由解析器附 warning |
| experience | 工作经验原文 |
| attendance | 实习出勤和时长原文；当前按“天/周”或月份文字识别 |
| source_tags | 页面提供的标签，不等于岗位必需项 |
| skills | 现有词表从标题和 JD 识别的技能提及，不等于必需/熟练 |
| posted_at | 当前未可靠提取，留空 |
| warnings | missing_company 等稳定警告码 |

Pydantic 拒绝额外字段，避免把 required_skills 一类尚未实现语义的字段偷偷塞入契约。列表使用 default_factory，每条岗位拥有独立列表。

正文只清理每行首尾和空行，保留内部文字、换行与原 Unicode 字形，方便回读证据。HTML 标题、公司等单行字段压缩多余空白。

当前不会拆解“Java、C++、TypeScript、JavaScript 或 Python”为必需技能列表；这一步只保留原句。

## 3. 顺着关键函数读一遍

1. `_single_node()` 检查主标题、主正文恰好一个，避免静默取错岗位。
2. `_resolve_url()` 从 canonical 读取身份，缺少时允许调用者提供 source_url；二者冲突时失败。
3. `_boss_url()` 限定 zhipin.com / www.zhipin.com 与详情路径；统一为 https://www.zhipin.com，移除 query 和 fragment；拒绝账户信息与显式端口。
4. `_optional_text()` 收集非空内容并去重。公司 logo 链接的空文字被忽略；多个不同名称则视为冲突。
5. `parse_boss_detail_html()` 限定主区域，保留 JD，分别收集标签和技能，然后构造 ImportedJob。

JD 选择器：
```css
.job-detail > .job-detail-section > .job-sec-text
```

为什么不是全局 .job-sec-text：真实页面还用同一个 class 存公司介绍。为什么不取整个 section：它还含技术标签、操作区和招聘者信息。

## 4. 手动运行与验收

在项目根目录使用现有虚拟环境：

```bash
env -u PYTHONPATH PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_importer.py
```

真实文件只由调用者读取，解析器本身不读：

```python
from pathlib import Path
from job_radar.importer import parse_boss_detail_html

html = Path("~/Desktop/boss-evidence/boss-ones-ai-agent.html").expanduser().read_text(encoding="utf-8")
job = parse_boss_detail_html(html)
print(job.model_dump(exclude={"description"}))
print(job.description)
```

本样本预期：AI Agent 开发实习生 / ONES / 深圳 / 180-200元/天 / 本科；attendance 为 4天/周 4个月，experience 为空；主 JD 为 790 字符、20 行。仅此样本已做真实验收，其他布局尚需新增样本验证。

针对性测试覆盖：正文排除公司介绍/推荐/招聘者/脚本，logo 空链接，多选语言原句，标签分离，可选缺失，必需空值/缺失/歧义，URL 冲突/跟踪参数，错误页面，纯解析无 I/O，Pydantic 类型和额外字段。

## 5. TXT 导入：jobradar-txt-v1 格式（已实现）

采用“有标签的元数据头 + 明确正文起点”，不从一段任意文本推测公司、城市和标题。让用户补几行头部，比用 LLM 猜字段更容易校验和追溯。

单个 UTF-8 TXT 文件只表示一个岗位，模板：

```text
格式版本: jobradar-txt-v1
来源: boss
岗位名称: AI Agent 开发实习生
岗位链接: https://www.zhipin.com/job_detail/89cdce766fdcb2020nF82N64F1JR.html
公司: ONES
城市: 深圳
薪资: 180-200元/天
学历: 本科
经验:
实习安排: 4天/周 4个月
正文:
岗位介绍
在这里粘贴真实岗位介绍。
岗位职责
1. 在这里粘贴职责。
任职要求
1. 在这里粘贴要求。
```

精确切分规则：
1. UTF-8 读取，可接受文件起始 BOM；CRLF/CR 统一为 LF。读取与哈希属于文件层。
2. 第一行非空内容必须为格式版本，值是 jobradar-txt-v1；初版不支持省略版本。
3. 元数据头部在第一次“整行去空白后等于 正文: 或 正文：”处结束。
4. 标记前的每个非空行必须是“已知字段名 + 第一个半角或全角冒号 + 值”。只按第一个冒号切分，https:// 中的冒号仍是值的一部分。
5. 标记后的全部文本到 EOF 都是 description；不再按“公司:”“薪资:”或“岗位职责”切分。正文内再次出现 正文: 也属于正文。
6. 未知头部字段、重复字段、未知格式版本、缺正文标记、缺标题/来源/URL/正文，均明确失败；不静默忽略拼错的头部。
7. 可选字段可以省略或留空，返回相应 missing_* 警告。头部只接受单行值，不推断多行元数据。
8. 字段映射：岗位名称→title，岗位链接→url，公司→company，城市→city，薪资→salary，学历→education，经验→experience，实习安排→attendance。
9. 第一版 TXT 的来源仅接受 boss 或 manual。boss 复用 Boss URL 身份校验；manual 需要一个可追溯的无账户 HTTP(S) 原始链接。离线文件路径不作岗位 URL。新增其他来源时另加验证规则。
10. skills 从标题与正文提取；TXT 初版没有 source_tags 头部，输出空列表。元数据仅用于版本选择，不进入 ImportedJob。
11. 正文保持段落，缺少“岗位职责”等固定标题不构成错误；description_kind 仍为 full_jd，表示人工明确提供详情正文，内容是否完整需要预览核对。

只有无头部的粘贴正文时，提示补充模板字段；先不增加智能猜测或 LLM 提取。导入模块的确定性与后续理解要求的 LLM 模块分工清晰。

### TXT 测试覆盖

- URL 中冒号保留；中文/英文冒号、BOM、CRLF、空行能处理。
- 正文里有 公司:、薪资:、正文: 时全文保留。
- 拼错字段、重复字段、空标题/正文、缺标记、非法 URL、未知版本/来源均失败。
- 已知头部顺序可调整（版本行仍在第一非空行），可选字段缺失有警告。
- HTML 与 TXT 最终返回同一种 ImportedJob，才能复用预览和后续匹配流程。

## 6. 第一步历史记录与当前入库边界

第一步只实现 HTML 纯解析，第二步增加 TXT 与单文件预览，第三步已实现批量层和独立 SQLite 存储。公司介绍区 Redis/Docker 独立练习按决定跳过，本节不再安排该练习。

旧采集库及 upsert_jobs 保持原样：同 URL 仍有完整 JD 被列表摘要覆盖的风险，旧表也不保存 attendance、source_tags 和 provenance。新证据走独立数据集，不调用旧 upsert。当前真实 15 条入库结果见第 10 节。

第一步历史基线为 67 项；新增 39 项后为 106 项；第二步后为 161 项；第三步后为 225 项。早期“尚未入库”的段落仅描述当时步骤，不替代当前状态。

## 7. 下一轮协作提示

```text
继续 JobRadar V1。先读 NOTES.md 的置顶下一步。
Job Importer 三步与真实 15 条 full_jd 入库已完成；独立库和报告路径见第 10 节，不重复 apply。
先讲解 docs/RESUME_PARSING_PLAN.md 的阶段 1 方案，等我确认后才开发 1A。
保护两库、证据原件、已有解析和测试；不自动接通旧 FastAPI 到新库。
```

## 8. 第二步：TXT 解析与单文件预览

### 直接运行

在项目根目录，使用现有虚拟环境：

```bash
# 预览一个真实 HTML；只在终端输出，不写文件、不入库
.venv/bin/python -B -m job_radar.import_preview \
  ~/Desktop/boss-evidence/boss-ones-ai-agent.html

# TXT 必须先补齐第 5 节模板头部；原文备份不要直接覆盖
.venv/bin/python -B -m job_radar.import_preview /完整路径/已补头部的岗位.txt

# HTML 缺少 canonical 时，可以显式提供原岗位 URL
.venv/bin/python -B -m job_radar.import_preview /完整路径/岗位.html \
  --source-url https://www.zhipin.com/job_detail/abc123.html
```

命令一次接受一个 .html / .htm / .txt 文件，按后缀选择解析器；不是文件夹批处理。
--source-url 仅适用于 HTML，已有 canonical 与它冲突时仍然报错；TXT 链接只能在头部填写。

输出包括绝对来源文件路径、原始字节 SHA-256、输入格式、UTC 预览时间、ImportedJob 所有字段和多行 JD。未知标量字段显示“未知”，空列表显示 []，同时显示 missing_* 警告。显示“待人工核对（未入库）”不代表已经确认字段。

预览时间字段 imported_at 只表示本次预览处理的时刻，不代表网页发布时间、浏览器另存时间或正式入库时间。posted_at 仍保持空值，除非后续有可靠来源。review_status 固定 unconfirmed。

### 证据集的使用约定

本地证据集有 15 份 HTML 和 15 份同名 TXT。TXT 是 JD 原文备份，没有模板头部，直接预览应失败并提示补头部。优先核对 HTML；如需 TXT 回退，应另存一份补齐格式版本、来源、岗位名称、岗位链接及正文标记的文件，保留原始备份。

本轮仅读取单个真实 HTML/TXT 作验收，没有逐个导入或改写这些证据文件。

### Python 函数入口

```python
from job_radar.txt_importer import parse_job_txt
from job_radar.import_preview import preview_file

# 纯函数只接收字符串，返回同一种 ImportedJob。
job = parse_job_txt(
    "格式版本: jobradar-txt-v1\n"
    "来源: manual\n"
    "岗位名称: Python 实习生\n"
    "岗位链接: https://careers.example.org/jobs/42\n"
    "正文:\n开发 Python API，使用 Git。"
)

# 文件读取、哈希和当前时间由预览层负责。
preview = preview_file("~/Desktop/boss-evidence/boss-ones-ai-agent.html")
print(preview.job.title)
print(preview.provenance.source_sha256)
print(preview.review_status)  # unconfirmed
```

职责分开：
- txt_importer.py / parse_job_txt：纯解析；版本与头部严格校验；正文从第一个独占标记保留到 EOF；没有时钟或文件读取。
- import_preview.py / preview_file：读取一次原始字节，计算 SHA-256，再 UTF-8 解码；将岗位与 ImportProvenance 分开放在 ImportPreview 中。
- import_preview.py / main：终端格式化输出；文件、编码或解析错误写 stderr，退出码 2；成功退出码 0。
- importer.py 与 import_models.py 保持第一步原样；TXT 的 Boss URL 复用现有校验，manual 使用契约的 HTTP(S) URL 校验。

### 验证命令与结果

```bash
# 仅第二步新增测试
.venv/bin/python -B -m pytest -q tests/test_txt_importer.py tests/test_import_preview.py

# 完整回归
.venv/bin/python -B -m pytest -q
```

本轮：第二步新增 55 项通过；完整回归 161 项通过（原有 106 项全部保留）。
测试覆盖版本与头部严格失败、首个冒号、BOM/不同换行、正文伪元数据、URL 规则、未知字段、纯解析无 I/O、预览原字节哈希、UTC 时间、单次读取、未知值展示、错误退出码以及真实模块命令。

复核原始字节哈希可用：
```bash
shasum -a 256 ~/Desktop/boss-evidence/boss-ones-ai-agent.html
```

### 你独立验收的一个小任务

另存一份已补头部的 TXT，在正文中加入“公司: 这一行属于 JD”和第二个“正文:”。预览确认两行仍在 JD 中，没有变成元数据；再用同名重复头部字段验证命令退出 2。这能验证“头部和正文的分界”是否符合你的理解。

以上为第二步历史验收；第三步的批量导入与持久化现已完成，见第 9、10 节。

## 9. 第三步：批量预览与独立数据集（2026-11-09）

### 当前状态

实现了批量层与独立 SQLite 存储；用户已完成真实 apply，**15 条完整 JD 已入库，全部待人工核对**。当前核验详见第 10 节。
默认事实库是 `~/Desktop/jobradar-v1-data/evidence.sqlite3`，不会复用 `data/jobs.db`。
代码不改动第一/二步解析器、契约、预览入口或已有测试。

### 1. 先生成预览报告

在项目根目录执行：

```bash
.venv/bin/python -B -m job_radar.batch_import \
  --input-dir "$HOME/Desktop/boss-evidence" \
  --database "$HOME/Desktop/jobradar-v1-data/evidence.sqlite3" \
  --report "$HOME/Desktop/jobradar-v1-data/reports/evidence-dry-run.json" \
  --dry-run
```

不传路径时采用以上默认值；仍需显式指定 `--dry-run` 或 `--apply`。
报告同时生成 JSON 和同名 Markdown；Markdown 含全部字段、完整 JD、来源 SHA-256、重复、失败与回退诊断，供你逐条核对。

- 只扫描顶层 .html（大小写不敏感），不递归、不把 TXT 备份当成第二份岗位。
- HTML 成功则不解析同名 TXT；HTML 失败才尝试同名有模板头部 TXT。
- 原始 TXT 未补头部时列为 fallback_requires_header；备份不会被程序改写。
- UTF-8、文件访问及解析失败逐项列出，不中断其他文件。
- 空目录生成 0 候选报告；只有失败/冲突而无可写候选时，apply 报错且不创建数据集。
- dry-run 只读已有独立库；库不存在时不会创建它。新 JSON/Markdown 报告会写到指定报告目录。
- 不接受报告写进证据目录，也拒绝报告与数据库、原证据的路径/文件别名冲突。

### 2. 看完报告再确认写入

**以下是写入操作的用法示例；真实首批已入库，不要把旧报告再 apply。新的导入须重新 dry-run 并核对。**

```bash
.venv/bin/python -B -m job_radar.batch_import \
  --input-dir "$HOME/Desktop/boss-evidence" \
  --database "$HOME/Desktop/jobradar-v1-data/evidence.sqlite3" \
  --report "$HOME/Desktop/jobradar-v1-data/reports/evidence-dry-run.json" \
  --apply --confirm
```

apply 会重新扫描并比较源文件、完整解析事实、实现版本和目标库逻辑快照；如果与报告不一致则拒绝写入，需重新 dry-run 和确认。计划哈希不是外部签名或权限系统，而是防止“核对的报告与写入内容不同”的本地一致性检查。

目标库会拒绝旧 jobs.db、它的软/硬链接、其他 SQLite 结构和不匹配版本。拿到写锁后再次比较快照，避免扫描与入库之间其他写入悄悄改变目标。

同一报告已 apply 后，数据库发生变化，该旧报告不能直接再用；再次 dry-run 后即可幂等重导。
新记录全部为 unconfirmed，不表示系统已替你核对事实，也不表示岗位仍在线招聘。

### 去重和冲突

- 身份键：source + 规范化 URL，不按标题去重。
- 相同身份、完整 ImportedJob 事实一致：批内合并；保留不同原始字节版本的来源记录。
- 相同身份、JD 或公司/城市等任何事实不同：冲突，整组跳过，不覆盖旧值。
- 与已有库事实完全一致：existing_exact，不增加事实行；补充新的 provenance，更新 last_imported_at，保留人工状态和原事实。
- warnings 也是导入契约的一部分；同 JD 但不同元数据/缺失警告不当作 exact。
- source_file_sha256 哈希原始 HTML/TXT 字节；jd_sha256 哈希解析后正文 UTF-8；fact_sha256 哈希完整 ImportedJob 规范 JSON。
- 来源唯一性按 job_id + 文件 SHA-256 + parser_version。逐字节相同的两个文件名只保留一份相同证据版本；报告仍列出两个文件。
- 部分失败/冲突的计划仍可明确确认后只导入报告里的成功候选。终端显示 skipped_failures/skipped_conflicts；问题项目不会自动放行。

### 三张表与字段

`dataset_store.py` 独立连接，不调用旧 upsert_jobs：

1. imported_jobs：ImportedJob 全字段（含 attendance、source_tags_json、skills_json、warnings_json）、JD/事实哈希、review_status 和入库时间。
2. job_provenance：原始绝对文件路径、原始字节 SHA-256、html/txt、parser_version、实际 UTC 入库时间、run 外键。
3. import_runs：report_path、plan_sha256、输入目录、目标库、实际运行时间与本次计数。

SQLite application_id/user_version 标记版本，description_kind CHECK 只接受 full_jd，来源与 review_status 也有约束。
完整一批的 schema/事实/provenance/run 在一个事务中提交，SQL 失败全部回滚。新库首次事务失败可能留下无表的空文件；不会擅自删除目标文件（避免误删并发产生的数据），该文件应检查后另行处理。

### 退出状态

| 状态码 | 含义 |
|---|---|
| 0 | dry-run 全部干净；或 apply 无跳过问题 |
| 1 | 报告含失败/冲突；或明确确认的 apply 已导入成功子集但仍有跳过问题 |
| 2 | 参数/目录/报告/数据集/确认失效等错误，无成功批次写入 |

### 测试与独立练习

```bash
# 仅第三步新增测试
.venv/bin/python -B -m pytest -q tests/test_batch_import.py tests/test_dataset_store.py

# 完整回归
.venv/bin/python -B -m pytest -q
```

新增 64 项，连同原 161 项，完整共 225 项。
真实目录验收报告为 15 HTML、15 个拟新增、0 个重复、0 个冲突、0 个失败；请以实际 dry-run 文件为准。

独立练习：在临时目录复制一份测试 HTML，两份仅改文件名，观察报告的批内重复；再改副本公司的可见名称，确认同 URL 被列为冲突而不是静默覆盖。只对临时证据和临时数据库实验，不改真实证据原件或现有库。




## 10. 真实入库结果与阶段 0 收尾（2026-11-09）

用户已完成真实导入；本次仅只读核验，没有重复运行 apply：

| 项目 | 核验值 |
|---|---|
| 独立证据库 | /Users/thc/Desktop/jobradar-v1-data/evidence.sqlite3 |
| imported_jobs | 15 |
| description_kind | 15 条 full_jd |
| review_status | 15 条 unconfirmed |
| job_provenance | 15，全部有来源路径、SHA-256、实际入库时间 |
| import_runs | 1 次 applied；inserted_count=15、failed_count=0 |
| 缺 provenance / 外键问题 | 0 / 0 |
| 原采集库 | data/jobs.db 保持 104 条 |

实际完成时间：2026-11-08T16:42:31.216765+00:00（北京时间 2026-11-09 00:42:31）。

原 dry-run：
- /Users/thc/Desktop/jobradar-v1-data/reports/evidence-dry-run.json
- /Users/thc/Desktop/jobradar-v1-data/reports/evidence-dry-run.md
- 计划 SHA-256：d31dfe249e5a30994f1c77b167615a844f95ccaf02647fe12e9dcd7ff4c86c1a

报告是**导入前**的预览快照，所以其中“未入库”保持原样；当前状态以库内 import_runs 和本节只读核验为准。已核对 URL 集合、来源文件 SHA-256、完整 JD 标记与入库记录均对应该计划。

已入库不等于人工确认，也不证明岗位仍然有效。后续使用哪些岗位做匹配/演示，应逐条核对并保留 review_status。既有 FastAPI 查询的是旧库 jobs 表，不应只修改数据库路径就指向新 imported_jobs；新增只读适配层留后续阶段。

阶段 0 数据准备与 Importer 工程收尾完成。阶段 1 先读 docs/RESUME_PARSING_PLAN.md 并确认方案，首个实现小步仅做 PDF 文本提取、预览与测试，不同步搭 LLM/上传/匹配框架。
