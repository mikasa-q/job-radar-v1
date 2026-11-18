# 阶段四 v3 输出协议冻结（Phase 0）

- 日期：2026-11-16（Asia/Shanghai）。用户已批准按优化方案实施 Phase 0–2；Phase 3 未授权。
- 依据：`STAGE4_VALIDATOR_V1_OPTIMIZATION.md` 第1–6节；本文件冻结新增业务政策，不倒改历史记录。
- 新生成协议：`match-analysis-prompt-v3`。报告仍 `match-report-v1`；MatchReport 字段/类型/状态/排名与 draft schema 不变，唯一模型定义变化为报告 prompt_version 允许值扩展及默认 v3。
- 完整提示词冻结在同目录 `STAGE4_V3_SYSTEM_PROMPT.txt`；Python v3 常量须逐字等于该文件，不在运行时读文档拼提示词。v1/v2 提示词文件原样保留。
- 默认离线；不读凭据、不发真实请求、不建真实provider。MockTransport和人工 authored fixture 只证明工程门禁。

## 1. 字段、引用与四层顺序

整份 draft 先完成一层的全部 claim，再进入下一层；同层按 claims 顺序，同 claim 按本节列出的顺序首错停止。成功返回未修改 draft；失败不修JSON、文本、字段或引用。单岗仍一次 complete；拒稿 draft=null，完整原响应仅进入显式本地sidecar，失败即停。

1. **结构**：严格JSON/有界字节/重复键/非有限数/围栏；现有 schema/类型/枚举/长度/额外字段、claim ID/重复引用/related关系；最后 key 必须相同。沿用 `analysis_output_limit` / `analysis_invalid_json` / `analysis_schema_error` / `analysis_key_mismatch`；内部 claim_status_mismatch 等仍统一schema码。
2. **规则**：subject 逐字为当前岗位 alternatives 中的单个规范技能名，不接受派生熟练度/年限主题；requirement_kind 必须为同主题且实际引用的选定规则 kind（required/bonus/mention），unknown类别不豁免。类别错 `analysis_requirement_kind_mismatch`；unknown finding只接受kind=unknown/status=unknown，否则 `analysis_unknown_as_inability`（这是v3结构化业务含义，不是旧正文词法含义）；已支持OR另一分支不能生成skill_gap，报 `analysis_alternative_already_supported`；建议关联finding的subject及requirement_kind必须一致，类别不一致 `analysis_advice_requirement_mismatch`。实际引用不存在/错作用域/缺同主题规则留证据层，不借其判文本。
3. **证据**：先逐引用核查ID membership及岗位作用域（`analysis_evidence_scope`），随后核查选定同主题rule引用（`analysis_invalid_rule_reference`；rule内容须逐字段匹配当前岗位同主题规则），再按status核查。supported须同主题JD+履历+该技能matched/evidence_found规则，沿用 `analysis_unsupported_positive` / `analysis_resume_support_mismatch`；unknown须同主题JD+scope并扫全部当前覆盖材料，不只扫模型选中的履历，已有使用记录或明确负向事实与unknown冲突报 `analysis_unknown_evidence_conflict`，缺JD/scope `analysis_unknown_requires_scope`；explicit_gap须引用同技能、同句、直接陈述的负向原履历，缺失报 `analysis_gap_requires_negative_evidence`；建议须引用其finding同主题岗位/证据，沿用 `analysis_advice_requires_finding_evidence`。
4. **文本安全**：顺序为概率→膨胀词→可选项强制措辞→其余短事实/unknown合同→敏感内容。同一claim此前层失败后后层标not_evaluated。前两项subject/text全部kind/status均查，无源文、引用、否定或advisory豁免。错误为 `analysis_forbidden_probability` / `analysis_unsupported_experience` / `analysis_optional_as_mandatory` / `analysis_text_contract_violation` / `analysis_text_sensitive_content`。

完整原材料/证据包重建仍在请求前由既有 build/validate_evidence_packet 负责，evidence_* 原码和哈希/ID作用域/原排名校验不删。内容ID不是来源真实性保证；Dense历史计算未重放等既有限制保持。

