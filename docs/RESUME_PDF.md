# 阶段 1A：PDF 文本提取与本地预览

日期：2026-11-09。用户已确认并实现 1A，最终测试与完整性结果以本轮交付记录为准。

这一小步只回答“PDF 里能读到哪些文字、文字在哪一页哪个区域”。它不会生成 `ResumeProfile`，不会推断技能熟练度，不调用 LLM、OCR、岗位库、匹配引擎或上传 API。

## 1. 固定依赖与版本确认

本次安装并固定：**PyMuPDF==1.26.7**，要求 Python `>=3.10`；开发环境 Python 为 3.10.6。
`requirements.txt` 与 `requirements.lock.txt` 都使用精确版本；项目的 `pyproject.toml` 从 lock 文件读取安装依赖，不另建依赖入口。
选择可在当前 Python / macOS 环境直接安装且已核对 API 的固定版本，不声称它是最新版本；后续升级应单独回归。

已有项目环境中，本次已安装，无需为了运行预览反复安装。需要在另一个已准备好的环境复现时，在项目根目录执行：

```bash
.venv/bin/python -m pip install PyMuPDF==1.26.7
.venv/bin/python -B -c 'import pymupdf; print(pymupdf.version[0])'
```

预期版本输出：

```text
1.26.7
```

本机原镜像没有该包，因此本次安装仅对该命令临时指定官方索引：

```bash
PIP_INDEX_URL=https://pypi.org/simple \
  .venv/bin/python -m pip install --no-cache-dir PyMuPDF==1.26.7
```

没有更改 pip 全局配置，没有升级 pip 或其他既有包。代码导入名是 `pymupdf`，不要另装名为 `fitz` 的包。

## 2. 数据流与三个模块

```text
本地 PDF 文件
  → resume_preview：有限读取原始字节、UTC 处理时间、绝对路径
  → resume_pdf：SHA-256 标识同一份 bytes，再用 PyMuPDF 提取 text / blocks / bbox
  → resume_models：契约校验
  → 终端显示分页文字、块坐标、来源与警告
```

- `resume_models.py`：`ResumeDocument`、`ResumePage`、`ResumeBlock` 及来源信息的 Pydantic 契约；拒绝额外字段。
- `resume_pdf.py`：`extract_resume_pdf(...)`，接收 PDF bytes，不自行读取路径、不联网、不写库、不保存 PDF。
- `resume_preview.py`：`preview_resume_file(...)` 与模块命令，处理有限文件读取、当前 UTC 时间和人类可读输出；SHA-256 使用适配器对同一份 bytes 算出的结果。
- `tests/test_resume_pdf.py`：离线合成 PDF 的契约、提取和入口测试；已有测试保留原样。

为什么先分层：以后 HTTP 上传也可以把 bytes 交给同一个适配器，而不让 PDF 引擎知道 FastAPI、岗位数据库或 LLM 的存在。哈希标识的是本次真正解析的原始字节，不是清洗后的简历文本。

## 3. 运行本地预览

在项目根目录，用你自己的一个文本型 PDF 路径替换示例：

```bash
.venv/bin/python -B -m job_radar.resume_preview "/完整路径/我的简历.pdf"
```

预览只向终端输出，不写 JSON、不覆盖原 PDF、不入库。输出会包含简历原文，演示或截图前自行检查联系方式等内容。

检查重点：

1. 页数和页号是否与 PDF 阅读器一致。
2. 中文、英文、标点和数字是否正确，项目名是否缺字。
3. 每一页的经历、时间和技能是否完整；是否存在只读到页眉、未读到正文的情况。
4. 文本块 `block_id`、`bbox` 和原文是否能回查对应页面区域。
5. 警告是否需要人工处理，不因为命令成功就认定画像正确。

你也可以只调用文件层函数：

```python
from job_radar.resume_preview import preview_resume_file

preview = preview_resume_file("/完整路径/我的简历.pdf")
print(preview.source_sha256)
for page in preview.document.pages:
    print(page.page_number, page.text)
    for block in page.blocks:
        print(block.block_id, block.bbox, repr(block.text))
```

## 4. 契约与证据的含义

| 内容 | 含义 |
|---|---|
| 原始文件 SHA-256 | 指向本次解析的 PDF 字节；不是身份信息或真实性证明 |
| parser / extractor version | JobRadar 解析规则版本与实际 PyMuPDF 版本 |
| 处理时间 | 本地入口提供实际 UTC 处理时刻；直接调用 bytes 适配器时可显式传入，省略则为未知；不是简历更新时间 |
| page_number | 从 1 连续编号，空白页也保留其位置 |
| width / height / rotation | 未旋转页面尺寸与显示旋转角，以 PDF point 为坐标单位；bbox 也采用未旋转页面坐标 |
| page.text | `get_text("text", sort=True)` 返回的分页文字，仅做轻度换行规范化 |
| block.text / bbox | `get_text("blocks", sort=True)` 的文字块与坐标，用于后续引文定位 |
| block_id | 同一文件字节、解析器/引擎版本内的块标识；换版本后不保证仍对应同一区域 |
| warnings | 提取观察结果，不代表已经诊断为某类版面或确认事实 |

