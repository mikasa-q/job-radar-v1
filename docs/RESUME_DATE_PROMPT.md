# 1C-2 日期提示词 v2

实施日期：2026-11-10（Asia/Shanghai）。前次实施阶段仅增强提示词、合成离线验证，无子代理、无真实生成、无密钥读取；本次新授权真实验证见末节，首份golden_mismatch后停止。

## 改动

`resume_structure.PROMPT_VERSION`：`resume-structure-prompt-v1` → **`resume-structure-prompt-v2`**。

`SYSTEM_PROMPT`增加日期专用规则及4个正例/2个反例。`STRUCTURE_VERSION`不变，因为画像契约与组装逻辑没变；仅提示词版本变更。

- `DateFact.value`和quote保持逐字原文，不为填写normalized剥离预计/至今等状态。
- 只有value本身是纯明确年份或年月时才填写`YYYY`或`YYYY-MM`，不补日期精度。
- 预计、至今、约、待定、入学/毕业状态、范围、含日或含糊片语的normalized为null，不以当前日期替代至今。
- 日期未知时整个日期字段null，年月无效时不制造空事实、不以normalized=null掩盖无效年月。
- 正反例仅日期局部片段，不作为文档证据；实际非空日期仍要提供page/block_id/逐字quote。反例禁止作为输出。

## 可复用提示词片段

以下内容已经加入固定SYSTEM_PROMPT，不是仅供讨论的草案：

```text
日期专用规则：DateFact.value 逐字保留日期片语及预计、至今、约等状态词，不为填写 normalized 而剥离状态、改写 value 或 quote。
仅当 value 本身完整表示明确年份或年月（例如 2025、2025年、2025.09、2025年9月）时，normalized 才可填 YYYY 或 YYYY-MM；年份不补月份，月份补足两位。
value 含预计、至今、约、待定、毕业、入学等额外状态，或是日期范围、含日、含糊日期时，normalized 必须为 null；不要用当前日期替代至今，也不要从片语中截出年份冒充纯日期。
日期未知时整个日期字段为 null，不编造空的 value/evidence；年月无效时字段为 null 并可写 warnings，不通过设置 normalized=null 来接受无效年月。
以下只演示日期 value/normalized 的局部片段（不是完整响应）；实际非空日期仍须返回 page/block_id/逐字 quote 的 evidence。反例禁止作为输出，修正只作模型填写指引，应用不会自动修复响应。
<date_normalization_examples>
正例（明确年份，保留精度）：{"value":"2025","normalized":"2025"}
正例（明确年月）：{"value":"2025.09","normalized":"2025-09"}
正例（预计状态）：{"value":"预计 2029 年毕业","normalized":null}
正例（至今状态）：{"value":"至今","normalized":null}
反例（禁止补确定日期）：{"value":"预计 2029 年毕业","normalized":"2029"}；应保留同一 value 并填写 normalized=null。
反例（禁止给年份补月份）：{"value":"2025","normalized":"2025-01"}；应保留同一 value 并填写 normalized="2025"。
</date_normalization_examples>
```

完整提示词仍通过 `from job_radar.resume_structure import SYSTEM_PROMPT` 复用。`build_structure_request`携带v2版本；APINebula适配器的原有固定提示词/版本匹配检查自然使用新常量，拒绝旧版本或替换后的提示词，不修改适配器源码。

## 不变项

1B的`DateFact.explicit_date_only`、逐字事实校验、引用/语境校验不放宽；模型坏响应仍失败，不自动清空normalized、不重试、不修复JSON、不规则兜底。

本地ResumeDraft schema和发给供应商的strict schema、黄金样本和既有测试不变；APINebula/gpt-6.1-sol/low、90秒、SDK、12000本地输入分配、8192总输出token、3生成/零重试/零计数API、单次US$0.0530088预留和US$0.20总预算、依赖及数据库保持。

本轮新增提示词会增加输入长度，但未提高上限。三份内置合成样本的本地输入分配（完整JSON UTF-8字节量+1024模板余量）分别为**8986 / 8479 / 8520**，均≤12000；它不是精确计费token。

## 离线验证

```bash
.venv/bin/python -B -m pytest -q tests/test_resume_structure_date_prompt.py
.venv/bin/python -B -m pytest -q
.venv/bin/python -B examples/resume_structure_apinebula_synthetic.py
```

新增31项全合成测试：

- 从实际prompt提取并验证4正例/2反例。
- 13种正确手写日期响应（年份、年月、预计、至今、范围、含日等）保留原value、quote和规范值通过。
- 9种不一致规范值仍报`schema_error / draft.education[0].end_date / date_normalization_mismatch`，只调用假客户端一次。
- 未知日期保持null，无效年月仍失败；示例没有进入原文资料，原文不插入固定prompt。
- 固定SDK配MockTransport验证新提示词真正进入请求，90秒/low/预算/零重试不变；3样本仍在本地上限内，旧prompt/version在HTTP前被拒绝。

专项31、完整693（原662+新增31）通过；独立副本回滚恢复v1及原662。回滚只撤销本轮路径，90秒和已有脱敏诊断保留。

**离线用的是手写响应与内存HTTP，不是模型提取成绩；本轮没有证明真实模型已经遵守v2。** 先前单次真实诊断仍为end_date/date_normalization_mismatch，不改写为成功。真实合成验证及调用预算留待用户另行确认；本轮没有读取auth.json或复用旧密钥。

## 参考

[OpenAI官方提示词文档](https://developers.openai.com/api/docs/guides/prompt-engineering)建议提供多样的输入与期望输出示例；模型是否遵守仍需评测，不能用示例测试替代真实效果验收。


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