## 2. 精确文本匹配协议

以下字面表与完整提示词共用；正则也须符合此语义，不额外解释否定。

| 组 | 冻结字面词/形态 | 失败码 |
| --- | --- | --- |
| 膨胀 | 精通、熟练、专家、资深、生产经验、生产环境经验、正式工作、生产系统、expert、proficient、senior、production experience、production system | analysis_unsupported_experience |
| 年限膨胀 | `[零〇一二两三四五六七八九十百千万0-9]+年(?:工作\|开发\|生产)?经验`；`[0-9]+year(?:s)?(?:of)?experience`（比较副本已去空白） | analysis_unsupported_experience |
| 概率/评分 | %（全角％经NFKC相同）、百分之、录用概率、胜任率、胜任概率、成功率、可信度、置信度、hire probability、hiring probability、chance of hiring、chance of being hired、confidence | analysis_forbidden_probability |
| 评价/限定 | 匹配度高、匹配度低、高度匹配、胜任、满足岗位要求、能力不足、不代表、不等于、不等同于、不意味着、不能据此、仅说明、限于、不足以、无法确定、尚不能、does not imply、not equivalent | analysis_text_contract_violation |
| 可选项建议强制 | 必须、必需、必补、硬性、must、required；仅bonus或已支持OR分支recommendation触发本码 | analysis_optional_as_mandatory |

比较副本：`unicodedata.normalize("NFKC", value).casefold()`，去除全部 `str.isspace()` 字符用于上述有限禁词匹配（包括中英文拆词）；保留原输出供长度/句式/证据/展示。英文禁词保持既有保守子串政策（如expert），不引入语义/词形模型。控制/零宽不自动删除后放行：任意Unicode类别Cc/Cf/Cs（含CR/LF/tab、零宽和双向控制）为text_contract_violation；词法优先级先于控制字符错误，若禁词被控制字符断开至少合同拒绝。不承诺无限Unicode变体全覆盖。

- 原text长度按Python `len(str)`，最多80 Unicode码点，不按UTF-8字节、JSON转义或字形计；空白-only不合规。subject仍使用原schema长度并受规范技能名限制。
- text为单一短句，无换行或分号（`;；`），无内嵌句末 `。！？!?`；可有一个末尾 `。` 或 `.`，不强制末标点。ASCII句点仅在两侧都是ASCII字母/数字时可作技能名/数字内部点（如Node.js），其他内部句点视为多句。禁止评价/免责声明/限定尾句；有限标记未覆盖的语义另作人工漏报检查，不宣称通用语言理解。
- 原text/subject不得含可识别联系方式、URL或本地路径：沿用retrieval_text和_LOCAL_PATH可检测范围作拒绝，不静默清理模型结论。不把固定证据表/原JD/limitations/检索分项当模型结论扫描。
- unknown正文恰为 `简历未提及 <subject> 使用经历。`，两侧空格为一个ASCII空格，subject必须是规则规范名；status/kind须先通过结构化门禁。不是本地生成/改写text的模板引擎。
- 正向事实保留课程/个人项目/实习等来源性质，不强求match_interpretation，不再输出同技能“熟练度/年限unknown”。建议为recommendation/advisory，关联finding，单句；允许“可补充 Docker 使用记录。”。

## 3. 全覆盖材料与明确差距

unknown的X固定为“<规范技能> 使用经历”，不是技能名字是否出现。课程项目使用即已有使用经历；正向记录即使未被claim选引也不可写unknown。计划学习Docker只代表计划，允许Docker unknown并保留原计划证据；明确从未使用SQL须explicit_gap，不降为unknown。歧义材料不得造unknown或认证explicit_gap，应省略并留人工核查；省略造成的finding覆盖缺失单独计量。

原材料负向采纳只认有限直接陈述、同技能同句的“从未使用/未使用/没有使用/没用过”等明确模式，允许说明标签和第一人称；英文never used/have not used同理。未来计划、条件假设、双重否定/不确定、他人经历、跨技能/跨句拼接均不能认证负向。`_NEGATIVE`仅针对被引用原履历，不服务生成结论否定豁免。正向支持也不能由计划/负向误认；无法判定材料不反向自动变成unknown。保持阶段二规则和阶段三结果原样。

