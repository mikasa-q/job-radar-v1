# 阶段 1D：匹配交接契约与离线验收

日期：2026-11-11（北京时间）。在 `feature/jobradar-v1` 上按用户确认的方案顺序实现，不使用子代理。

## 1. 本步输出什么

本步实现的是 Python 数据模型和纯函数，不是 HTTP 接口，也不是匹配引擎。

```text
1B build_rule_profile → ResumeProfile ┐
                                    ├→ build_matching_input(profile, preferences=...)
1C StructureResult.profile ──────────┘   → MatchingInput
                                              ↓
调用方准备好的 list[JobCandidate] → build_matching_request(..., purpose=...)
                                              ↓
                                      MatchingRequest（无排名）
                                              ↓
                                      阶段 2 的匹配引擎
```

- `matching_models.py`：SearchPreferences、MatchingInput、JobProvenance、JobCandidate、MatchingRequest。
- `matching_input.py`：两个纯函数，不访问文件、数据库、时钟、网络或模型。
- `examples/matching_input_synthetic.py`：内存合成证据 + 1B/1C 假客户端演示。
- `tests/test_matching_input.py`：137 项新增合成验收，另保留既有 719 项。

`MatchingRequest` 表示数据符合交接格式，不表示岗位适合、不表示原文已人工确认，也不表示岗位还在招聘。

## 2. 简历侧：复用 1B/1C，不再生成另一套事实

`build_matching_input(profile: ResumeProfile, *, preferences: SearchPreferences) -> MatchingInput`

| 输出字段 | 来源与语义 |
|---|---|
| schema_version | matching-input-v1 |
| profile_schema_version | 保留 resume-profile-v1，与交接模型版本分开 |
| profile_id / profile_version | 来自 resume_id / profile_version，不生成新 ID 或版本 |
| builder_version / source | 保留构建器版本、原件 SHA-256、parser/extractor 版本 |
| skills | 原 SkillEvidence：规范名、原文 mention、context、逐字段 evidence |
| education / projects / work_experience | 原 1B 模型，含日期原文/规范值及各字段证据，三类不混合 |
| summary | 原 TextFact 或 None，不生成总结 |
| preferences | 独立用户输入，不从简历或岗位推断 |
| warnings / review_status | 原样保留；引用校验通过不自动确认 |

1B 规则只填技能：其教育/项目/工作为空和 not_extracted 警告继续保留。1C 使用 `StructureResult.profile`，不接原始模型 JSON；`comparison` 仍是独立规则/模型差异诊断，不取并集、不据此补技能。两者都走同一个转换函数。

### 必经的上游证据校验

1B 和 1C 的构建流程已经调用 `validate_profile_evidence`。手工编辑、外部恢复或可能被修改的画像，使用前仍须调用：

```python
from job_radar.resume_profile_rules import validate_profile_evidence
from job_radar.matching_models import SearchPreferences
from job_radar.matching_input import build_matching_input, build_matching_request

# document 必须是调用方保留的原 ResumeDocument；不在转换函数内读文件。
checked = validate_profile_evidence(profile, document)
matching = build_matching_input(checked, preferences=SearchPreferences())
# jobs 是调用方已准备好的 list[JobCandidate]；1D 不负责从 SQLite 取出。
request = build_matching_request(matching, jobs, purpose="debug")
```

转换函数只重验结构，不持有原文档，不能重新证明 quote 存在或语义正确。它不设置 evidence_validated 之类的证明字段。若校验后又修改了画像，应重新校验；一个可变 Python 对象不是不可伪造的凭证。

### SearchPreferences

- `cities: list[str] | None`：用户接受的城市备选。
- `target_roles: list[str] | None`：用户目标岗位备选。
- `available_days_per_week: int | None`：用户可出勤天数，1–7。
- `available_duration_months: int | None`：用户可实习月数，正整数。

默认全为 None，即未填写，不代表不限城市/随时到岗/默认满足。城市、岗位列表填写时须非空且每项非空白；`[]` 不作“任意”的隐式写法。数值不接受 bool、字符串或小数。单位写在字段名中，本步不解释 JD 中的要求，也不判定这些偏好是否满足要求。

## 3. 岗位侧：事实 + 存储身份 + 多来源信封

`JobCandidate` 的 schema_version 为 job-candidate-v1：

- `dataset_id`：调用方稳定分配的不透明数据集标识，不是绝对库路径；与 job_id 共同定位岗位。
- `job_id`：未来来自 imported_jobs.id 的正整数，本步由合成样本显式给定。
- `job: ImportedJob`：完整 JD、URL、标题、公司、城市、学历、经验、出勤、技能、标签、警告等全部原字段。
- `jd_sha256`：description 的 UTF-8 SHA-256。
- `fact_sha256`：ImportedJob.model_dump() 经 ensure_ascii=False / sort_keys=True / separators=(",", ":") / allow_nan=False 的 JSON UTF-8 SHA-256。
- `provenance: list[JobProvenance]`：至少一个来源版本。
- `review_status`：显式提供 unconfirmed / confirmed / rejected，不从 ImportedJob 猜。

JobProvenance 保留库内来源记录 ID（provenance_id）、source_file_sha256、html/txt、parser_version、UTC imported_at、import_run_id。不携带 source_file 绝对路径。imported_at 支持 Python 的 UTC datetime 或含 T 与 UTC 时区的 ISO 字符串，便于未来 SQLite/JSON 适配；不接受 epoch 数字、无时区或非 UTC 时间。

