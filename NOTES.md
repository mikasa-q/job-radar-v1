## 当前（2026-11-18，S1画像已人工确认，J1单岗报告已放行）

- 用户确认S1真实画像结果，继续原两笔候选批次的第2笔：仅J1单岗报告1次，gpt-6.1-sol/low、match-analysis-prompt-v3、top_n=1，串行零重试首失败停止。S1已完成不重跑，不自动扩样本/Top3。
- S1实际usage2060/492/2552，本次保守费¥.00553410；累计actual¥.05873868/reserve¥.7359144/保守暴露¥.79465308。cap¥1.00不重置；J1预留R¥.0670488，发送前双占用投影¥.92875068。
- 画像/报告唯一共享账本仍为 /Users/thc/.codex/visualizations/2026/11/16/01a1e44a-3bb4-72e2-a459-d45b853efeac/jobradar-stage5-live-s1/LIVE_RUN/api-store/state.json；保留原S1 attempt/event，不初始化/复制/新开收费根。J1完整wire只追加一次authorize审批。
- 原BM25用已存S1真实画像+原4岗位debug匹配；已核对历史命中J1/J2/J3变为J1/J3/J4/J2，用户明确接受新顺序并授权重冻，J1仍为唯一报告目标；原4候选和共同技能规则/覆盖率保持，不删除画像项目来抹平差异。默认LLM/Hybrid仍关闭、Ready.real_llm=false。报告需显式LiveBackend(...allow_report=True)及同root/scope/完整wire审批；mock保持模拟横幅，live显示真实响应横幅。
- key仅真实执行命令读取~/.claude-mem/.env的OPENAI_COMPAT_API_KEY，同命令临时映射OPENAI_API_KEY；不打印、不写新凭据文件。离线实现/测试/审计不读key、不出网。
- 本批说明docs/STAGE5_LIVE_J1.md；runner examples/stage5_j1_checkpoint.py；交付 /Users/thc/.codex/visualizations/2026/11/16/01a1e44a-3bb4-72e2-a459-d45b853efeac/jobradar-stage5-live-j1；真实结果以该目录J1_RUN/RESULT.json、VERIFICATION.txt为准，本节不预称已发/成功。代码回滚不退收费事实，原阶段1–4/旧provider/schema/数据库/历史报告不改，无commit/push。
- 停止点：J1一次完成或首失败即汇报，等待用户检查；本批最多两次已批准生成，不自动追加调用。领域review_status仍unconfirmed，用户本轮确认不自动改领域审核状态。

> 下列“S1后等待/J1未放行”是上一轮停止点，不覆盖本节最新授权；旧结果与raw保持原意。

## 当前（2026-11-18，阶段5真实S1画像首笔已放行）

- 用户确认离线验收，明确放行两次候选真实调用；本轮只S1画像1次，等用户人工确认后才J1报告1次。模型gpt-6.1-sol/low、后续报告v3；串行零重试首失败停止，不一次跑完。
- 仅显式LiveBackend注入固定canonical root/scope，默认llm_disabled/Hybrid仍关闭；当前live只准profile，report拒绝。旧阶段1–4领域/旧provider/schema和数据库不改。
- key仅真实执行命令从~/.claude-mem/.env读取OPENAI_COMPAT_API_KEY，同命令映射OPENAI_API_KEY后执行并清理，不打印、不落新凭据文件。离线准备/测试不读取key。
- 历史actual¥.05320458/reserve¥.6829056不重置，cap¥1.00；画像12000/8192，报告20000/8192；新rate input.78+cache-create.975/output3.90每百万，两个端点共用唯一持久收费journal。本批max2，全局新增上限9保持，不扩样本。
- 新运行根/本批交付：/Users/thc/.codex/visualizations/2026/11/16/01a1e44a-3bb4-72e2-a459-d45b853efeac/jobradar-stage5-live-s1。真实状态/usage/本地保守实际费以LIVE_RUN/RESULT.json和VERIFICATION.txt为准，本节不预称已发送成功。报告本轮not_run。
- 说明docs/STAGE5_LIVE_S1.md；runner examples/stage5_s1_checkpoint.py；S1原文字面核对/新PDF与完整wire hash预冻结，发送前持久reserve/send-intent，usage后原domain校验与原子完成。回滚仅代码不退账。
- 停止点：真实首笔完成或首失败后汇报，等用户人工确认，绝不重跑S1/新root重开额度/自动发J1；无commit/push。

> 下列“仅离线/真实backend尚无”是前一轮历史；默认服务仍关闭，本批显式首笔运行权限只见本节与本批RESULT。

## 当前（2026-11-17，阶段5真实适配先离线实施）

- 用户确认真实适配方案；本批仅契约v1.1增补、单一持久收费账本、MockTransport适配和离线/回归/独立回滚验收，不读取真实凭据、不发真实请求、不跑真实小样本。
- 新说明：docs/STAGE5_LIVE_CONTRACTS.md、docs/STAGE5_LIVE_OFFLINE.md；新快照docs/STAGE5_OPENAPI_V1_1.json。旧阶段5契约/OpenAPI及阶段1–4领域/旧供应商/数据库/历史报告不改。
- 默认llm_disabled、hybrid_not_enabled保持，Ready.real_llm=false；仅显式程序化注入严格MockTransport才能运行模拟收费链路。尚无真实backend入口，模拟报告top_n=1；真实启用需另行最小改动和放行。
- 账本和资源/幂等共用私有原子journal：完整wire/context/root/审批绑定，发送前send-intent和双占用，usage持久后再原领域校验与原子发布，未知usage/失败/未完重启全局halt。同键重放不重发，已有OWNER而journal缺失拒绝重建。
- 历史cap¥1.00/actual¥0.05320458/reserve¥0.6829056/保守暴露¥0.73611018不变；本批真实新增费用¥0。新批候选最多2次（共享硬上限9），gpt-6.1-sol/low/v3，画像12000/8192、报告20000/8192；新input .78+cache-create .975、output3.90每百万。完整UTF8 JSON字节+1024是保守本地分配，不冒称供应商硬token上限。
- 完整验收计数、精确命令/结果、原hash/数据/index和非空虚拟账本不回退证据见本批VERIFICATION.txt：/Users/thc/.codex/visualizations/2026/11/16/01a1e44a-3bb4-72e2-a459-d45b853efeac/jobradar-stage5-live-offline。
- 停止点：交付离线证据后等用户确认；不自动运行S1画像或J1报告。后续若放行真实backend和首笔，先冻结S1完整wire/hash与账本，只发一次画像后人工检查；J1单岗报告再次确认。代码回滚不得删除/减退收费事实；无commit/push/新依赖。

> 下列“本轮只做方案/未实施”属于先前轮次历史，不覆盖上节本批状态。

## 下一步（2026-11-17 21:44，阶段 5 第二步已确认，授权进入真实适配方案阶段）

- 用户已确认第二步离线交付（专项 173 / 全量 2133，基线 1997 / 改后 2133 / 回滚 1997，回滚恢复 298 文件哈希；真实 LLM/小样本/新费用 0），授权直接进入下一阶段。
- 本轮只做真实适配方案、不发真实请求：先按契约第 6 节提出最新累计预算（上限 ¥1.00，已花 ¥0.05320458、预留 ¥0.6829056、保守暴露 ¥0.73611018）、真实小样本方案（样本/次数/token 限额/费率）、跨请求持久收费账本方案；模型 gpt-6.1-sol/low、报告 v3；串行、零重试、首失败即停，最多 9 次新增生成；画像/报告共用预算和次数。
- 未确认前保持 llm_disabled 与 hybrid_not_enabled，不读凭据、不发真实请求。方案讲完停下等用户确认再跑。

## 阶段5第二步（2026-11-17，三路接口与离线汇总）

- 用户授权按已冻结契约让A简历/B匹配/C报告三个子代理并行实施；各独占模块与专项测试。主代理独占应用注册、共享运行/存储、预算门禁、报告展示、端到端和独立回滚。本步无真实LLM/真实小样本/新依赖安装。
- 说明：docs/STAGE5_STEP2.md；权威契约与OpenAPI不改。三路从原离线骨架提取为独立mixin+register_routes，原阶段1–4算法/报告生成/旧api.py/数据库结构和数据保持。
- 上传multipart/进程清理、匹配同代快照/配置/formal门禁、报告快照绑定/只读下载已强化。共享journal每次读重验、写失败fail-stop、分发异常持久interrupted；任务成功仍不代表LLM完成。
- 新展示包装显式区分offline/scripted/live，只替换新展示固定横幅；live分支仅合成audit验证，HTTP真实入口仍固定503。历史renderer/报告/raw不改。
- 本批专项/完整回归、本机Uvicorn合成HTTP、独立回滚与原hash恢复的最终实测数见本批VERIFICATION.txt（不沿用旧轮次或代理中途成绩）。本批交付：/Users/thc/.codex/visualizations/2026/11/16/01a1e44a-3bb4-72e2-a459-d45b853efeac/jobradar-stage5-step2。
- 停止点：第二步离线交付后等待用户确认，不自动启用真实供应商/Hybrid或运行真实样本；¥1.00累计预算不重置，历史actual .05320458/reserve .6829056/保守暴露 .73611018不变。本轮LLM新费用¥0，预算纯函数仍不是真实收费账本。

