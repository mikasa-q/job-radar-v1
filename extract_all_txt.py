"""批量从 boss-evidence 里每个 HTML 提取 JD 正文写成同名 .txt。

用法（在 JobRadar 项目根目录）：
    .venv/bin/python -B extract_all_txt.py [文件夹]
默认文件夹：~/Desktop/boss-evidence
已有 .txt 的跳过；解析失败的打出来，不中断整批。
"""
import sys
from pathlib import Path

from job_radar.importer import parse_boss_detail_html


def main() -> int:
    folder = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.home() / "Desktop" / "boss-evidence"
    if not folder.is_dir():
        print(f"文件夹不存在：{folder}")
        return 2
    html_files = sorted(folder.glob("*.html"))
    done = skipped = failed = 0
    for html_path in html_files:
        txt_path = html_path.with_suffix(".txt")
        if txt_path.exists():
            print(f"跳过（已有）: {html_path.name}")
            skipped += 1
            continue
        try:
            job = parse_boss_detail_html(html_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"失败: {html_path.name} -> {exc}")
            failed += 1
            continue
        txt_path.write_text(job.description, encoding="utf-8")
        print(f"已写入: {txt_path.name} ({job.title})")
        done += 1
    print(f"完成：新增 {done}，跳过 {skipped}，失败 {failed}，共 {len(html_files)} 个 HTML")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
