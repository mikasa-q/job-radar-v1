# 阶段 1B：ResumeProfile 画像契约与规则基线

日期：2026-11-10。用户已确认设计；本阶段由主代理顺序实现，不使用子代理，只使用合成简历样本验证。1A 沿用已有提取实现，没有重新读取用户简历。

## 1. 三层职责

```text
ResumeDocument（1A：分页、原文块和坐标）
  → build_rule_profile（1B：小词表技能提及规则基线）
  → ResumeProfile（逐事实证据契约）
  → validate_profile_evidence（绑定这份文档、检查引用）
```

- `src/job_radar/resume_profile.py`：画像、教育、项目、工作、日期和证据模型。
- `src/job_radar/resume_profile_rules.py`：纯规则函数及引用校验。
- `tests/test_resume_profile.py`：全合成文本/文档/PDF 测试。
- `examples/resume_profile_synthetic.py`：可直接运行的合成 PDF → 画像 → 篡改引文失败演示。
- 原 `resume_models.py`、PDF 适配器、预览、旧岗位 `parser.extract_skills` 和所有已有测试保持不变。

本步不调用 LLM，不做匹配、文件上传、OCR、数据库存储或新 HTTP 接口，不安装新依赖。

## 2. 模型：每个事实字段各自带引文

```python
TextFact(
    value="合成大学",
    evidence=[EvidenceRef(
        page=1,
        block_id="p001-b001",
        quote="教育经历：合成大学，计算机，本科在读。",
    )],
)
```

- `TextFact.value` 保留原文，必须逐字出现在至少一条引文中；引文至少一条、不可空白。本版不对学校、职位、学历做自动同义词推断。
- `EvidenceRef` 包含从 1 开始的 `page`、`block_id`、逐字 `quote`。引用文本不清洗、不重排、不跨块拼接。
- 一项教育/经历至少有一个事实；未知字段为 `None`，集合未提取为 `[]`，不接受全空经历来伪装提取成功。
- 每个非空学校、专业、职位、公司、日期、经历描述等字段各自携带证据，而不是一条经历只挂一段万能引用。

| 对象 | 主要字段 |
|---|---|
| `ResumeProfile` | `schema_version=resume-profile-v1`、`resume_id`、`profile_version`、`builder_version`、`source`、`education`、`skills`、`projects`、`work_experience`、`summary`、`warnings`、`review_status` |
| `ProfileSource` | 文档 schema、原始 bytes SHA-256、parser/extractor version；不包含本地路径或联系方式 |
| `Education` | `school`、`major`、`degree`、`status`、`start_date`、`end_date` |
| `ProjectExperience` | `name`、`role`、`nature`、起止日期、`description`、`technologies` |
| `WorkExperience` | `company`、`role`、`employment_type`、起止日期、`description`、`technologies` |
| `SkillEvidence` | 规范 `name`、带引文的 `mention`、上下文 `context` |
| `DateFact` | 原文 `value`、证据、可空的 `normalized` |

项目性质、实习性质、教育状态保留原文证据，例如“课程项目”“实习”“在读”。技能 `context` 为 `course/project/internship/work/skill_list/unspecified`，是上下文提示，不是工作性质认证。其他事实项不生成熟练度、经验年限、排名或匹配分。

`resume_id` 由调用方提供，不用哈希冒充人的身份；纯函数不读取时钟、不生成随机 ID。同一份简历的编辑版本由调用方维护 `profile_version`；本步不做持久化或自动修订系统。

默认状态是 `unconfirmed`。校验函数原样保留人工状态，引用校验成功也不自动转为 `confirmed`。

## 3. 日期与未知值

- 原文 `2025.09` 可另给 `normalized="2025-09"`；支持明确的年、年月及常见中英文分隔格式。
- 仅写 `2025` 时，不允许补成 `2025-01`；不给日精度。
- `至今`、`预计 2029 年毕业` 等保留原文，规范日期留空，不读当前日期、不推断已取得学历。
- 明确年月的无效月份、起止倒置会失败；混合年/年月精度只比较确定的先后，不编造月份。
- `skills=[]`、教育为空或摘要为空表示未提取，不表示不会技能、没有学历或没有经历。
- 电话邮箱、期望城市、出勤要求不在画像里；求职偏好仍留独立 `SearchPreferences`，本步不实现。

## 4. 证据校验是独立且必经的一步

`ResumeProfile.model_validate(...)` 只处理结构与局部约束。业务使用前需要：

```python
checked = validate_profile_evidence(profile, document)
```

函数返回重新校验后的独立副本，并检查：

1. 原始文件 SHA-256、文档 schema、两种提取版本是否与该 `ResumeDocument` 完全一致。
2. 每条引用的页是否存在、块是否属于该页、引文是否逐字存在于块原文。
3. 所有嵌套字段的引用，而非只看顶层技能。
4. 技能规范名与原始提及是否对应；技能必须是独立 token，不能把 Pythonista 中的 Python 当证据。
5. 每条技能引文都含相应提及；原始整块/分节中有已识别的否定、学习计划、招聘要求时，不接收正向技能。

