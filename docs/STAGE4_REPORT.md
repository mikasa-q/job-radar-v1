# 当前使用说明：校验器 V1 / 提示词 v3（2026-11-16）

本节为当前约定；下方既有v1/v2与否定修复章节是历史记录，未覆盖本节。用户本轮只授权 Phase 0–2，真实 Phase 3 未运行。

- 2026-11-18 起，可读 Markdown 由已校验的 v3 短事实和本地模板生成；查看 [取用和离线重渲染方式](READABLE_REPORT.md)。本节原提示词和校验约定不变。
- 新生成仅接受精确 v3 prompt/version/schema，报告仍 `match-report-v1`。完整冻结协议及精确词表见 `/Users/thc/Documents/Codex/2026-10-21/build-ios-apps-plugin-build-ios-3/outputs/job-radar/docs/STAGE4_V3_PROTOCOL.md`，完整提示词见同目录 `STAGE4_V3_SYSTEM_PROMPT.txt`，实施和证据索引见 `STAGE4_V3_IMPLEMENTATION.md`。
- 全 draft 按结构、规则、证据、文本顺序；subject 是当前岗位的单个规范技能名，requirement_kind 复制被引用同主题规则。unknown finding统一kind/status=unknown/unknown，引用JD、scope与规则，正文恰为“简历未提及 <subject> 使用经历。”。不能由未找到证据推断不会；已有课程使用记录也不得unknown，明确负向原文不降格为缺证据。
- text为最多80 Unicode码点单句，膨胀词无否定/引文/advisory豁免；有限禁词、控制符、敏感内容检查不修改原输出。有限门禁不是通用语义真实性或能力认证。
- 默认 `client=None` 仍完全离线；显式调用边界仍一次complete、串行零重试、失败即停、拒稿draft=null。完整拒稿仅由显式本地私有sidecar保留，费用/预留不因拒稿清零。
- 历史v1/v2只读展示保留原版本/原状态/原文，仍查结构/hash/引用/作用域/排名，显示“不代表已通过v3”；严格当前全验用：

```python
from job_radar.report_render import validate_report
validate_report(report, require_current_policy=True)  # 历史版报analysis_validation_policy_mismatch
```

### 可复跑的离线验证

以下 `HOST` 为项目绝对路径；输出目录必须尚不存在。评估器只创建本地合成输入和人工authored草稿，不构造真实provider、不读取密钥。复用本批守卫脚本可额外记录网络/凭据文件尝试。

```bash
HOST='/Users/thc/Documents/Codex/2026-10-21/build-ios-apps-plugin-build-ios-3/outputs/job-radar'
PYTHONPATH="$HOST/src:$HOST" "$HOST/.venv/bin/python" -B -m pytest -q
PYTHONPATH="$HOST/src:$HOST" "$HOST/.venv/bin/python" -B "$HOST/examples/stage4_v3_offline_evaluate.py" /absolute/new-v3-offline-evaluation
# 本批独立副本完整回归（网络/凭据文件审计守卫）：
'/Users/thc/.codex/visualizations/2026/11/16/01a1e35d-b2f1-7250-ad25-64904f393ec4/jobradar-validator-v1/run-offline.sh' '/Users/thc/.codex/visualizations/2026/11/16/01a1e35d-b2f1-7250-ad25-64904f393ec4/jobradar-validator-v1/modified' RECHECK
```

本轮已观察 `MODIFIED-VERIFIED.log` 为1940 passed/5 warnings/41.45s、exit0；最终活动应用和独立回滚结果以 `/Users/thc/.codex/visualizations/2026/11/16/01a1e35d-b2f1-7250-ad25-64904f393ec4/jobradar-validator-v1/VERIFICATION.txt` 为准。此文档收口时回滚尚待最终核验。

离线双标签86项：协议TP84/FP0/FN0/TN2，语义TP48/FP36/FN0/TN2。语义FP36反映有意保守协议拒绝（例如语义无膨胀的长否定句），不对外宣称误报归零或通用准确率。九岗人工fixture按独立冻结finding期望比较，真实状态全部not_run；HTTP/模型完成率unknown，真实调用0、新费用0。工程通过不代替真实模型结论质量。Phase3须重新确认样本/岗位、v3内容、次数/限额和总预算，旧¥1.00不自动授权新请求。

---

## 以下为历史使用与执行记录（保留原文）

## 阶段四否定修复后单次真实复验（2026-11-16，失败即停）