## 阶段5第一步（2026-11-16，共享契约与离线骨架）

- 用户仅授权主代理串行冻结共享契约并建立最小离线HTTP闭环；本步不派子代理、不读凭据、不调真实LLM、不跑真实小样本。完成后等待用户确认，不自动进入A/B/C。
- 冻结说明：docs/STAGE5_CONTRACTS.md；运行说明：docs/STAGE5_FASTAPI.md。新增api_v1应用组合旧API；版本化端点/类型化信封/资源ID与快照/任务状态/错误/持久幂等/预算关闭政策已冻结。
- 最小闭环：合成PDF → 现有提取 → rules画像 → 现有阶段3 BM25检索 → client=None离线报告；任务succeeded不等于分析完成，report=not_run/review=unconfirmed。LLM/Hybrid入口固定503；预算纯函数不是真实收费执行器。
- 原阶段1–4核心、旧api.py、数据库结构与数据保持；新增私有文件journal，不迁移SQL。新API展示包装去掉离线报告的scripted旧横幅，历史renderer/报告不改。只新增锁定本机已有python-multipart==0.0.32，不安装/升级依赖。
- 37项专项通过；TestClient与真实Uvicorn本机合成HTTP链路通过，真实LLM0。最终完整回归/活动应用/独立回滚/原hash恢复/精确命令与结果以本批VERIFICATION.txt为准，不沿用旧成绩。
- 本批证据：/Users/thc/.codex/visualizations/2026/11/16/01a1e44a-3bb4-72e2-a459-d45b853efeac/jobradar-stage5-step1。
- 已知边界：单实例/本地单用户、256资源/256MiB journal、PDF受监督子进程；规则/检索线程不宣称可强杀；真实LLM/Hybrid完整适配与跨请求收费账本留待后续确认。

## 下一步（2026-11-16 20:35，阶段 5 第一步已授权）

- 用户已确认阶段 5 只读方案（2026-11-16 20:17 新窗口 idx 2）。今晚先走第一步：主代理串行冻结共享契约（端点、响应信封、资源 ID/快照、任务状态、错误码、幂等与预算规则），建立最小离线闭环。
- 第一步不派子代理、不调真实 LLM、不跑真实小样本；A/B/C 并行实施等契约冻结完成后再说。

## 下一步（2026-11-16 19:53，阶段 4 已闭环，待开阶段 5）

- 阶段 4 已闭环：用户 2026-11-16 19:53 亲口确认三份 Top3 报告全过。Phase 3 v3 真实调用 9/9 通过（9 次请求、0 重试，预期结论 24/24，引用 73 处、0 无依据新增；累计 ¥0.05320458，预算 ¥1.00 内；回归 1960/1969/1960）。
- 下一步：开阶段 5 FastAPI 整合。建议开新窗口、选 JobRadar 项目文件夹；先只讲方案、不写代码：先读 NOTES.md 和仓库里阶段 5 的计划文档（按 NOTES.md 里指的那份），再讲阶段 5 要交付什么（把阶段 1–4 串成可跑的服务：简历上传/解析、检索匹配、LLM 匹配报告的端点怎么定、请求/响应长什么样、错误和离线降级怎么做）、端点怎么拆、能不能拆给子代理并行做、怎么分工和汇总、测试怎么验（先离线/模拟 LLM，再接真实小样本）、不做什么（不改阶段 1–4 的匹配、检索和报告逻辑，不动数据库结构；真实 LLM 验收先按阶段 4 的小样本预算来，不超预算跑）。在 feature/jobradar-v1 分支上做。讲完等用户确认再写。
- 阶段 5 整合报告模板时顺手改掉报告顶部“预设响应只验证工程链路”旧横幅（本批是真实调用，旧文案会误导）。

## 下一步（2026-11-16 17:24，Phase 3 真实调用已授权）

- 跑 Phase 3 真实调用，v3 提示词，gpt-6.1-sol / low（REASONING_EFFORT='low' 写死在 report_apinebula.py，不改）。
- 样本不变：S1/S2/S3 合成材料，Top3；先 S1/J1 一次人工检查，等用户确认再跑其余；串行、零重试、首失败即停，最多 9 次。
- 预算：总预算 ¥1.00，已花 ¥0.0185；凭据走 ~/.claude-mem/.env 导出为 OPENAI_API_KEY，不打印 key、不写新文件。

## 当前：校验器 V1 / 提示词 v3（2026-11-16，Phase 0–2 实施）

- 用户已确认方案并仅授权 Phase 0–2。本轮先在独立 modified 副本冻结协议与完整提示词，再实现四层门禁、迁移测试；活动工作区应用、独立回滚与最终审计由主代理收口，结果以本批 `VERIFICATION.txt` 为准。本节不预称尚未核验的回滚已经通过。
- 新生成使用 `match-analysis-prompt-v3`，报告仍 `match-report-v1`；draft/report 字段、排名、审核状态保持，仅报告 prompt_version 允许值扩展及默认 v3。原 v1/v2 提示词和真实 raw 不改。
- 整份 draft 按结构 → 规则 → 证据 → 文本逐层完成；删除生成正文否定白名单/helper 及 UNKNOWN_DENIAL。膨胀词不因否定/引用/原材料含同词/advisory 而豁免；80 Unicode 码点单句，unknown 精确为“简历未提及 <规范 subject> 使用经历。”，不是本地模板改写。
- unknown 检查全部覆盖材料：课程项目使用也算使用经历；计划学习不算已有使用；明确同技能负向原文走 explicit_gap；歧义不认证为缺证据。规则类别必须绑定实际引用的同主题规则，不动阶段1–3和检索排序。
- v1/v2 仅历史只读，保留原版本/状态/原文，仍核查通用结构、哈希、引用作用域与排名，明确“不代表已通过v3”。`validate_report(..., require_current_policy=True)` 对历史版报 `analysis_validation_policy_mismatch`，不复制旧否定校验器来假装重演。
- 本轮已观察基线：`1757 passed, 5 warnings in 32.10s`，exit 0；阶段性修改副本完整回归：`1940 passed, 5 warnings in 41.45s`，exit 0（`MODIFIED-VERIFIED.log`）。后续最终修改/活动/回滚复测的精确命令、数量和时间以本批 `VERIFICATION.txt` 为准，不沿用旧成绩。
- 离线评估86项：协议 TP84/FP0/FN0/TN2；人工语义标签 TP48/FP36/FN0/TN2。36项语义无错误但违反短事实协议的拒绝成本公开保留，不声称语义误报归零。S1/S2/S3 九岗是人工 authored fixture，独立冻结期望与实际 finding 比较，覆盖均1.0、无依据新增0；不等于真实模型质量。
- 本轮真实请求0、凭据读取0、新费用¥0；S1/S2/S3真实状态均 `not_run`。历史累计¥0.01850004、原失败预留保留；Phase 3 等用户另行授权，原¥1.00预算不自动延用。无新依赖、embedding/第二LLM/V2事实DSL模板架构或阶段5。
- 实施说明：`/Users/thc/Documents/Codex/2026-10-21/build-ios-apps-plugin-build-ios-3/outputs/job-radar/docs/STAGE4_V3_IMPLEMENTATION.md`；冻结协议：`/Users/thc/Documents/Codex/2026-10-21/build-ios-apps-plugin-build-ios-3/outputs/job-radar/docs/STAGE4_V3_PROTOCOL.md`；本批证据：`/Users/thc/.codex/visualizations/2026/11/16/01a1e35d-b2f1-7250-ad25-64904f393ec4/jobradar-validator-v1`。

> 以下置顶接力、v2/否定修复及阶段记录属于历史快照；涉及“当前v2”“待确认”“helper放行”等措辞均不覆盖本节的v3状态。历史 raw、哈希及费用记录保留原意。

## 下一步（2026-11-16 16:00，方案已确认，新窗口实施）

- 用户已确认《JobRadar 阶段四校验器 V1 优化方案》（docs/STAGE4_VALIDATOR_V1_OPTIMIZATION.md）。新窗口已读 NOTES.md 顶部和完整方案，确认接上，核心口径已理解。
- 授权范围：按方案实施 Phase 0-2（冻结 v3 协议 → 四层门禁与 v3 最小接线 → 离线、完整回归与独立回滚）。真实调用（Phase 3，S1/S2/S3）先不跑，等用户另行授权；原 ¥1.00 预算不自动延用到 v3 新请求。
- 约束不变：预算 ¥1.00，累计已 ¥0.01850004；凭据走 ~/.claude-mem/.env 导出，不打印、不落盘 key，不重建 auth.json；串行、零重试、失败即停；不换模型（仍用 gpt-6.1-sol）。

## 下一步（2026-11-16，换窗口接力）