`page.text` 和 `blocks` 来自两个提取输出模式，可能采用不同的空间排序与换行策略。不要要求“把 block 文本直接拼起来”必须逐字等于 `page.text`；后续的引用应在指定块原文内验证，而不是跨模式猜测 offset。

`sort=True` 不保证复杂多栏的人类阅读顺序。例如左栏教育与右栏项目可能按 y 坐标交错。1A 保留坐标和块证据，不实现通用版面恢复，也不把错位文本交给模型静默修补。

## 5. 资源、错误与警告

默认输入上限为 **10 MiB（10 × 1024 × 1024 bytes）和 10 页**，属于本项目的本地保护性默认值，不是 PDF 格式上限。文件层限制读取量；适配器再次检查实际 bytes 和引擎识别的文档类型，不能只相信 `.pdf` 扩展名。

程序调用者可通过 `ResumePDFError.code` 区分问题；CLI 成功退出 `0`，文件或提取错误退出 `2`，在 stderr 显示错误码而不是正常结果或 traceback。

| 错误码 | 含义 |
|---|---|
| empty_pdf / invalid_input / invalid_limit | 空 bytes、输入类型错误或资源上限不是正整数 |
| invalid_pdf / password_required | 非有效/不可恢复的损坏 PDF，或需要密码 |
| size_limit / page_limit | 实际字节或页数超限 |
| no_pages / no_extractable_text | 无页面或全文无可提取文字 |
| hash_mismatch / invalid_timestamp | 调用者传入的哈希或时间不符合契约 |
| invalid_text_block / extract_failed | 文本块不符合契约，或引擎提取失败 |
| unsupported_extension / file_error | 预览后缀不支持，或本地文件读取失败 |

需要区分的情况包括：空文件、非 PDF/损坏 PDF、需要密码、大小或页数超限、全文无可提取文字。无文字的部分页面继续保留页号和警告；全部没有有效文字时不返回一个貌似正常的空简历。

警告的判读：

- `pdf_repaired`：引擎修复了 PDF 结构后才提取成功，内容可能不完整，需要与原文件比对；这不是已验证的修复准确性。
- `no_extractable_text`：当前页没有提取到有效文字；可能为空白页、图像页或其他情况，不等同于“确定扫描件”。
- `low_text_content`：当前规则为有效文字少于 40 个非空白字符，需要比对页面；这是可测试的提示阈值，不是准确率结论，数字水印也可能导致图像简历有少量文字。
- `possible_layout_issue`：分离文字块在同一高度范围出现时给出的保守几何提示，不表示已经识别或修复全部多栏/表格。
- `replacement_characters`：提取文本出现替换字符 `�`，需要与原 PDF 比对字体/缺字。
- `some_pages_no_extractable_text`：整份文档有其他可读页，但部分页面没有文字。
- 某页有文字，不代表整份 PDF 的图片区域都被读取；1A 不开启 OCR。

需要密码的 PDF 本步不接收密码，不自动尝试解密。字体映射有问题时，即使提取到字符也可能缺字；必须与原 PDF 比对。

## 6. 离线测试与验收

```bash
# 仅阶段 1A 新增测试
.venv/bin/python -B -m pytest -q tests/test_resume_pdf.py

# 完整回归（原有 225 项与新增测试一起跑）
.venv/bin/python -B -m pytest -q
```

本轮专项 **64 项通过**，完整回归 **289 项通过**（原 225 + 新增 64）；PyMuPDF 导入产生 5 条上游 SWIG DeprecationWarning，未屏蔽。

测试使用 PyMuPDF 在内存中生成合成 PDF，不依赖网络、真实简历、桌面证据库或 OCR 工具。合成样本覆盖中文/英文、分页、文本块/坐标、空白与图像页、加密/损坏输入、资源边界、文件哈希和预览入口；通过明细与原字节完整性见最终验收记录。

**真实简历尚未提供，因此没有宣称已完成真实 PDF 验收。** 你提供或本地预览一份脱敏文本型简历后，按第 3 节逐项比对，这是进入画像结构化前的验收环节。

### 你独立修改的一个小任务

只在测试合成样本中增加第三页，放入“项目：Python / FastAPI”与一行英文；自己增加断言：

1. 总页数变为 3，最后页号是 3。
2. 中文与英文都保留。
3. 新块 ID 不与前两页重复，且 bbox 能回查新页面区域。

再添加一张空白页，验证它没有被偷偷删掉，并出现无可提取文字的警告。不要为练习改写真实简历、岗位证据或数据库。

## 7. 1A 之后

先验收文本和证据，再确认 1B：`ResumeProfile`、规则基线、字段证据校验。LLM 结构化在 1C；扫描件 OCR、HTTP 上传、简历存储和匹配都不随本次小步提前实现。