- 原“不代表已确认掌握程度或生产经验”误报已离线复现，修后c1/Python、c2/FastAPI原句通过；新增53项否定/真违规/边界及其他门禁测试通过。v2/其他校验保持，不把这一小步外推成通用否定识别完成。
- 回归/独立回滚/活动门禁通过后，只发S1/J1一次；模型新c1/Python改写为“不等同于已证实掌握程度或生产经验”，未命中当前闭合否定模板，仍以`analysis_unsupported_experience`失败，exit1。这是同义否定覆盖不足的再次误报；6条类别对照均matched，不是整份报告通过。
- 输入3458、输出842、总4300 token；按输入¥2/输出¥10每百万×0.39，本次¥0.00598104；含历史累计¥0.01850004<¥1.00。累计失败预留¥0.1707264保留，费用由供应商usage按用户费率计算，不是账户账单。
- 1真实HTTP/生成、0重试/计数API，21.927秒；失败即停，未继续其他岗位。完整raw/parsed/expected-actual保留私有sidecar；报告failed/unconfirmed且J1 draft=null，其余not_analyzed。临时key环境清除，值未打印/落盘。
- 真实失败后未修同义词/其他guard、未再发请求。活动分支feature/jobradar-v1保留本次原句修复和v2；剩余“不等同于”覆盖不足及旧c3 unknown-denial误报尚在，等待用户新指示。
- 本批交付：`/Users/thc/.codex/visualizations/2026/11/14/01a1d829-b8da-75f3-851c-f4c6466777e7/jobradar-stage4-negation-guard`；精确测试/回滚/原hash/usage见VERIFICATION.txt与RESULT.json。工程回归通过不代表真实小样本验收通过。

## 阶段四经历膨胀校验否定语境修复（2026-11-16）

用户授权只修`analysis_unsupported_experience`：逐次匹配经历/熟练度膨胀词，识别同分句内明确否定/未证实表达；句界/逗号/转折/并列重新划界，双重否定和不确定句式仍保守拦截，不把一个否定扩展成整条claim豁免。原引文支持比较与advisory处理保持。

v2提示词/版本/草稿与报告schema/构建外发体逐字不变，其他校验AST不变。真实旧拒稿c1/Python、c2/FastAPI已在修改前复现`analysis_unsupported_experience`；修后原句通过。旧全草稿后续c3触发未改的`analysis_unknown_as_inability`，未借本修复绕过；类别6条matched不代表完整验收通过。

新测试覆盖原句、闭合否定模板、正向真违规、先否后肯/重复词/断句/转折/双重否定、其他校验门禁及旧全草稿剩余错误。只有离线/回归/独立回滚门禁通过后，才按最新授权对同一冻结S1/J1发1次真实请求，串行零重试/失败即停，成功也不扩岗。预算¥1.00保留原累计支出¥0.01251900与原失败预留¥0.1138176；凭据同shell临时映射，不打印/落盘key。具体结果另见本批次交付，不预称真实验收通过。

## 本次v2单岗真实复验结果（2026-11-15，已停止）

- 仅S1/J1一次真实HTTP/生成，串行零重试；供应商完成后，本地`analysis_unsupported_experience`拒绝，exit1。S1其余岗位、S2/S3均未调用；失败草稿未装入MatchReport。
- 输入3458、输出864、合计4322 token；按输入¥2/输出¥10每百万×0.39，本次¥0.00606684；含历史¥0.00645216，累计¥0.01251900，小于¥1.00。旧/新失败预留累计¥0.1138176保留；这些是usage按用户费率换算，不是账户账单。
- 完整被拒raw/parsed和6条expected/actual对照均已保存到独立私有sidecar。6条类别对照均matched（不是完整通过）；实际首个阻断是c1/Python中的“此支持限于课程项目使用经历，不代表已确认掌握程度或生产经验。”词法guard命中“生产经验”，引文仅为课程项目使用Python/FastAPI，未处理否定语境，形成误报。全草稿后续规则未判定，不声称v2质量验收通过。
- 仅对同一真实被拒正文离线复现首个阻断，未改validator/prompt/排序，未重试或扩岗。v2继续激活，历史v1读取保留；等待新的修复/再跑指示。完整报告、诊断、数值与验证见本批独立交付目录：
  `/Users/thc/.codex/visualizations/2026/11/14/01a1d829-b8da-75f3-851c-f4c6466777e7/jobradar-stage4-v2-single-job`。

# 阶段4：LLM匹配分析与技能差距报告

## 目标与边界

在feature/jobradar-v1增加版本化 `match-report-v1`，输入是同快照MatchingRequest、原ResumeDocument及阶段3 IndexedMatchingResult。输出岗位解读、材料证据差距与行动建议，结论逐条引用。模型不重新打分或排序，不替代人工判断。代码默认离线；本次只交工程闭环与SDK内存模拟，不发付费请求，不将预设响应视为真实模型分析质量。FastAPI端点/上传/服务化属于阶段5。