校验双哈希与传入事实一致，来源 ID 及（文件哈希、解析器版本）不重复；算法与既有 dataset_store.job_fingerprints 的输出有合成对照测试，但新模块不导入存储层、不读文件。哈希只绑定传入内容，不证明 JD 真实或来源确实属于该数据库记录；未来只读 adapter 必须核对 dataset_id、job_id 与 provenance 的实际关联。

不把 skills 自动升级为必须/可选/加分要求；完整 JD 仍保留“或”“加分”等原文。ImportedJob 缺失标量仍是既有空字符串，不在 1D 擅自更改契约或补值。

## 4. 请求与人工状态门禁

`build_matching_request(profile: MatchingInput, jobs: list[JobCandidate], *, purpose: Literal["debug", "formal"]) -> MatchingRequest`

- purpose 必须显式提供；默认不假定为正式使用。
- debug：保留所有三种人工状态，用于检查数据管道，包括 rejected 样本；不据此得出正式推荐结论。
- formal：画像和每个岗位必须全部 confirmed，否则报 `formal_requires_confirmed`；rejected 和 unconfirmed 都不进入正式请求。
- 不修改任何人工状态，不写数据库。confirmed 由调用方提供，本步不实现身份授权、确认记录存储或真实性认证。
- 保持岗位输入顺序，不排序、不按技能筛选、不静默去重。同数据集内重复 job_id 或相同 source+URL 明确失败；不同数据集内相同局部 ID 可共存。
- 空岗位列表合法，表示当前没有候选数据，不是“匹配度为 0”或“匹配失败”。

输出没有 score/rank/requirements/embedding/录用概率。未来阶段 2 的结果应区分支持项、未发现证据项、约束满足/不满足/未知及对应简历/JD 证据，本步不固化或实现 MatchResult。

## 5. 严格边界与可变对象

新模型拒绝额外字段和非法版本/类型。两个公共函数从当前嵌套内容重建并重验，捕获 model_copy(update=...)、列表就地修改和嵌套对象构造绕过，返回独立副本，不共享输入列表/模型。

对象仍是可变 Pydantic 模型，并非不可变审计快照；某次跨字段赋值失败后，应丢弃该对象，从已知有效数据重建。发送/消费之前重新通过公共边界校验。模型构造成功不等于引用校验成功。

本步不新增姓名、电话、邮箱、头像、年龄、性别等匹配字段，也不构造 embedding_text。带证据的原文片段仍可能含联系方式，不宣称该投影已脱敏；未来向量化/外发另做显式选取与检查。

## 6. 离线运行与验收

在项目根目录，使用已有环境：

```bash
.venv/bin/python -B examples/matching_input_synthetic.py
.venv/bin/python -B -m pytest -q tests/test_matching_input.py
.venv/bin/python -B -m pytest -q
```

演示输出：

```text
mode=synthetic/offline; client=fake (no real model)
1B.skills=Python
1C.skills=Python
planned_Docker=excluded
preferences=unspecified; education=[] (unknown)
request=matching-request-v1; purpose=debug; jobs=1 (unranked)
review_status=unconfirmed/unconfirmed
evidence=1:p001-b002; provenance_versions=1
scores=absent; database_reads=0; network_calls=0
formal=formal_requires_confirmed
upstream_tampered_quote=quote_mismatch
```

137 项新增测试覆盖三份原有 1C 黄金样本同入口、1B 未知/警告、规则差异不合并、全部人工状态组合、显式偏好边界、完整事实与多来源、双哈希对照与篡改、严格 schema、JSON 往返、输入不修改/输出独立、上游引用篡改失败、空列表/顺序、重复身份，以及禁用网络/SQLite/文件读取/客户端调用后的纯函数执行。独立导入测试确认不加载旧 API、存储层或供应商适配器。

原始基线 719 项通过；新增后应为 856 项。实测耗时、逐字输出和退出状态以同批次 VERIFICATION.txt 为准。合成假响应证明的是接线和契约，不是 LLM 提取准确率，也不是匹配质量。

教学练习：将演示中的引文篡改段落移到投影之前，先调用 validate_profile_evidence，观察 quote_mismatch；再把 purpose 改 formal，观察未确认数据被门禁拦下。不要通过修改 review_status 来冒充人工核对。

## 7. 1D原边界与阶段2后续状态（2026-11-11更新）

- 阶段2已新增独立只读 evidence_repository adapter；1D本身仍不读库，不把新库路径塞给旧storage.query_jobs。
- 阶段2已新增有限要求解析/三态约束、BM25/规则、可选Embedding协议和RRF；真实模型质量验收尚待后续固定配置，见docs/MATCHING_ENGINE.md。
- 阶段3保留质量评测、长JD分块、持久/增量索引与资源基准；BM25/RRF不重复实现。
- FastAPI、HTTP 上传、旧 API 接通及服务化：阶段 5。
- 本步不接真实 LLM、不读取密钥、不安装依赖、不改既有解析器/画像/模型提示词/黄金预期/数据库。

文档曾核验的 15 条 JD 均为 unconfirmed，是历史证据，不是本轮重新查询结果；正式演示前仍需逐条核对所用岗位。
