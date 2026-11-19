# JobRadar V1 核心流程

实线表示 V1 HTTP 接口已实现的路径，边上的模式和审批条件仍适用。虚线是检索内核可用、但 V1 HTTP 接口未开放的向量通道。启用 LiveBackend 不会打开该通道。

```mermaid
flowchart TD
    PDF["简历 PDF"] --> EXTRACT["隔离进程提取文本与来源引用<br/>ResumeDocument"]
    EXTRACT -->|"默认 rules"| PROFILE["简历画像 ResumeProfile"]
    EXTRACT -->|"显式 llm，LiveBackend 且通过审批"| PROFILE
    PROFILE --> INPUT["构建 MatchingRequest"]
    EVIDENCE["只读岗位证据库"] -->|"按请求提供的候选键取岗"| INPUT

    subgraph RETRIEVAL["索引检索与规则匹配"]
        PROJECT["投影检索查询并核对索引候选"] --> RULES["评估岗位规则，排除不满足硬约束的候选"]
        RULES --> BM25["BM25 词法排名"]
        RULES -. 仅内核 Hybrid .-> DENSE["向量检索：查询与 JD 分块比相似度<br/>每岗取最佳分块"]
        BM25 --> RRF["RRF 按通道名次融合"]
        DENSE -.-> RRF
        RRF --> RANK["保留规则证据覆盖，融合分并列时用于排序<br/>IndexedMatchingResult"]
    end
    INPUT --> PROJECT
    INDEX["派生索引：词法文档，可选岗位分块向量"] --> PROJECT
    INDEX -.-> DENSE

    RANK --> SAVED["保存匹配快照：请求、检索结果与简历文档"]
    SAVED --> PACKET["校验快照，构建岗位与简历证据包"]
    PACKET -->|"默认 offline，不调用模型"| OFFLINE["离线报告：排序、规则覆盖及引用<br/>分析状态 not_run"]
    PACKET -->|"显式 llm，须通过请求与预算审批"| LIVE["LiveBackend（allow_report=True）<br/>对已选岗位生成分析草稿"]
    LIVE --> VALIDATE["本地校验草稿结构、证据引用与结论"]
    VALIDATE --> REPORT["报告中的技能结论<br/>有证据支持、unknown 或有明示证据的技能差距"]
    OFFLINE --> OUTPUT["报告 JSON / Markdown"]
    REPORT --> OUTPUT
```

- **画像与匹配。** [`api_v1_resumes.py`](../src/job_radar/api_v1_resumes.py) 隔离提取 PDF，默认走规则画像。[`api_v1_matching.py`](../src/job_radar/api_v1_matching.py) 校验画像证据，从只读证据库取出显式指定的候选，调用索引匹配；HTTP 请求 `hybrid` 会被拒绝。
- **BM25、向量与 RRF。** [`rag_engine.py`](../src/job_radar/rag_engine.py) 在检索前评估规则。默认 `bm25_rules` 只把 BM25 名次交给 RRF；内核 `hybrid` 需要兼容的向量索引和显式注入的 embedding 客户端，才会将 [`dense_hits`](../src/job_radar/rag_index.py) 产出的向量名次一起送入融合。没有 LLM 重排。
- **报告与技能差距。** [`api_v1_reports.py`](../src/job_radar/api_v1_reports.py) 从已完成的匹配快照生成报告，不重新检索。Live 分析草稿在本地通过 [`report_analysis.py`](../src/job_radar/report_analysis.py) 校验后，结论才随报告呈现。若结论仅是材料未提及某技能的使用经历，则标记 `unknown`；明确的 `skill_gap / explicit_gap` 要引用简历中的直接否定证据，不能把缺证据当作不会。离线报告不生成这些模型结论。

启动与验证方式见 [README](../README.md#快速上手) 和 [Docker 启动说明](DOCKER.md)。