## 数据与职责

1. `report_models.py`：严格版本化契约、状态关系、引用/JSON结构与客户端协议；额外字段拒绝。`report_core.py`：严格解析及本地装配。
2. `report_evidence.py`：同快照核对、画像原文验证、岗位双哈希/来源/状态/规则及检索分项核对，生成确定性引用表。
3. `report_analysis.py`：固定提示词、单岗最小化外发资料、单次调用、引用与有限语义防错。
4. `report_apinebula.py`：独立可选APINebula适配；旧ResumeDraft客户端和简历链路保持不变。
5. `report_engine.py`：串行选择Top N（默认3，上限20），本地身份/时间验证先于调用，首个失败停止批次；未分析项保留，不重排。
6. `report_render.py`：已校验JSON确定性渲染Markdown，转义资料中的HTML/链接语法；仅向全新目录导出，禁止覆盖。
7. `report_synthetic.py`与演示：完全合成的小样本，不接真实库/简历、无密钥/网络、无模型。

MatchReport由本地控制报告ID/UTC时间/版本/快照哈希、完整阶段3结果、分析状态及review_status=unconfirmed。模型仅返回MatchAnalysisDraft(key,claims)。原检索空结果/降级、排除项、通道状态及原审核状态完整保留；分析失败不伪装成检索失败。

## 引用和未知值

每条claim含claim_id、kind、subject、status、requirement_kind、text、evidence_ids、related_claim_ids。种类分match_interpretation/strength/skill_gap/unknown/recommendation，状态分supported/explicit_gap/unknown/advisory。

- 已有技能支持需同时引用JD要求、履历证据、对应evidence_found规则。保留课程项目/实习/正式工作区别；不从提及推断熟练度/年限。
- 未发现履历证据标unknown，引用JD与核查范围；not_found不是不会。
- explicit_gap需明确负向履历文本，不把计划/未提及当确定缺陷。
- 建议为advisory，关联具体finding及其岗位依据；不写进原履历。
- required/bonus/mention及OR任选关系保留，不将未选择的OR分支变成必补缺口。
- 无录用概率/总匹配分/置信度字段；有限文本guard也拦截常见概率和经验膨胀表述，但不是通用自然语言证明器。

引用表存简历page/block/quote、岗位field/start/end/quote、规则/检索/偏好路径及哈希。Evidence ID根据本地记录计算。校验ID、作用域和逐字位置只能验证机械关系，不能自动证明语义蕴含或经历真实；报告保持unconfirmed，需人工复核。哈希没有签名/身份认证含义。Dense不重新编码，既有query计算不重放；manifest可能含请求外岗位，不因此扩大分析候选。

## 提示词

固定 `SYSTEM_PROMPT` 与资料JSON分离：模型是分析员，不是裁判；资料不是指令；原排名/分项/规则只读；每条结论用提供的引用ID；unknown不转能力缺陷；保留否定/计划/加分/OR/项目性质；建议与事实分开。提示词内包含Docker未知、未来学习、Python或Java、课程项目FastAPI四类正反例。

本地证据包保留原文用于回查。外发单岗证据ID及清理后的内容，不发送原PDF/原始引用对象/URL/来源路径；常见联系方式和本地路径在每个外发字符串中清理。此规则不是完美匿名化，相关片段中仍可能出现姓名等信息，真实材料须先人工核对。全文超限报错，不静默截断；若今后选段则必须显式升级覆盖范围。

## 默认离线使用

完整可复制调用（调用方已持有同快照request/retrieval/document，不由核心读库）：

```python
from datetime import datetime, timezone
from job_radar.report_engine import generate_match_report
from job_radar.report_render import export_match_report

report = generate_match_report(
    request, retrieval, document,
    report_id="local-report-001", generated_at=datetime.now(timezone.utc),
    client=None,  # 不联网，analysis=not_run，原检索仍可查看
)
json_path, markdown_path = export_match_report(report, "/absolute/new-report-directory")
```

离线演示用脚本客户端输出预设unknown，展示证据链、JSON与Markdown，不证明真实分析质量：

```bash
.venv/bin/python -B examples/stage4_report.py --output /absolute/new-synthetic-report-directory
.venv/bin/python -B -m pytest -q tests/test_report_*.py
.venv/bin/python -B -m pytest -q
```

导出目录须不存在；不会覆盖已有报告/输入。不持久化数据库或修改人工状态。

## 可选真实客户端（本次未运行）