- 旧主窗口（2026-11-14 11:46）已满：最后一次 token 计数 input 233,470 / 窗口 258,400，约 90%，新窗口从这里接手。
- 《JobRadar 阶段四校验器 V1 优化方案》已写好：docs/STAGE4_VALIDATOR_V1_OPTIMIZATION.md（45KB，2026-11-16 15:47），仅新增该文档，382 个既有文件未变，未写实现代码、未调用模型。状态：方案待确认。
- 方案核心：校验分结构/规则/证据/文本安全四层；删否定白名单，膨胀词出现即直接拒；unknown 改查结构化状态，正文统一“简历未提及 X 使用经历。”；测试原 53 项删 1 留 52。embedding、第二个 LLM、V2 结构化+模板渲染、阶段 1-3 及排序、schema 字段改动，全部不做。
- 等用户确认方案后，再按文档四步实施：1 冻结协议（v3 短事实提示词、禁词、正反例、错误码）→ 2 逐项处置 10 个命名正则、5 处内联正则、11 个函数 → 3 调整测试（误报/漏报分开统计）→ 4 验收（先离线、回归与独立回滚，再另行授权 S1/S2/S3 真实调用）。
- 约束不变：预算 ¥1.00，累计已 ¥0.01850004；凭据走 ~/.claude-mem/.env 导出，不打印、不落盘 key，不重建 auth.json；串行、零重试、失败即停；不换模型（仍用 gpt-6.1-sol，Astra 5 倍价且不解决校验器问题）。

## 阶段四校验器 V1 优化方案文档已完成（2026-11-16，待确认）

- 按 GPT 第二轮建议：V1 先优化现有校验器，不立即重构成结构化+模板渲染（那是 V2）；动手前先出方案文档，用户确认后再改代码。
- 方案编写前只读核对发现 3 个缺口：unknown 统一写法不适用于 S3 已提及技能（Docker/SQL 等）、c4/c5 熟练度未知不能写成“未提及”、否定句在新禁词政策下是契约拒绝而非误报。v3 新错误码与 unknown 的 X 语义在方案中冻结。
- 三次 S1/J1 真实调用均失败（累计 ¥0.01850004），原因都是校验器词法误报，不是模型理解错：第二次 6 条类别全对，第三次模型换“不等同于”同义表达被漏检。换 Astra 不解决此问题。

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

## 阶段四v2已确认激活（2026-11-15，单岗真实复验）

1. 用户明确确认v2候选并授权离线复检后仅S1/J1真实调用一次，预算提升¥1.00；既有费用¥0.00645216/失败预留¥0.0569088继续保留。无其他岗位授权，串行零重试、失败即停；成功也不自动续跑。
2. 活动prompt/version改为match-analysis-prompt-v2，逐字采用原候选；原v1文本冻结，旧v1报告可读取/往返且不改标版本。MatchReport仍v1；证据校验/规则/排序/草稿schema保持。
3. 单次runner采用top_n=1/max_attempts=1并在真实HTTP前核对新v2外发体；移除旧wrapper重复预校验，确保analyze_job的拒绝sink先保存完整正文及expected/actual再停批次。凭据沿用户指定同shell临时映射，不打印或落盘key。
4. 本批确切离线/真实结果以独立交付VERIFICATION.txt与RESULT.json为准，不预先记成功。下方候选待确认记录属于历史；当前以此节为准。

## 阶段四诊断补充（2026-11-15，提示词v2候选待确认）

1. 已完成一次真实S1/J1：输入3032/输出1048，供应商completed后analysis_requirement_kind_mismatch；按约定1请求0重试即停，其他8岗位未调用。费用¥0.00645216计入原¥0.60，失败预留¥0.0569088保留。历史被拒正文没保存，具体claim/subject/类别unknown，不把合成反例冒作真实原文。
2. 用户确认先补完整拒绝诊断；现新增可选本地sidecar，保留完整raw/parsed草稿及逐claim expected/actual/规则引用。失败draft不进入报告，默认不落盘；文件私有权限/新建不覆盖、写失败停批次。未放松validator/改排序/契约/旧业务规则。
3. 独立提示词v2候选见docs/STAGE4_PROMPT_V2_CANDIDATE.md；当前活动提示词仍v1。合成同分支mismatch可复现，人工正确草稿通过同一校验；不声称证明真实v2质量。等待用户确认候选后才激活并仅重跑S1/J1，串行零重试/失败即停，不自动发余下样本。
4. 本轮离线改动/测试/独立回滚精确结果见交付VERIFICATION.txt；无新增真实请求/费用/密钥读取，不进入阶段5。下方原实现记录保持为历史。

## 当前状态（2026-11-14，阶段四工程实现已交付，真实LLM质量待单独确认）

1. 用户已确认阶段四方案并授权实施：先主代理串行冻结 `match-report-v1` 和最小离线闭环，再 A证据/B分析/C供应商适配并行；当前分支 `feature/jobradar-v1`。本轮新增独立 `report_*` 层，保留阶段一至三接口/算法和既有编辑，不提交、不推送、不改分支。
2. 新增 MatchReport JSON 与同源Markdown：输入同快照 MatchingRequest、原 ResumeDocument、IndexedMatchingResult；保留岗位原排名、BM25/Dense/RRF分项、规则、通道/排除项及人工状态。LLM仅生成有引用的分析草稿，本地装配身份/版本/哈希/UTC时间/状态，报告始终unconfirmed。
3. 已实现原文/快照/规则与检索分项核对、引用ID内容绑定、跨岗位/错误引文拒绝、未知与差距区分、建议关联finding、单岗最小化提示词、可选独立APINebula适配、串行Top N（默认3）与失败停止、本地确定性导出。unknown不等于不会，not_found不冒充能力不足；不生成录用概率/总匹配分，不重排、不自动淘汰。
4. 本次验收是离线工程正确性与真实固定SDK的内存HTTP模拟；合成报告明确标注scripted，不证明真实LLM分析质量。真实付费LLM请求0、密钥读取0，无真实简历/岗位库读取、检索调参、模型下载或依赖安装。新客户端的模型/费率/输入输出限额/次数/预算须在真实批次前重新确认，不继承旧简历批次额度。
5. 已有1398项基线重新运行通过；阶段四专项、最终完整回归、活动应用与独立回滚的准确数量/命令/字面输出/退出码，以本批次VERIFICATION.txt为准。使用说明 `docs/STAGE4_REPORT.md`，冻结契约 `docs/STAGE4_CONTRACTS.md`，离线演示 `examples/stage4_report.py`。交付目录：`/Users/thc/.codex/visualizations/2026/11/14/01a1d829-b8da-75f3-851c-f4c6466777e7/jobradar-stage4`。
6. 机械引用校验不是语义蕴含/来源真实/能力认证；正向支持保守限于可解释的技能规则，未评估学历/经验继续未知。历史MatchingResult不含完整请求哈希，现时快照绑定不能反证历史请求真实性；Dense查询计算不重放，索引superset限制明确保留。完整原始证据仅本地保存，外发做常见联系方式/路径最小化但不承诺完美匿名。
7. 本轮未修改旧源码、既有测试、依赖、证据库/旧库、人工标注/评测报告、索引或审核状态。不做FastAPI端点/上传/服务化（阶段5）、Reranker、微调或扩大采集。阶段三已验收状态、人工前置评测共享岗位组/无相关查询/Dense10000预算停止等证据边界继续保留。
8. 当前停止点：阶段四工程闭环已交付，等待用户测试反馈；真实LLM小样本质量验收待新的配置/发送内容/预算确认后再做，不自动追加付费批次或进入阶段五。下方为历史记录，不覆盖当前安排。

## 阶段三实施记录（2026-11-13，已完成并通过用户完整回归验收）

1. 主代理先串行写入rag_models.py及STAGE3_CONTRACTS.md冻结接口，再按A评测/B分块/C索引并行实现；共享模型/引擎/依赖/文档仍主代理单写。源码只新增rag_*，旧MatchingRequest/MatchingResult/match_jobs/BM25/RRF/规则源码保持原样。
2. A交付标注格式校验、岗位去重Recall@1/5/10/MRR@10/nDCG@10手算测试、未判断/空相关集、分组防泄漏、dev调参/test单冻结配置、逐查询差异/错误与哈希报告。主代理补显式candidate_keys全集及缺失模型哈希门禁；合成预期永不记为人工标签。当时人工样本质量未验收；2026-11-14的人工标注前置评测及共享岗位组限制见下方独立记录。
3. B交付句段/真实tokenizer offset分块、标题/指令/特殊token预算、重叠推进、末尾覆盖、原文/清理映射/编码哈希、最佳chunk岗位聚合。C交付独立SQLite generation、增删改/幂等/元数据重用、float32流式候选子集向量/缓存、失效重建、原子CURRENT/中断恢复/损坏检测。主代理补单reader快照防跨generation竞态及仅变动投影重算。
4. 独立可选运行时先锁定wheel确切URL/SHA，再安装/导入/tensor与pip check预检通过，随后固定三模型revision与必要文件哈希。官方主机网络/DNS失败已记录，显式镜像传输并逐文件对照官方Git/LFS哈希；离线推理不隐式联网、不换模型/后端，不改默认requirements或真实数据库。
5. 三真实模型CPU/batch1串行合成接线均通过Hybrid、长JD末尾、原生/统一128token预算、重启、元数据零重编码、超长查询严格报错/允许降级两配置；无假向量补成绩。初轮E5/BGE/MiniLM进程树采样峰值约883/377/844MiB，模型加载约4.84/2.70/4.56秒；这些只是本机合成语料实测，不是8GB所有负载保证。具体复测见证据。
6. 三模型真实6变体×3合成意图消融执行通过（默认/调参BM25、Dense、Hybrid、整篇Dense、最佳chunk），不证明普遍质量/分块增益，未使用人工留出集挑参。真实BGE生命周期增删改/仅两块重编码/删除不召回/增量等于重建/中断/失效/元数据复用通过；默认BM25无模型依赖与旧审核状态门禁保持。
7. 词法派生索引100/1000/10000规模通过（查询候选100/1000/1000）；三模型Dense100/1000均完成。三模型10000全量构建各触发预先冻结180秒预算，被监督进程结束(-9，shell247)，不记通过、不延长预算、不声称OOM。当前精确检索，无Qdrant/ANN服务。
8. 工程专项220项（A58/B65/C63/引擎8/runtime11/公共契约15）通过；完整回归与独立回滚的最终数量/字面输出/退出状态以本批次VERIFICATION.txt为准。原1165项基线本轮重新通过。使用docs/STAGE3_RAG.md、运行时docs/STAGE3_RUNTIME.md、实测docs/STAGE3_RESULTS.md；结构合成、真实可运行、人工质量三类不混记。
9. 用户已完成完整回归并确认通过，阶段三已收尾。收尾时人工标注/留出相关性评测尚无完成记录；2026-11-14新增前置评测由用户单独授权且已确认结果，见下节，不将小样本共享候选库评测外推为独立留出质量通过。Dense10000预算停止等既有证据边界保留，不自动追加调参、评测或实现任务。阶段四等待用户通知，暂不编写代码或方案。

