import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from job_radar.cli import main
from job_radar.crawler import crawl, fetch_html

FIXTURES = Path(__file__).parents[1] / "sample_data"


def test_local_fetch_returns_html_and_file_uri():
    html, url = fetch_html(str(FIXTURES / "jobs.html"))
    assert "job-card" in html
    assert url.startswith("file:///")


def test_http_fetch_uses_timeout_status_and_final_url():
    response = Mock()
    response.headers = {"Content-Type": "text/html;charset=utf-8"}
    response.encoding = "utf-8"
    response.url = "https://www.python.org/jobs/"
    response.text = "<h1>岗位</h1>"
    with patch("requests.get", return_value=response) as get:
        html, url = fetch_html("https://www.python.org/jobs", timeout=7)
    response.raise_for_status.assert_called_once()
    assert get.call_args.kwargs["timeout"] == 7
    assert html == response.text and url == response.url


@pytest.mark.parametrize("error", [requests.Timeout("timeout"), requests.HTTPError("403")])
def test_http_errors_have_clear_message(error):
    with patch("requests.get", side_effect=error):
        with pytest.raises(RuntimeError, match="HTTP 请求失败"):
            fetch_html("https://www.python.org/jobs/")


def test_non_html_response_rejected():
    response = Mock(headers={"Content-Type": "application/json"})
    with patch("requests.get", return_value=response):
        with pytest.raises(ValueError, match="Content-Type"):
            fetch_html("https://example.org/api")


def test_http_default_encoding_is_utf8():
    response = Mock(headers={"Content-Type": "text/html"}, encoding="ISO-8859-1",
                    text="岗位", url="https://www.python.org/jobs/")
    with patch("requests.get", return_value=response):
        fetch_html(response.url)
    assert response.encoding == "utf-8"


def test_unknown_url_scheme_rejected():
    with pytest.raises(ValueError, match="HTTP"):
        fetch_html("ftp://example.org/jobs")


def test_keyword_filter_and_snapshot(tmp_path):
    snapshot = tmp_path / "saved.html"
    jobs = crawl(str(FIXTURES / "jobs.html"), keyword="docker", save_html=str(snapshot))
    assert len(jobs) == 2
    assert snapshot.read_text(encoding="utf-8") == (FIXTURES / "jobs.html").read_text(encoding="utf-8")
    assert crawl(str(FIXTURES / "jobs.html"), keyword="not-present") == []


def test_invalid_timeout():
    with pytest.raises(ValueError, match="timeout"):
        crawl(str(FIXTURES / "jobs.html"), timeout=0)


def test_cli_success(tmp_path, capsys):
    output = tmp_path / "nested" / "jobs.json"
    with patch("sys.argv", ["job_radar", str(FIXTURES / "jobs.html"), "--output", str(output)]):
        main()
    assert len(json.loads(output.read_text(encoding="utf-8"))) == 2
    assert "采集 2 条岗位" in capsys.readouterr().out


def test_cli_verification_preserves_existing_output(tmp_path, capsys):
    source = tmp_path / "verify.html"
    source.write_text("<title>安全验证 - BOSS直聘</title>", encoding="utf-8")
    output = tmp_path / "jobs.json"
    output.write_text('[{"previous": true}]', encoding="utf-8")
    args = ["job_radar", str(source), "--site", "boss", "--output", str(output)]
    with patch("sys.argv", args), pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "验证页面" in capsys.readouterr().err
    assert output.read_text(encoding="utf-8") == '[{"previous": true}]'


def test_cli_missing_file_reports_error(tmp_path, capsys):
    with patch("sys.argv", ["job_radar", str(tmp_path / "missing.html")]):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 2
    assert "采集失败" in capsys.readouterr().err
