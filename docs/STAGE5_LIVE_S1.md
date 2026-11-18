# 阶段5真实首笔：S1画像检查点（2026-11-18）

用户确认离线验收并放行两次候选真实生成；当前只执行S1画像一次，之后停止人工检查，J1报告不自动发送。

- 默认服务仍llm_disabled/Hybrid关闭，无env/HTTP启用开关。只有显式注入LiveBackend及本地root/scope/完整wire审批才有权限。
- 画像复用原S1文本：课程项目：使用 Python 和 FastAPI 开发接口。新建PDF后原解析产生尾换行，逐block核对去尾换行后的字面文本；新PDF/provenance/document hash分别冻结，不冒称与历史hash相同。
- 历史已花0.05320458/保留reserve0.6829056/cap1.00只导入一次。唯一本批LIVE_RUN/api-store/state.json由画像和后续J1共用；现只审批profile完整wire，report需人工确认再追加批准事件。批次max2且不扩全局9上限。
- 模型gpt-6.1-sol/low，画像12000/8192，新费率input0.78+cache-create0.975/output3.90每百万；90秒传输超时非硬强杀，SDK/transport/外层重试均0，拒绝重定向与env代理。首失败或未知usage全局halt，不补跑。
- wire本地输入分配=完整JSON UTF8字节+1024；超限停在预检，不截断证据。取得usage先落账，原domain校验之后与资源成功原子完成billing。review_status保留unconfirmed。
- prepare阶段0凭据0网络；发送阶段同一条命令只从指定.env字段临时映射OPENAI_API_KEY，exec单次runner，不打印/落新凭据文件。原始响应及usage作为非凭据私有证据保存。
- 实际费用指供应商返回usage乘已确认费率计算的本地保守金额（包含缓存创建叠加），不冒称账户账单实扣。预算保留完整R，unknown usage绝不填0。
- 真实backend当前只准profile；报告生成继续拒绝，直到后续用户人工检查确认后单独开启J1。S1领域/人工预期拒绝也应记已知usage并停止。
- 代码回滚不触运行账本，不退金额/次数/halt。若旧版本支持同schema会保留事实；所有默认LLM入口仍关闭。独立副本回滚测试及hash在交付VERIFICATION.txt中记录。

接续：先读本批RESULT、画像和账本；用户人工确认S1后，才在同一运行根按原BM25生成匹配，若排名/候选有差异先报；随后冻结J1单岗v3报告完整wire。不得重建账本或重跑S1。

## 本批工程交付边界

真实backend构造本身不联网；client构造也不发请求。发送唯一由已持久send-intent后的SDK生成路径触发。backend的root/scope/允许类型与账本再校验，禁止将mock账本/任意目录冒充真实批准。默认入口不因环境key存在启用。真实运行仅primary执行一次，子代理只能离线实现/验证。

## 检查点异常与保守Ready口径

- 提交后的状态读取/sidecar/检查点写入异常，同样使本批停止。runner不再提交操作，先在store仍打开时等待唯一已接受worker收尾，让供应商已知usage按原路径落账，再持久halt；不重试、不退款，也不把已成功的领域资源改成失败。之后尽力写脱敏失败RESULT与账本/raw；诊断文件继续写失败也不撤销halt或once marker。账本持久化故障保留fail-stop，缺失usage/账本费用记null，不记零。
- 本检查点的`Ready.real_llm`即使显式注入LiveBackend仍保守固定false。该字段不声明真实注入链路就绪或供应商可用性；真实S1准入由固定root/scope/完整wire审批与持久账本门禁检查，Ready不发付费或探测请求。默认LLM/Hybrid关闭保持不变。