## 人工标注前置评测（2026-11-14，结果已获用户确认；不是阶段四）

1. 用户已完成45条人工标注，并确认本轮评测结果。本次只记录既有结果；前置评测不等于阶段四实施，也不意味着全部查询达到理想排序。
2. 输入：`/Users/thc/Desktop/jobradar-v1-data/evaluation/manual-v1.json`（3查询×15岗位，45条human_judged）与`/Users/thc/Desktop/jobradar-v1-data/evidence.sqlite3`（15条JD）；通过项目read_job_candidates以mode=ro/query_only读取，显式传15个JobKey作为candidate_keys。标注、数据库及岗位审核状态保持不变。
3. 固定模型BAAI/bge-small-zh-v1.5，revision `7999e1d3359715c523056ef9478215996d62a620`，512维、CLS、归一化、CPU/batch1/离线本地快照，11个模型文件哈希校验通过；不比较模型、不调参。EngineConfig(mode=hybrid,top_k=10)，其余默认k1=1.5/b=0.75/title_weight=2.0/recall_k=50/rrf_k=60，ChunkConfig(max_tokens=512,overlap_tokens=32)，15条JD生成24个chunk，最大实际token数511。
4. 复用阶段三真实消融的精确文本查询路径：原BM25 + DerivedIndex.dense_hits最佳chunk岗位聚合 + 原fuse_rrf，取岗位Top10；无虚构简历画像或规则过滤/二次排序。dev-python、dev-data、test-mobile各执行一次真实Hybrid，共3次，BM25/Dense均ok，无Dense降级。随后分别对dev（dev-python+dev-data）与test（test-mobile）的原始split视图调用rag_evaluation.evaluate_rankings，不改标注字段。

| 查询 / split | 相关岗位数（grade>=1） | Recall@1 | Recall@5 | Recall@10 | MRR@10 | nDCG@10 | error_categories |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| dev-python / dev | 10 | 0.1 | 0.5 | 0.9 | 1.0 | 0.9671719390753982 | ranking_error |
| dev-data / dev | 5 | 0.0 | 0.4 | 1.0 | 0.5 | 0.7016521873278284 | ranking_error |
| test-mobile / test | 0 | null | null | null | null | null | no_relevant_jobs |
| **总体macro（等于dev macro）** | — | 0.05 | 0.45 | 0.95 | 0.75 | 0.8344120632016133 | ranking_error=2；no_relevant_jobs=1 |

- Macro按查询等权，排除null，不把无相关岗位查询当作0；各指标metric_query_support=2。test macro各项均null、support=0。
- dev-python的10个相关岗位命中9个，Top10遗漏ID2（grade=1）；dev-data全部5个相关岗位进入Top10，但无关岗位排在前列。两查询nDCG@10<1.0，均如实保留ranking_error，未据此调参。
- test-mobile的15条grade全0，known_relevant_count=0；所有指标null并带no_relevant_jobs是预期结果，原人工标注不变。

Top10岗位ID（按检索排名，不是ID排序）：

- dev-python：`10 → 12 → 1 → 5 → 3 → 15 → 11 → 4 → 6 → 13`。
- dev-data：`4 → 1 → 10 → 13 → 9 → 2 → 15 → 12 → 11 → 5`。
- test-mobile：`10 → 5 → 12 → 1 → 15 → 11 → 3 → 4 → 2 → 13`。

**分组边界**：原整表validate_dataset返回`eval_job_group_leakage`，因为同一15岗位组同时出现于dev/test。原数据的query/split/job_group/grade未改；各split视图分别通过校验并传入完整15候选计算指标。报告保留full_dataset_validation_errors及shared_job_groups，job_group_independent_holdout=false；这是共享候选库的描述性评测，不作为岗位组独立留出验收，小样本结果不外推普遍质量。

文件与验证证据：

- 新增可复用脚本：`/Users/thc/Documents/Codex/2026-10-21/build-ios-apps-plugin-build-ios-3/outputs/job-radar/examples/stage3_manual_evaluate.py`；新增13项离线测试：`/Users/thc/Documents/Codex/2026-10-21/build-ios-apps-plugin-build-ios-3/outputs/job-radar/tests/test_stage3_manual_evaluate.py`。本次确认记录不修改这两个文件。
- 原始报告：`/Users/thc/Desktop/jobradar-v1-data/evaluation/manual-v1-report.json`，含完整Top10标题/公司/grade/BM25与Dense分项、split结果、参数及数据/模型哈希。报告SHA256：`871d0ffce89475d44127072df135ded68d5bcb778ee57e2eb5cf5390336cb741`。
- 原人工评测批次证据：`/Users/thc/.codex/visualizations/2026/11/13/01a1d5ac-573f-7cf0-aaf4-48d8672aa247/manual-v1-hybrid-evaluation/VERIFICATION.txt`。真实进程exit0，约27.79秒，进程树采样峰值389976064字节；报告RRF/evaluate_rankings/独立指标复算一致。
- 原评测批次完整测试：BASELINE `1385 passed, 5 warnings in 27.61s`；MODIFIED `1398 passed, 5 warnings in 32.26s`；独立ROLLBACK `1385 passed, 5 warnings in 47.14s`，均exit0。以上是原评测批次记录，不冒充本次文档更新新跑结果；本次文档验证另见`/Users/thc/.codex/visualizations/2026/11/13/01a1d5ac-573f-7cf0-aaf4-48d8672aa247/manual-v1-notes-confirmed/VERIFICATION.txt`。
- 当前停止点：用户已确认前置评测结果，NOTES已记录；不自动继续检索、调参或阶段四，等待下一步通知。

## 阶段三：RAG 检索优化历史方案（2026-11-12确认，2026-11-13已完成并通过用户完整回归验收）

以下保留确认时的方案与验收口径，其中“当前”“实施前”“待完成”等表述属于当时语境；实施结果见上方记录，当前状态以置顶收尾记录为准。

### 目标、依据与输入输出

目标：在阶段二引擎上交付可衡量的检索质量、可追溯的长JD处理、可持续更新的本地索引，以及8GB目标机器上的真实资源数据。阶段三只做检索工程与质量优化，不生成LLM回答。

依据：NOTES原指向的 docs/STAGE2_MATCHING_PLAN.md 开头“阶段三仍有的实质交付”，并参考 docs/MATCHING_ENGINE.md 和现有接口。docs/STAGE3_FASTAPI.md 是旧版API教程，不是本轮RAG阶段三计划。旧文档中LLM分析“阶段3/4”的表述，以用户最新边界为准：LLM匹配分析与差距报告留阶段4，FastAPI服务化留阶段5，本轮均不实施。

- 外部继续接收 MatchingRequest（MatchingInput + list[JobCandidate]），业务结果保留 MatchingResult 的岗位排序、检索分项、规则证据、身份/哈希/来源、人工状态与通道状态。
- 现有 match_jobs 保留为内存基线。持久索引走显式入口，chunk命中、索引版本与构建/评测信息采用显式版本化扩展；具体签名在实施首步串行冻结，不在本文冒称已实现。
- 同一候选集与快照验证索引路径/内存路径等价；索引不扩大请求岗位集合、不绕过debug/formal审核门禁，不修改画像或岗位review_status。检索相关性不是录用概率或胜任率。

### 交付一：人工标注评测、调参与消融

- 建议从20–30个查询意图、50–100条去重JD的小型完整标注集起步；缺口先用合成/脱敏文本，标注仍由人完成。区分样本文本来源、人工标注状态、机器合成预期、证据引用与数据版本；未判断项单列，不当负例。
- 人工相关性标签：0不相关、1部分相关、2高度相关。冻结二值相关阈值（起步建议>=1）、分级增益与指标口径；不以规则技能交集自动生成“人工金标”。如实报告标注覆盖、不一致及复核记录。
- 同一画像/近重复岗位分组切分开发集与留出测试集，避免泄漏。开发集调参，冻结配置后才运行留出测试；不反复看测试集挑参数。若暂缺人工标注，只交评测工具和合成正确性结果，质量验收仍记待完成。
- 以去重后的岗位排名计算 Recall@1/5/10、MRR@10、nDCG@10；固定相关阈值、nDCG增益与折扣、并列排序及空相关集处理。无相关岗位查询单独报告，不用零分母或剔除难例抬高成绩。
- 对比阶段二默认基线、调参BM25、真实Dense、Hybrid，以及分块/聚合消融；调现有k1/b/title_weight、召回深度、rrf_k等参数，不重写BM25/RRF。记录参数版本、样本/索引/模型哈希、逐查询差异与错误类型，小样本不外推普遍准确率。

