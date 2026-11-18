# 1C-2 黄金差异诊断与字段边界修复

日期：2026-11-10。用户要求定位后直接修复，先离线回归，再首份真实验证，首份过才后两份；不放宽校验，黄金本身有问题须先说明。本轮无子代理，仅内置合成文本。

## 1. 已定位的缺口与证据边界

前次prompt-v2真实批次只留golden_mismatch、schema/证据链通过和结束日期三项布尔值，未保存原始响应/实际shape/差异字段。**旧响应具体不匹配字段仍未知**，本轮不将合成复现或新成功冒充旧响应取证。

代码审计发现：现有SYSTEM_PROMPT要求逐字原文/摘录自述/规范技能，但没有明确顶层技能与technologies的汇总关系、技能顺序、学历与就读状态的片段边界、自述标签及句末分隔标点边界；黄金比较却是严格字串/列表顺序相等。

合成变体在原本地校验下复现：

| 合成变化 | 本地schema/证据链 | 原精确黄金 | 新诊断path/rule |
|---|---|---|---|
| 自述正文多保留句号 | 通过 | 失败 | golden.summary / text_span_mismatch |
| 三个正向技能倒序 | 通过 | 失败 | golden.skills / list_order_mismatch |
| 在读状态包含邻接学历词 | 通过 | 失败 | golden.education_status[0] / value_mismatch |
| 仅technologies有技能、顶层skills为空 | 通过 | 失败 | golden.skills / list_length_mismatch |

黄金中学校/状态/课程项目/实习/摘要和技能均有原文支持。本轮**黄金fixture、expected内容和精确比较不改**，不增加允许形式、不忽略标点/顺序、不取集合代替原比较；先用明确指令对齐该精确提取约定。它是prompt契约缺口修复，不是已经证明旧模型填错的字段修复，也不是业务validator补丁。

## 2. 修复

### prompt-v3

`PROMPT_VERSION`由resume-structure-prompt-v2升为resume-structure-prompt-v3。只加字段边界与完整性指令，既有日期专用规则和4正2反例保留。

可复用新增指令（完整版本仍通过resume_structure.SYSTEM_PROMPT导入）：

```text
字段边界与完整性规则：在原文明确时填写对应字段，不因同一事实已有其他引用就省略。
顶层 skills 汇总所有正向技能，包括项目和工作中的 technologies；后者仍分别填写，不互相替代。按文档 page/block 顺序及块内技能首次出现顺序返回，规范名相同只汇总一条，evidence 可保留多个原文位置；计划、否定及招聘要求仍排除，不补熟练度。
education.degree 仅摘录学历层次，education.status 仅摘录就读状态，不把两者合成一个值；学校、项目性质和工作类型同样只摘录对应字段，不包含邻接标签或其他字段。
summary 仅摘录明确自述标签后的正文片段，去掉标签和用作分隔符的句末标点，不改写正文、不增删正文中的标点；没有正向自述时为 null，计划/否定不是自述摘要。
```

局部例子使用“硕士在读/注重复现/Go和SQL”，没有内嵌黄金学校、摘要或预期输出。应用不剥离标点、重排技能、补字段、删字段或修复JSON；模型输出原样交给原本地validator和原golden精确比较，失败即停。

提示词UTF8为4433字节；三样本本地分配10386/9879/9920，均<=12000。分配按完整请求JSON UTF8+1024，不是精确计费token；8192总输出上限不变。

### 脱敏黄金诊断

合成runner在`actual != case['expected']`的原分支中，额外输出`golden_diagnostic`，然后仍exit1停止；不改变真值判断或让诊断决定通过。

- 只允许固定七个golden字段路径，最多8条，索引最多0..7；不把任意字段名/值拼入输出。
- 固定规则包括text_span_mismatch、presence_mismatch、list_length_mismatch、list_order_mismatch、value_mismatch、count_mismatch、type_mismatch、shape_keys_mismatch。
- list_order_mismatch的Counter仅分类诊断，**不是集合相等即通过**；列表仍逐项精确比较。
- 不输出actual/expected值、原文、quote、模型正文、哈希或异常消息。schema诊断与原失败路径照旧保留。
- 诊断不会自动补发API、重试或放行后续样本。

