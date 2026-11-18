# JobRadar

一个面向 AI 和 Python 后端实习岗位的最小采集器。当前版本先用本地 HTML fixture 演示完整流程：

```text
HTML 页面 -> 解析岗位字段 -> 提取技术关键词 -> URL 去重 -> 保存 jobs.json
```

## 运行

进入项目目录：

```bash
cd job-radar
PYTHONPATH=src python -m job_radar.cli sample_data/jobs.html --output data/jobs.json
```

预期输出：

```text
采集 2 条岗位，已保存到 data/jobs.json
```

也可以把 `sample_data/jobs.html` 替换成公开网页 URL：

```bash
PYTHONPATH=src python -m job_radar.cli https://example.com/jobs --output data/jobs.json
```

HTTP 模式使用 `requests`，只采集你有权访问的公开页面，并遵守网站的 robots.txt、服务条款和访问频率要求。

## 测试

```bash
PYTHONPATH=src pytest -q
```

## 当前能力

- 解析岗位标题、公司、城市、描述、链接和来源
- 从岗位标题与描述中提取 Python、FastAPI、MySQL、LLM、RAG 等关键词
- 按岗位 URL 去重，保留首次出现的记录
- 统一以 UTF-8 JSON 保存结果
- 本地 fixture 模式不依赖网络，方便学习和测试

## 下一步

1. 把解析器改成一个真实公开来源的适配器。
2. 增加 SQLite 持久化和岗位增量更新。
3. 使用 FastAPI 增加 `/jobs` 和 `/stats/skills` 接口。
4. 再考虑异步请求、定时任务和 LLM 岗位分类。
