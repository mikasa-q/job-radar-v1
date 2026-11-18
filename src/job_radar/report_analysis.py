"""Single-job analysis boundary: fixed prompt, minimized wire data, cited drafts.

Mechanical and conservative language checks are deliberately incomplete semantic
checks. They are not a calibrated capability, hiring, or truth classifier.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from pydantic import ValidationError

from .report_core import parse_analysis
from .report_models import (
    AnalysisClient, AnalysisConfig, AnalysisRequest, AnalysisResponse, CallAudit,
    EvidencePacket, JobAnalysis, MatchAnalysisDraft, PROMPT_VERSION, ReportError,
    strict_json,
)
from .retrieval_models import JobKey
from .retrieval_text import retrieval_text

from .report_prompt_v3 import SYSTEM_PROMPT_V3 as SYSTEM_PROMPT


# References and source metadata stay local. Content keeps contextual block text
# after minimization but not raw reference objects, local paths or source URLs.
_LOCAL_FIELDS = frozenset({
    'quote','resume_ref','job_span','resume_evidence','job_evidence','evidence',
    'provenance','url','source_file','source_path','file_path','source_sha256',
    'jd_sha256','fact_sha256','source_file_sha256','span','mapping','encoding_text',
    'encoding_sha256','block_id','page','start','end','source','parser_version','extractor_version',
})
_LOCAL_PATH = re.compile(
    r'(?i)(?:file://[^\s，。；;]*|(?<![A-Za-z0-9])/(?:Users|home|private|tmp|var|Volumes|etc|opt|Applications|Library|usr|root)/[^\s，。；;]*|(?<![A-Za-z0-9])[A-Z]:\\[^\s，。；;]*)'
)
# Frozen literal/shape checks apply to a comparison copy only (protocol section 2).
_PERCENT = re.compile(r'%|百分之|录用概率|胜任率|胜任概率|成功率|可信度|置信度|hireprobability|hiringprobability|chanceofhiring|chanceofbeinghired|confidence')
_INFLATION = re.compile(r'精通|熟练|专家|资深|生产经验|生产环境经验|正式工作|生产系统|expert|proficient|senior|production(?:experience|system)|[零〇一二两三四五六七八九十百千万0-9]+年(?:工作|开发|生产)?经验|[0-9]+years?(?:of)?experience')
# Original-source-only finite grammar; never an output-negation exemption.
_NEGATIVE = re.compile(r'(?:从未使用|未使用|没有使用|没用过)\s*{subject}(?:过)?|(?:i\s+)?(?:never\s+used|have\s+not\s+used)\s+{subject}', re.I)
_MANDATORY = re.compile(r'必须|必需|必补|硬性|must|required')
_FUTURE = re.compile(r'计划|打算|准备学|想学|未来|plan(?:ning)?\s+to|intend\s+to|will\s+learn', re.I)
_TEXT_MARKERS = re.compile(r'匹配度高|匹配度低|高度匹配|胜任|满足岗位要求|能力不足|不代表|不等于|不等同于|不意味着|不能据此|仅说明|限于|不足以|无法确定|尚不能|doesnotimply|notequivalent')

# Original evidence only: a sentence delimiter must not erase an earlier
# hypothetical, quoted or other-person frame. This finite conservative gate is
# not a model-output negation whitelist and does not infer the frame's scope.
_SOURCE_CONTEXT_AMBIGUITY = re.compile(
    r'如果|假如|假设|假定|若|除非|条件|并非|不是|未必|不一定|可能|或许|也许|'
    r'不确定|他人|同事|朋友|别人|团队|原话|转述|引述|引用|[“”「」『』"]|'
    r'\b(?:if|unless|hypothetical|suppose|supposing|assume|assuming|'
    r'colleague|someone|friend|team|quoted?|quotation|said|uncertain|perhaps|maybe)\b',
    re.I,
)


def _clean_text(value: str) -> str:
    return _LOCAL_PATH.sub(' ', retrieval_text(value)).strip()


def _wire_content(value, depth=0):
    if depth > 32:
        raise ReportError('analysis_input_depth')
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, list):
        return [_wire_content(v,depth+1) for v in value]
    if isinstance(value, dict):
        return {k:_wire_content(v,depth+1) for k,v in value.items()
                if k not in _LOCAL_FIELDS and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',k)}
    if value is None or type(value) in (int,bool):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ReportError('analysis_input_value')


def _checked(packet, key):
    try:
        packet=EvidencePacket.model_validate(packet)
        key=JobKey.model_validate(key)
    except (ValueError,TypeError,RecursionError):
        raise ReportError('analysis_invalid_packet') from None
    if key not in packet.selected_keys:
        raise ReportError('analysis_key_not_selected')
    # Keys/paths are structural references, not free text that may be rewritten.
    if _clean_text(key.dataset_id) != key.dataset_id or '/' in key.dataset_id or '\\' in key.dataset_id:
        raise ReportError('analysis_unsafe_identity')
    ranked=next((r for r in packet.retrieval.matching.results if r.key==key),None)
    if ranked is None:
        raise ReportError('analysis_key_not_ranked')
    return packet,key,ranked


def build_analysis_request(packet: EvidencePacket, key: JobKey, *, config: AnalysisConfig|None=None) -> AnalysisRequest:
    packet,key,ranked=_checked(packet,key)
    config=AnalysisConfig() if config is None else AnalysisConfig.model_validate(config)
    records=[]
    title=None
    for record in packet.evidence:
        if record.key is not None and record.key!=key:
            continue
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.\[\]:-]*',record.path):
            raise ReportError('analysis_unsafe_evidence_path')
        try:
            content=_wire_content(strict_json(record.value_json))
        except ReportError:
            raise
        except (ValueError,TypeError,RecursionError):
            raise ReportError('analysis_invalid_evidence_json') from None
        records.append({'evidence_id':record.evidence_id,'kind':record.kind,'path':record.path,
                        'key':record.key.model_dump() if record.key else None,'content':content})
        if record.kind=='job' and record.job_span is not None and record.job_span.field=='title':
            title=_clean_text(record.quote or '')
    context={
        'title':title, 'rank':ranked.rank,
        'bm25':_wire_content(ranked.bm25.model_dump(mode='json')) if ranked.bm25 else None,
        'dense':_wire_content(ranked.dense.model_dump(mode='json')) if ranked.dense else None,
        'fusion_score':ranked.fusion_score,
        'rules':_wire_content(ranked.rules.model_dump(mode='json')),
        'channels':_wire_content([c.model_dump(mode='json') for c in packet.retrieval.matching.channels]),
        'retrieval_status':packet.retrieval.matching.status,
        'actual_mode':packet.retrieval.matching.actual_mode,
    }
    data={'schema_version':'match-analysis-input-v1','key':key.model_dump(),
          'purpose':packet.retrieval.matching.purpose,'context':context,'evidence':records,
          'limitations':[_clean_text(v) for v in packet.limitations]}
    serialized=json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    schema=json.dumps(MatchAnalysisDraft.model_json_schema(),ensure_ascii=False,separators=(',',':'))
    allocation={'system_prompt':SYSTEM_PROMPT,'packet':data,'schema':strict_json(schema),
                'prompt_version':PROMPT_VERSION,'timeout_seconds':config.timeout_seconds,
                'max_output_bytes':config.max_output_bytes}
    try:
        if len(json.dumps(allocation,ensure_ascii=False,separators=(',',':')).encode('utf-8'))>config.max_input_bytes:
            raise ReportError('analysis_input_limit')
    except UnicodeError:
        raise ReportError('analysis_invalid_input_text') from None
    return AnalysisRequest(system_prompt=SYSTEM_PROMPT,packet_json=serialized,output_schema_json=schema,
        timeout_seconds=config.timeout_seconds,max_output_bytes=config.max_output_bytes,prompt_version=PROMPT_VERSION)


def _same_subject(left, right):
    return left.casefold().strip()==right.casefold().strip()


def _subject_in(subject, value):
    return bool(re.search(r'(?<![a-z0-9_])'+re.escape(subject)+r'(?![a-z0-9_])',value,re.I))


def _comparison(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if not c.isspace())


def _structured_draft(draft, packet, key):
    packet, key, ranked = _checked(packet, key)
    try:
        draft = MatchAnalysisDraft.model_validate(draft)
    except (ValueError, TypeError, RecursionError):
        raise ReportError('analysis_schema_error') from None
    if draft.key != key:
        raise ReportError('analysis_key_mismatch')
    return draft, packet, key, ranked


def _claim_references(claim, all_refs, key):
    refs = []
    for identifier in claim.evidence_ids:
        record = all_refs.get(identifier)
        if record is None or (record.key is not None and record.key != key):
            raise ReportError('analysis_evidence_scope')
        refs.append(record)
    return refs


def validate_analysis_references(draft, packet, key):
    """Historical read-only checks, not a replay or current policy approval.

    Nested model validation checks record/packet hashes and ranking snapshots;
    the caller must still label the original version/status as historical.
    """
    draft, packet, key, _ = _structured_draft(draft, packet, key)
    all_refs = {e.evidence_id: e for e in packet.evidence}
    for claim in draft.claims:
        _claim_references(claim, all_refs, key)
    return draft


def _source_clauses(value):
    # Preserve commas, newlines and colon labels: a condition/other-person
    # prefix must not be cut off before negative-evidence recognition.
    return [s.strip() for s in re.split(r'[。；;!！?？]', value) if s.strip()]


def _direct_negative(subject, sentence):
    """Finite original-source grammar: same-skill direct first-person statement.

    Fullmatch rather than scanning for an unrelated negative token prevents
    conditions, other people's experience and double negations being certified.
    """
    skill = re.escape(subject) + r'(?![a-z0-9_])'
    prefix = r'(?:(?:个人说明|个人经历|自述|技能说明|说明|简历注明|简历说明)\s*[:：]\s*)?(?:(?:我|本人)\s*)?'
    statement = _NEGATIVE.pattern.format(subject=skill)
    return bool(re.fullmatch(prefix + r'(?:' + statement + r')\s*[.]?', sentence, re.I))


def _source_state(subject, records):
    """Return absent/plan/used/negative/ambiguous for the supplied full scope.

    Any unclassified mention blocks an unknown absence assertion. Only a plain
    future-learning clause is accepted as a non-use mention; no text is edited.
    """
    states = set()
    for record in records:
        try:
            value = strict_json(record.value_json)
        except (ValueError, TypeError, RecursionError):
            raise ReportError('analysis_invalid_packet') from None
        source = value.get('block_text', record.quote or '') if isinstance(value, dict) else record.quote or ''
        # Check the complete supplied block before cutting clauses. In ambiguous
        # frames, neither a later bare usage sentence nor a bare denial is
        # attributed to the candidate. Missing certainty is not absence.
        if _subject_in(subject, source) and _SOURCE_CONTEXT_AMBIGUITY.search(source):
            states.add('ambiguous')
            continue
        for clause in _source_clauses(source):
            if not _subject_in(subject, clause):
                continue
            if _direct_negative(subject, clause):
                states.add('negative')
            elif re.fullmatch(r'(?:(?:学习计划|计划)\s*[:：]\s*)?(?:(?:我|本人)\s*)?(?:计划学习|打算学习|准备学|想学|未来学习)\s*' + re.escape(subject) + r'(?![a-z0-9_])', clause, re.I) or re.fullmatch(r'(?:i\s+)?(?:plan(?:ning)?\s+to\s+learn|intend\s+to\s+learn|will\s+learn)\s+' + re.escape(subject) + r'[.]?', clause, re.I):
                states.add('plan')
            elif _FUTURE.search(clause) or re.search(r'如果|假如|若|并非|不是|未必|不一定|可能|他人|同事|朋友|别人|团队|从未|未使用|没有使用|没用过|不会|不使用|\b(?:if|unless|colleague|someone|not|never)\b', clause, re.I):
                states.add('ambiguous')
            elif re.search(r'(?:使用|用|采用|利用).{0,80}?' + re.escape(subject) + r'(?![a-z0-9_])|\b(?:used?|using)\s+.{0,80}?' + re.escape(subject) + r'(?![a-z0-9_])|' + re.escape(subject) + r'\s*(?:开发|编写|实现|构建|制作)', clause, re.I):
                states.add('used')
            else:
                states.add('ambiguous')
    if 'used' in states:
        return 'used'
    if 'negative' in states:
        return 'negative'
    if 'ambiguous' in states:
        return 'ambiguous'
    return 'plan' if states else 'absent'


def _short_fact(claim):
    text = claim.text
    if len(text) > 80 or not text.strip() or any(unicodedata.category(c) in {'Cc', 'Cf', 'Cs'} for c in claim.subject + text):
        return False
    if any(c in text for c in ';；\r\n') or len(text.splitlines()) > 1:
        return False
    punctuation = unicodedata.normalize('NFKC', text)
    if ';' in punctuation:
        return False
    body = punctuation[:-1] if punctuation.endswith(('。', '.')) else punctuation
    if any(c in body for c in '。！？!?'):
        return False
    for i, char in enumerate(body):
        if char == '.' and not (0 < i < len(body)-1 and body[i-1].isascii() and body[i-1].isalnum() and body[i+1].isascii() and body[i+1].isalnum()):
            return False
    if _TEXT_MARKERS.search(_comparison(claim.subject + ' ' + text)):
        return False
    if claim.status == 'unknown' and text != f'简历未提及 {claim.subject} 使用经历。':
        return False
    return True


def validate_analysis(draft: MatchAnalysisDraft, packet: EvidencePacket, key: JobKey) -> MatchAnalysisDraft:
    """Current v3 full validation: all claims finish each layer before the next.

    This is conservative finite policy enforcement, not semantic entailment.
    No branch repairs model text, references, field values or source material.
    """
    # Layer 1: structure over the whole draft.
    draft, packet, key, ranked = _structured_draft(draft, packet, key)
    all_refs = {e.evidence_id: e for e in packet.evidence}
    by_claim = {c.claim_id: c for c in draft.claims}
    contexts = []
    # Layer 2: canonical skill / selected rule category / finding relationships.
    for claim in draft.claims:
        subject_rules = [s for s in ranked.rules.skills if claim.subject in s.alternatives]
        if not subject_rules or claim.requirement_kind == 'unknown' or not any(s.kind == claim.requirement_kind for s in subject_rules):
            raise ReportError('analysis_requirement_kind_mismatch')
        # Inspect only valid same-job rule refs here. Invalid/missing refs are
        # deliberately left to layer 3, so a reference attack is not text-tested.
        cited_rules = []
        for identifier in claim.evidence_ids:
            record = all_refs.get(identifier)
            if record is not None and record.kind == 'rule' and record.key == key:
                value = strict_json(record.value_json)
                cited_rules.extend(s for s in subject_rules if value == s.model_dump(mode='json'))
        if cited_rules and any(s.kind != claim.requirement_kind for s in cited_rules):
            raise ReportError('analysis_requirement_kind_mismatch')
        if claim.status == 'unknown' and claim.kind != 'unknown':
            raise ReportError('analysis_unknown_as_inability')
        if claim.kind == 'skill_gap' and any(len(s.alternatives) > 1 and s.status == 'evidence_found' for s in subject_rules):
            raise ReportError('analysis_alternative_already_supported')
        if claim.kind == 'recommendation' and any(by_claim[i].requirement_kind != claim.requirement_kind for i in claim.related_claim_ids):
            raise ReportError('analysis_advice_requirement_mismatch')
        contexts.append((claim, subject_rules, cited_rules))
    # Layer 3: claim membership and exact rule/source support over the whole draft.
    all_resume = [e for e in packet.evidence if e.kind == 'resume']
    for claim, subject_rules, cited_rules in contexts:
        refs = _claim_references(claim, all_refs, key)
        if (not cited_rules or any(e.kind == 'rule' and not any(
                strict_json(e.value_json) == s.model_dump(mode='json')
                for s in subject_rules) for e in refs)):
            raise ReportError('analysis_invalid_rule_reference')
        resume_refs = [e for e in refs if e.kind == 'resume']
        subject_jobs = [e for e in refs if e.kind == 'job' and _subject_in(claim.subject, e.quote or '')]
        kinds = {e.kind for e in refs}
        if claim.status == 'supported':
            supported = [s for s in cited_rules if s.status == 'evidence_found' and claim.subject in s.matched]
            if not subject_jobs or not resume_refs or not supported:
                raise ReportError('analysis_unsupported_positive')
            if not any(e.resume_ref == source for s in supported for source in s.resume_evidence for e in resume_refs):
                raise ReportError('analysis_resume_support_mismatch')
            # A legacy rule match is not permission to turn plans/negative source
            # into usage. This is a stage-four acceptance guard, not a rule edit.
            if _source_state(claim.subject, resume_refs) != 'used':
                raise ReportError('analysis_resume_support_mismatch')
        if claim.status == 'unknown':
            if not subject_jobs or 'scope' not in kinds:
                raise ReportError('analysis_unknown_requires_scope')
            if any(s.status == 'evidence_found' and claim.subject in s.matched for s in subject_rules) or _source_state(claim.subject, all_resume) not in {'absent', 'plan'}:
                raise ReportError('analysis_unknown_evidence_conflict')
        if claim.status == 'explicit_gap':
            negatives = []
            for record in resume_refs:
                value = strict_json(record.value_json)
                source = value.get('block_text', record.quote or '') if isinstance(value, dict) else record.quote or ''
                if (not _SOURCE_CONTEXT_AMBIGUITY.search(record.quote or '')
                        and not _SOURCE_CONTEXT_AMBIGUITY.search(source)
                        and any(_direct_negative(claim.subject, sentence) for sentence in _source_clauses(record.quote or ''))
                        and any(_direct_negative(claim.subject, sentence) for sentence in _source_clauses(source))):
                    negatives.append(record)
            if not subject_jobs or not negatives:
                raise ReportError('analysis_gap_requires_negative_evidence')
        if claim.kind == 'recommendation':
            findings = [by_claim[i] for i in claim.related_claim_ids]
            job_ids = {e.evidence_id for e in subject_jobs}
            if not subject_jobs or not all(claim.subject == f.subject and job_ids.intersection(f.evidence_ids) for f in findings):
                raise ReportError('analysis_advice_requires_finding_evidence')
    # Layer 4: output-only text policy. Source evidence/score tables are not scanned.
    for claim, subject_rules, _ in contexts:
        text = _comparison(claim.subject + ' ' + claim.text)
        if _PERCENT.search(text):
            raise ReportError('analysis_forbidden_probability')
        if _INFLATION.search(text):
            raise ReportError('analysis_unsupported_experience')
        if claim.kind == 'recommendation' and _MANDATORY.search(_comparison(claim.text)) and any(s.kind == 'bonus' or (len(s.alternatives) > 1 and s.status == 'evidence_found') for s in subject_rules):
            raise ReportError('analysis_optional_as_mandatory')
        if not _short_fact(claim):
            raise ReportError('analysis_text_contract_violation')
        normalized = unicodedata.normalize('NFKC', claim.subject + ' ' + claim.text)
        if _clean_text(normalized) != normalized.strip():
            raise ReportError('analysis_text_sensitive_content')
    return draft


_ERROR_CODES=frozenset({
    'analysis_input_depth','analysis_input_value','analysis_invalid_packet',
    'analysis_key_not_selected','analysis_unsafe_identity','analysis_key_not_ranked',
    'analysis_unsafe_evidence_path','analysis_invalid_evidence_json','analysis_input_limit',
    'analysis_invalid_input_text','analysis_schema_error','analysis_key_mismatch',
    'analysis_evidence_scope','analysis_forbidden_probability',
    'analysis_requirement_kind_mismatch','analysis_invalid_rule_reference',
    'analysis_unsupported_positive','analysis_resume_support_mismatch',
    'analysis_unknown_requires_scope','analysis_unknown_as_inability',
    'analysis_gap_requires_negative_evidence','analysis_unsupported_experience',
    'analysis_alternative_already_supported','analysis_optional_as_mandatory',
    'analysis_advice_requires_finding_evidence','analysis_invalid_response',
    'analysis_refused','analysis_incomplete','analysis_output_limit','analysis_invalid_json',
    'analysis_advice_requirement_mismatch','analysis_unknown_evidence_conflict',
    'analysis_text_contract_violation','analysis_text_sensitive_content',
    'analysis_validation_policy_mismatch',
})

_PROVIDER_CODES=frozenset({
    'provider_timeout','provider_body_limit','invalid_provider_json','invalid_analysis_request',
    'input_allocation_limit','attempt_limit','budget_limit','response_model_mismatch',
    'invalid_provider_status','invalid_provider_output','output_byte_limit','unknown_usage',
    'usage_limit','provider_error','client_closed','run_halted',
})

def _failure_audit(client, audit):
    if audit is not None:return audit
    try:
        current=getattr(client,'last_call_audit',None)
        return CallAudit.model_validate(current) if isinstance(current,CallAudit) else None
    except Exception:return None

def _halt(client):
    try:
        method=getattr(client,'halt',None)
        if callable(method):method('analysis_validation_failed')
    except Exception:pass

def analyze_job(packet, key, *, client: AnalysisClient, config=None, rejection_sink=None) -> JobAnalysis:
    key=JobKey.model_validate(key)
    audit=None;attempted=False;raw_draft_text=None
    try:
        request=build_analysis_request(packet,key,config=config)
        attempted=True
        response=client.complete(request)
        if not isinstance(response,AnalysisResponse):
            raise ReportError('analysis_invalid_response')
        response=AnalysisResponse.model_validate(response)
        audit=response.audit
        if response.status!='completed':
            raise ReportError('analysis_'+response.status)
        raw_draft_text=response.text
        draft=parse_analysis(raw_draft_text,max_output_bytes=request.max_output_bytes)
        draft=validate_analysis(draft,packet,key)
        return JobAnalysis(key=key,status='completed',draft=draft,audit=audit)
    except TimeoutError:
        code='analysis_client_timeout'
    except ReportError as exc:
        if exc.code in _ERROR_CODES:code=exc.code
        elif exc.code in _PROVIDER_CODES or re.fullmatch(r'provider_http_[1-5][0-9]{2}',exc.code):
            code='analysis_provider_'+exc.code.removeprefix('provider_')
        else:code='analysis_client_error'
    except (ValidationError,ValueError,TypeError,RecursionError):
        code='analysis_validation_error'
    except Exception:
        code='analysis_client_error'
    if raw_draft_text is not None and rejection_sink is not None:
        try:
            from .report_diagnostics import build_rejection
            rejection_sink(build_rejection(raw_draft_text,code,packet,key))
        except Exception:
            code='analysis_diagnostic_write_failed'
    if attempted:audit=_failure_audit(client,audit)
    _halt(client)
    return JobAnalysis(key=key,status='failed',error_code=code,audit=audit)
