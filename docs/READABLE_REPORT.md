# 可读岗位报告：v3 结构化事实 → 本地 Markdown

模型仍调用原严格 JSON Schema，只返回 `key` 和逐条 `claims`（规范技能、岗位要求类别、证据状态、短事实、引用及关联结论）。提示词 `match-analysis-prompt-v3`、模型/effort、`match-report-v1` 和已有 HTTP 接口的请求/响应格式均不变。先执行原有结构、规则、证据、文本安全四层门禁，再从通过的报告渲染；模板不会修改、补写或重新评判模型结论，也不引入第二次模型请求。历史 v1/v2 只做原有只读校验并标注历史版本，不重新认定为 v3。

## 阅读方式

- 先看已分析岗位、证据支持项、材料未提及项（`unknown`）、履历明确否定项与单独的建议。没有对应结论时不显示空分类，不补建议；未分析岗位只展示原排序和规则覆盖。`unknown` 不等于不会。
- 每条结论后有可点击短编号。同一证据在一份报告只编号一次；附录只展示实际引用的岗位原文、简历片段、规则或核查范围。长引文明确标为节选。引用节选保留原文，不是新增结论。
- 完整 JSON 仍含每条原文、证据 ID、全部证据、来源哈希、检索分项和审核状态。Markdown 源码的每个编号旁有对应的原证据 ID（HTML 注释）；不改原排名，不生成综合分或录用概率。引用原材料可能含个人信息，分享前请检查。

## 获取报告

### 上传 PDF 直接下载（默认离线）

在本机 `/docs` 展开 **上传 PDF，一站式生成岗位报告**，选一个文件，点 **Execute**。不用手抄 ID 或轮询；成功直接返回 Markdown（HTTP 201）。同一份 PDF 若需要在故障后重试，复用 `Idempotency-Key`，服务不会重跑已完成的步骤；也可用 `Location` 查看已保存报告。服务端用配置好的证据库和索引做规则画像、BM25＋规则匹配，再生成报告。规则画像和离线报告不调用模型。

```bash
curl -fS http://127.0.0.1:8080/v1/reports/from-pdf \
  -H 'Idempotency-Key: my-pdf-report-0001' \
  -F 'file=@/absolute/resume.pdf;type=application/pdf' -o match-report.md
```

显式 `?mode=llm` 仍用离线规则画像，只让经审批的 LLM 分析全库排序的**首岗**；裁剪的是报告岗位数量，不裁剪核查证据。已绑定精确 `context_sha256` + `payload_sha256`、有剩余次数和预算时返回 HTTP 202 与一个 `status_url`；只查看该报告任务即可。没有符合审批时明确拒绝，不会收费或静默降级成离线模式。不会自动审批、重试或重新生成画像。

仅对合成材料运行一站式演示：`.venv/bin/python -B examples/stage5_pdf_report.py --output /absolute/new-directory`。

### 现有任务和历史报告

新生成的报告：沿用 `GET /v1/reports/{report_id}?format=markdown` 下载 Markdown；不带 `format` 取得原结构化结果及 Markdown。已保存的报告不在 GET 时改写，原下载保持原样。

重渲染已有报告：从仓库根目录运行，输出目录须不存在。不会覆盖原 JSON、Markdown 或账本，也不会读密钥：

```bash
.venv/bin/python -B examples/export_readable_report.py \
  --store-state "$RUN_ROOT/api-store/state.json" \
  --report-id "$REPORT_ID" --output /absolute/new-report-directory
```

也可以传 `--report-json /absolute/match-report.json` 代替 `--store-state` 和 `--report-id`。仅在知道来源时传 `--mode offline|scripted|live`；裸 JSON 默认不声明生成来源。导出同目录的 `match-report.md` 和 `match-report.json`，后者保留原结构化数据。

## 验收

`tests/fixtures/stage4_v3_report_baseline.json` 来自阶段四冻结离线评估的原九份人工预设响应；测试逐岗比对请求数据哈希、证据包哈希、原排名、24 条结论和每条 Markdown 引用对应的原证据 ID。覆盖四层拒绝、历史报告、转义、建议关联和 HTTP 生成/下载。不把预设响应当作新模型质量验收。现有已结算的真实 J1 可直接用上述命令重渲染，无需重新生成。
