import json
from pathlib import Path

import pytest

from job_radar.cli import main
from job_radar.config import load_filter_config


FIXTURE = Path(__file__).parents[1] / "sample_data" / "jobs.html"


def test_config_accepts_saved_filter_values(tmp_path):
    path = tmp_path / "filters.json"
    path.write_text(json.dumps({
        "title": "Python", "city": ["北京", "深圳"], "remote_only": False,
    }), encoding="utf-8")
    assert load_filter_config(str(path)) == {
        "title": ["Python"], "city": ["北京", "深圳"], "remote_only": False,
    }


def test_config_rejects_unknown_field(tmp_path):
    path = tmp_path / "filters.json"
    path.write_text('{"salary_min": 10}', encoding="utf-8")
    with pytest.raises(ValueError, match="未知字段"):
        load_filter_config(str(path))


def test_cli_config_filters_without_repeating_conditions(tmp_path, capsys):
    config = tmp_path / "filters.json"
    config.write_text('{"title": ["Python"], "city": ["北京"]}', encoding="utf-8")
    output = tmp_path / "jobs.json"
    import sys
    old_argv = sys.argv
    try:
        sys.argv = [
            "job_radar", str(FIXTURE), "--config", str(config), "--output", str(output),
        ]
        main()
    finally:
        sys.argv = old_argv
    assert len(json.loads(output.read_text(encoding="utf-8"))) == 1
    assert "采集 1 条岗位" in capsys.readouterr().out
