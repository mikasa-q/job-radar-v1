from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_filter_config
from .crawler import crawl
from .storage import list_jobs, upsert_jobs


def main() -> None:
    parser = argparse.ArgumentParser(description="采集公开岗位页面并保存为 JSON")
    parser.add_argument("source", nargs="?", help="HTML 文件路径或 http(s) URL；SQLite 查询模式可省略")
    parser.add_argument("--output", default="data/jobs.json", help="输出 JSON 路径")
    parser.add_argument("--site", choices=["auto", "demo", "boss", "python-org"], default="auto",
                        help="页面适配器，默认自动识别")
    parser.add_argument("--timeout", type=float, default=15, help="单次请求超时秒数")
    parser.add_argument("--save-html", help="保存本次响应 HTML，方便离线复现")
    parser.add_argument("--base-url", help="解析本地快照时用于拼接相对链接")
    parser.add_argument("--config", help="读取保存的 JSON 筛选配置；命令行参数优先")
    parser.add_argument("--keyword", help="采集后按标题和列表摘要筛选，不是站内搜索")
    parser.add_argument("--title", action="append", help="标题包含文字；可重复，重复值为 OR")
    parser.add_argument("--city", action="append", help="地点包含文字；可重复，重复值为 OR")
    parser.add_argument("--company", action="append", help="公司包含文字；可重复，重复值为 OR")
    parser.add_argument("--skill", action="append", help="技能列表包含技能；可重复，重复值为 OR")
    parser.add_argument("--exclude-keyword", action="append",
                        help="标题或摘要不能包含文字；可重复")
    parser.add_argument("--remote-only", action="store_true", help="只保留地点含 Remote 的岗位")
    parser.add_argument("--database", help="同时写入 SQLite 数据库文件")
    parser.add_argument("--query-city", help="从 SQLite 查询城市；需要 --database")
    parser.add_argument("--query-keyword", help="从 SQLite 查询标题或摘要；需要 --database")
    parser.add_argument("--limit", type=int, help="限制 SQLite 查询返回条数")
    args = parser.parse_args()
    try:
        query_requested = (
            args.query_city is not None
            or args.query_keyword is not None
            or args.limit is not None
        )
        if query_requested:
            if args.source:
                parser.error("SQLite 查询模式不需要 source，请直接使用 --database")
            if not args.database:
                parser.error("SQLite 查询需要 --database")
            queried = list_jobs(
                args.database, city=args.query_city, keyword=args.query_keyword,
                limit=args.limit,
            )
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(queried, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"查询到 {len(queried)} 条岗位，已保存到 {output}")
            return
        if not args.source:
            parser.error("采集模式需要提供 source；SQLite 查询请使用 --query-city 等参数")
        config = load_filter_config(args.config)
        filters = {
            "keyword": args.keyword if args.keyword is not None else config.get("keyword"),
            "title": args.title if args.title is not None else config.get("title"),
            "city": args.city if args.city is not None else config.get("city"),
            "company": args.company if args.company is not None else config.get("company"),
            "skill": args.skill if args.skill is not None else config.get("skill"),
            "exclude_keyword": (
                args.exclude_keyword
                if args.exclude_keyword is not None
                else config.get("exclude_keyword")
            ),
            "remote_only": args.remote_only or config.get("remote_only", False),
        }
        jobs = crawl(args.source, site=args.site, timeout=args.timeout,
                     save_html=args.save_html, base_url=args.base_url, **filters)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.database:
            stored = upsert_jobs(args.database, jobs)
        else:
            stored = None
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"采集失败：{exc}\n")
    message = f"采集 {len(jobs)} 条岗位，已保存到 {output}"
    if stored is not None:
        message += f"；SQLite 已写入/更新 {stored} 条"
    print(message)


if __name__ == "__main__":
    main()
