# 阶段二：轻量匹配与检索引擎

日期：2026-11-11。输入沿用1D `MatchingRequest`，不改简历/岗位事实契约。默认BM25+规则；本轮Embedding/Hybrid测试使用脚本向量，真实模型质量与资源验收尚待后续配置。

## 1. 输出和主调用

```python
from job_radar.matching_engine import match_jobs
from job_radar.retrieval_models import EngineConfig

# request为已有1D MatchingRequest；引用核验仍在1B/1C上游完成。
result = match_jobs(request, config=EngineConfig(hard_constraints=["city"]))
```

`MatchingResult`包含画像ID/版本/source/人工状态、purpose、请求模式/实际模式、status、完整配置、通道状态、ranked results、excluded和warnings。每条结果含数据集/岗位ID、URL、JD/事实哈希、多来源记录、岗位人工状态、BM25与dense原始分数和排名、RRF融合分数、规则分项和证据。

- 默认mode=bm25_rules，不加载任何ML包。
- purpose=debug/formal及原人工状态门禁重新校验；算法不改变review_status。
- status区分ok/degraded/no_query/no_candidates/no_hits。空画像或空岗位不是网络故障，更不输出虚假分数。
- 排名是检索信号，不是录用概率或完整胜任度。
- skills/经历之外只用明确target_roles作为查询，不从学校/公司/城市猜求职方向。
- 查询文本去重但保留支持路径/原始引用；编码文本移除常见email/URL/电话号码，原始证据仍保留，非完美脱敏承诺。
- 默认query/JD编码文本最多16000字符、1000岗位；超限明确失败，不静默截断。真实模型token上限可能更小，也明确拒绝。

## 2. BM25、Dense与融合

`retrieval_text.py`统一字符bigram中文基线和独立技术词token，显式Python3/Python 3/py.test/Tool-Calling别名；不模糊匹配Pythonista。

`retrieval_bm25.py`是无新依赖的weighted-token BM25：标题权重同时参与词频/长度归一化，正IDF，固定参数，不凑零分结果。不是通用搜索引擎或BM25F。

`retrieval_embedding.py`提供EmbeddingClient协议、ScriptedEmbeddingClient假客户端、纯Python稳定余弦、批次形状/维度/非有限/零向量校验、固定错误码、进程内有限缓存和LocalSnapshotEmbeddingClient可选适配器。Dense只编码RetrievalDocument.text；模型/版本/维度和query/passage角色进入缓存键，不共用不同模型的向量。

`retrieval_fusion.py`采用RRF排名融合，避免直接相加异尺度分数。两通道先各取recall_k，融合按score、规则required覆盖率同分次序、稳定岗位身份排序，再取top_k。默认top_k=10/recall_k=50/rrf_k=60；参数有类型/范围约束，top_k不超过recall_k。

## 3. 规则：证据覆盖，不是能力等级

`matching_rules.py`只识别有限技能词表与明确信号：必须/要求/must/required、加分/优先/bonus/preferred，纯OR备选组、纯AND独立组。混合连接/条件/识别不了的要求保留mention与warning；否定要求不算正向required。ImportedJob.skills不直接当必须要求。

SkillCheck给出alternatives/kind、evidence_found或not_found、支持技能/简历路径与EvidenceRef，以及JD JobSpan(field/start/end/quote)。`not_found`只表示未发现支持，不是不会。required覆盖率=有证据的required组/可解析required组；分母为0时None，解析不全会保留warning，不能解读成完整JD满足率。

ConstraintCheck给出城市、出勤天数、持续月数三态。仅明确hard_constraints配置且not_satisfied时排除；unknown继续留待确认。无法可靠解释的范围、条件、否定语句保留未知。学历/经验字段暂不自动判断，输出not_evaluated警告；不把预计毕业当已取得学历，不把课程项目当正式工作。

## 4. 8GB机器与显式降级

主机实测8GiB Intel Mac，未安装torch/sentence_transformers/transformers/numpy。模型候选与阶段三边界详见 [阶段二计划](STAGE2_MATCHING_PLAN.md)。本轮未下载模型、未安装运行时、无真实Embedding调用。

```python
# 有意先交BM25+规则：不是故障，dense.status=disabled。
config = EngineConfig(mode="bm25_rules")

# 允许向量失败时降级；缺客户端会明确返回degraded+原因。
config = EngineConfig(mode="hybrid", allow_dense_fallback=True)
result = match_jobs(request, config=config)
assert result.actual_mode == "bm25_rules"
assert result.status == "degraded"
```

缺客户端的reason为embedding_client_missing。启用客户端后的加载/编码/向量错误使用dense_*固定码；raw异常/模型正文不拼入错误码。allow_dense_fallback=false时明确抛错，不自动重试/换模型/换API。全体岗位因硬约束排除时无需加载向量模型。

本地快照适配器需绝对snapshot目录（目录名为40位revision）、已存在config.json、明确候选model_id；CPU/batch=1默认、local_files_only=True、trust_remote_code=False。只通过lazy导入加载调用方预先安装的SentenceTransformer；真实runtime版本尚未在本轮安装锁定，stub测试不是实际runtime兼容性验收。使用前需独立确认安装与model revision、资源预算。

同步客户端不提供硬墙钟超时，也不能捕获OS杀进程；真实模型测量需隔离进程，超时/退出由外围进程监督处理后用默认BM25路径重跑，不声称同进程异常捕获能解决OOM。

## 5. 证据库独立只读适配

```python
from job_radar.evidence_repository import read_job_candidates
jobs = read_job_candidates(database_path, dataset_id="caller-stable-dataset-id")
# 后续沿用build_matching_request，核心match_jobs不读取数据库。
```

adapter使用mode=ro/query_only和同一事务快照，核对application_id/user_version、外键、ImportedJob结构/双哈希、至少一条provenance与多来源关联。缺库不创建，旧库不兼容明确失败，不读取source_file原件，不暴露本地绝对路径，不写人工状态。

本轮只在测试临时库运行，不重新读取真实证据库，也不把历史15条unconfirmed自动升级为confirmed。

## 6. 离线运行

```bash
.venv/bin/python -B examples/matching_engine_synthetic.py
.venv/bin/python -B -m pytest -q tests/test_matching_engine.py tests/test_retrieval_bm25.py tests/test_retrieval_embedding.py tests/test_matching_rules.py
.venv/bin/python -B -m pytest -q
```

演示使用内存合成简历和3岗位：词项相关岗位入选、城市硬冲突排除；另展示显式hybrid→bm25_rules降级。假向量专项只证明余弦/融合/协议正确，不证明真实语义召回。实现前基线856通过；新增309项（BM25 68、Embedding/stub 76、规则122、集成43），副本完整1165通过。最终活动目录/回滚的精确命令、输入与逐字输出见同批次VERIFICATION.txt。

## 7. 阶段边界

阶段二：内存BM25/向量接口/RRF/有限规则/只读adapter/离线验收；BM25+规则先交。

阶段三：标注与留出评测、消融调参、长JD分块/聚合、持久索引/增量更新/删除/重建、资源基准与规模化存储选择。具体承诺和验收条目已写在STAGE2_MATCHING_PLAN.md开头，不是空壳。

后续LLM匹配分析、差距报告、Reranker、FastAPI端点/上传/服务化不在本轮。阶段二不修改1D、原解析/画像、既有测试、基础依赖、数据库或证据原件；活动分支feature/jobradar-v1，未提交推送。
