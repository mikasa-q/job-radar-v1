# 阶段四校验器 V1 优化实施记录：Phase 0–2

日期：2026-11-16（Asia/Shanghai）。用户已确认优化方案；本记录对应独立 modified 副本的协议、实现和离线验证。活动仓库应用、最终独立回滚及包装审计由主代理收口；本文件不预称尚未核验的步骤通过。最终证据以 `/Users/thc/.codex/visualizations/2026/11/16/01a1e35d-b2f1-7250-ad25-64904f393ec4/jobradar-validator-v1/VERIFICATION.txt` 为准。

## 1. Phase 0：先冻结再改代码

- 在交付根保存281个纳入任务快照的既有文件哈希及原Git状态；基线与modified均从原始内容复制。范围/排除项见snapshot-scope.json；排除.git/.venv/缓存/data/work等，不读取凭据。已有未提交文件保持原态。
- 重跑原始基线：`1757 passed, 5 warnings in 32.10s`，exit0。BASELINE.log/.exit保留字面输出；审计守卫网络及凭据文件尝试为0。
- 冻结协议：STAGE4_V3_PROTOCOL.md SHA256 `81c2c3436bafe086c6ff3d199089c6ae316e39ad9677936b6c01e990b3e20c22`；完整STAGE4_V3_SYSTEM_PROMPT.txt SHA256 `2535acd77917cea1b0ab61182d6e0f9a27d6f20cfba380e7d78891aa107de1b2`。实现常量与完整文本逐字相同；冻结先于源码修改。
- 两份真实历史raw重新核验SHA256与方案0.2一致：`75351fcd7507c75ed01b572520bf70c481b8af447bac23a1d1df59bc01f7e724`、`ed1501186492338db02e2b1069f05abf5dfea6f5aa2571ccdeaf3c91cdf8408c`。原raw和fixture不覆写；短事实改写始终标authored。

## 2. Phase 1：最小接线与四层门禁

修改report_analysis.py/report_models.py/report_render.py/report_demo_client.py，新增report_prompt_v3.py。新生成版本为match-analysis-prompt-v3；报告仍match-report-v1。draft schema字段/长度上限1600保持，80字是v3业务规则；报告仅prompt_version枚举/默认扩v3。v1/v2提示词原文保留。

|层|当前行为|典型固定码|
|---|---|---|
|结构|严格JSON、schema、唯一ID/引用关系、返回key；整稿完成后才下一层|analysis_invalid_json / analysis_schema_error / analysis_key_mismatch|
|规则|单个逐字规范subject、选定同主题规则类别、unknown结构、OR及建议类别|analysis_requirement_kind_mismatch / analysis_unknown_as_inability / analysis_alternative_already_supported / analysis_advice_requirement_mismatch|
|证据|逐引用membership/作用域、具体规则内容绑定、支持/全材料unknown/同技能负向/建议关联|analysis_evidence_scope / analysis_invalid_rule_reference / analysis_unknown_evidence_conflict / analysis_gap_requires_negative_evidence|
|文本|概率→膨胀词→可选强制→短事实与unknown→敏感内容；不修原文|analysis_forbidden_probability / analysis_unsupported_experience / analysis_optional_as_mandatory / analysis_text_contract_violation / analysis_text_sensitive_content|

整份draft按同一层全claim检查，再进入下一层；首错后后层not_evaluated。删除生成正文否定helper、三项否定专用正则及UNKNOWN_DENIAL，膨胀词任何出现都拒，不因advisory/引用/来源同词放行。NFKC/casefold/去空白仅供比较；原text最多80 Unicode码点、单句、无控制/零宽插入。unknown精确短句“简历未提及 <subject> 使用经历。”由模型填写，本地不自动替换成模板。

unknown扫描全部当前材料而非仅模型所引；课程项目使用也冲突，计划不是已有使用，明确负向不归unknown。explicit_gap只采纳有限同技能直接负向原文，原句与完整block上下文共同核对；条件/双否定/他人/跨技能拼接不认证。复杂歧义保守拒绝，而非自动造缺证据。有限模式仍不是任意自然语言蕴含保证。

