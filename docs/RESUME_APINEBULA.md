# 阶段 1C-2：APINebula 合成简历结构化

用户确认供应商与模型，并确认 `reasoning.effort=low`。本步顺序执行，不使用子代理；仅使用仓库原有三组合成简历，不接个人简历、岗位库、上传、OCR或匹配。

## 配置与边界

| 项目 | 固定值 |
|---|---|
| 供应商 / 地址 | APINebula / `https://apinebula.ai/v1` |
| 模型 / reasoning | `gpt-6.1-sol` / `low` |
| SDK | `openai==3.49.0`；`jiter==0.18.0`、`sniffio==1.3.1` |
| SDK重试 | `max_retries=0`，应用无重试，无切模型/接口兜底 |
| 请求 | Responses API，严格JSON Schema；`tools=[]`、`store=False`、`stream=False`、`truncation=disabled` |
| 上限 | 本地输入分配≤12000；`max_output_tokens=8192`含推理；块≤128KiB/1000个；结果JSON≤128KiB |
| 超时 | 90秒传输超时和单次处理截止检查；不声称可强杀进程或精确墙钟上限 |
| 次数 | 一个客户端实例最多3次生成；计数API请求为0 |
| 预算 | 应用预留总额US$0.20；失败占次数和全部预留，usage不明立即停止 |

固定HTTP客户端禁止重定向，`trust_env=False`避免环境代理改变目的地；SDK忽略环境中的自定义base URL和组织。仅显式构造客户端才读取环境变量；导入模块不读密钥、不联网。客户端共享锁串行化预算检查和调用。

`store=False`是请求参数，不是对第三方服务保留策略的证明。

## 文件与调用链

- `src/job_radar/resume_structure_apinebula.py`：固定SDK客户端、请求复核、严格Schema转换、本地计数分配、预算、HTTP响应处理和纯数字审计。
- `tests/test_resume_structure_apinebula.py`：真实固定SDK配合 `httpx2.MockTransport` 的纯内存合成测试，不连接socket。
- `examples/resume_structure_apinebula_synthetic.py`：默认离线；显式 `--live` 后仅允许内置三组合成样本，不接受任意简历路径。

继续复用1C-1的 `structure_resume` 和1B逐事实证据校验。模型仍只拥有六键；ID/版本/来源/人工状态由本地维护。规则只做差异对照，不合并、不修复输出。额外字段、缺引用、伪造quote、否定/计划语境门禁仍按原逻辑失败。

### 严格Schema转换

本地模型含可省略字段，而供应商strict格式要求全部属性出现在required中。适配器生成独立的schema：递归补全required、`additionalProperties=false`，移除default/title；原有nullable、enum、引用和约束保留。未知标量用null、列表用[]，不修改1B/1C-1模型。响应仍由原本地模型和引用链重新验证，strict响应不等于语义正确。

## 本地计数，不发计数API

用户更正：不调用 `/responses/input_tokens`，仅计3次真实生成。最终实现不含该endpoint调用，也不安装tokenizer或下载编码表。

本地门禁值 = **包含模型、prompt、strict schema、blocks、reasoning的序列化请求UTF-8字节数 + 1024模板余量**。超过12000直接在HTTP前停止，不截断。

这是保守资源分配，不是供应商精确计费token计数；第三方可能有额外隐式模板，1024也不是经证明的上界。使用usage复核实际输入≤12000、总输出≤8192和total一致；超限停止后续调用，但不宣称能追溯阻止已经发生的超额计费。若需要精确、事前的token保证，须另行验证该路由的tokenizer/模板。

实际生成带完整输入，不在两次请求间传输不一致的schema。

## 费率、缓存与预算

用户提供的令牌分组：动态倍率0.39、standard；每百万token人民币实价：输入0.78、输出3.90、缓存读0.039、缓存创建0.975。该信息是用户确认的分组信息，不冒充后台账单核验。

预留不依赖缓存优惠；为覆盖供应商可能将缓存创建另计的情况，输入按 `0.78+0.975=1.755` 预留：

```text
单次：12000 × 1.755 / 1M + 8192 × 3.90 / 1M = ¥0.0530088
三次：¥0.1590264
```

美元预算继续为0.20；本轮另以 **每¥1占用US$1预留额度** 的保守分配保留美元预算余量。这里不是汇率换算、不采用供应商名义美元credit等于真实美元的推断，不增加人民币或美元的消费额度。`RateCard.usd_reserve_per_unit`标明此预算分配；CNY金额和USD预留分别记录。

