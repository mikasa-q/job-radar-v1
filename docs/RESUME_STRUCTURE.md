# 阶段 1C-1：离线结构化闭环

日期：2026-11-10。用户已确认先做 1C-1。本步由主代理顺序实现，无子代理；新增简历验证仅用合成文本、内存生成 PDF 和预设假响应。没有真实 LLM、网络传输、新 SDK、密钥读取或依赖安装。

## 1. 这一步完成了什么

1A 得到原文块，1B 定义画像及引用规则，本步把“客户端响应 → 草稿 → 本地画像 → 校验 → 对照”接通。**假客户端不提取事实，只返回人为写好的响应；本步验证程序链路，不证明模型提取质量。**

```text
ResumeDocument
  → 校验并复制文档快照
  → StructureRequest（固定提示词 + JSON文本块 + 本地schema）
  → 注入的 StructureClient.complete(request)，只尝试一次
  → ClientResponse（完成 / 未完成 / 模型未返回内容的状态）
  → 严格JSON解析 + ResumeDraft
  → 本地组装 ResumeProfile
  → 复用1B validate_profile_evidence
  → 规则技能基线差异对照，不合并
  → StructureResult(profile, comparison)
```

文件分工：

- `src/job_radar/resume_structure.py`：草稿、客户端协议、请求/响应、错误分类和完整编排。
- `src/job_radar/resume_structure_fake.py`：可记录请求、按次返回响应或抛异常的假客户端；无提取智能。
- `tests/test_resume_structure.py`：合成专项测试。
- `tests/fixtures/resume_structure_golden.json`：3组人为编写的合成文档、响应和独立预期字段；并非真实模型输出。
- `examples/resume_structure_synthetic.py`：合成两页PDF → 固定假响应 → 画像 → 篡改引用失败演示。

原1A、1B源文件、旧岗位解析器、已有测试与依赖保持不变。

## 2. 草稿与本地管理字段分开

客户端输出只允许六个顶层键，全部必需：

```text
education / skills / projects / work_experience / summary / warnings
```

- 未提取集合为 `[]`，未知摘要为 `null`，不通过省略顶层键冒充完整响应。
- 嵌套 Education / ProjectExperience / WorkExperience / SkillEvidence / TextFact / DateFact 直接复用1B；嵌套未知字段仍允许为空或省略，保持原业务契约。
- `resume_id`、`profile_version`、`source`、`builder_version`、`review_status`由程序提供，模型若额外返回则报 `schema_error`，不是静默覆盖。
- `review_status`固定由本地新建画像得到 `unconfirmed`。引文正确不自动确认。
- `builder_version=resume-structure-v1`、请求提示词版本 `resume-structure-prompt-v1`；调用方提供ID和画像版本。

`ResumeDraft.model_json_schema()`是本地校验格式。本轮没有宣称任何供应商的严格结构化输出能原样接受该schema；相关转换、SDK和模型配置留1C-2。

## 3. 客户端协议与错误边界

```python
class StructureClient(Protocol):
    def complete(self, request: StructureRequest) -> ClientResponse: ...

result = structure_resume(
    document,
    client=explicitly_injected_client,
    resume_id="synthetic-001",
    profile_version=1,
)
checked_profile = result.profile
comparison = result.comparison
```

`StructureRequest`是冻结dataclass，包含独立字符串而非可变文档引用：

- `system_prompt`：固定指令，简历内容不插入此字符串；
- `blocks_json`：仅包含 page / block_id / 原文text，不发送阅读辅助page.text、坐标、文件路径、哈希、用户ID或提取时间；
- `output_schema_json`：本地schema；
- `timeout_seconds`、`max_output_bytes`及提示词版本。

**本轮不做隐私遮蔽器。** 合成样本不含真实个人信息；未来真实接入前须另确认脱敏与引用映射，不能假定“仅发文本”等于自动去除联系方式。

`ClientResponse.status`由客户端适配层给出，不从模型生成的简历JSON里推断。completed / refused / incomplete 分别处理，即便未完成响应中有看似完整JSON也不当成功使用。

单次调用，无自动重试，无代码围栏剥除、末尾大括号截取或JSON修复；失败不回落成规则画像。拒绝重复JSON键、NaN/Infinity、非法Unicode和结构不符输出。

| 错误码 | 含义 |
|---|---|
| `invalid_document` / `no_text_blocks` | 文档不符合1A契约或没有原文块 |
| `invalid_identity` / `invalid_limit` | 本地ID、版本或资源参数不合法 |
| `input_limit` / `output_limit` | 完整输入/响应超过上限，不截断继续 |
| `client_timeout` / `client_error` | 客户端超时或其他异常 |
| `invalid_response` | 客户端返回非约定信封或信封不合法 |
| `model_refused` / `incomplete_response` | 模型未提供所需响应或输出未完成 |
| `empty_response` / `invalid_json` | 完成响应为空或JSON解析失败 |
| `schema_error` | 草稿/业务契约不通过 |
| `evidence_error` | 1B证据校验失败，保留 `evidence_code` 和字段 `path` |

