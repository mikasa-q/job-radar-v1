"""Preview reads exactly one file and presents unconfirmed source evidence."""
import hashlib
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

from job_radar.import_models import ImportedJob
from job_radar.import_preview import main, preview_file
from job_radar.importer import ImportParseError


FIXTURE = Path(__file__).parent / "fixtures" / "boss-detail.html"
URL = "https://www.zhipin.com/job_detail/abc123.html"
TXT = (
    "格式版本: jobradar-txt-v1\n来源: boss\n岗位名称: Python 实习生\n"
    f"岗位链接: {URL}\n正文:\n职责：Python API\n\n要求：Git"
)


@pytest.mark.parametrize("suffix", [".html", ".htm", ".HTML"])
def test_html_preview_uses_existing_parser_and_collects_provenance(tmp_path, suffix):
    path = tmp_path / ("岗位" + suffix)
    raw = FIXTURE.read_bytes()
    path.write_bytes(raw)
    before = datetime.now(timezone.utc)
    result = preview_file(path)
    after = datetime.now(timezone.utc)
    assert isinstance(result.job, ImportedJob)
    assert result.job.title == "AI Agent 开发实习生"
    assert result.job.company == "ONES" and len(result.job.description) == 790
    assert result.provenance.source_file == str(path.resolve())
    assert result.provenance.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert result.provenance.input_format == "html"
    assert before <= result.provenance.imported_at <= after
    assert result.provenance.imported_at.utcoffset().total_seconds() == 0
    assert result.job.posted_at == "" and result.review_status == "unconfirmed"


def test_txt_preview_hashes_original_bom_and_crlf_bytes(tmp_path):
    path = tmp_path / "job.txt"
    raw = b"\xef\xbb\xbf" + TXT.replace("\n", "\r\n").encode("utf-8")
    path.write_bytes(raw)
    preview = preview_file(path)
    assert preview.provenance.input_format == "txt"
    assert preview.provenance.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert preview.provenance.source_sha256 != hashlib.sha256(TXT.encode()).hexdigest()
    assert preview.job.description == "职责：Python API\n\n要求：Git"
    assert preview.job.city == "" and preview.review_status == "unconfirmed"
    assert path.read_bytes() == raw


def test_read_once_without_writing_network_or_database(tmp_path, monkeypatch):
    path = tmp_path / "job.txt"
    path.write_text(TXT, encoding="utf-8")
    real_read = Path.read_bytes
    reads = []

    def read_once(file):
        reads.append(file)
        return real_read(file)

    def unexpected(*args, **kwargs):
        raise AssertionError("预览不得写文件、联网或连接数据库")

    monkeypatch.setattr(Path, "read_bytes", read_once)
    monkeypatch.setattr(Path, "write_bytes", unexpected)
    monkeypatch.setattr(Path, "write_text", unexpected)
    monkeypatch.setattr(sqlite3, "connect", unexpected)
    monkeypatch.setattr(requests, "get", unexpected)
    result = preview_file(path)
    assert reads == [path.resolve()]
    assert result.job.title == "Python 实习生"


def test_relative_path_is_resolved_for_provenance(tmp_path, monkeypatch):
    path = tmp_path / "job.txt"
    path.write_text(TXT, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert preview_file("job.txt").provenance.source_file == str(path.resolve())


def test_html_explicit_url_fallback(tmp_path):
    path = tmp_path / "job.html"
    html = FIXTURE.read_text(encoding="utf-8")
    start = html.index('<link rel="canonical"')
    end = html.index(">", start) + 1
    path.write_text(html[:start] + html[end:], encoding="utf-8")
    assert preview_file(path, source_url=URL).job.url == URL


def test_txt_rejects_url_override_instead_of_hiding_header_conflicts(tmp_path):
    path = tmp_path / "job.txt"
    path.write_text(TXT, encoding="utf-8")
    with pytest.raises(ImportParseError, match="模板头部"):
        preview_file(path, source_url=URL)


def test_cli_prints_fields_unknowns_provenance_and_multiline_jd(tmp_path, capsys):
    path = tmp_path / "job.txt"
    path.write_text(TXT, encoding="utf-8")
    assert main([str(path)]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert "待人工核对" in output.out and "未入库" in output.out
    assert f"SHA-256: {hashlib.sha256(path.read_bytes()).hexdigest()}" in output.out
    assert "city: 未知" in output.out and "source_tags: []" in output.out
    assert "missing_city" in output.out
    assert "JD 正文：\n职责：Python API\n\n要求：Git\n" in output.out


@pytest.mark.parametrize("kind", [
    "missing", "invalid_utf8", "no_header", "unsupported", "directory", "invalid_html",
])
def test_cli_errors_have_status_2_without_partial_record(tmp_path, capsys, kind):
    path = tmp_path / "job.txt"
    if kind == "invalid_utf8":
        path.write_bytes(b"\xff\xfe")
    elif kind == "no_header":
        path.write_text("岗位职责\n只是 JD 原文备份", encoding="utf-8")
    elif kind == "unsupported":
        path = tmp_path / "job.pdf"
        path.write_bytes(b"%PDF")
    elif kind == "directory":
        path.mkdir()
    elif kind == "invalid_html":
        path = tmp_path / "job.html"
        path.write_text("<title>安全验证</title>", encoding="utf-8")
    assert main([str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "预览失败" in captured.err
    assert "Traceback" not in captured.err
    if kind == "no_header":
        assert "模板头部" in captured.err


def test_module_command_works_outside_project_directory(tmp_path):
    path = tmp_path / "岗位.txt"
    path.write_text(TXT, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-B", "-m", "job_radar.import_preview", str(path)],
        cwd=tmp_path, capture_output=True, text=True,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )
    assert result.returncode == 0, result.stderr
    assert "title: Python 实习生" in result.stdout and "JD 正文：" in result.stdout

