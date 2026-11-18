# 阶段5契约增补 v1.1：持久收费与真实适配边界

本增补经用户确认的真实适配方案进入**离线实施/模拟验收**，不是发送真实请求的放行。原 `STAGE5_CONTRACTS.md`、`STAGE5_OPENAPI.json` 保留为 v1.0 冻结历史；v1.1 独立快照为 `STAGE5_OPENAPI_V1_1.json`。应用元数据版本为 `1.1.0`，`/v1` 路径与 `jobradar-api-v1` 信封家族保持。

## 1. 新旧契约兼容矩阵

| 位置 | v1.0 | v1.1 | 旧行为 |
|---|---|---|---|
| `Resource.execution_mode` | pdf/rules/bm25_rules/offline | 增加 llm | 原四值保持；不得把真实调用标作 rules/offline |
| `Ready.real_llm` | 仅 false | boolean，默认 false | 只表示本地授权/账本/发送门禁就绪，不付费探测上游 |
| `ProfilePayload.comparison` | null | 原领域 SkillComparison 或 null | rules 仍 null；结构化画像返回原差异，不取技能并集 |
| API info | step1 标题/版本 | 离线真实适配标题、1.1.0 | 实施阶段明确区别于已上线真实供应商 |

只增加 `SkillComparison` 的 OpenAPI 定义；其余领域 schema、请求、路由、operationId、ID、信封字段、错误码和任务状态保持。严格枚举客户端需升级，不宣称新 OpenAPI 与旧快照逐字相同。

资源 kind/mode 组合为：resume/pdf，profile/rules 或 llm，match/bm25_rules，report/offline 或 llm。来源约束由本地编排核对，不由模型输出声明。成功画像和报告保持原 `review_status=unconfirmed`；任务 succeeded 不等于模型 completed。

## 2. 当前实际发送门禁

本次交付的供应商执行边界只接受程序化注入的 **MockTransport** 与合成测试凭据；默认服务没有该注入，`mode=llm` 继续返回 503 `llm_disabled`。不读取真实凭据、不构造真实传输、不发送真实请求、不运行真实小样本。Hybrid 继续返回 503 `hybrid_not_enabled`，包括 `allow_dense_fallback=true`。

当前模拟报告入口仅接受 `top_n=1`，其余值在发送前返回 `core_rejected`；HTTP 请求 schema 为兼容旧离线模式保留原范围。离线报告仍沿用原 `top_n` 行为，多岗收费执行不作为本次已完成能力。将来开放多岗时，每个岗位的生成均须独立预留/记次，不得把一个 HTTP 请求当一次生成。

本机 Uvicorn 或 TestClient 离线闭环不代表真实供应商验收；MockTransport 响应是人工脚本，不代表模型质量。将来真实 backend、S1 画像首笔放行及后续 J1 报告检查点均需单独确认，不从本次实现授权推导发送授权。

## 3. 共享账本与预算

采用既有私有运行目录、OWNER、flock 与原子 `state.json`，增补版本化 billing 段；不迁移 SQL。资源、幂等与收费事件在同一 journal 中提交，不为画像/报告建立两份额度。

账本只在显式管理初始化步骤导入一次历史种子；运行期不自动初始化。授权绑定 canonical 运行根、billing scope、种子来源/hash、到期时间和批准的完整 wire/context hash。缺失、损坏、不匹配、另一实例占锁或未批准操作均停止发送。新key/新资源/新进程不重置金额或次数；另建离线目录不继承真实调用权。

| 累计基线 | CNY |
|---|---:|
| cap | 1.00 |
| actual | 0.05320458 |
| retained reserve | 0.6829056 |
| actual + reserve | 0.73611018 |
| 剩余保守空间 | 0.26388982 |

金额使用 Decimal 字符串，拒绝浮点、NaN、无穷、负数。历史记录与费率来源不重算、不释放；它们是本地已记费用口径，不是账户账单。

新批次费率每百万输入 0.78、输出 3.90；保守叠加缓存创建 0.975。该缓存项仅用于新批次，不回灌阶段4历史报告费用。

- 画像：完整请求本地输入分配12000，输出上限8192，单次R=0.0530088。
- 单岗报告：完整请求本地输入分配20000，输出上限8192，单次R=0.0670488。
- 输入分配为完整JSON UTF-8字节+1024余量，不是精确计费token或供应商硬输入token限额。超限停止预检，不截断证据或自动扩限。
- 固定 gpt-6.1-sol/low；报告 match-analysis-prompt-v3；90秒传输超时，零SDK/外层重试，串行1，首失败全局停止，无计数API。
- 最多9次新增生成由画像/报告共享，是硬上限而非可跑满额度；本次候选小批次最多2次（S1画像一次，人工检查后J1报告一次），不是一次HTTP请求=一次生成。

## 4. 发送与恢复语义

幂等先命中原资源，不再读取父依赖或重复收费；同键异输入仍409。换key仍受批准的wire/context、批次和全局次数约束。

真实边界执行顺序为：本地校验/构造完整wire → 共享锁复核授权/预算/次数/halt → 原子持久预留R、次数+1、send-intent及未结算usage上界R → fsync文件/replace/fsync目录成功 → 单次供应商发送 → 先持久usage/原始诊断与费用 → 原领域校验 → 发布结果。

发送前预算检查含新增usage空间：`actual + retained reserve + pending usage ceiling + 2R <= cap`。usage已知后按冻结RateCard记actual、解除对应pending上界，但R永久保留；unknown usage不填零，pending保留且全局halt。画像+报告两次在分配上界成立时的双占用投影0.97622538；该投影不是账户账单封顶保证。

超时、HTTP错误、拒绝、不完整、未知/越界usage、模型不符、schema/证据失败或持久化失败均停止；有已知usage仍记费用。任务失败保留固定error，无成功payload；报告partial/拒绝正文仅在私有诊断中留存，不伪装成功报告。

发送意图落盘后的崩溃无论是否送达都不自动重发；重启保留金额、次数、pending和事件，任务interrupted，账本halt。恢复须人工对账和新的明确放行。

## 5. 回滚与不做的事项

代码回滚不回滚收费事实：actual/reserve/次数/halt/事件不降、不删；先关闭发送再回滚代码。不兼容账本版本停止加载，不将新版journal覆盖成旧schema，也不借回滚触发补跑。

本增补不改阶段1–4的PDF提取、画像/匹配/检索/报告算法、提示词或validator，不动数据库结构、历史报告/raw，不开启Hybrid/生产鉴权/多用户/分布式队列。只读哈希防意外漂移，不声称防止有写权限者伪造全部账本。