## 4. 历史只读与新生成入口

- v1/v2仅历史读取/展示：保留原prompt_version、status、原文与review_status，不套v3文本政策，不声称重新通过当前全验，显示“历史只读，不代表已通过v3”。仍校验通用结构、包/记录内容哈希、ID/岗位作用域、引用和原排名。
- 不保留/复制删除的否定helper或旧白名单来假装重演历史。同v2含不同历史validator，精确历史结果只由原快照/日志证明。
- 当前全验请求只认v3；显式要求当前全验而输入历史版时报 `analysis_validation_policy_mismatch`。边界利用现有report.prompt_version及函数选项，不增schema字段。
- 新provider gate只接受精确v3 prompt/version/schema，旧version+旧prompt也拒绝；不因历史可读而获准新生成。
- 新错误码加入本地固定码白名单，保留完整拒稿sidecar及usage/reserve。`analysis_unknown_as_inability`历史码解释不回写。

## 5. 冻结正反例及两套标签

以下为人工authored fixture/人工期望，不是真实模型新输出；evidence_id从实际本地冻结证据包选取，不发明示例ID。

| 样本/输入原文 | 合规结论 | 语义错误负例/协议负例 |
| --- | --- | --- |
| S1课程项目使用Python和FastAPI开发接口 | Python/FastAPI各strength/supported/required；“课程项目使用 Python 开发接口。”；Docker unknown/unknown/bonus，“简历未提及 Docker 使用经历。” | “熟练Python”“不代表生产经验”均禁词；前者无依据膨胀，后者语义无膨胀但协议不合规；已有使用的Python不得unknown |
| S2个人项目使用Python和Git编写命令行工具 | Python支持Python或Java的required OR；Git strength/supported/bonus，“个人项目使用 Git。” | Java必补是语义错误；“Git证明达到加分标准”是评价协议负例 |
| S3课程Python/FastAPI、计划学习Docker、从未使用SQL | Docker unknown固定短句；SQL skill_gap/explicit_gap/required，“简历注明从未使用 SQL。”，引用该原句 | Docker supported把计划当已使用；SQL unknown把明确否定降为未提及，均语义错误 |
| Docker unknown关联建议 | “可补充 Docker 使用记录。”、recommendation/advisory/bonus，关联同主题finding | “无需把Docker视为必补”语义可能不强制但协议仍因必补拒绝 |
| 两份历史c1/c2长否定限定 | 新的人工短事实另存，不覆盖历史raw | 原“不代表/不等同于…生产经验”保留语义无膨胀标签，v3政策拒绝；不得声称模型已产出短事实 |

标签A=人工语义正确性；标签B=v3输出合同合规。正类=应拒。分别统计TP/FP/FN/TN、FPR=FP/(FP+TN)、FNR=FN/(TP+FN)，分母0=unknown；另列层/攻击类型、首错/运行错误、政策保守成本交叉表、合规正例接纳、关键篡改漏报、finding覆盖/无依据新增claim、status分布。整草稿首错和隔离claim分开；未执行后层not_evaluated。

Phase 2门槛：冻结离线关键安全/类别/ID篡改FN=0；合规短事实FP=0；每个漏报逐例列明；有限测试集成绩不外推一般准确率。真实S1/S2/S3全部not_run，供应商完成率unknown；Phase 3另行授权，不自动使用旧预算。

## 6. 历史raw与原基线

两份历史raw SHA256经独立读取sidecar的raw_draft_text重新计算与方案0.2完全一致：

- v2-single-job：`75351fcd7507c75ed01b572520bf70c481b8af447bac23a1d1df59bc01f7e724`。
- negation-guard：`ed1501186492338db02e2b1069f05abf5dfea6f5aa2571ccdeaf3c91cdf8408c`。

原始文件和既有fixture一字不改；核验日志在交付目录historical-raw-hashes.json。基线按本轮重新运行输出，不复用历史1757成绩。独立副本保留所有纳入快照的既有文件与SHA256，回滚不在活动仓库试验。
