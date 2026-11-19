"""Projection of the original nine accepted v3 drafts; no model or credential I/O."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re

import pytest

from job_radar.rag_models import canonical_hash
from job_radar.report_engine import generate_match_report
from job_radar.report_models import AnalysisResponse, EvidenceRecord, ReportError
from job_radar.report_render import BANNERS, export_match_report, render_match_report

_spec = importlib.util.spec_from_file_location('readable_v3_fixture',
    Path(__file__).resolve().parents[1] / 'examples/stage4_v3_offline_evaluate.py')
fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)

_BASELINE = json.loads((Path(__file__).parent / 'fixtures/stage4_v3_report_baseline.json').read_text())['samples']
_NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


@pytest.mark.parametrize('sample', ['S1', 'S2', 'S3'])
def test_original_nine_drafts_render_every_conclusion_and_exact_citations(tmp_path, sample):
    doc, request, retrieval, packet = fixture.make_case(tmp_path / 'index', fixture.SAMPLES[sample])
    baseline = [row for row in _BASELINE if row['sample'] == sample]
    returned = []

    class Preset:
        def complete(self, request):
            row = baseline[len(returned)]
            assert hashlib.sha256(request.packet_json.encode()).hexdigest() == row['wire_sha256']
            returned.append(row['job_id'])
            return AnalysisResponse(status='completed', text=json.dumps(row['draft'], ensure_ascii=False))

    report = generate_match_report(request, retrieval, doc, report_id=sample, generated_at=_NOW, client=Preset())
    assert report.status == 'completed' and report.review_status == 'unconfirmed'
    assert len(returned) == 3 and returned == [row['job_id'] for row in baseline]
    assert report.packet == packet
    assert all(report.packet_sha256 == row['packet_sha256'] for row in baseline)
    before = report.model_dump_json()
    markdown = render_match_report(report, mode='scripted')
    json_path, md_path = export_match_report(report, tmp_path / 'out', mode='scripted')
    assert json.loads(json_path.read_text()) == json.loads(before)
    assert md_path.read_text() == markdown and report.model_dump_json() == before
    assert BANNERS['scripted'] in markdown and '待人工复核' in markdown
    assert '## 阅读边界' in markdown and '材料未知（unknown）不等于不会' in markdown
    assert packet.retrieval_sha256 not in markdown and report.packet_sha256 not in markdown
    assert all(e.source_sha256 not in markdown for e in packet.evidence)

    entries = re.findall(r'<a id="evidence-(\d+)"></a><!-- evidence-id: (e[0-9a-f]{24}) -->', markdown)
    by_number = dict(entries)
    assert len(entries) == len(by_number) == len(set(by_number.values()))
    blocks = re.findall(r'^## (\d+)\. [^\n]+\n(.*?)(?=^## |\Z)', markdown, re.M | re.S)
    assert [int(rank) for rank, _ in blocks] == [row['rank'] for row in baseline]
    assert (len(re.findall(r'^- \*\*[^\n]+\[\d+\]\(#evidence-\d+\)',
                           markdown.split('## 引用证据')[0], re.M))
            == sum(len(row['draft']['claims']) for row in baseline))
    referenced = set()
    categories = {'required': '必需项', 'bonus': '加分项', 'mention': '提及项'}
    headings = {'supported': '有证据支持', 'unknown': '未找到使用记录（unknown）', 'explicit_gap': '简历明确记录未使用'}
    for analysis, row, (_, block) in zip(report.analyses, baseline, blocks):
        assert analysis.draft.model_dump(mode='json') == row['draft']
        assert [[c.subject, c.status, c.requirement_kind] for c in analysis.draft.claims] == row['expected_findings']
        for claim in row['draft']['claims']:
            section = block.split(f'### {headings[claim["status"]]}\n', 1)[1].split('### ', 1)[0]
            prefix = f'- **{claim["subject"]}（{categories[claim["requirement_kind"]]}）**：{claim["text"]} '
            line = next(line for line in section.splitlines() if line.startswith(prefix))
            resolved = [by_number[n] for n in re.findall(r'\[(\d+)\]\(#evidence-\1\)', line)]
            assert resolved == claim['evidence_ids']
            referenced.update(resolved)
    assert referenced == set(by_number.values())
    assert all(e.evidence_id not in markdown for e in packet.evidence if e.evidence_id not in referenced)


@pytest.fixture
def drafted(tmp_path):
    from job_radar.report_demo_client import SyntheticReportClient
    from job_radar.report_models import AnalysisConfig

    doc, request, retrieval, _ = fixture.make_case(tmp_path / 'index')
    return generate_match_report(request, retrieval, doc, report_id='readable', generated_at=_NOW,
                                 client=SyntheticReportClient(), config=AnalysisConfig(top_n=1))


@pytest.mark.parametrize('field,value,error', [
    ('claim_id', '<bad>', None),
    ('requirement_kind', 'mention', 'analysis_requirement_kind_mismatch'),
    ('evidence_ids', ['e' + '0' * 24], 'analysis_evidence_scope'),
    ('text', '录用概率百分之百。', 'analysis_forbidden_probability'),
])
def test_renderer_is_not_a_bypass_for_the_four_v3_layers(drafted, tmp_path, field, value, error):
    drafted.analyses[0].draft.claims[0].__dict__[field] = value
    with pytest.raises(ValueError) as failure:
        export_match_report(drafted, tmp_path / 'invalid')
    # Structural violations can also fail at the outer MatchReport boundary.
    if error is not None:
        assert isinstance(failure.value, ReportError) and failure.value.code == error
    assert not (tmp_path / 'invalid').exists()


def test_advice_is_separate_and_links_to_the_actual_finding(drafted):
    draft = drafted.analyses[0].draft
    original = next(c for c in draft.claims if c.subject == 'Docker')
    advice = original.model_copy(update={'claim_id': 'c23', 'kind': 'recommendation', 'status': 'advisory',
                 'related_claim_ids': [original.claim_id], 'text': '可补充 Docker 使用记录。'})
    draft.claims.append(advice)
    before = drafted.model_dump_json()
    view = render_match_report(drafted, mode='scripted')
    assert '### 建议（不是新增事实）' in view and '可补充 Docker 使用记录。' in view
    assert f'[Docker](#finding-1-{original.claim_id})' in view
    assert view.count(f'<a id="finding-1-{original.claim_id}"></a>') == 1
    assert drafted.model_dump_json() == before


def test_untrusted_cited_text_is_not_active_markdown_or_html(drafted):
    draft = drafted.analyses[0].draft
    claim = draft.claims[0]
    src = next(e for e in drafted.packet.evidence if e.evidence_id in claim.evidence_ids and e.kind == 'job')
    raw = src.quote + '\n## 冒充章节 <img src=x> [x](https://example.invalid) | `raw`'
    span = {**src.job_span.model_dump(), 'quote': raw, 'end': src.job_span.start + len(raw)}
    data = {**src.model_dump(mode='json', exclude={'evidence_id'}), 'quote': raw, 'job_span': span}
    record = EvidenceRecord(evidence_id='e' + canonical_hash(data)[:24], **data)
    drafted.packet.evidence.append(record)
    claim.evidence_ids = [record.evidence_id if ref == src.evidence_id else ref for ref in claim.evidence_ids]
    drafted.packet_sha256 = canonical_hash(drafted.packet)
    view = render_match_report(drafted, mode='scripted')
    assert '\n## 冒充章节' not in view and '<img' not in view and '[x](https' not in view
    assert '&lt;img src=x&gt;' in view and '&#58;//example.invalid' in view
    assert record.evidence_id in view