审计的 `reported_generation_cost_ceiling_units` 将usage乘上述保守价，不是实际扣款；缓存命中可能使实际账单更低，动态分组价格变更仍须在新运行前核对。不对账户级支出作保证。应用预算及次数是单进程/客户端实例级，重复启动是新批次，需人工再次核对，不能当全账户限额。

任何认证/限流/服务错误、未知usage、模型不符、未完成状态均停止当前批次；SDK不自动重试。语义/引文校验失败后，演示立即结束，不把失败改写成成功。

## 密钥生命周期

适配器只从进程环境 `OPENAI_API_KEY` 读取，不读配置文件、不接受代码内硬编码密钥、不打印key。用户另外明确指定：仅真实调用进程从 `~/.codex/auth.json` 的同名字段读取并临时放环境，finally移除，进程结束释放；本轮一次性执行器在项目外完成该步骤。

密钥不进入源码、演示参数、审计、异常消息或交付文件。例外/HTTP错误只返回固定code或状态码。日志不记录原始响应正文；数字usage和本地校验结果用于验收。

## 运行

项目根目录：

```bash
.venv/bin/python -B -m pytest -q tests/test_resume_structure_apinebula.py
.venv/bin/python -B examples/resume_structure_apinebula_synthetic.py
.venv/bin/python -B -m pytest -q
```

默认演示显示 `mode=offline; real_requests=0; scripted_responses_not_model_quality`；三条`result=validated`只是预设响应与证据链通过，不是模型成绩。

需要单独运行真实合成批次时，先在该进程环境设置密钥，再使用已核对的分组价：

```bash
.venv/bin/python -B examples/resume_structure_apinebula_synthetic.py --live \
  --group user-confirmed-standard-0.39 --unit CNY \
  --input-rate 0.78 --output-rate 3.90 --cache-create-rate 0.975 \
  --usd-reserve-per-unit 1
```

此命令每次启动会建立新批次；不是建议在失败后直接重跑。不要在聊天或命令行参数中填密钥。

可复用提取提示词继续使用 `resume_structure.SYSTEM_PROMPT`：它将简历块作为资料、要求六键及逐字引用，并排除计划/否定语境；固定prompt不插入简历原文。调用示例：

```python
from job_radar.resume_structure import structure_resume
from job_radar.resume_structure_apinebula import APINebulaStructureClient, RateCard

rates = RateCard("confirmed-standard", "CNY", "0.78", "3.90", "1", "0.975")
client = APINebulaStructureClient(rates)  # OPENAI_API_KEY仅环境读取
try:
    result = structure_resume(synthetic_document, client=client, resume_id="synthetic-001",
                              timeout_seconds=90.0)
finally:
    client.close()
```

## 先前30秒批次验证记录（保留历史）

离线专项95项、原524项加本步合计619项通过，旧1A/1B/1C-1及既有解析器未改。默认离线演示三组合成响应全部通过字段预期和引用链。

真实批次：按用户授权在单一进程临时读取指定key；第一份合成简历在30秒配置下得到 `client_timeout`，计数API为0、生成尝试1次。本批次立即停止，未重试、未发后两份、未延长超时或换接口/模型。usage未取得，实际扣款未知，预留US$0.0530088不释放。进程finally移除密钥环境，未将密钥写入任何交付物。

当时原始审计的累积已知金额为0，**只表示没有获得usage，绝非实际零费用**；最终审计字段在存在未知usage时明确返回 `null` / `usage_known=false`。保留原始执行记录不改写。

此次尚未获得一份真实模型结构化结果，不宣称模型准确率或该网关strict兼容性已通过；离线通过只证明固定SDK和本地协议行为。要再次真实运行，须另行核对本次是否扣费和路由延迟，并由用户确认新的尝试预算，不自动续跑。

完整命令、退出码、原始输出与副本回滚证据保存在交付 `VERIFICATION.txt`。

## 参考