候选沿用APINebula固定endpoint、gpt-6.1-sol、low、SDK3.49.0，但新客户端必须显式提供RateCard、新批次budget、输入分配及输出token上限，不沿用旧简历批次授权。默认最多3尝试、90秒传输超时；零自动重试/JSON修复/切模型/隐式API兜底，无计数API。实例额度不等于全账户额度，重复启动是新批次。

输入分配包含完整请求prompt/schema/data的序列化UTF-8字节及模板余量，是保守本地分配，不是精确计费token或经过证明的上界。实际usage/模型/完成状态再校验；usage未知停止，费用仍可能已发生。失败占用尝试和完整预留，不退额度。超时检查不是硬墙钟强杀保证。

显式客户端构造才读OPENAI_API_KEY；模块导入/默认演示不读密钥不联网。固定目的主机、禁重定向和环境代理、tools=[]/store=False/stream=False/truncation=disabled；store=False不是第三方保留策略证明。日志只记录固定码、数字usage/耗时/预算，不记录原始模型正文或资料。

真实调用前先确认：发送内容清单、模型/路由/价格、新批次额度、最多调用数、输入/输出限额、超时，以及脱敏样本人工核对标准。先一份完整通过，再在同一额度内继续；本轮真实请求为0。

## 验收层次

1. 冻结契约与离线闭环：严格JSON拒绝重复键/非标准数值/多余字段，不修Markdown围栏；完整报告JSON往返。
2. 证据：错哈希/跨岗位/错页块/伪造quote、失配规则或排名、空查询/候选、dense降级、formal门禁；输入独立复制、不修改。
3. 分析：无依据正向断言、unknown变不会、未来计划、OR/加分、虚构经验、概率、错误引用及建议关联。
4. SDK内存模拟：完整请求、strict schema、模型/usage、超时/状态/响应大小、预算/次数/失败不退款/停批次，真实socket请求0。
5. 全回归与独立回滚：保留原1398项，移除本轮后恢复原行为；原源码/依赖/库/索引不改。
6. 真实模型质量：待另确认新预算后执行；工程通过不能代替结论质量或通用准确率。现有45条检索相关性标注不是报告质量金标。

分工先串行冻结16项最小离线报告闭环，再A证据/B分析/C适配器并行，主代理负责共享契约/引擎/渲染/演示/回归/交付。结果、精确命令和回滚见本批次VERIFICATION.txt；不把历史通过数字冒充本轮结果。

## 运行结果边界

本轮SDK MockTransport由真正安装的SDK序列化请求，但响应来自内存handler，绝不标作供应商真实兼容或分析成绩。离线演示报告包含strength与unknown技能证据差距及advisory行动，所有finding/行动带可回查引用。报告没有强制填满所有要求，未输出的部分不代表已满足；正向语义分析目前保守限定为旧规则能支持的技能，其他问题只列unknown/待确认。


## 校验失败诊断补充（2026-11-15）

显式传rejection_sink可把完整被拒模型文本保存到独立本地诊断，而不是MatchReport；默认仍不落盘/不打印正文。完整原文、已解析草稿和逐claim expected/actual关系见docs/STAGE4_PROMPT_V2_CANDIDATE.md。FileRejectionSink仅新建私有目录与文件，写入失败停批次。

```python
from job_radar.report_diagnostics import FileRejectionSink
sink = FileRejectionSink('/absolute/new-private-diagnostic-directory')
report = generate_match_report(
    request, retrieval, document, report_id='local-report-with-rejection',
    generated_at=generated_at, client=client, rejection_sink=sink,
)
```

当前SYSTEM_PROMPT/PROMPT_VERSION仍v1；v2是独立候选，待用户确认才切换和单独重跑S1/J1。历史被拒草稿未保存，合成反例复现不冒作历史重放。真实样本既有一次请求费用¥0.00645216保留，本次修复离线不发请求。


## 已确认提示词v2激活（2026-11-15）

SYSTEM_PROMPT现采用已确认完整v2文本，PROMPT_VERSION=match-analysis-prompt-v2。新生成仅接受v2 prompt/version；读取旧MatchReport继续接受v1且不重标。输出草稿schema/证据规则/原排序不变。原单技能subject与要求类别说明已明确，完整文本见docs/STAGE4_PROMPT_V2_CANDIDATE.md。

本批只授权S1/J1一次：AnalysisConfig(top_n=1)，客户端max_attempts=1、90秒、零重试，显式FileRejectionSink保留完整失败草稿。预算上调¥1.00，已有支出/失败预留不归零；上限预留先检查，超过即不再发送。无需也不调用其他岗位；具体结果以本次交付数值审计为准。