### 交付二：长JD证据分块与岗位聚合

- 按段落/句子切分，过长句再按真实tokenizer的offset细分；编码长度预算包括模型指令、标题与特殊token，不静默截断。重叠量和块大小版本化并用开发集比较。
- Chunk保留dataset/job身份、JD哈希、原文字段/字符起止位置/逐字引用、分块版本/序号及编码文本哈希。原文与清理后的编码文本分开，保留映射；清理联系方式不破坏原文证据定位。
- 第一版保留整篇JD的BM25路径，Dense编码chunk；先将chunk命中聚合为岗位排名，再复用已有RRF。起步用最佳chunk分数，不简单累加让长JD占优；每岗位只占一个结果位置，保留命中片段供回查。
- 规则仍判断完整岗位事实，chunk命中不代表满足要求。画像查询超过模型token上限时显式报错或走已允许的通道降级；查询分段等新策略须版本化验证，不隐式截断。

### 交付三：独立派生索引、持久化与增量更新

- 起步优先独立SQLite派生索引库，持久保存岗位/chunk元数据、词项统计和可选真实向量；不改证据库或旧jobs库。向量采用紧凑存储、分批读取/加载，不预设把全量向量变成Python浮点对象常驻内存。
- 支持首次构建、重启加载、新增、内容修改、删除、无变化跳过及幂等重跑。仅来源/审核状态变化更新元数据，不无谓重编码；内容修改重算受影响的投影/块/向量，删除清理关联与缓存。
- BM25文档数/df/平均长度随增删改正确维护，继续复用阶段二计算。阶段二在硬约束过滤后的候选集计算统计；持久路径要保持同一候选集统计语义，不能悄悄以全库统计替代而声称等价。
- 索引清单记录schema、数据快照/内容哈希、投影、分词/别名、分块、model_id/revision、tokenizer、池化、归一化、维度、运行时与构建配置版本；query/passage角色和文本哈希进入向量缓存键。

### 交付四：版本失效、原子重建与恢复

- 数据变更按差异增量处理；模型/tokenizer/池化/维度/分块等变化使对应通道索引失效并重建，禁止混用旧向量。纯查询/排序参数变化记录实验版本，不一律触发重编码。
- 新generation在临时位置构建、校验完整性与版本后原子切换；中断保留旧generation，旧版本仅在兼容性校验通过时可查询，不静默服务过期索引。
- 验收损坏检测、schema不兼容、缓存失效、构建中断、重启恢复、删除不再召回、重复更新无额外变化，以及重建结果与增量结果一致。

### 交付五：Embedding真实实测与8GB降级边界

- 已于2026-11-12方案核对时复核官方模型卡；实施下载前再次核对所选固定revision的模型卡、许可证、tokenizer、池化、指令、长度及必要文件清单。当前没有固定模型revision，也没有已验收运行时组合，不把候选设置写成实测结果。

| 候选（官方模型卡） | 维度/原生token上限 | 使用要求与定位 |
| --- | --- | --- |
| intfloat/multilingual-e5-small | 384 / 512 | query: / passage: 前缀；平均池化并归一化，中英检索候选 |
| BAAI/bge-small-zh-v1.5 | 512 / 512 | 短查询推荐“为这个句子生成表示以用于检索相关文章：”，passage不加指令；按模型池化配置归一化，中文优先候选 |
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | 384 / 128 | 平均池化，多语言短文本对照；不预设更省内存，也不把128当512 |

模型卡：https://huggingface.co/intfloat/multilingual-e5-small 、https://huggingface.co/BAAI/bge-small-zh-v1.5 、https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 。

**实施前置门槛：先锁定运行时组合，再下载模型。**本机已核验8GiB Intel x86_64、Python3.10.6，当前基础环境没有torch/sentence_transformers/transformers/numpy。PyTorch官方公告从2.3起停止macOS x86_64二进制包，因此不能直接pip安装latest或仅凭small名称判断能跑；官方依据：https://dev-discuss.pytorch.org/t/pytorch-macos-x86-builds-deprecation-starting-january-2024/1690 。

1. 主代理先确认OS/CPU/Python与候选runtime兼容性，将Python、torch、sentence-transformers、transformers、tokenizers、numpy及相关传递依赖的确切版本/来源/哈希锁定，在独立可选环境完成安装与导入兼容性预检；不污染默认依赖。预检未通过则不下载模型，也不擅自源码编译或改换运行后端。
2. 运行时门槛通过后，再固定模型revision/文件清单/哈希，逐个下载必要权重，不并发下载并加载三个模型。实际推理只加载已有本地快照、local_files_only，禁用远程代码执行与隐式网络补文件。
3. 单模型独立进程、CPU、batch=1开始；先真实编码少量合成文本，再逐级扩大。记录加载/查询编码/岗位编码/搜索耗时、进程树峰值RSS、退出原因；一个模型进程完全退出后才测下一个。
4. 同时报告统一短文本预算和各模型原生长度配置；MiniLM更多chunk的额外成本计入，不以字符数冒称token数，不以权重文件大小冒称峰值内存。8GB是否可用以真实测量为准，不用假向量补成绩。
5. 默认先交BM25+规则可运行版，Embedding是显式开启的可选通道。mode=bm25_rules时Dense disabled，不加载运行时。Hybrid成功才记真实混合成功；加载/编码/超时/资源失败仅在allow_dense_fallback=True时返回BM25+规则，标明status=degraded、actual_mode和固定原因；调用方未允许降级则明确报错，不伪造零分或零向量。
6. 独立监督进程处理超时/子进程退出并执行同一显式降级约定，不声称同进程异常捕获可保护OS免于OOM。兼容性/资源失败记录实情，保留默认BM25+规则；不自动切收费API、不自动换模型或后端。

### 交付六：延迟/内存基准与向量库选型

- 基准规模按100→1,000→10,000岗位逐级推进，并记录chunk数、文本长度和维度；前一级超预先确定的资源预算就停止扩大。现有入口的岗位/文本限额不悄悄放宽，较大规模使用显式基准入口和资源配置。
- 区分首次启动/模型加载、模型已加载且查询未缓存、缓存命中；分别报告编码与检索耗时、查询p50/p95、吞吐、进程树峰值RSS、索引磁盘体积、全量构建/增量更新/重建耗时。运行前冻结重复次数、预热方式、缓存条件及资源预算。
- 合成扩容证明资源特性，不证明相关性效果。先测本地精确检索；目标规模确有延迟/内存/更新瓶颈时才提出Qdrant等向量库实验，对照精确结果测ANN召回损失和额外运维成本，不预先引入向量服务。

### 验收、分工与后续实施顺序

- 先离线合成测试：原文offset/中英混排/超长句/末尾要求/重叠/岗位去重；增删改/重启/幂等/损坏/失效/中断/原子切换；默认无模型依赖、两种失败降级配置。指标用手工可核算排名列表验证，不用假向量。
- 再真实本地Embedding合成语料接线与资源测量；随后人工标注样本评测Recall@K/MRR/nDCG，分开报告“工具正确性”“真实模型可运行性”“人工样本质量”。旧阶段二假向量历史测试不充当阶段三模型验收。
- 阶段二1165项是已有通过记录；后续实施先重新跑当时基线、保留旧功能与审核门禁，再增加专项、完整回归及独立恢复验证；本轮仅文档记录不产生阶段三测试成绩。

| 顺序/负责人 | 冻结或实施内容 | 汇总边界 |
| --- | --- | --- |
| 主代理先串行 | 冻结chunk、索引清单、标注格式、指标口径、错误/结果兼容接口与文件写入边界 | 未冻结不分派实现；共享契约与样本语义由主代理单写 |
| A（评测）并行 | 标注格式校验、手算指标测试、调参/消融执行器、报告与专项测试 | 不代替人工标注，不伪造模型质量 |
| B（长文分块）并行 | 分块、原文映射、岗位聚合及专项测试 | 按冻结契约工作，不改BM25/RRF算法 |
| C（索引）并行 | 独立存储、增删改、版本失效、原子重建/恢复及专项测试 | 不改真实证据库、不越过请求候选集 |
| 主代理串行实测与汇总 | 先锁运行时再下载固定模型、受控真实实测、引擎接入、交叉验证、完整回归与NOTES更新 | 共享契约/依赖/入口/NOTES单写；不并发加载模型，不把部分通过记成整体完成 |

历史方案记录轮的结束点为只记录方案；2026-11-12用户已授权开始实施，当前以置顶状态为准。后续交接须记当前分支/工作路径、冻结接口、runtime/model精确版本、数据及索引清单、已跑命令与结果、失败项和下一步；当前继续原窗口。

### 明确不做