历史v1/v2以现有prompt_version路由只读展示：保留原状态/原文/版本，通用结构/hash/ID作用域/引用/排名仍核查，明确“不代表已通过v3”。`validate_report(..., require_current_policy=True)`报analysis_validation_policy_mismatch。不复制已删helper，也不承诺单凭v2版本精确还原多份历史validator。新provider gate只认精确v3，不让“历史可读”成为新生成许可。

Phase1 guarded smoke输出PHASE1_SMOKE PASS，exit0；evidence/provider/engine专项194 passed in7.54s，exit0。单次complete/usage与预留/sidecar/串行halt保持，拒稿draft=null。不加新依赖，不改阶段1–3、数据库、人工状态、BM25/Dense/RRF或排序，不实现V2事实DSL/模板渲染或阶段5。

## 3. Phase 2：测试迁移及双指标

原53项否定回归仅删除1项依赖旧helper的白盒断言；其余52项样本保留并按v3迁移期望，另加替代黑盒/短事实正例。旧真实c1/c2隔离后因禁词拒；整份raw首错为已有Python/FastAPI使用记录与unknown矛盾的analysis_unknown_evidence_conflict，不把前层拒绝伪记成文本层通过。

新增矩阵涵盖结构/规则/unknown全覆盖材料/原文负向语境/禁词全角空白控制符/80码点单句/敏感内容/历史当前全验/ID哈希排名/sidecar新码及usage/失败停止。独立人工期望映射检查S1/S2/S3×3岗finding覆盖，并以删finding/新增claim变异测试防止自证；人工草稿通过不证明真实模型遵循协议。

已观察完整修改副本运行：MODIFIED-VERIFIED.log=`1940 passed, 5 warnings in 41.45s`，exit0。此前重复回归与阶段失败均保留日志；最终复跑数量和独立回滚结果另见VERIFICATION，不将历史1757或阶段性1940自动冒作最终验收。

OFFLINE_EVALUATION_VERIFIED/evaluation.json与evaluation.md包含86项双标签：

|标签（正类=应拒）|TP|FP|FN|TN|FPR|FNR|
|---|---:|---:|---:|---:|---:|---:|
|v3输出协议|84|0|0|2|0|0|
|人工语义正确性|48|36|0|2|36/38≈94.74%|0|

该有限攻击/协议测试集刻意含36项“语义无错误但写法不合约”的保守拒绝；其语义FP公开保留，不泛化为实际用户分布，也不靠改标签称误报归零。分母0为null/unknown，按层/组、逐项首错和not_evaluated、运行错误独立分桶。九岗独立期望覆盖均1.0，无依据新增0；报告逐岗raw人工草稿及本地wire/hash，source=authored_fixture。

## 4. 可复跑命令

```bash
HOST='/Users/thc/Documents/Codex/2026-10-21/build-ios-apps-plugin-build-ios-3/outputs/job-radar'
DELIVERY='/Users/thc/.codex/visualizations/2026/11/16/01a1e35d-b2f1-7250-ad25-64904f393ec4/jobradar-validator-v1'
# 项目完整回归
PYTHONPATH="$HOST/src:$HOST" "$HOST/.venv/bin/python" -B -m pytest -q
# 本批独立副本加守卫回归（输出RECHECK.log/.exit）
"$DELIVERY/run-offline.sh" "$DELIVERY/modified" RECHECK
# 新输出目录必须不存在；只跑本地人工fixture，不构造provider
PYTHONPATH="$HOST/src:$HOST" "$HOST/.venv/bin/python" -B "$HOST/examples/stage4_v3_offline_evaluate.py" /absolute/new-v3-offline-evaluation
```

最终回滚使用本批可执行ROLLBACK.sh指定独立副本，原始与变更hash/guard/幂等/补丁重构/归档核验由最终VERIFICATION记录。本记录撰写时该最终核验尚未完成，不在活动仓库试验回滚。MODIFIED_FILE/DIFF_FILE/VERIFICATION.txt/ROLLBACK.sh的最终内容和四路径也以主代理最终交付为准。

## 5. 停止点

本轮真实HTTP/模型调用0、凭据读取0、新费用¥0；S1/S2/S3真实均not_run，供应商完成率unknown。历史已发生¥0.01850004及失败预留保留；原¥1.00不自动授权v3新批次。Phase3须用户另行确认样本/岗位、完整v3内容、次数/输入输出限额/总预算；届时仍串行、零重试、失败即停。本轮工程交付不等于真实小样本质量通过。
