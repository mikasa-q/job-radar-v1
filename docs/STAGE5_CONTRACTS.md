# 阶段5 / 第一步共享契约冻结 v1

日期：2026-11-16（北京时间）。分支：feature/jobradar-v1。
授权：主代理串行冻结并建立最小离线闭环；本步不派子代理、不读凭据、不调真实LLM、不运行真实小样本。后续A/B/C与真实验收须用户再次确认。

## 1. 权威接口与当前实现边界

权威模型：`api_v1_models.py`；应用：`api_v1.create_app(ServiceSettings)`；存储：`api_v1_store.ResourceStore`；编排：`api_v1_runtime.OfflineService`；预算纯函数：`api_v1_budget.py`。阶段1–4模型、算法、提示词、校验、原API源码不改。

| HTTP | 输入 | 成功响应 |
|---|---|---|
| GET /health | 无 | 原有 `{status:ok}`，只表示存活 |
| GET /v1/ready | 无 | Envelope[Ready]，依赖就绪200，否则503，data.status=not_ready |
| GET /v1/jobs | limit=20(1..100), offset=0(>=0) | Envelope[JobsPage]，完整证据岗位，空列表200 |
| GET /v1/jobs/{dataset_id}/{job_id} | 复合岗位身份 | Envelope[JobCandidate] |
| POST /v1/resumes | multipart，仅一个file；Idempotency-Key | 201 Envelope[Resource[ResumeDocument]] |
| GET /v1/resumes/{resume_id} | rsm_ID | Envelope[Resource[ResumeDocument]] |
| POST /v1/resumes/{resume_id}/profiles | `{mode:rules\|llm}`；幂等键 | 202 Envelope[Accepted] |
| GET /v1/profiles/{profile_id} | prf_ID | Envelope[Resource[ProfilePayload]] |
| POST /v1/matches | MatchCreate；幂等键 | 202 Envelope[Accepted] |
| GET /v1/matches/{match_id} | mtc_ID | Envelope[Resource[MatchPayload]] |
| POST /v1/reports | `{match_id, mode:offline\|llm, top_n:3}`；幂等键 | 202 Envelope[Accepted] |
| GET /v1/reports/{report_id} | rpt_ID；format=json\|markdown | JSON信封或text/markdown附件 |

旧 /jobs、/jobs/stats、/jobs/{job_id} 继续读旧jobs库，保留原错误/响应。新接口只用独立evidence_repository只读适配器；配置DB/索引路径仅来自服务端，不接受HTTP路径或自造检索结果。

第一步开放pdf/rules/bm25_rules/offline；llm固定503 llm_disabled；hybrid固定503 hybrid_not_enabled（包括allow_dense_fallback=true）。后续启用Hybrid时仅沿用原引擎明确允许的Dense失败降级，不允许绕过快照/索引损坏。本步不装模型、不构建真实数据索引。

## 2. 响应信封与状态

`{schema_version:jobradar-api-v1, request_id:req_<32hex>, data:T|null, error:ApiError|null}`。data/error恰一非null；错误含固定code/message及最多8个不含输入值的字段路径；未知字段名替换__field__。新接口返回X-Request-ID、Cache-Control:no-store。Markdown是唯一业务下载体例外。

Resource字段：resource_id/kind/state/execution_mode/created_at/updated_at/input_sha256/payload_sha256/payload/error。时间为真实带时区UTC。资源前缀rsm/prf/mtc/rpt + 下划线 + UUID4的32位小写hex；ID不是文件路径、用户身份或人工认证。服务本地单用户使用，不对公网提供鉴权能力。

TaskState：queued → running → succeeded | failed；重启时queued/running → interrupted，终态不可回退。成功必须有payload及hash且无error；失败/中断必须无payload且有固定error；排队/运行无结果。GET任务返回200表示查询成功，必须检查state。

Accepted：resource_id/state/status_url；POST任务首次/重放均202，可能已是终态。上传成功/同键重放均201，提取失败返回具体4xx/5xx且保留失败资源；同键不再解析。

ProfilePayload：resume_id + 完整document + 原ResumeProfile + comparison=null（规则未调用模型，不伪造模型比较）。MatchPayload：document + 原MatchingRequest + index_manifest + engine_config + 原IndexedMatchingResult + snapshot_sha256。ReportPayload：match_id + 同一snapshot_sha256 + 原MatchReport + 同源Markdown。

任务成功不代表模型成功：离线报告任务succeeded，但内部report.status=not_run、所有analysis=not_analyzed、draft=null、review_status=unconfirmed。原排名/分项/排除项/审核状态保持。横幅只在新API展示包装替换为“本报告未调用LLM”，不改历史报告/renderer/生成逻辑。

## 3. 请求与快照

MatchCreate固定profile_id、purpose(debug默认/formal)、SearchPreferences、显式candidate_keys（最多1000、不重复，可空）、原EngineConfig（默认bm25_rules/top_k10；HTTP不放大max_jobs）。输入额外字段拒绝。formal复用原confirmed门禁，API不修改审核状态。

服务捕获完整document/profile/request/jobs/manifest；执行检索前核对同一索引generation，执行使用原引擎的pinned reader。报告只用已存MatchPayload，不重读当前证据库/索引。快照hash包含完整原始证据、配置与审核元数据，不代表材料真实性。