不重做阶段二BM25/RRF、不写FastAPI端点、不做LLM匹配分析/差距报告/生成式回答或Reranker、不自动微调、不扩大采集、不改变人工审核状态。LLM匹配分析与差距报告留阶段4，HTTP/上传/服务化留阶段5；真实模型效果和8GB资源验收由实际证据决定。

## 历史收尾状态（2026-11-11，阶段二已完成并通过用户完整回归验收）

1. 用户已运行完整回归并反馈测试通过；阶段二匹配与检索引擎按已确认范围记为已完成、验收通过。
2. 已交付 MatchingRequest → MatchingResult、BM25、规则、可选 Embedding 接口与 RRF 混合检索、证据库只读适配器及离线合成验收；默认 BM25+规则。既有新增309项、完整1165项自动回归记录保留；用户本次反馈不补造新的测试数量、耗时或模型效果。
3. 本次完成状态不扩大真实模型验收：真实 Embedding 模型尚未下载或运行，语义质量、运行时兼容性及8GB资源表现仍待后续单独确认；显式降级和人工审核状态边界保持不变。
4. 阶段三尚未开始。暂不编写阶段三代码或展开实施，等待用户下一步通知，不自动续做；既有阶段三边界说明仅作历史规划保留。
5. 本次仅更新 NOTES.md 的进度与验收记录；源码、测试、依赖、数据库、画像/岗位 review_status 及既有证据保持不变。活动分支 feature/jobradar-v1；下方历史记录中的“下一步”保留当时语境，当前安排以本节为准。

## 阶段二实现记录（2026-11-11，BM25+规则先交；现已通过用户完整回归验收）

1. 阶段一已收尾并通过用户完整回归；1D契约不变。用户确认阶段二后，先串行冻结retrieval_models/text公共契约，再A BM25/B Embedding/C规则并行实现；主代理汇总RRF、只读adapter、演示及集成验证。
2. 默认BM25+规则无需新依赖；输出MatchingResult含身份/哈希/多来源、检索分项/排名、规则证据覆盖与三态约束、人工状态与通道状态。显式hard约束冲突单列排除，未知不冒充不满足，RRF不直接叠加原始分数。
3. Embedding提供注入接口/纯Python余弦/脚本客户端/内存缓存/已有本地固定快照的可选适配器。真实模型未下载、运行时未安装、真实语义/8GB资源尚未验收；不把假向量成绩写成模型效果。默认bm25_rules，hybrid只有allow_dense_fallback=True才允许固定错误码降级，返回degraded及实际bm25_rules。
4. 模型候选：multilingual-e5-small（384维、中英优先）、bge-small-zh-v1.5（512维、中文优先）、paraphrase-multilingual-MiniLM-L12-v2（384维、128-token短文本对照）；主机8GiB Intel，批大小1/CPU建议须经后续实测。候选/显式降级/依赖边界见docs/STAGE2_MATCHING_PLAN.md。
5. 阶段三保留实质工作：人工标注与留出评测/消融调参、长JD分块与聚合、持久索引/增量更新/删除/失效/重建、8GB冷/热启动和峰值RSS基准；Qdrant按规模实测决定。BM25/Hybrid/RRF已挪阶段二，不重复充当阶段三交付。
6. 本轮只用合成简历、岗位与临时证据库测试；不读真实岗位库，不改人工状态/旧功能/基础依赖，不接LLM分析/差距报告/Reranker/FastAPI。使用见docs/MATCHING_ENGINE.md，演示examples/matching_engine_synthetic.py。先由用户验收BM25+规则；真实Embedding与阶段三后续工作另行确认，不自动续做。
7. 本轮新增309项离线测试（BM25 68、Embedding/stub 76、规则122、集成43）；副本完整1165项通过（原856项保持，5个既有上游警告）。最终活动目录、独立回滚与逐字命令/结果以本批次VERIFICATION.txt为准。规则保守处理英文上限/可选数量，JD引文包含要求/加分的完整句段。

## 历史收尾状态（2026-11-11，阶段一已收尾）

1. 用户已运行完整回归并反馈测试通过；阶段 1D 匹配接口已完成并通过本次验收。
2. 阶段一按已确认范围收尾：1A 文本型 PDF 提取与预览、1B 带证据画像契约与规则基线、1C LLM 结构化、1D 匹配交接契约与纯转换均已完成；个人简历、OCR及匹配效果等未实施内容不因此视为已验收。
3. 1D 交付保留在 feature/jobradar-v1；已有137项新增专项及856项完整自动回归记录见下方完成记录和 docs/MATCHING_INPUT.md。本次用户反馈不改变画像或岗位的 review_status。
4. 阶段二尚未开始。暂不编写阶段二代码或展开实施，等待用户下一步通知，不自动续做。
5. 本次仅更新 NOTES.md 的进度与收尾记录；源码、测试、依赖、数据库及既有证据保持不变。下方历史记录中的“下一步”保留当时语境，当前安排以本节为准。

## 阶段 1D 完成记录（2026-11-11，已通过用户完整回归验收）

1. 用户确认方案后顺序实施，无子代理；新增 matching_models.py / matching_input.py：独立 SearchPreferences、带证据画像投影、完整岗位/多来源信封、MatchingRequest 纯组装。
2. 1B ResumeProfile 与 1C StructureResult.profile 共用入口；规则 comparison 不合并；未知、证据、版本、警告、人工状态原样保留。原 ResumeDocument 的引用核验仍在上游，不把投影当证据通过凭证。
3. 显式 debug/formal；formal 要求画像及岗位全部 confirmed。岗位双哈希校验与多来源保留、严格字段/嵌套重验/独立复制；不排序、不打分、不生成任职要求。
4. 新137项离线合成测试；原719项基线通过，新增后完整856项；最终逐字输出、独立副本回滚与四件套见本轮 VERIFICATION.txt。使用见 docs/MATCHING_INPUT.md；演示 .venv/bin/python -B examples/matching_input_synthetic.py。
5. 不接SQLite/FastAPI/算法/Embedding/真实LLM，不读密钥、不装依赖、不改旧功能或两库。证据库读取adapter和匹配引擎留阶段2，HTTP留阶段5；阶段2需用户另行确认。

## 历史记录（2026-11-10，黄金差异修复与3份真实合成验证通过）

1. 已完成：prompt-v3补字段边界、完整顶层技能汇总/首次出现顺序、学历与状态分离、自述正文边界；黄金比较新增有界脱敏path/rule诊断。未改黄金样本/精确比较，不放宽schema/validator、不修复模型JSON。
2. 定位边界：旧响应未保存，具体旧差异未知；合成变体复现摘要句号/技能顺序/状态跨度/顶层技能遗漏可通过本地却失败golden，暴露prompt未明确的字段约定。修复指令不证明旧响应是哪一项。
3. 新26项合成测试；完整719项离线先通过，再真实首份31.536秒通过后顺序跑后两份9.892/3.398秒，3/3通过本地schema/证据链与原黄金预期，总44.828秒exit0；review_status仍unconfirmed。
4. 本轮3生成、0计数API、零重试；固定APINebula/gpt-6.1-sol/low/90秒/原SDK与预算。usage6332输入+1723输出=8055；保守费用上界CNY0.017832360非账单，预留US$0.1590264保留。密钥仅真实进程临时读取后清除，不用子代理、只用合成简历、不入库/提交/建分支。
5. 活动目录保留v3及诊断；独立副本回滚恢复v2/原693项，当前完整719项通过。完整说明见docs/RESUME_GOLDEN_DIAGNOSTIC.md。本批次已结束，不追加生成；下一功能小步另行确认，3份样本成绩不等于一般简历准确率。

# JobRadar 当前状态

更新时间：2026-11-14（阶段四工程实现已交付；真实LLM质量待新预算确认，阶段五未开始）

## 当前版本

工作目录：`/Users/thc/Documents/Codex/2026-10-21/build-ios-apps-plugin-build-ios-3/outputs/job-radar`

原始版本保留在 `/Users/thc/Documents/Codex/2026-09-27/niu/outputs/job-radar`，未覆盖。

## 本次打包更新（2026-11-03）

- 新增 `pyproject.toml`：包名 `job_radar`，版本 `0.1.0`，Python `>=3.10`，从 `src` 发现包。
- 安装依赖直接读取现有 `requirements.lock.txt`，32 项精确依赖与安装元数据一致；锁文件和原有依赖版本不变。
- `.gitignore` 先增加 `*.egg-info/`、`build/`、`dist/`，生成的打包文件已确认被忽略。
- 现有 `.venv` 已执行 `pip install -e .`。本机配置的镜像未找到构建用 setuptools，仅该次安装临时使用官方索引；没有修改 pip 全局配置。
- README 和第三阶段教程已改成安装后直接运行，不再要求设置模块搜索路径或 Uvicorn 的 `--app-dir`。历史文档 `docs/README-20261101.md` 保持不变。
- `env -u PYTHONPATH .venv/bin/pytest -q`：67 项通过；`env -u PYTHONPATH ./CHECK.sh modified`：67 项通过。
- 真实启动 Uvicorn（无 `PYTHONPATH`、无 `--app-dir`），curl `/health` 和 `/jobs?limit=1` 均 HTTP 200；测试服务已停止。
- 在独立副本和独立虚拟环境测试回滚：恢复本次 5 个路径、移除副本的可编辑安装，旧方式运行仍为 67 项通过；工作目录的安装保留。
- 源码、测试逻辑、`pytest.ini`、依赖文件和数据库 SHA-256 保持不变；未增加业务功能。
- 验证记录：`verification/packaging/VERIFICATION.txt`；本次专用回滚：`verification/packaging/ROLLBACK.sh`。如需使用历史阶段回滚，先撤销本次打包更新。