重新校验模型 dump，可发现列表就地修改及 `model_copy(update=...)` 绕过构造检查的情况。常规赋值也启用 Pydantic 校验；跨字段赋值失败后建议丢弃该候选对象并重新构造，不把对象可变性当成审计记录。

| `ProfileEvidenceError.code` | 含义 |
|---|---|
| `source_mismatch` | 文件哈希或提取版本关联错误 |
| `missing_page` / `missing_block` | 引用位置不存在 |
| `quote_mismatch` | 引文不在指定块内 |
| `skill_name_mismatch` | 技能规范名与原始说法不一致 |
| `skill_mention_mismatch` | 引文没有独立的对应技能提及 |
| `non_positive_skill_context` | 已识别的否定、计划或要求语境 |

结构不合法时抛 Pydantic `ValidationError`，来源/引用不符时抛上述错误；后者消息只含错误码和字段路径，不回显简历正文。多个问题时返回遇到的首个问题。

**存在逐字引文并不证明语义归类正确，更不证明经历真实。** 例如“参观某大学”并不证明在那里就读；本版没有通用语义验证器。哈希关联依赖调用方提供的真实提取结果，不是文档真实性认证。人工核对状态始终独立。

## 5. 保守技能规则的边界

`build_rule_profile(document, *, resume_id)` 自动生成的只是**技能提及基线**：

- 复用旧词表作为候选对照，并单独定位原始提及，不更改旧函数。
- 当前词表：Python、FastAPI、MySQL、Redis、Git、Linux、Docker、pytest、LLM、Tool Calling、RAG、LangGraph、MCP、SQL。
- 忽略大小写；显式额外别名只有 `Python3/Python 3 → Python`、`py.test → pytest`、`Tool-Calling → Tool Calling`；不做模糊拼写修正。
- 同一块同一技能保留一项，不同页/块保留多项以保留证据；按文档页/块与首次出现位置排序。
- 正向提及不等于熟练使用。词表外如 Rust 不会被规则自动识别；契约可接受带原文引用的手动 Rust 事实。
- 一块有已识别的否定/计划/招聘要求，整块跳过，包括同块的正向句子；可降低混淆，但会漏掉真实正向提及。
- 独立“学习计划”“岗位要求”“暂未使用”等标题对后续块/页持续生效，直到明确的白名单正向分节标题。普通句子“计划学习 Docker”只屏蔽其自身块；不会把后来独立的 Python 项目块一起吞掉。
- 文本块顺序、显式标题与有限中英文模式不是完整语义理解；多栏关联、未覆盖的否定表达或指令式文字仍须人工核对。内容只作为资料，不执行。

教育、项目、工作、摘要**有契约和验证测试，但本规则不自动归类填充它们**，分别保留 `[]/None` 和 `not_extracted:*` 警告。这不是声称解析出完整履历。1C 再用结构化适配填入这些字段。

主要警告有 `rules_skills_only`、`not_extracted:*`、`excluded_skill_context:<block_id>` 和 `no_positive_skill_evidence`。同时传播文档警告与 `page_<n>:<warning>`；不因为有技能就消除版面问题。

## 6. 直接运行合成演示

在项目根目录执行（不接收用户文件，不保存 PDF）：

```bash
.venv/bin/python -B examples/resume_profile_synthetic.py
```

预期输出：

```text
skills=Python,FastAPI
evidence=2:p002-b001
review_status=unconfirmed
education=[] (not extracted)
tampered_quote=quote_mismatch
```

演示在内存生成两页 PDF：第一页“计划学习 Docker”，第二页“课程项目：使用 Python 和 FastAPI 实现合成接口”；Docker 不进入正向集合，另外两项引用第二页。然后修改引文，验证校验失败。

## 7. 测试与自己的练习

```bash
# 仅新增1B：合成文本、文档和PDF
.venv/bin/python -B -m pytest -q tests/test_resume_profile.py

# 完整回归
.venv/bin/python -B -m pytest -q
```

本阶段专项 **123 项**，完整 **412 项**（原 289 + 新增 123）通过；PyMuPDF 上游 5 条 SWIG 弃用警告未屏蔽。最终运行输出、保护哈希与副本回滚记录见交付 `VERIFICATION.txt`。

独立练习：在合成测试中增加第三页“实习：使用 Redis”，断言 Redis 的页号、块号和原文；再将它改为“暂未使用 Redis”，断言不进入正向集合。不要改旧岗位解析器来通过此测试。

## 8. 后续确认点

先由用户运行合成演示和测试、核对字段设计与保守规则。确认后再设计 1C 的 LLM 适配与假响应测试；真实调用、供应商、模型与发送内容要另外确认。1D 才涉及匹配输入转换。