存储以私有目录(0700)、OWNER、实例flock和一个原子state.json(0600)实现。journal含版本、固定预算政策、资源、输入、冻结执行快照、幂等绑定及hash；先写临时文件/fsync，再原子replace/fsync目录。启动逐资源/输入/快照/payload重验。hash防意外漂移，不是防有写权限者伪造的签名。存储上限256MiB/256资源；达到限额显式store_unavailable，不删除未过期的幂等记录。

原PDF只在有界上传/解析过程中存在，不写入业务journal；document原文证据保留用于报告。multipart临时文件在form上下文结束关闭。资源保存无自动TTL；用户应使用独立私有运行目录，后续清理/多用户策略另定。

## 4. 幂等与执行

所有POST要求8..128位 `[A-Za-z0-9._:-]` Idempotency-Key。作用域为路由（含父资源ID）+ key，持久保存其hash，不存原键；输入使用排序JSON的canonical_hash，上传按实际字节SHA256+长度绑定，不按文件名。

同键同输入返回原资源，且在读父资源/数据库之前命中；同键异输入409 idempotency_conflict。失败/中断也重放原状态，不重执行。不同key视为新操作；付费阶段还必须受全局次数/预算约束。

单实例flock禁止两服务共享同一root；一个工作线程执行离线画像/匹配/报告，最多4个待执行+执行槽（可配1..16）；满返回429且不写幂等占位。上传解析同样占槽。资源创建和幂等绑定在同一journal原子提交。成功POST202前已持久化queued；进程崩溃重启只标interrupted，不自动补跑。本步线程型本地规则/检索没有可强杀超时承诺；其输入采用既有引擎限额。

PDF独立spawn子进程：实际文件<=10MiB、<=10页、输出JSON<=8MiB；墙钟默认15秒（可配<=30秒），CPU10秒，父进程ps采样RSS>256MiB终止；RSS为采样保护，不声称硬内存封顶或OS级OOM保障。macOS不依赖不可用RLIMIT_AS。超时/异常清理子进程；不做OCR。

## 5. 错误码

权威映射ERROR_HTTP；业务错误不直接输出原异常/路径/供应商正文。

- 404 resource_not_found；405 method_not_allowed。
- 409 idempotency_conflict / formal_requires_confirmed / snapshot_conflict。
- 413 payload_too_large / pdf_size_limit；415 unsupported_media_type。
- 422 invalid_request / idempotency_key_required / idempotency_key_invalid / core_rejected；PDF各码pdf_empty_pdf、pdf_invalid_pdf、pdf_password_required、pdf_page_limit、pdf_no_pages、pdf_no_extractable_text、pdf_extract_failed。
- 429 queue_full。
- 503 llm_disabled / hybrid_not_enabled / dataset_unavailable / index_unavailable / snapshot_corrupt / store_unavailable / dependency_not_ready / task_interrupted / budget_exhausted / attempt_limit / budget_halted。
- 502 upstream_error、504 execution_timeout：为后续/受监督执行预留；500 internal_error。
- 无查询、空候选、无命中是现有业务状态，不伪装成异常；不触发LLM。
- 已接受任务失败：GET200，resource.state=failed及error；不把错误吞成成功报告。

## 6. 预算冻结（本步不授权真实调用）

cap ¥1.00；历史actual ¥0.05320458；历史reserved ¥0.6829056；保守总暴露¥0.73611018。来自阶段4最终RESULT，非账户账单。全部Decimal字符串，拒绝NaN/负数/浮点。

预算全局累计，不按请求/资源/重启重置。每次真实请求前先持久预留完整上界和次数，再出网；max新增生成9，串行1、零重试、首失败停。LLM画像/报告共用预算和次数，不双开额度。失败/未知usage不释放预留；未知usage停止；已知usage累加actual但保留reserve，保守重复占用明确记录。计数API禁用。

纯函数reserve/record_usage只用于冻结算术与测试，**不是持久收费执行器**；真实入口require_live_authorization固定抛llm_disabled；不存在env/HTTP开关。新API仅把不可变policy写journal，未写真实预留。本步无供应商适配接入。后续启用前须核对最新旧账、确认样本/次数/token限额/费率、做跨请求持久账本并另获授权。模型gpt-6.1-sol/low、报告v3；不继承旧批次可发送权限。

## 7. 验收与后续交接

先合成PDF+临时证据库+独立索引的TestClient端到端；确认HTTP与原核心函数结果相等、数据库不变、真实调用0、LLM/Hybrid拒绝、幂等并发/重启/漂移/队列及预算算术；完整回归；独立副本回滚与原hash恢复；真实Uvicorn仅本机合成离线HTTP验证。精确命令/结果以交付VERIFICATION.txt为准。

A/B/C后续只按本契约分模块实现；共享模型/存储/执行器/预算/依赖/应用入口/NOTES由主代理单写。当前停止点是第一步交付，等待用户确认，不派发、不真实验收。

可复用交接提示词：

```text
只按 docs/STAGE5_CONTRACTS.md 冻结契约工作。先核对本轮授权范围与原始hash。
不改阶段1–4核心、旧API或数据库结构；不把任务succeeded当LLM成功。
资源ID/快照/错误/幂等/预算由共享层统一；不重复创建账本或覆盖共享文件。
真实LLM仍关闭；需要启用时先交离线模拟证据，等待用户另行确认。
```
