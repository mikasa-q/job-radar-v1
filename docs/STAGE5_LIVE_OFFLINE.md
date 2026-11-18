# 阶段5真实适配：离线实现与验收说明

## 本次交付含义

用户确认的是契约增补、单一持久收费账本、画像/报告供应商边界及**先离线验收**。本次默认 `llm_disabled` 与 `hybrid_not_enabled` 继续生效；只有程序化注入 MockTransport 的合成测试会通过模拟发送链。没有真实供应商上线、真实小样本成绩或新的费用结论。

权威增补见同目录 `STAGE5_LIVE_CONTRACTS.md`；旧 `STAGE5_CONTRACTS.md` 与 `STAGE5_OPENAPI.json` 不覆盖，新快照 `STAGE5_OPENAPI_V1_1.json` 单独冻结。完整实施依据保留在：
`/Users/thc/.codex/visualizations/2026/11/16/01a1e44a-3bb4-72e2-a459-d45b853efeac/jobradar-stage5-live-plan/PLAN.md`。

## 组件边界

- 领域层仍调用原 `structure_resume`、`generate_match_report`、BM25及引用/语义校验；不替换黄金预期、不修复被拒JSON、不重排结果。
- 新 billing 段和资源/幂等共用私有原子 journal，画像/报告共享额度、次数和全局halt；审批由本地显式管理操作写入，HTTP无启用/预算/费率开关。
- 独立供应商边界复用原完整wire构造约定，发送前持久预留和send-intent，usage落账后才发布领域结果；当前只接受 MockTransport，绝不因存在环境变量而启用真实传输。
- 原结构化画像的 SkillComparison 进入新响应；规则画像仍 comparison=null；未知信息、材料来源和 review_status 不自动晋升。
- 报告显示来源必须显式声明；模拟链路显示模拟，不将provider名字或completed状态当真实调用证明。历史renderer和历史报告不改。

## 验收矩阵

1. **契约**：原路由、operationId、请求/信封/领域字段不变；仅execution_mode、Ready.real_llm、comparison与info允许指定差分，旧/新快照都保留。
2. **默认门禁**：无程序化测试注入时两LLM端点固定503且不创建计费尝试；Hybrid即使允许fallback也503。
3. **完整模拟链路**：合成PDF/临时证据库/独立索引 → 模拟画像 → 原匹配 → 模拟单岗报告 → JSON/Markdown查询；调用次数、hash、usage、费用与资源一一对应，下载不重发。
4. **共享约束**：并发画像/报告串行；同键重放一次发送，换key/换根/重启不重置；当前模拟报告仅接受top_n=1，多岗请求在发送前拒绝，不以一次HTTP掩盖多次生成；批次2次与全局9次共同限制。
5. **故障窗口**：预留落盘前、send-intent后、响应到达后、usage入账时、结果发布时分别故障；磁盘失败、未知usage、拒绝/超时/非法JSON/模型不符均保留费用占位并停止后续任务。
6. **预算**：保留旧actual .05320458/reserve .6829056；费用与完整reserve双占用；每次还为pending usage留空间；Decimal精确边界/非法数值与费率变化被覆盖。
7. **恢复/回滚**：事件链/金额绑定校验；单实例锁；缺账本不自动seed；发送不确定不补跑；独立代码回滚恢复原hash但不改变非空虚拟收费账本金额、次数、halt和事件。
8. **回归**：旧API、离线rules/BM25/report not_run链路与阶段1–4全量测试保持；临时数据库以外的真实数据不写。

测试命令由主代理本批 `VERIFICATION.txt` 记录精确执行与字面结果；不沿用代理中途或旧批次计数。本次工件应包括独立修改包、差分、精确验证记录和可执行回滚脚本。收费运行根不属于可回退代码delta。

## 真实首笔之前

先由用户检查离线/MockTransport、完整回归和账本不回退证据，再单独放行真实backend与首个画像检查点。待批准样本只含S1画像一次、其后人工确认再J1报告一次；模型gpt-6.1-sol/low，报告v3，画像12000/8192、报告20000/8192，串行零重试首失败停。任何body超限或结果改变先停下重新冻结，不挪用余量扩样本。

本轮不读取真实凭据，不发真实请求，不跑真实小样本，不运行旧阶段4批次；成功的模拟输出不等于真实质量验收。

## 可复用接续提示词

```text
先读NOTES.md、docs/STAGE5_LIVE_CONTRACTS.md、docs/STAGE5_LIVE_OFFLINE.md及本批VERIFICATION.txt。
区分已完成离线适配和未放行真实backend，不依据MockTransport完成状态宣称真实模型上线。
历史actual/reserve不重置，画像/报告共用账本与最多9次硬上限；候选真实批次最多2次。
若用户另行放行真实首笔，先检查账本、样本与完整wire hash、费率、token分配和恢复状态，
只发S1画像一次后停给用户人工检查；未获得后续确认不发J1报告。
代码回滚不得回退运行账本；Hybrid继续关闭；阶段1–4核心、数据库和历史报告保持。
```
