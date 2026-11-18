# 阶段二：匹配与检索引擎（实现契约冻结版）

日期：2026-11-11。用户确认后，主代理先串行冻结公共契约，再并行 A（BM25）、B（Embedding）、C（规则）。原计划中的 BM25/Hybrid/RRF 前移到阶段二；1D 输入契约保持。

## 阶段三仍有的实质交付（尚未实施）

阶段二交付轻量内存引擎与规则，不把阶段三清空。阶段三做检索工程化与质量迭代：

1. 人工标注的相关性数据集与留出评测；BM25/向量/混合消融、Recall@K/MRR/nDCG 对比、错误分类和参数版本。
2. 长JD的证据保留分块、重叠/聚合策略与对照实验，不静默截断长文。
3. 索引持久化、数据快照/模型/投影版本绑定、增量更新/删除、缓存失效、原子切换与重建恢复。
4. 8GB目标机器的冷/热启动、延迟、峰值RSS、吞吐基准；根据数据规模再决定是否引入Qdrant等检索服务。
5. 本地索引schema迁移与失败恢复的集成验收。不是重复写BM25/RRF，也不提前实现HTTP服务。

LLM匹配分析与差距报告仍留后续阶段3/4的独立确认；Reranker为后续独立小步；FastAPI/上传/服务化留阶段5。本轮都不实现。

## Embedding 候选与 8GB 显式降级

模型卡核对日期：2026-11-11。实际机器 sysctl hw.memsize=8589934592（8GiB），uname -m=x86_64；当前环境无torch/sentence_transformers/transformers/numpy。内存/运行时间尚无真实模型测量，模型参数量不等于推理峰值内存。

| 候选 | 角色 | 维度 / 原生长度 | 注意点 |
|---|---|---|---|
| intfloat/multilingual-e5-small | 首选中英检索候选 | 384 / 最多512 tokens | 查询加query:，岗位加passage:；平均池化并归一化 |
| BAAI/bge-small-zh-v1.5 | 中文优先候选 | 512 / 最多512 tokens | 检索短query推荐“为这个句子生成表示以用于检索相关文章：”；passage不加指令 |
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | 多语言短文本对照，不承诺更小 | 384 / 128 tokens | 不是长JD默认模型，不能静默截断 |

参考模型卡：
- https://huggingface.co/intfloat/multilingual-e5-small
- https://huggingface.co/BAAI/bge-small-zh-v1.5
- https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2

先交 BM25+规则默认路径，不增加基础依赖。Embedding为注入式可选通道；假客户端只验接线/余弦/缓存/错误，不能当语义准确率。可选本地客户端只加载调用方已有的固定版本本地模型快照，不自动下载/安装、不读取密钥、不自动换API。真实模型下载、运行时安装、具体revision与机器实测另行确认。

- mode=bm25_rules：Embedding明确disabled，不是故障降级。
- mode=hybrid且allow_dense_fallback=false：缺客户端、加载/编码失败、超时/资源失败明确终止。
- mode=hybrid且allow_dense_fallback=true：可识别向量通道失败时返回BM25+规则，输出degraded状态、固定错误码与实际模式。分数不伪造为0，不宣称混合成功。
- 模型隔离进程被OOM杀死，外围执行器退出/超时也须返回显式失败后重新走BM25路径；同进程异常捕获不宣称能保护整个OS。默认先交BM25+规则规避此依赖。

## 阶段二交付与顺序

Primary goal：MatchingRequest进来后，产出可追溯的排序、规则分项、未知值和通道状态，而不是胜任率。

1. 主代理冻结retrieval_models.py/retrieval_text.py与本文公共签名、共享样本语义。
2. A/B/C各只写各自模块与专项测试，不改1D/共享模型/依赖/NOTES。
3. 主代理实现RRF、引擎编排、独立证据库只读adapter、合成演示和集成测试。
4. 先离线合成与假向量测试，再完整回归；真实Embedding效果明确留待固定模型实测，不冒称已验收。
5. 保存原哈希、修改副本、补丁、完整验证与独立回滚；活动项目保留实现，不提交或推送。

## 冻结公共签名（新增，不改变1D）

- retrieve_bm25(query: RetrievalQuery, documents: list[RetrievalDocument], config: EngineConfig) -> list[RetrievalHit]
- retrieve_dense(query: RetrievalQuery, documents: list[RetrievalDocument], config: EngineConfig, *, client: EmbeddingClient) -> list[RetrievalHit]
- evaluate_rules(profile: MatchingInput, candidate: JobCandidate, *, hard_constraints: list[str]) -> RuleResult
- 主代理：match_jobs(request: MatchingRequest, *, config: EngineConfig | None = None, embedding_client: EmbeddingClient | None = None) -> MatchingResult

RetrievalQuery/Document都是带投影版本的规范输入，不自动读文件。证据库adapter是外围I/O；核心只接MatchingRequest。主代理统一版本化tokenize/别名和投影。各模块进出均重验结构、保持身份，规则不把缺证据变成不会。最终RRF决定主序，规则覆盖只用于同分次序，明确硬冲突单列excluded；不把原始BM25/cosine相加。

## 规则与计分语义

BM25采用正IDF：log(1+(N-df+0.5)/(df+0.5))；k1=1.5，b=.75，title_weight=2.0。标题词频和长度都乘title_weight，正文为1，这是weighted-token BM25基线，不冒称BM25F。中文先采用确定性的字符bigram，技术词独立token，后续分词改善用阶段三评测决定。

RRF默认k=60，分数为两路1/(k+rank)之和；单路也使用同样公式。融合后按RRF主序、required组覆盖率同分次序、数据集/岗位ID稳定次序，不叠加原始BM25/cosine。Dense不设置伪精度阈值，负/零余弦也可能排入召回；结果不是满足任职要求的证明。

可选硬约束仅city/attendance/duration；未知不排除。有限岗位技能词表/显式must/bonus/OR/AND解析，歧义降为mention并警告，不用LLM补齐；学历/经验暂不判断，字段存在就明确未评估。required覆盖率=null表示无可可靠解释的required组，不设100%。

## 验收边界

本轮交付口径是“BM25+规则可独立使用，Embedding/Hybrid协议与计算以假向量离线验收，可选本地快照适配器完成接线与失败测试”。不将本地真实模型的语义质量、加载峰值内存或8GB实际可用性记为已完成。后续固定runtime/model revision后，在独立进程batch=1运行合成语料，记录冷启动/编码延迟与RSS；若仍超预算保留BM25+规则，不自动换云API或把失败隐藏为零分。
