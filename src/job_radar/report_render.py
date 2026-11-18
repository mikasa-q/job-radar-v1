"""Deterministic local rendering. Untrusted text is escaped, never active HTML."""
from __future__ import annotations
import html
import json
from pathlib import Path
from .report_models import MatchReport,ReportError,PROMPT_VERSION

def _text(value):
    value=html.escape(str(value),quote=True).replace('\r',' ').replace('\n',' ')
    for char in ('\\','`','*','_','[',']','{','}','#','|','!','>'):
        value=value.replace(char,'\\'+char)
    return value

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
            validator(analysis.draft,report.packet,analysis.key)
    return report

def render_match_report(report: MatchReport) -> str:
    report=validate_report(report)
    packet=report.packet; matching=packet.retrieval.matching
    lines=['# 岗位匹配分析报告','',f'- 报告：{_text(report.report_id)}',f'- 生成时间：{report.generated_at.isoformat()}',
        f'- 版本：{report.schema_version} / {report.prompt_version}',f'- 分析状态：{report.status}；人工复核：{report.review_status}',
        f'- 检索状态：{matching.status}；模式：{matching.actual_mode}；用途：{matching.purpose}',
        f'- 证据包 SHA256：`{report.packet_sha256}`','',
        '**检索排名与证据覆盖不是录用概率；unknown不等于不会。预设响应只验证工程链路，不证明真实模型分析质量。**','', '## 岗位与分析','']
    if report.prompt_version != PROMPT_VERSION:
        lines += ['**历史只读，不代表已通过v3；保留原版本、状态和原文，不重演历史校验器。**', '']
    by_key={(a.key.dataset_id,a.key.job_id):a for a in report.analyses}
    titles={(e.key.dataset_id,e.key.job_id):e.quote for e in packet.evidence if e.kind=='job' and e.path=='job.title' and e.key}
    for row in matching.results:
        key=(row.key.dataset_id,row.key.job_id); a=by_key[key]
        title=titles.get(key) or f'{key[0]}/{key[1]}'
        lines += [f'### {row.rank}. {_text(title)}',f'- 岗位：{_text(key[0])}/{key[1]}；原审核状态：{row.review_status}',
            f'- BM25：{row.bm25.score if row.bm25 else "未启用或未命中"}；Dense：{row.dense.score if row.dense else "未启用或未命中"}；RRF：{row.fusion_score}',
            f'- 规则必需项证据覆盖：{row.rules.covered_required}/{row.rules.total_required}；coverage={row.rules.coverage}',
            f'- 分析：{a.status}'+(f'；错误：{a.error_code}' if a.error_code else '')]
        if a.draft:
            for c in a.draft.claims:
                cites=' '.join(f'[{ref}](#{ref})' for ref in c.evidence_ids)
                lines.append(f'- **{_text(c.kind)} / {_text(c.status)} / {_text(c.subject)}**：{_text(c.text)} {cites}')
                if c.related_claim_ids:lines.append('  - 关联结论：'+', '.join(c.related_claim_ids))
        lines.append('')
    lines+=['## 排除项与检索通道','']
    for row in matching.excluded:
        lines.append(f'- {_text(row.key.dataset_id)}/{row.key.job_id}：{row.reason}；规则原样保留在JSON。')
    for channel in matching.channels:
        lines.append(f'- {channel.channel}：{channel.status}；原因：{_text(channel.reason or "无")}')
    lines+=['','## 局限与警告','']
    lines.extend('- '+_text(v) for v in packet.limitations+matching.warnings)
    lines+=['- 文本引用校验不等于语义支持已获人工确认；未分析岗位不作能力结论。','','## 引用表','']
    for e in packet.evidence:
        where=f'{e.key.dataset_id}/{e.key.job_id}' if e.key else '全局画像/偏好'
        lines += [f'<a id="{e.evidence_id}"></a>',f'### {e.evidence_id}',
            f'- 类型：{e.kind}；归属：{_text(where)}；路径：{_text(e.path)}',f'- 来源哈希：`{e.source_sha256}`']
        if e.resume_ref:lines.append(f'- 简历：第{e.resume_ref.page}页 / {e.resume_ref.block_id}')
        if e.job_span:lines.append(f'- 岗位原文：{_text(e.job_span.field)} [{e.job_span.start}:{e.job_span.end}]')
        if e.kind=='scope':
            content='核查范围：本次提供的画像投影；未找到证据不等于缺少能力。原始文档引用已本地复核。'
        elif e.kind=='retrieval':
            content='原检索排名/分项及通道状态，详见上方岗位与原始JSON。'
        elif e.kind=='rule':
            value=json.loads(e.value_json)
            content=('技能：'+ '/'.join(value.get('alternatives',[]))+'；类型：'+value.get('kind','')+'；证据状态：'+value.get('status','')) if 'alternatives' in value else ('约束：'+value.get('name','')+'；状态：'+value.get('status','')) if 'name' in value else '完整规则结果见原始JSON；coverage是规则证据覆盖，不是胜任率。'
        else:content=e.quote if e.quote is not None else e.value_json
        lines += ['- 内容：'+_text(content),'']
    return '\n'.join(lines)+'\n'

def export_match_report(report: MatchReport, directory: str|Path) -> tuple[Path,Path]:
    """New directory only; never overwrite existing artifacts or inputs."""
    report=validate_report(report)
    markdown=render_match_report(report)
    payload=report.model_dump_json(indent=2)+'\n'
    path=Path(directory).expanduser().absolute()
    path.mkdir(parents=True,exist_ok=False)
    jp=path/'match-report.json'; mp=path/'match-report.md'
    try:
        with jp.open('x',encoding='utf-8') as f:f.write(payload)
        with mp.open('x',encoding='utf-8') as f:f.write(markdown)
        checked=MatchReport.model_validate_json(jp.read_text())
        if checked!=report or mp.read_text()!=render_match_report(checked):raise ReportError('report_export_mismatch')
    except Exception:
        for p in (jp,mp):
            if p.is_file():p.unlink()
        path.rmdir()
        raise
    return jp,mp
