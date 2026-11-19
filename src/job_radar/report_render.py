"""Deterministic Markdown templates over a validated structured report."""
from __future__ import annotations

import html
from pathlib import Path
import re
from typing import Literal
import unicodedata

from .report_models import (
    EvidencePacket, EvidenceRecord, MatchAnalysisDraft, MatchReport, PROMPT_VERSION, ReportError, strict_json,
)
from .retrieval_models import JobKey, MatchingResult, SkillCheck

ReportMode = Literal['offline', 'scripted', 'live']
BANNERS = {
    'offline': '本报告未调用 LLM；分析状态为 not_run。',
    'scripted': '本报告使用模拟响应，仅验证工程链路，不代表真实模型分析质量。',
    'live': '本报告包含真实模型响应；结论仍需人工复核。',
}
_UNSPECIFIED = '本报告未标注生成来源。'
_REPORT_STATUS = {'completed': '分析完成', 'partial': '部分岗位分析完成',
                  'failed': '分析未完成', 'not_run': '未运行模型分析'}
_REQUIREMENT = {'required': '必需项', 'bonus': '加分项', 'mention': '提及项', 'unknown': '岗位要求类别未知'}
_SECTIONS = (
    ('supported', '有证据支持'),
    ('unknown', '未找到使用记录（unknown）'),
    ('explicit_gap', '简历明确记录未使用'),
    ('advisory', '建议（不是新增事实）'),
)
_FIELDS = {'title': '职位名称', 'description': '职位描述', 'company': '公司', 'city': '城市',
           'salary': '薪资', 'education': '学历', 'experience': '经验', 'attendance': '出勤', 'posted_at': '发布时间'}
_PREFERENCES = {'preferences.cities': '意向城市', 'preferences.target_roles': '目标岗位',
                'preferences.available_days_per_week': '每周可出勤天数',
                'preferences.available_duration_months': '可实习月数'}
_NO_RESULTS = {'no_query': '当前材料没有形成可用的检索词。', 'no_candidates': '本次没有候选岗位。',
               'no_hits': '本次检索没有返回岗位。'}
_CAVEATS = {
    'source_truth_and_recruiting_status_not_verified': '未核验来源真实性或岗位当前招聘状态。',
    'index_snapshot_may_include_jobs_outside_request': '索引可能含本次未指定的岗位；报告只展示本次检索结果。',
    'contact_minimization_is_not_full_anonymization': '本报告未完全匿名化，分享前请检查引用中的个人信息。',
    'dense_query_computation_not_replayed': '密集检索的查询计算未重放。',
    'retrieval_dense_degraded': '密集检索降级，实际使用的检索通道以原始 JSON 为准。',
    'debug_inputs_may_be_unconfirmed': '本次是调试检索，输入可能未经人工确认。',
    'structured_candidate_unconfirmed': '结构化简历画像尚未获确认。',
    'low_text_content': '简历提取到的文本较少，引用可能无法覆盖全部履历。',
    'pdf_repaired': '简历 PDF 经过读取修复，请对照原件。',
    'some_pages_no_extractable_text': '简历有页面未提取到文字。',
    'replacement_characters': '简历文字含替换字符，请对照原件。',
    'possible_layout_issue': '简历文本顺序可能受排版影响，请对照原件。',
}
_ALWAYS_EXPLAINED = {'citations_validate_location_not_semantic_entailment',
                     'retrieval_scores_are_not_hiring_probabilities', 'scope_absence_is_not_ability_absence'}
_MARKDOWN = re.compile(r"[\\`*_\[\]{}#|!~$]")
_AUTOLINK = re.compile(r'(?<=www)\.|:(?=//)|@', re.IGNORECASE)


def _text(value: str) -> str:
    """Insert untrusted data as inline text, not Markdown structure or HTML."""
    inline = ''.join(' ' if unicodedata.category(c) in {'Cc', 'Cf', 'Cs', 'Zl', 'Zp'} else c for c in value)
    escaped = _MARKDOWN.sub(lambda m: '\\' + m[0], html.escape(inline, quote=True))
    return _AUTOLINK.sub(lambda m: f'&#{ord(m[0])};', escaped)


def validate_report(report: MatchReport, *, require_current_policy: bool = False) -> MatchReport:
    """Revalidate structure/references; old versions are only historical display.

    Explicit current-policy certification never accepts an old prompt version.
    This does not replay the version-ambiguous historical negation validator.
    """
    from .report_analysis import validate_analysis, validate_analysis_references
    report=MatchReport.model_validate(report)
    historical = report.prompt_version != PROMPT_VERSION
    if historical and require_current_policy:
        raise ReportError('analysis_validation_policy_mismatch')
    validator = validate_analysis_references if historical else validate_analysis
    for analysis in report.analyses:
        if analysis.draft is not None:
            validator(analysis.draft, report.packet, analysis.key)
    return report