## 学习进度

- 用户希望先理解项目结构、每个文件的职责和实际运行，不再要求逐行掌握解析器。
- 当前继续使用同步 requests。第三阶段已接 SQLite 与 FastAPI，同步查询路由使用普通 def；尚未加入异步数据库、Agent。
- HTML 获取与字段解析分离；真实站点规则集中在新增的 sources.py。

## 本次已完成

- Boss 公开首页最初正常响应的 81 条岗位已解析；真实列表卡片保存在 sample_data/boss-home.html。
- Boss 后续请求进入验证页，程序明确报错，退出 2，不以空结果覆盖 JSON。
- Python.org 招聘页通过新版 CLI 实时联网采集 21 条岗位，保存 JSON 和响应 HTML。
- 说明首页推荐并非 Python 搜索结果；列表摘要并非完整 JD；字段未提供时保留为空。
- 新增 `filters.py` 和 CLI 自定义筛选：title/city/company/skill/exclude-keyword/remote-only；不同选项 AND，同一选项重复 OR。
- 新增 `config.py` 和 `--config`：筛选条件可以保存为 JSON；命令行同名参数优先。
- 新增 `storage.py` 和 `--database`：使用 SQLite 保存岗位，按 URL upsert 去重；支持 `--query-city`、`--query-keyword` 和 `--limit` 查询。
- 修正原测试中因示例数据变动导致的过期断言，新增来源适配、HTTP 异常、CLI、JSON 保留、配置和 SQLite 测试；原有 39 项加本次 28 项，共 67 项通过。
- 原始文件及 SHA-256 已保存，回滚在独立副本验证；交付目录保留修改版。

## 第三阶段已完成

- api.py：GET /health、/jobs、/jobs/stats、/jobs/{job_id}；自动 /docs 与 /openapi.json。
- schemas.py：Pydantic 响应模型；参数校验、分页、技能精确匹配、来源筛选。
- storage.py：只读连接、SQL 参数绑定、同一快照计数与分页；同步连接显式关闭。
- 保留阶段二快照与 SHA-256；独立副本回滚恢复 39 项测试，不改动岗位数据库。
- 现有 data/jobs.db 为 102 条历史岗位（boss 81 + python.org 21）；未扩大采集范围。
- docs/STAGE3_FASTAPI.md：JD/面经来源、基础、工程进阶、场景题、学习路线与运行步骤。

## 历史 API 学习待办（保留记录，当前以置顶下一步为准）

1. 按 README 启动 Uvicorn，打开 /docs，练习 city/keyword/limit/offset。
2. 对比 requests 发请求与 FastAPI 接请求，理解返回的 items 和 total。
3. 阅读 api.py 与 storage.py 的调用链；解释 200 空结果、404、422、503。
4. 读一个 TestClient 测试，再独立新增一个组合查询测试。

## 结果文件

- data/boss-home.jobs.json：本次真实 Boss 首页快照的 81 条岗位。
- data/python-org.jobs.json：本次真实联网获取的 21 条岗位。
- data/jobs.db：SQLite 岗位数据库示例。
- sample_data/snapshots.json：精简快照的来源与 SHA-256。
- verification/stage3/VERIFICATION.txt：本阶段基线、修改版、独立回滚与真实 HTTP 验证。
- docs/STAGE3_FASTAPI.md：第三阶段完整教程。


## Job Importer 第一步（2026-11-07）

- 已完成：ImportedJob 契约、纯 Boss 详情 HTML 解析、39 项针对性测试与精简真实布局样本；完整 pytest 共 106 项通过。
- 真实样本 ~/Desktop/boss-test.html：AI Agent 开发实习生 / ONES / 深圳，JD 790 字符、20 行；保留原文、实习安排、页面标签及技能提及的边界。
- TXT 导入格式与字段切分规则已在 docs/JOB_IMPORTER.md 确定；本步没有实现 TXT、文件导入入口或入库。
- 当前本地数据库统计仍为 104 条（boss 81 + python.org 23）；上方第三阶段的 102 条是历史记录。本步未修改数据库。
- 后续状态：公司区域 Redis/Docker 独立练习已决定跳过；文件预览、TXT 和独立数据集入库已在第二/三步完成。
- 已知问题：同 URL 的旧 upsert 可能用列表摘要覆盖完整 JD；attendance/source_tags/溯源字段尚无存储；入库须另定策略。
- 运行及讲解：docs/JOB_IMPORTER.md；保留原有 29 个 verification/packaging 未跟踪文件，不提交、不建分支、不安装依赖。


## Job Importer 第二步（2026-11-07）

- 已完成：txt_importer.parse_job_txt 按 jobradar-txt-v1 实现纯 TXT 解析；返回同一种 ImportedJob。
- 已完成：import_preview.preview_file 与 python -m job_radar.import_preview 单文件预览；只读文件一次，SHA-256 基于原始字节；provenance 含绝对来源路径、格式与 UTC 处理时间，review_status=unconfirmed。
- 预览输出字段、缺失警告和多行 JD；无头部 TXT 明确报错，不推断岗位元数据。桌面 boss-evidence 的 15 份 TXT 是原文备份，HTML 失败时需另存并补头部再预览。
- 测试：新增 55 项 TXT/预览测试全部通过；完整 pytest 161 项通过（第一步与原 106 项保持原样）。
- 验收：真实 boss-ones-ai-agent.html 预览成功；对应无头部 TXT 按约定失败；输入证据文件与数据库保持不变。
- 使用：docs/JOB_IMPORTER.md 第 8 节；本步未改 HTML 解析器、ImportedJob 契约或已有测试，未安装依赖、未接 SQLite、未做批量入库。
- 后续状态：第三步的批量导入、JD 防降级与 provenance 持久化已完成，真实入库核验见下方。


## Job Importer 第三步实现（2026-11-09，已入库）

- 已完成：dataset_store.py 独立 SQLite 三表、full_jd 约束、完整事实 URL 去重、防覆盖、原库/别名拒绝、事务与逻辑快照检查；不调用旧 upsert。
- 已完成：batch_import.py 顶层 HTML 扫描、严格 TXT 回退、逐项失败收集、批内/已有库冲突分类、JSON+Markdown 完整 JD 预览、dry-run/apply --confirm CLI。
- 新增 64 项批量/存储测试；完整回归 225 项通过，原有 161 项及第一/二步实现未改。
- 真实 dry-run：15 HTML、15 拟新增、0 回退、0 重复、0 冲突、0 失败。报告位于 ~/Desktop/jobradar-v1-data/reports/evidence-dry-run.json 及同名 .md。
- 目标独立库：~/Desktop/jobradar-v1-data/evidence.sqlite3；2026-11-09 00:40 已 --apply --confirm，插入 15 条、provenance 15 条，run_id 5c2e9853-f1d1-4152-b4ba-0b5bf8fa6cc4；验收全过（imported_jobs 15 行、provenance 15 行、review_status 全 unconfirmed、description_kind 全 full_jd、15 个不同 URL）。
- 入库初始状态 unconfirmed；完整事实和 provenance 分开，原始 SHA-256 与实际 UTC 入库时间留痕；同内容重导不覆盖事实或人工状态。
- 保持不变：data/jobs.db、storage.py/upsert、既有解析/契约/测试、桌面15份HTML+15份TXT、用户提取脚本；不安装依赖、不提交、不建分支。
- 运行与故障处理见 docs/JOB_IMPORTER.md 第9节；第三步已完成，后续补数据时重跑 dry-run 再 apply。


## 真实证据入库核验与阶段 0 收尾（2026-11-09）

- 用户已完成入库；本次只读核验，不执行 apply、不更改人工状态。
- 证据库：/Users/thc/Desktop/jobradar-v1-data/evidence.sqlite3。
- 表统计：imported_jobs=15、job_provenance=15、import_runs=1；15 条 description_kind=full_jd，review_status 全为 unconfirmed，缺 provenance=0。
- 实际入库完成时间：2026-11-08T16:42:31.216765+00:00（北京时间 2026-11-09 00:42:31）；该时间来自 import_runs，不是文件修改时间推测。
- 导入前 dry-run 报告保留原样：/Users/thc/Desktop/jobradar-v1-data/reports/evidence-dry-run.json 与同名 .md；它们仍写“未入库”是当时快照，不代表现在状态。
- 已核对 15 个岗位 URL、provenance SHA-256 与原计划/证据一致，外键检查通过；原 data/jobs.db 仍为 104 条。
- 注意：现有 FastAPI 仍查询旧 jobs 表，未自动接通独立 imported_jobs；V1 读适配留阶段 2/5，不把新库路径直接填给旧 storage.query_jobs。
- 文档：旧公司区域练习已跳过；阶段 1 的原理、替代方案、契约、边界、匹配接口及验收见 docs/RESUME_PARSING_PLAN.md。
- 本轮只改文档，保留全部源码/测试/依赖/两库/证据文件。阶段 1 首个小步等待用户确认后开发。


## 阶段 1A：PDF 提取与本地预览（2026-11-09）