例如篡改引文会得到：`code=evidence_error, evidence_code=quote_mismatch`。多个问题以首次遇到的问题为准，不承诺同时报告所有错误。

顶层异常消息只返回码；不包含原文、客户端异常中的URL、密钥或响应正文，异常链显示也抑制原始异常。本层不写日志。`warnings`仍是候选资料，不应直接当成无敏感内容的生产日志。

## 4. 有限资源，不夸大超时能力

默认：

- `max_input_bytes=128*1024`：序列化后的 UTF-8 `blocks_json` 字节数，包含JSON键和转义开销；固定提示词/schema另计。
- `max_output_bytes=128*1024`：完成响应text的UTF-8字节数；在解析前检查。
- `max_blocks=1000`。
- `timeout_seconds=30.0`：交给客户端执行的超时预算。

字节不是token，以上不是供应商上下文或费用保证。本步不做分块多次调用；超限明确失败，避免截断导致经历残缺。

**同步编排层不强制终止挂住的客户端。** 客户端协议要求实现方遵守超时并抛 `TimeoutError`；本轮用假异常验证处理路径，没有网络超时或进程隔离实测。供应商SDK超时、输出token预算和费用限制留1C-2；不为此引入线程或后台任务框架。

## 5. 规则只比较，不是第二套事实真相

`comparison`中：

- `rule_only_skills`：规则提到，模型候选没有；
- `model_only_skills`：模型候选提到，规则没有；
- 比较会覆盖候选顶层skills及项目/工作条目里的technologies；按规范名集合对照，不比较熟练度和经历归属。

差异加入 `rule_only_skill:<name>` / `model_only_skill:<name>` 警告，不取并集、不自动补漏、不因Rust不在旧词表就直接失败。每项模型技能仍须先通过1B引用与语境校验。

原1B整块否定/计划/招聘要求门禁完整保留，包括跨块风险标题。若一个块同时含正向和计划句，本步不放宽规则；模型返回该块的正向技能会导致 `evidence_error`，不静默删一项继续。

文档与逐页质量警告由本地传播，模型返回空warnings也删不掉。规则侧的 `not_extracted:*` 不误传给已结构化候选；不代表候选内容真的完整。六个键全部返回空集合/空摘要时，结果显式加 `no_extracted_facts`；这是合法的空候选，不是假称客户端失败。

## 6. 合成黄金预期的意义与局限

3组样本包含：教育/课程项目/实习/预计毕业、部分字段未知、否定与学习计划无正向技能。测试逐项核对学校、在读、课程性质、实习性质、摘要、技能和各经历数量，并校验引用与日期。

另外测试刻意返回“课程项目被写进工作经历”：引文确实存在时，1B仍可能通过。测试用独立预期的类别发现差异，**不是运行时实现了通用语义纠错器**。真实模型漏提、误归类和内容真实性仍要后续真实合成评测/人工核对。

假客户端按预设给出“正确响应”，只能证明这些响应的处理和验证符合设计；不报告LLM准确率，不把假响应当成模型提取成绩。

## 7. 运行合成演示和测试

在项目根目录运行：

```bash
.venv/bin/python -B examples/resume_structure_synthetic.py
.venv/bin/python -B -m pytest -q tests/test_resume_structure.py
.venv/bin/python -B -m pytest -q
```

演示只在内存生成两页PDF，不接收用户文件、不保存PDF、不读密钥。输出：

```text
client=fake (scripted; no model call)
education=合成大学/在读
project=课程项目
skills=Python
review_status=unconfirmed
client_calls=1
tampered_quote=evidence_error/quote_mismatch
```

本轮专项 **112项**、完整 **524项**（原412+新增112）通过；5条PyMuPDF上游SWIG弃用警告未屏蔽。完整原始输出、原件哈希和独立副本回滚见交付 `VERIFICATION.txt`。

独立练习：复制一条合成教育事实，更改其 `block_id` 为不存在的编号，预期 `evidence_error/missing_block`；再让假客户端抛 `TimeoutError`，预期只调用一次并得到 `client_timeout`。

## 8. 下一步仍需确认

先验收1C-1合成演示和错误行为，再决定1C-2供应商/模型/SDK/预算/真实调用。此阶段没有调用LLM、匹配、数据库、上传、OCR，也没有处理个人简历。


## 9. 后续1C-2状态（2026-11-10）

APINebula适配器已经实现，固定gpt-6.1-sol/low；计数改为纯本地，零计数API。合成离线专项95、完整619通过。首个真实合成生成请求client_timeout，1次后停止，usage未知，未续跑。原1C-1代码与契约未改；详细配置、计费预留、密钥生命周期和局限见[RESUME_APINEBULA.md](RESUME_APINEBULA.md)。


## 10. 当前提示词版本v2（2026-11-10）

后续已增强日期规则与正反例，当前prompt_version=resume-structure-prompt-v2（上文v1是1C-1历史记录）。模型value/quote保留逐字原文，纯年月可规范化，预计/至今等为null。仅提示词升级，不改草稿/画像契约和本地validator；v2离线专项31/完整693通过，未新增真实模型请求。见[RESUME_DATE_PROMPT.md](RESUME_DATE_PROMPT.md)。
