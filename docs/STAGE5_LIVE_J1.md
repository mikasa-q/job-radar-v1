# 阶段5 J1 单岗真实报告检查点

本检查点接续用户人工确认的 S1，仍使用 S1 的唯一 `LIVE_RUN/api-store/state.json`，不初始化或复制收费账本、不重跑画像。

- `examples/stage5_j1_checkpoint.py prepare S1_ROOT J1_ROOT` 仅离线：复用已成功的完整画像/文档，构造独立四原岗位证据库与索引，用原 BM25/debug 匹配；岗位 ID、JD/事实 hash 和审核状态保持，独立导入产生的新 provenance 时间/运行标识单独声明。
- 原规则画像排序是 J1/J2/J3。新模型画像的项目描述按既有投影进入查询，可能使候选/排序变化；prepare 总是冻结 `RANKING_REVIEW.json`，不自动授权付费。变化时状态为 `frozen_waiting_ranking_confirmation`，先向用户报告并重新冻结，不删除项目字段或改变匹配逻辑来抹平差异。
- 用户 2026-11-18 已明确接受新命中顺序 **J1→J3→J4→J2**，报告目标仍为 J1。runner 在 prepare/check/authorize 重验此精确顺序、原四候选完整事实/审核状态及共同岗位技能规则/覆盖率；其他顺序即使提供匹配的 hash 也拒绝。原历史差异状态不改写为“无变化”。
- 人工确认明确接受冻结排名 hash 后，才执行 `authorize --accept-ranking-sha256 HASH`。该步骤只向同一账本 append 单个 J1 完整 wire/context 审批，不变 cap、费率、expiry、max_calls；新报告上限 1 次，本批画像+报告合计上限 2 次，原共享硬上限 9 不扩。
- J1 使用 `gpt-6.1-sol/low`、报告 `match-analysis-prompt-v3`、top_n1、输入本地分配<=20000、输出<=8192、90秒，SDK/HTTP/外层零重试。R=¥0.0670488，发送前双占用。费用按 usage 与输入 .78+cache-create .975 /输出3.90每百万计算，不冒称账户实扣。
- `execute` 只接受已授权冻结 manifest；`J1_LIVE_STARTED` O_EXCL 一次标记不可自动删除。显式 `LiveBackend(...,allow_report=True)` +同 root/scope/完整审批才允许一次报告；默认服务和 Hybrid 仍关闭，Ready.real_llm 保守 false。
- key 仅主代理发送进程按原授权同命令读取指定 .env 字段并映射 OPENAI_API_KEY，不打印或落新凭据文件。离线准备/测试不读取真实凭据。
- usage/raw 先记账，再运行原 v3 领域校验，成功资源和计费完成同事务；JSON/Markdown同源，横幅声明真实模型。报告仍 unconfirmed，人工状态不自动升级。
- 状态读取/sidecar/诊断异常停止新操作，等待已接受唯一任务结算，再持久 halt 和尽力写脱敏证据；已知 usage 不丢失，unknown不填0，预留/次数不回退，成功领域资源不改失败。首失败不补跑。
- 结束后停下等待人工报告检查；不扩岗/样本，不自动发送其他请求，不改阶段1–4核心/SQL结构/历史报告。

复用命令（独立占位路径）：

```sh
.venv/bin/python -B examples/stage5_j1_checkpoint.py prepare S1_ROOT J1_ROOT
.venv/bin/python -B examples/stage5_j1_checkpoint.py check S1_ROOT J1_ROOT
# 仅人工接受新冻结排名之后：
.venv/bin/python -B examples/stage5_j1_checkpoint.py authorize S1_ROOT J1_ROOT --accept-ranking-sha256 HASH
# 同命令按已批准凭据路径映射环境后，最多执行一次：
.venv/bin/python -B examples/stage5_j1_checkpoint.py execute S1_ROOT J1_ROOT
```
