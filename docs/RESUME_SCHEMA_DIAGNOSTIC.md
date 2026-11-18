# 1C-2：schema_error 定位记录

日期：2026-11-10（Asia/Shanghai）。本轮无子代理，仅合成样本。用户另行明确授权首份再生成一次，记录脱敏字段路径/规则码；没有后两份或重试。

## 1. 真实观察结论

本次单次真实诊断输出：

```json
{
  "stage": "draft",
  "issues": [
    {
      "path": "draft.education[0].end_date",
      "rule": "date_normalization_mismatch"
    }
  ]
}
```

**本次失败在第一条教育经历的结束日期，由 `DateFact.explicit_date_only` 拒绝；不是SDK超时、JSON解析、供应商信封或后续文档引用匹配失败。** 当前规则的充要条件是：`normalized` 非null，且不等于 `_explicit_date(value)` 返回的允许规范值。原值、规范值与引文未被记录，不推测模型具体填了什么。

首份合成黄金样本结束日期预期为：

```json
{"value":"预计 2029 年毕业","normalized":null}
```

该字段还包含原有evidence（以上仅展示日期部分）。预计/至今等状态不是一个已经明确完成的纯年月，不从这种复合状态值补规范日期。

本地合成反例将该预期的normalized改为`"2029"`，可以精确复现同一路径/规则；**这是人为反例，不代表真实模型一定返回了该字面值**。另一种不一致的纯年月规范化同样会触发此码。

先前90秒批次只留下`schema_error`，没有响应或字段错误。此次新诊断定位的是**本次响应**；没有证据证明旧响应一定同因。此前文档直接把旧错误说成ResumeDraft失败过于确定，本轮更正：旧代码还可能在ResumeProfile装配时产生同码。

## 2. 为什么 strict schema 仍有 schema_error

调用链：

```text
Responses HTTP/信封/usage/模型检查
→ completed text
→ 严格JSON解析
→ ResumeDraft.model_validate             # schema_stage=draft
→ 本地组装ResumeProfile.model_validate   # schema_stage=profile
→ 1B文档引用校验                         # evidence_error
→ 黄金字段对照
```

本地`model_validator`执行的跨字段规则不会自动变成JSON Schema表达式。wire strict schema约束键、类型、枚举、nullable、pattern等，不执行Python自定义逻辑。

本轮用5种全合成反例验证“满足当前wire schema的所有使用关键字，但本地拒绝”：

| 合成变化 | 本地规则码 |
|---|---|
| 预计毕业被填非null规范日期 | `date_normalization_mismatch` |
| 学历事实被改写成引文没有的字串 | `fact_value_not_quoted` |
| 引用page与block_id页号不符 | `evidence_page_mismatch` |
| 经历全null/空列表 | `empty_entry` |
| warnings含空白字串，草稿通过但画像失败 | `blank_warning`（profile阶段） |

测试中仅实现当前schema已使用关键字的断言求值器，遇到未知关键字即失败；不是新增生产JSON Schema库或声称通用schema实现。未安装任何依赖。

OpenAI官方结构化输出文档说明结构化输出遵循提供的JSON Schema；此处仍应保留本地业务校验。[官方文档](https://developers.openai.com/api/docs/guides/structured-outputs)也不能代替第三方路由实际行为证据。

## 3. 已落地的诊断改动

- `ResumeStructureError`新增 `schema_stage`、`schema_issues`，原 `code`、`evidence_code`、`path`、`str(error)=="schema_error"` 兼容。
- `SchemaIssue`是冻结dataclass，仅`path`/`rule`；诊断最多8条、路径长度上限256、仅固定模型字段名/有界下标。
- 未知/额外字段名改成`<unknown_field>`，未知错误类型/自定义消息归类固定通用码；不复制模型输入中的字段名、原文、quote、异常msg/ctx/URL。
- 两个校验点分别标记`draft`和`profile`。原异常链仍用`from None`抑制，不输出Pydantic原始ValidationError。
- 合成演示只在schema_error时额外输出脱敏`schema_diagnostic`，仍立即返回exit1，不修复/删字段/兜底。
- 新增33项纯合成测试：具体路径规则、两个阶段、输入脱敏、未知字段名脱敏、8条上限/不可变、wire/local差异、单次失败停止、既有配置不变。

**本轮未改业务validator、提示词、wire schema、黄金样本、90秒、模型、reasoning、预算、重试、依赖。** 定位完成不等于本次提取通过；实际仍exit1，无画像交付。

## 4. 复现及后续建议

离线测试：

```bash
.venv/bin/python -B -m pytest -q tests/test_resume_structure_diagnostic.py
.venv/bin/python -B -m pytest -q
```

只读复用诊断字段：

```python
try:
    result = structure_resume(synthetic_document, client=client, resume_id="synthetic-001",
                              timeout_seconds=90.0)
except ResumeStructureError as error:
    if error.code == "schema_error":
        diagnostic = {
            "stage": error.schema_stage,
            "issues": [{"path": item.path, "rule": item.rule}
                       for item in error.schema_issues],
        }
```

下一步建议先确认提示词补充：明确`DateFact.value`保留逐字原文；仅纯明确年月规范化，预计/至今/带状态或不确定日期的`normalized=null`，并加入一正一反示例。保持1B校验不放宽，先离线测试，再按新确认预算做真实验证。该提示词修改**尚未执行**，不为失败响应自动清空normalized后再验收。

## 5. 费用和凭证记录

- 真实生成只1次；计数API0，零重试，不发后两份。
- 单次批次耗时33.242秒；usage输入1430、输出1395、总2825，本地输入分配7266。
- 保守费率推算上界¥0.00795015不是实际账单；整次US$0.0530088预留保留。旧批次未知费用不回收、不改写。
- 凭证仅本次真实进程按用户指定从auth.json同名字段临时放环境，finally清除；原始key/原始模型正文不入日志/源码/交付。
- 离线专项33项、完整662项；独立副本回滚应恢复此前629项。完整原始命令/输出/exit与哈希见交付VERIFICATION.txt。


## 6. 后续日期提示词v2已实施（2026-11-10）

用户确认后已将prompt版本升级v2，明确纯年月才normalized、预计/至今等状态为null并保留原文，补4正例2反例。只增加指令，不改DateFact/证据校验/JSON schema，不自动修复坏响应。新31项合成测试、完整693通过，三样本本地输入分配8986/8479/8520均<=12000。本轮真实生成0、密钥读取0；先前诊断记录保持原样，真实效果尚待另确认。详见[RESUME_DATE_PROMPT.md](RESUME_DATE_PROMPT.md)。


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