- 用户已确认第一小步：resume_models.py 定义分页/文本块/来源契约；resume_pdf.py 仅适配 PDF bytes；resume_preview.py 负责本地文件读取、原始字节哈希与终端预览。
- 固定版本 PyMuPDF==1.26.7 已安装，并加入 requirements.txt 与 requirements.lock.txt；原镜像未提供该包，本次安装临时指定 https://pypi.org/simple，没有修改 pip 全局配置、升级 pip 或其他既有依赖。
- 保留 PDF 页号、文本块 ID、文本、bbox 与解析器/提取器版本；提取结果只是文档证据，不推断技能熟练度或生成 ResumeProfile。
- 默认上限 10 MiB / 10 页；空文件、非 PDF、不可恢复损坏、需密码、超限、全文无可提取文字分别处理；可恢复损坏带 pdf_repaired 警告；部分无文字页保留页码和警告。
- 本地预览不联网、不写库、不覆盖 PDF；多栏/表格提取顺序仍须人工核对，未开启 OCR。
- 新增测试全离线生成合成 PDF；专项 64 项、完整回归 289 项通过；5 条 PyMuPDF 上游 SWIG 弃用警告保留未屏蔽。真实简历 PDF 尚未提供，因此尚未完成真实简历验收。
- 使用、字段与错误说明见 docs/RESUME_PDF.md；下一步先由用户运行测试和预览，再确认是否进入 1B。两份岗位库与证据原件继续保留。


## 阶段 1B：画像契约与规则基线（2026-11-10）

- 用户确认字段与证据设计后实现，无子代理；新增 resume_profile.py、resume_profile_rules.py、合成测试与演示，不改 1A、旧解析器及已有测试/依赖/数据库。
- TextFact/DateFact 各字段附 page/block_id/逐字 quote；技能规范名与原始 mention 分开；绑定原文件 SHA-256 和 parser/extractor version。校验成功不自动确认人工状态。
- 保守规则排除已识别否定/学习计划/招聘要求块；独立风险标题覆盖后续块直至明确新分节；有歧义宁可留警告。未知不等于不会，不生成熟练度/年限/匹配分。
- 规则只自动填技能；教育/项目/工作/摘要保留契约和 not_extracted 警告，完整语义提取留 1C。专项 123 项、全量 412 项合成/既有回归通过；独立副本回滚原 289 项通过。
- 使用：docs/RESUME_PROFILE.md；演示：.venv/bin/python -B examples/resume_profile_synthetic.py。
- 本轮交付目录：/Users/thc/.codex/visualizations/2026/11/10/01a1c4c2-2553-7db0-9660-72c741bb2aa2/jobradar-resume-1b；含 MODIFIED_FILE、DIFF_FILE、VERIFICATION.txt、可执行 ROLLBACK.sh。活动目录保留实现，回滚仅在副本测试。


## 阶段 1C-1：离线结构化闭环（2026-11-10）

- 用户确认先做离线闭环；新增 resume_structure.py、resume_structure_fake.py、合成黄金样本、测试和演示，不改1A/1B/旧解析器/既有测试/依赖/数据库。
- 模型草稿仅可返回 education/skills/projects/work_experience/summary/warnings 六键；ID/版本/来源/人工状态由本地控制，额外字段明确失败。
- 单次调用，无重试、无静默JSON修复或规则兜底；空白响应/无效JSON/格式/引用/超时/未完成状态区分。固定提示词与JSON资料分离，来源/原文引用继续复用1B。
- 输入/输出默认各128KiB UTF-8，输入最多1000块；超时预算30秒交客户端执行，不宣称同步层可强杀挂起客户端。字节上限不是token/费用保证。
- 规则和候选技能差异单独报告，不取并集；文档质量警告保留，自动状态仍unconfirmed。假客户端不具备提取智能；黄金样本不证明真实模型准确率或通用语义归类正确。
- 专项112项、完整524项通过；独立副本回滚原412项通过。无子代理、全合成、无网络/真实模型/本人简历/密钥/新依赖/入库。
- 使用：docs/RESUME_STRUCTURE.md；演示：.venv/bin/python -B examples/resume_structure_synthetic.py。
- 本轮交付：/Users/thc/.codex/visualizations/2026/11/10/01a1c4c2-2553-7db0-9660-72c741bb2aa2/jobradar-resume-1c1；MODIFIED_FILE、DIFF_FILE、VERIFICATION.txt、可执行ROLLBACK.sh；活动目录保留1C-1。


## 阶段1C-2：APINebula适配与首批合成验证（2026-11-10）

- 用户确认APINebula https://apinebula.ai/v1 / gpt-6.1-sol / reasoning low；固定SDK安装，原有依赖版本不变。
- 严格schema独立转换、完整请求本地输入分配、SDK真实HTTP内存模拟、usage和模型校验、预算/次数锁与零重试实现；原1B/1C-1字段/证据语境规则未放宽。
- 用户分组价CNY/1M输入0.78/输出3.90/缓存创建0.975；保守叠加输入与缓存创建预留，单次¥0.0530088，三次¥0.1590264；另每CNY单位按1USD预算额度分配（非汇率）。预算US$0.20，失败不退预留。
- 真实调用进程读取用户指定key且finally清除；首份合成请求client_timeout，1生成/0计数API，立即停止，无后两份，无usage/扣费结论，不宣称提取成功。
- 离线专项95、完整619通过；95新增均为合成样本、固定SDK与MockTransport，无socket。后续真实批次需用户确认；参考docs/RESUME_APINEBULA.md。
- 交付目录：/Users/thc/.codex/visualizations/2026/11/10/01a1c4c2-2553-7db0-9660-72c741bb2aa2/jobradar-resume-1c2；MODIFIED_FILE、DIFF_FILE、VERIFICATION.txt、可执行ROLLBACK.sh；活动目录保留实现。


## 阶段1C-2：90秒重验（2026-11-10）

- 用户授权30→90秒并只先跑首份，完整通过才继续后2份；新批次最多3生成，每次零重试，模型/low/费用预留保持。
- 修改APINebula TIMEOUT_SECONDS=90.0并让演示显式传90；不只改SDK而漏掉1C-1默认30秒。新增10项合成测试，专项105/完整629通过，无依赖改动。
- 真实执行首份1生成/0计数API，批次34.298秒返回schema_error，本地ResumeDraft字段校验失败；没有后2份/重试。usage1430输入+1395输出=2825；保守费用上界¥0.00795015非账单，预留US$0.0530088保留。
- 密钥仅真实进程临时使用，finally清除；没写入代码/日志。原始响应未保存，不推测具体schema错误字段；后续诊断与新调用须用户再确认。
- 交付目录：/Users/thc/.codex/visualizations/2026/11/10/01a1c4c2-2553-7db0-9660-72c741bb2aa2/jobradar-resume-1c2-timeout90；MODIFIED_FILE、DIFF_FILE、VERIFICATION.txt、可执行ROLLBACK.sh。独立副本恢复原30秒与619测试，活动项目保留90秒。


## 阶段1C-2：schema_error定位（2026-11-10）

- 用户批准首份单次真实脱敏诊断，1生成0计数/无重试/后2份；33.242秒输出draft.education[0].end_date / date_normalization_mismatch，exit1。仅证明本次响应，不把旧未留存响应推定同因。
- 日期规范值非null且不匹配明确年月；黄金预计毕业应null。实际值未记，不推测字面内容；没有自动清空/修复后算成功。
- resume_structure.py增SchemaIssue与schema_stage/schema_issues，最多8条固定字段名路径/规则码，不记录值/引文/msg/ctx/未知字段名；草稿和画像装配分阶段。原错误消息/业务验证保持。
- 新增33项全合成测试，完整662通过；独立副本回滚恢复此前629项。无依赖、schema、prompt、模型、90秒或预算变动，无子代理。
- docs/RESUME_SCHEMA_DIAGNOSTIC.md记录真实诊断、wire/local校验差异和下一步提示词建议（尚未实施）。
- 交付目录：/Users/thc/.codex/visualizations/2026/11/10/01a1c4c2-2553-7db0-9660-72c741bb2aa2/jobradar-resume-1c2-schema-diagnostic；MODIFIED_FILE、DIFF_FILE、VERIFICATION.txt、可执行ROLLBACK.sh，活动目录保留诊断版。


## 阶段1C-2：日期提示词v2（2026-11-10）

- 用户确认日期提示词增强：固定SYSTEM_PROMPT加纯年月/保留状态/未知日期规则和4正例2反例，PROMPT_VERSION=v2；不修改validator、wire schema、黄金样本或修复响应。
- 新增tests/test_resume_structure_date_prompt.py共31项合成测试，完整693通过；三份样本本地分配8986/8479/8520<=12000。独立副本回滚原662通过，活动v2保留。
- 真实请求0/密钥读取0/无子代理；模型low/90秒/预算/依赖/数据库不变。真实v2效果等新预算确认后验证，不把离线预设响应当模型成绩。
- 文档docs/RESUME_DATE_PROMPT.md含完整可复用日期片段/例子/运行说明；交付目录：/Users/thc/.codex/visualizations/2026/11/10/01a1c4c2-2553-7db0-9660-72c741bb2aa2/jobradar-resume-1c2-date-prompt-v2；MODIFIED_FILE、DIFF_FILE、VERIFICATION.txt、可执行ROLLBACK.sh。