- [APINebula接入](https://docs.apinebula.ai/docs/cli/codex)与[价格](https://apinebula.ai/en/fee)。
- [OpenAI官方模型文档](https://developers.openai.com/api/docs/models/gpt-6.1-sol)与[结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)。第三方实际兼容性以本轮观察为准。


## 90秒调整（2026-11-10）

用户授权将超时从30秒改90秒，重跑首份合成简历；**只有首份完整通过本地证据链及黄金预期后，才顺序发后两份**。任一失败即停止，不因HTTP成功就跳过语义/引用校验。新批次最多3生成、每次零重试，无计数API；固定APINebula/gpt-6.1-sol/low、12000输入分配、8192总输出tokens、费率、每次US$0.0530088预留、批次最多US$0.1590264预留和US$0.20总预算不变。旧批次未知扣费及预留不作退款处理，两批记录分开。

实现点：`resume_structure_apinebula.TIMEOUT_SECONDS=90.0`覆盖HTTP客户端、SDK默认值、请求上限、剩余时间与响应读取检查；**演示调用 `structure_resume(..., timeout_seconds=TIMEOUT_SECONDS)` 显式传递90秒**。仅改适配器常量而不改调用方会被1C-1默认30秒截住，本轮测试专门覆盖该链路。中性离线层1C-1原有30秒默认保留，其他使用者可显式选择更短超时；不修改其契约。

新增10项合成测试，覆盖30/31/60/90秒请求、旧30秒之后90秒之前响应成功、演示三份顺序/失败停止、第一份超时/黄金不匹配/证据失败均不发后两份，以及中性默认未改。适配器专项105项、完整629项通过；无新依赖。

密钥只在这次真实调用进程从用户指定auth.json字段读入环境，关闭客户端后finally移除；不复制auth文件、不将密钥存入源码/日志/交付物。一次性执行器仅调用演示一次，不自动重启批次。OpenAI官方Python文档仍要求显式`max_retries=0`来关闭SDK默认重试，本轮保持该值。

### 90秒批次观察结果

一次性执行器只启动了一次新批次：首份 `full_education_course_internship` 收到响应后得到 **`schema_error`**；批次耗时34.298秒（不是单独网络时长）。这次不是timeout；返回JSON通过了解析步骤，但当时未区分 `ResumeDraft` 草稿与 `ResumeProfile` 装配两处字段校验（两处都会映射成schema_error）。未记录原始模型正文或具体字段错误，本轮不推测出错字段，也不追加付费请求来获取诊断。

- 生成尝试1，零重试，计数API为0；后两份未发。
- usage：输入1430、输出1395、合计2825；本地输入分配7266（分配不是精确token）。
- 审计费用保守上界¥0.00795015（含保守缓存创建叠加），不是实际扣款；整次US$0.0530088预留保留。
- 审计 `halted=null` 只代表适配器传输/信封/usage处理没有报错；外层schema校验失败后演示已经立即停止，绝非整体成功或准许继续。
- `credential_environment=cleared`；未打印/保存密钥或auth文件。

首份未验收通过，后续不自动续跑，不静默改schema、提示词、模型或JSON修复。活动目录保留90秒实现，待用户确认如何诊断schema错误及新的验证预算。离线专项105、完整629项通过，独立副本回滚恢复原619项行为；回滚不撤销已发生的网络请求/扣费。

超时仍是SDK传输超时/程序检查，不是墙钟强杀保证。


## schema_error诊断（2026-11-10）

用户另行授权首份再生成1次，仅记录脱敏路径/规则。新增诊断区分draft/profile，保持原消息和业务校验不变。真实观察：`draft.education[0].end_date / date_normalization_mismatch`，耗时33.242秒，仍exit1；1生成0计数，无重试/后两份。usage1430输入/1395输出；单次预留不变，密钥finally清除。

此次证明结束日期的非空normalized与raw value允许规范值不一致；未记录具体字段值，不推测字面内容。预计毕业黄金预期是normalized=null，离线反例可复现相同规则，但不是旧响应取证。旧90秒批次没留字段诊断，不能据此认定旧批次也同因。完整说明、五类wire/local差异、脱敏策略和后续提示词建议见[RESUME_SCHEMA_DIAGNOSTIC.md](RESUME_SCHEMA_DIAGNOSTIC.md)。新33项/完整662通过，提示词/schema/validator/90秒/模型low/预算/依赖保持原样。


## 日期提示词v2（2026-11-10）

用户确认后已增强固定prompt的日期规则与4正例2反例，版本resume-structure-prompt-v2；保留原文/状态，纯年月才规范化，预计/至今等normalized=null。原validator/schema/90秒/模型low/费用预留/零重试/上限不改。新31项、完整693项离线通过；三份样本本地输入分配8986/8479/8520。此次未读密钥/真实调用，不把手写响应通过当模型准确率或对旧错误的修复验收。说明见[RESUME_DATE_PROMPT.md](RESUME_DATE_PROMPT.md)。


## 提示词v2真实合成验证（2026-11-10，新批次）

用户另行确认后，一次性执行器从本轮隔离副本导入当前v2代码；固定APINebula `https://apinebula.ai/v1`、`gpt-6.1-sol`、`reasoning=low`、90秒、SDK零重试、最多3生成与原预算预留。首份必须通过全部本地校验和黄金预期才继续后两份；计数仅本地。执行器有单次启动锁，失败不自动重启。

### 观察结果

首份 `full_education_course_internship`：

- `structure_resume`已经返回，因此本次通过草稿/画像schema与本地证据链校验；没有`schema_error`。
- 针对先前错误，另做只输出布尔值的日期断言：`field_present=true`、`raw_value_preserved=true`、`normalized_is_null=true`。它要求字段保留黄金样本日期原文且normalized=null，不记录实际字段文本。
- 现有runner的黄金形状比较得到 **`golden_mismatch`**，exit1立即停止。后两份`unknown_scalars`、`no_positive_evidence`未调用。
- 黄金比较包含技能列表、学校、在读状态、项目性质、工作类型、摘要及经历数量。现有runner只输出一个不匹配码，未保存具体字段差异、实际shape或原始响应；**具体不匹配字段和成因尚未定位**，不推测遗漏、顺序或摘要改写等原因。
- 本次结束日期正确且未出现schema_error，不代表所有日期/其他样本已修复，也不代表完整真实合成验收通过；黄金预期仍失败。旧30秒/90秒/单次诊断批次保持原结果，不追溯改写。

### 调用与费用

- 首份/批次耗时30.290秒（包含本地处理，不是单独网络时长）。
- 生成尝试1，SDK/应用零重试，计数API0，本地计数检查1；后两份未发。
- usage：输入1855，输出1320，合计3175；本地输入分配8986（完整JSON UTF-8字节+1024，非精确计费token）。
- 用户确认费率推算的保守上界 **CNY0.008403525**，包含输入费和缓存创建费保守叠加，不是实际扣款或账单。
- 整次保留 **US$0.0530088** 预算额度；本批次US$0.20上限、单次预留/三次最多US$0.1590264不变，失败不退预留；旧批次费用和预留不回收。
- `halted=null`仅说明适配器传输/响应信封/usage无错误；外层黄金比较失败已经停止，绝非整体成功。
- 密钥只在该真实进程从用户指定auth.json的OPENAI_API_KEY字段临时读取并放环境；关闭客户端后finally清除，进程已退出。日志只记录固定状态码、上述布尔断言及数字usage，不保存密钥/原始模型正文。

### 收尾与下一步

本轮只更新NOTES和日期提示词/APINebula/schema诊断文档，源码、prompt-v2、schema/validator、黄金样本、测试、SDK/依赖、预算及数据库保持原样。基线、修改版及独立回滚副本均运行完整693项离线回归；完整字面输出、命令、退出状态与哈希保存在本批次`VERIFICATION.txt`。

下一步先离线为黄金比较补充有界脱敏的字段路径/规则诊断（不记录字段值），保留现有判定；新的真实生成次数与预算另行确认。本轮不追加生成、不改黄金预期来通过、不放宽业务校验或自动修复输出。文档回滚只恢复本轮4份文档，不撤销已经发生的API调用或费用，代码仍为v2。


## 后续黄金差异修复与prompt-v3（2026-11-10）

用户要求定位后直接修复，离线过再真实首份，首份过才后两份。旧v2响应未保存，具体旧差异未知；合成复现确认字段跨度/技能顺序和汇总约定没有被旧prompt明确。保留黄金expected与原精确比较，不接受等价放宽；prompt-v3补顶层技能汇总/首次顺序、学历状态分离、自述正文边界，runner增加最多8条固定path/rule黄金差异诊断，不输出原值、不改响应。schema/validator/适配器/90秒/模型low/SDK/预算不改。

新26测试、专项57、完整719先通过；随后新真实批次首份31.536秒通过才后两份9.892/3.398秒，3/3通过原黄金和本地schema/证据链，总44.828秒exit0，review_status仍unconfirmed。日期存在/原文保留/normalized=null三项true。生成3/计数API0/零重试，usage6332输入+1723输出=8055；保守费用上界CNY0.017832360非账单，全部US$0.1590264预留保留，密钥finally清除。旧失败记录不追溯修改；本次没有实际golden失败，新诊断只经离线反例验证。

源码中完整新增片段、证据边界、三份结果和回滚见[RESUME_GOLDEN_DIAGNOSTIC.md](RESUME_GOLDEN_DIAGNOSTIC.md)。三样本本地分配10386/9879/9920<=12000（非精确计费token）。活动保留v3，独立回滚恢复v2/原693项；本轮不再发请求，不宣称一般准确率。
