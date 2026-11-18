from __future__ import annotations

import argparse
import json
from pathlib import Path

from .crawler import crawl


def main() -> None:
    parser = argparse.ArgumentParser(description="采集公开岗位页面并保存为 JSON")
    parser.add_argument("source", help="HTML 文件路径或 http(s) URL")
    parser.add_argument("--output", default="data/jobs.json", help="输出 JSON 路径")
    args = parser.parse_args()
    jobs = crawl(args.source)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"采集 {len(jobs)} 条岗位，已保存到 {output}")


if __name__ == "__main__":
    main()
