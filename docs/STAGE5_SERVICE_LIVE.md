# 阶段 5：从本机服务启动 LiveBackend

默认命令不变：`python -m uvicorn job_radar.api_v1:app --host 127.0.0.1 --port 8000`。它不读取密钥，也不使用真实后端，即使指向已经有账本的目录。Hybrid 仍关闭，既有接口不变，另有[一站式 PDF 报告](READABLE_REPORT.md)；`/v1/ready` 仍是保守的证据库/索引就绪检查，`real_llm=false` 不表示对上游做过探测。

## 仅本机显式启动

```bash
.venv/bin/python -B -m job_radar.api_v1_serve \
  --store-root "$RUN_ROOT/api-store" \
  --evidence-db "$J1_EVIDENCE/evidence.sqlite3" \
  --index-root "$J1_EVIDENCE/derived" \
  --dataset-id synthetic-stage4-acceptance-v1 --port 8000
```

只绑定 `127.0.0.1`，单进程、单实例、单工作队列，不支持热重载，不用于公网或多用户。启动时先在独占锁下校验持久账本、canonical root、scope、有效期和剩余授权，再从 `~/.claude-mem/.env` 读唯一的 `OPENAI_COMPAT_API_KEY` 字面赋值（可加引号，不执行文件）。不接受环境变量、命令行或 HTTP 传入的密钥；若设置了 SDK 的 `OPENAI_CUSTOM_HEADERS`，启动会拒绝，以防覆盖认证头。不记录密钥或保存到本地账本。启动不创建账本，不发请求。每次生成仍需完整 wire/context 批准。服务关闭先等待已接收的任务结算，再释放后端的密钥引用。

## 一次服务验证：S1 → J1

1. 离线准备（无密钥、无供应商网络请求）：`.venv/bin/python -B examples/stage5_service_checkpoint.py prepare "$RUN_ROOT" "$PREVIOUS_STORE"`。`RUN_ROOT` 必须是新目录；`PREVIOUS_STORE` 指向已完成的旧 S1/J1 共享 `api-store`。校验旧账本后只读导入累计金额和次数，不复制或改写旧事件；复用原 S1 合成 PDF，只冻结新 scope 的 S1 请求。
2. 获得真实验证授权后显式启动服务。按 `S1_PLAN.json` 的路径、请求体和幂等键提交 S1 一次，经 GET 轮询并检查画像。失败，或结果与合成证据不符，就停止，不补跑。用新 S1 画像、原四岗位做一次只读 `bm25_rules` / `debug` 匹配；不能以旧画像替换。匹配失败或 J1 不是首位时不发报告。服务证据库和索引可直接使用以前 J1 检查点的冻结产物，不重建历史库。
3. 停止服务释放锁。`stage5_service_checkpoint.py plan-report "$RUN_ROOT" "$MATCH_ID"` 核对新画像绑定、原四岗位身份及事实哈希、J1 首位、报告 wire 和最新双占用预算。核对冻结 `J1_PLAN.json` 后，再用 `authorize-report "$RUN_ROOT" "$MATCH_ID" --accept-plan-sha256 HASH` 追加一次本地审批。不涉及密钥，不改 cap 或次数。重启服务，按计划提交 `mode=llm, top_n=1` 一次。其他 `top_n` 在服务门禁拒绝。串行零重试，首失败停止，报告仍待人工审阅。

`gpt-6.1-sol / low`、报告 `match-analysis-prompt-v3`、90 秒、完整输入本地分配和输出上限均沿用阶段 5。最多 2 次新生成，不加岗、不增加样本。原 provider、阶段 1–4 领域逻辑、数据库结构和历史结果不变。代码回滚不退账。

## 当前预算（人民币）

`stage5-service-live-20261118` 的有效上限已从 **¥1.10 调到 ¥2.00**。旧 scope 仍为 ¥1.00。原 genesis、哈希链的历史事件和金额不重写；新增一个 `raise_service_cap` 事件，后续预算检查使用有效上限，历史发送仍按当时的上限校验。

本机离线管理命令（先停止占用同一账本的服务；已应用时不会重复追加）：

```bash
.venv/bin/python -B examples/stage5_service_checkpoint.py raise-budget-cap "$RUN_ROOT"
```

命令只改现有 scope 的上限，不补发请求、不清除失败或未结算状态、不续期、不增加次数或 wire 审批。当前账本仍已用完既有的两次授权。旧 J1 的 Markdown 可用[离线导出](READABLE_REPORT.md)，不用开启 Live。不得用旧版代码写新账本，也不要用旧 `state.json` 覆盖当前账本。

## 历史参考：原 ¥1.10 的首次验证

| 项目 | 金额 |
| --- | ---: |
| 从旧账本承接的累计实际费用 | ¥0.06684288 |
| 承接的保留预留（非实际费用） | ¥0.80296320 |
| 当时上限（旧 scope 为 ¥1.00） | ¥1.10 |
| S1 发送前双占用 | ¥0.97582368 |
| J1 发送前双占用（含上一次 S1 的实际费用作为估计） | ¥1.06244658 |
| J1 发送前双占用（S1 用满分配且 usage 已知） | ¥1.10992128，拒绝 |

J1 最终以本次 S1 已结算 usage 重算；如果 S1 的保守费用超过 ¥0.04308752，J1 不发送。费用是按供应商 usage 和已冻结费率计算的本地保守值，预留不是已扣金额。上次同一对样本合计约 ¥0.01364；本次实际费用取决于实际 usage。