## 3. 离线验证先于真实请求

新增26项合成测试，覆盖原三份黄金精确通过、8类局部合法但golden失败的变体、字段路径/规则、原值与额外字段名不泄漏、8条上限、输入不修改、类型错误、schema先失败和三份顺序门禁。

原日期测试只把prompt版本断言从v2改v3，其31项日期规则/正反例/坏响应拒绝保留。专项57项通过；完整**719 passed, 5 warnings in 16.99s**后才读取临时凭证执行真实批次。原693项基线通过；活跃修改版719项和独立回滚v2/693项另行回归并记录于VERIFICATION.txt。

```bash
.venv/bin/python -B -m pytest -q tests/test_resume_structure_golden_diagnostic.py tests/test_resume_structure_date_prompt.py
.venv/bin/python -B -m pytest -q
.venv/bin/python -B examples/resume_structure_apinebula_synthetic.py
```

最后一条默认offline，仅预设响应/内存HTTP，不是追加真实请求。

## 4. 本轮真实结果

一次性执行器有启动锁、719回归通过检查和六个关键文件哈希检查，仅从本轮副本导入v3代码。首份完整通过本地校验、黄金精确比较和结束日期三项布尔断言后，才进入后两份；所有review_status仍unconfirmed。

| 合成样本 | 结果 | 耗时秒 | usage输入/输出/合计 |
|---|---|---:|---|
| full_education_course_internship | validated | 31.536 | 2200 / 1322 / 3522 |
| unknown_scalars | validated | 9.892 | 2061 / 370 / 2431 |
| no_positive_evidence | validated | 3.398 | 2071 / 31 / 2102 |

批次**3/3通过，exit0，总44.828秒**（包含本地处理，不是单独网络时长）；schema/证据链/原黄金全部通过。结束日期存在/原文保留/normalized=null三项true。零重试、生成3、计数API0、本地计数3。没有实际失败触发新黄金诊断，本轮诊断码的验证来自离线反例，不伪造真实差异。

- 固定APINebula https://apinebula.ai/v1 / gpt-6.1-sol / low / 90秒；openai3.49.0/jiter0.18.0/sniffio1.3.1及原适配器源码不变。
- 输入6332、输出1723、总8055；输出usage不额外扣除推理token。
- 用户确认standard分组0.39实价按每1M CNY输入0.78、输出3.90、缓存读0.039、缓存创建0.975。保守费用上界**CNY0.017832360**（输入加缓存创建叠加），不是实际账单扣款。
- 保留本批次全部预留**US$0.1590264**，每次0.0530088；US$0.20上限不变；该分配不是汇率。旧批次费用/预留/失败结果不改写。
- 密钥只在真实调用进程从用户指定auth.json字段临时读入OPENAI_API_KEY环境，关闭客户端后finally清除，进程已退出。密钥/原始响应/实际字段值不写日志/代码/交付；没有复制auth文件。

## 5. 保持与回滚

本地DateFact/TextFact/语境/引用validator、ResumeDraft/ResumeProfile schema、wire strict schema、黄金fixture及原精确比较不变。适配器模型/low/90秒/本地计数/预算/重试/依赖/数据库保持，不入库、不提交、不建分支。

交付包含9路径（7现有+2新增），原始260文件哈希保留；独立副本回滚恢复v2及原693测试，原有schema诊断/90秒/SDK留存，活动目录保留v3。ROLLBACK.sh仅恢复本轮文件，不撤销API调用/费用。完整命令、输入、字面输出/exit、保护哈希和guard/幂等/补丁重建见同批次VERIFICATION.txt。

本次通过仅是三份合成样本、当前模型/参数的观察，不是一般简历准确率或未来每次稳定通过的证明。批次已结束，不追加付费调用；下一功能小步另行确认。

## 参考

[OpenAI官方结构化输出文档](https://developers.openai.com/api/docs/guides/structured-outputs)：schema约束仍需结合应用语义校验与评测；本轮第三方路由的行为以本次结果为准。