def _provenance(report: MatchReport, mode: ReportMode | None) -> str:
    if mode is None:
        return _UNSPECIFIED
    if mode not in BANNERS:
        raise ValueError('invalid_execution_mode')
    if mode == 'offline' and (report.status != 'not_run' or any(
            item.status != 'not_analyzed' for item in report.analyses)):
        raise ReportError('analysis_invalid_response')
    if mode == 'live' and not any(item.draft is not None and item.audit is not None and
            item.audit.provider == 'apinebula' and item.audit.attempts > 0 for item in report.analyses):
        raise ReportError('analysis_invalid_response')
    return BANNERS[mode]


def _findings(draft: MatchAnalysisDraft, rank: int, numbers: dict[str, int], *, historical: bool) -> list[str]:
    lines = []
    by_id = {claim.claim_id: claim for claim in draft.claims}
    targets = {ref for claim in draft.claims for ref in claim.related_claim_ids}
    for status, heading in _SECTIONS:
        claims = [claim for claim in draft.claims if claim.status == status]
        if not claims:
            continue
        if historical and status in {'unknown', 'explicit_gap'}:
            heading = {'unknown': '未知（历史结论）', 'explicit_gap': '明确差距（历史结论）'}[status]
        lines += [f'### {heading}', '']
        for claim in claims:
            links = []
            for ref in claim.evidence_ids:
                number = numbers.setdefault(ref, len(numbers) + 1)
                links.append(f'[{number}](#evidence-{number})')
            anchor = f'<a id="finding-{rank}-{claim.claim_id}"></a>' if claim.claim_id in targets else ''
            lines.append(f'- {anchor}**{_text(claim.subject)}（{_REQUIREMENT[claim.requirement_kind]}）**：'
                         f'{_text(claim.text)} {" ".join(links)}')
            if claim.related_claim_ids:
                related = '、'.join(f'[{_text(by_id[ref].subject)}](#finding-{rank}-{ref})'
                                   for ref in claim.related_claim_ids)
                lines.append(f'  - 关联结论：{related}')
        lines.append('')
    return lines


def _quote(value: str) -> str:
    if len(value) <= 220:
        return f'「{_text(value)}」'
    return f'「{_text(value[:220])}…」（节选，全文见 JSON）'


def _evidence_text(record: EvidenceRecord, title: str | None) -> tuple[str, str]:
    if record.job_span is not None:
        field = _FIELDS.get(record.job_span.field, record.job_span.field)
        return f'岗位原文 · {_text(title or "本岗位")} / {_text(field)}', _quote(record.job_span.quote)
    if record.resume_ref is not None:
        ref = record.resume_ref
        value = strict_json(record.value_json)
        block = value.get('block_text') if isinstance(value, dict) else None
        context = (f'；所在段落：{_quote(block.strip())}' if isinstance(block, str)
                   and ref.quote in block and ref.quote.strip() != block.strip() else '')
        return f'简历原文 · 第 {ref.page} 页', _quote(ref.quote) + context
    if record.kind == 'rule':
        try:
            rule = SkillCheck.model_validate(strict_json(record.value_json))
        except ValueError:
            return '规则核对', '完整规则条目见原始 JSON。'
        group = ' 或 '.join(_text(skill) for skill in rule.alternatives)
        result = ('规则找到以下技能的简历证据：' + '、'.join(_text(skill) for skill in rule.matched)
                  if rule.status == 'evidence_found' and rule.matched else
                  '规则未找到简历证据' if rule.status == 'not_found' else '完整结果见原始 JSON')
        return '规则核对', f'{group}（{_REQUIREMENT[rule.kind]}）；{result}。'
    if record.kind == 'scope':
        return '材料核查范围', '仅限本次提交的简历和派生画像；不外推未提交的经历。'
    if record.kind == 'retrieval':
        return '检索快照', '原排名见岗位标题；检索分项和通道信息见原始 JSON。'
    value = strict_json(record.value_json)
    label = _PREFERENCES.get(record.path, '用户偏好')
    if value is None:
        return label, '未提供。'
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return label, '、'.join(_text(item) for item in value)
    if type(value) is int:
        return label, str(value)
    return label, '详细内容见原始 JSON。'


def _limitations(packet: EvidencePacket, matching: MatchingResult) -> list[str]:
    lines = ['- 检索排序与规则覆盖不是录用概率；材料未知（unknown）不等于不会。',
             '- 引用可用于回查来源，不能代替人工复核；未分析的岗位不作能力结论。']
    extras = False
    for code in dict.fromkeys(packet.limitations + matching.warnings):
        if code in _CAVEATS:
            lines.append('- ' + _CAVEATS[code])
        elif code not in _ALWAYS_EXPLAINED:
            extras = True
    if extras:
        lines.append('- 其余检索或材料诊断见原始 JSON。')
    return lines


