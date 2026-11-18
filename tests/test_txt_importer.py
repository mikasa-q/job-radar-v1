"""TXT grammar, evidence preservation and strict failure behavior."""
import builtins
import sqlite3
from pathlib import Path

import pytest
import requests

from job_radar.import_models import ImportedJob
from job_radar.importer import ImportParseError
from job_radar.txt_importer import parse_job_txt


URL = "https://www.zhipin.com/job_detail/abc123.html"
HEADER = (
    "格式版本: jobradar-txt-v1\n来源: boss\n"
    f"岗位名称: Python 实习生\n岗位链接: {URL}\n"
)
BODY = "岗位职责\n开发 Python API\n\n任职要求\n熟悉 Git 或 SQL。"
TXT = HEADER + "正文:\n" + BODY


def test_boss_record_shares_contract_and_preserves_body():
    record = parse_job_txt(
        HEADER + "公司: 示例公司\n城市: 深圳\n薪资: 200元/天\n"
        "学历: 本科\n经验: 不限\n实习安排: 4天/周\n正文:\n" + BODY,
    )
    assert isinstance(record, ImportedJob)
    assert record.source == "boss" and record.description_kind == "full_jd"
    assert record.title == "Python 实习生" and record.url == URL
    assert record.company == "示例公司" and record.city == "深圳"
    assert record.salary == "200元/天" and record.education == "本科"
    assert record.experience == "不限" and record.attendance == "4天/周"
    assert record.description == BODY
    assert record.skills == ["Python", "Git", "SQL"]
    assert record.source_tags == [] and record.posted_at == "" and record.warnings == []


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_bom_mixed_colons_blank_lines_and_header_order(newline):
    text = (
        "\ufeff\n格式版本：jobradar-txt-v1\n\n公司：某公司\n"
        f"岗位链接：{URL}\n岗位名称 : Python 实习生\n来源: boss\n  正文：  \n" + BODY
    ).replace("\n", newline)
    record = parse_job_txt(text)
    assert record.company == "某公司" and record.url == URL
    assert record.description == BODY


def test_body_looks_like_header_but_is_never_reparsed():
    body = "公司: 正文提及的公司\n薪资：按能力\n正文:\n格式版本: 正文中的字样\n\n  保留内部缩进"
    assert parse_job_txt(HEADER + "正文:\n" + body).description == body


def test_optional_missing_and_empty_values_stay_unknown_with_warnings():
    missing = parse_job_txt(TXT)
    empty = parse_job_txt(HEADER + "公司:\n城市：  \n经验:\n正文:\n" + BODY)
    assert missing == empty
    assert missing.company == missing.city == missing.experience == missing.attendance == ""
    assert set(missing.warnings) == {
        "missing_company", "missing_city", "missing_salary",
        "missing_education", "missing_experience", "missing_attendance",
    }


def test_nonstandard_jd_headings_are_valid():
    record = parse_job_txt(HEADER + "正文:\n我们在找伙伴\n可选语言：Python 或 Java")
    assert "可选语言" in record.description


def test_boss_identity_reuses_html_normalization():
    text = TXT.replace(URL, "http://zhipin.com/job_detail/abc123.html?from=txt#top")
    assert parse_job_txt(text).url == URL


def test_manual_source_preserves_original_http_url():
    url = "https://careers.example.org:443/jobs/42?lang=zh#requirements"
    record = parse_job_txt(TXT.replace("来源: boss", "来源: manual").replace(URL, url))
    assert record.source == "manual" and record.url == url


@pytest.mark.parametrize("key,value", [
    ("来源", "boss"), ("岗位名称", "Python 实习生"), ("岗位链接", URL),
])
@pytest.mark.parametrize("action", ["remove", "empty"])
def test_required_header_fields_are_never_guessed(key, value, action):
    line = f"{key}: {value}\n"
    text = TXT.replace(line, "" if action == "remove" else f"{key}: \n")
    with pytest.raises(ImportParseError, match=key):
        parse_job_txt(text)


@pytest.mark.parametrize("text,match", [
    ("岗位职责\n开发 Python API", "模板头部"),
    ("正文:\n只有原文", "格式版本"),
    (TXT.replace("jobradar-txt-v1", "jobradar-txt-v2"), "格式版本"),
    (TXT.replace("格式版本: jobradar-txt-v1\n", ""), "模板头部"),
    ("来源: boss\n" + TXT, "模板头部"),
    (TXT.replace("来源: boss", "来源: python.org"), "boss 或 manual"),
    (HEADER + "城市名: 深圳\n正文:\n原文", "未知"),
    (HEADER + "无冒号的头部行\n正文:\n原文", "字段"),
    (HEADER + "公司:\n公司: 示例\n正文:\n原文", "重复"),
    (HEADER + "格式版本: jobradar-txt-v1\n正文:\n原文", "重复"),
    (HEADER + BODY, "字段"),
    (HEADER, "正文"),
    (HEADER + "正文: 行内正文", "独占一行"),
    (HEADER + "正文:\n  \n", "正文不能为空"),
    ("", "格式版本"),
])
def test_malformed_or_unlabelled_txt_fails_explicitly(text, match):
    with pytest.raises(ImportParseError, match=match):
        parse_job_txt(text)


@pytest.mark.parametrize("source,url", [
    ("boss", "https://example.org/job_detail/abc.html"),
    ("boss", "https://www.zhipin.com/gongsi/abc.html"),
    ("boss", "file:///tmp/job.html"),
    ("manual", "file:///tmp/job.txt"),
    ("manual", "javascript:alert(1)"),
    ("manual", "https://user:secret@example.org/jobs/1"),
    ("manual", "https://example.org:99999/jobs/1"),
    ("manual", "https://"),
])
def test_source_specific_invalid_urls_fail(source, url):
    text = TXT.replace("来源: boss", f"来源: {source}").replace(URL, url)
    with pytest.raises(ImportParseError):
        parse_job_txt(text)


def test_txt_parsing_is_deterministic_and_has_no_io(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("TXT 纯解析不得执行 I/O")

    monkeypatch.setattr(builtins, "open", unexpected)
    monkeypatch.setattr(Path, "read_bytes", unexpected)
    monkeypatch.setattr(Path, "read_text", unexpected)
    monkeypatch.setattr(requests, "get", unexpected)
    monkeypatch.setattr(sqlite3, "connect", unexpected)
    assert parse_job_txt(TXT) == parse_job_txt(TXT)