def _key(key: JobKey) -> tuple[str, int]:
    return key.dataset_id, key.job_id


def render_match_report(report: MatchReport, *, mode: ReportMode | None = None) -> str:
    """Render only accepted facts; all labels, ordering and links are local."""
    report = validate_report(report)
    banner = _provenance(report, mode)
    packet = report.packet
    matching = packet.retrieval.matching
    historical = report.prompt_version != PROMPT_VERSION
    titles = {_key(e.key): e.quote for e in packet.evidence
              if e.kind == 'job' and e.key is not None and e.path == 'job.title'}
    numbers: dict[str, int] = {}
    completed = sum(a.status == 'completed' for a in report.analyses)
    lines = ['# 岗位匹配分析报告', '', f'**{_REPORT_STATUS[report.status]} · 待人工复核**', '', banner, '']
    if historical:
        lines += ['**历史只读，不代表已通过v3；保留原结论，不用当前规则重新认定。**', '']
    if packet.selected_keys:
        lines += [f'已完成模型分析：{completed} / {len(packet.selected_keys)} 个选定岗位。', '']
    if not matching.results:
        lines += [_NO_RESULTS.get(matching.status, '本次没有可展示的岗位。'), '']
    for row, analysis in zip(matching.results, report.analyses):
        title = titles.get(_key(row.key)) or f'{row.key.dataset_id}/{row.key.job_id}'
        lines += [f'## {row.rank}. {_text(title)}', '']
        if analysis.status == 'not_analyzed':
            lines += ['只展示检索与规则结果，未做模型分析。', '']
        elif analysis.status == 'failed':
            lines += [f'模型分析未通过；没有可展示的模型结论。错误码：`{analysis.error_code}`。', '']
        rules = row.rules
        coverage = (f'找到 {rules.covered_required} / {rules.total_required} 组必需技能的简历证据。'
                    if rules.total_required else '规则未识别到可核对的必需技能组。')
        lines += [f'**必需技能的证据覆盖**：{coverage}', '']
        if analysis.draft is not None:
            lines.extend(_findings(analysis.draft, row.rank, numbers, historical=historical))
    if matching.excluded:
        lines += ['## 规则排除项', '', '以下岗位因明确的硬约束冲突未参与排序，不是模型结论：', '']
        lines.extend(f'- {_text(row.key.dataset_id)}/{row.key.job_id}' for row in matching.excluded)
        lines.append('')
    if numbers:
        lines += ['## 引用证据', '', '正文中的数字可跳转到对应证据；岗位和简历原文是引用，不是额外结论。', '']
        records = {record.evidence_id: record for record in packet.evidence}
        for ref, number in numbers.items():
            record = records[ref]
            label, body = _evidence_text(record, titles.get(_key(record.key)) if record.key else None)
            # Short local numbers for readers; original IDs remain recoverable without clutter.
            lines.append(f'- <a id="evidence-{number}"></a><!-- evidence-id: {ref} --> **[{number}] {label}**：{body}')
        lines.append('')
    lines += ['## 阅读边界', '', *_limitations(packet, matching), '', '---', '',
              f'**报告版本**：{report.schema_version} / {report.prompt_version}', '',
              f'**原始报告**：{_text(report.report_id)}', '',
              '完整证据、来源哈希、原始状态和检索分项保留在原始 JSON。']
    return '\n'.join(lines) + '\n'


def export_match_report(report: MatchReport, directory: str | Path, *,
                        mode: ReportMode | None = None) -> tuple[Path, Path]:
    """Export unchanged structured JSON and a readable view, without overwriting."""
    report = MatchReport.model_validate(report)
    markdown = render_match_report(report, mode=mode)
    payload = report.model_dump_json(indent=2) + '\n'
    path = Path(directory).expanduser().absolute()
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    jp, mp = path / 'match-report.json', path / 'match-report.md'
    try:
        with jp.open('x', encoding='utf-8') as f:
            f.write(payload)
        with mp.open('x', encoding='utf-8') as f:
            f.write(markdown)
        checked = MatchReport.model_validate_json(jp.read_text(encoding='utf-8'))
        if checked != report or mp.read_text(encoding='utf-8') != render_match_report(checked, mode=mode):
            raise ReportError('report_export_mismatch')
    except Exception:
        for p in (jp, mp):
            if p.is_file():
                p.unlink()
        path.rmdir()
        raise
    return jp, mp
