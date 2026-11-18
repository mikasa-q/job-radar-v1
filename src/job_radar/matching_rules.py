"""Conservative Stage 2 lexical evidence checks, not capability prediction.

The caller must already have checked resume references against its document.
This module cannot certify semantic truth; it recognizes a finite grammar and
leaves ambiguous requirements unknown rather than silently strengthening them.
"""
from __future__ import annotations

import re

from .matching_models import JobCandidate, MatchingInput
from .retrieval_models import ConstraintCheck, JobSpan, RuleResult, SkillCheck
from .retrieval_text import tokenize

# A deliberately small, versioned vocabulary; not an inferred skills ontology.
_SKILL_NAMES = (
    'Python', 'FastAPI', 'MySQL', 'Redis', 'Git', 'Linux', 'Docker', 'pytest',
    'LLM', 'Tool Calling', 'RAG', 'LangGraph', 'MCP', 'SQL', 'Go', 'Rust',
    'Java', 'JavaScript', 'TypeScript', 'C++', 'C#', 'Kubernetes', 'React',
)
_SKILL_ALIASES = {name: [name] for name in _SKILL_NAMES}
_SKILL_ALIASES['Python'] += ['Python3', 'Python 3']
_SKILL_ALIASES['pytest'] += ['py.test']
_SKILL_ALIASES['Tool Calling'] += ['Tool-Calling']
_SKILLS = {
    name: re.compile(r'(?<![A-Za-z0-9_])(?:' + '|'.join(
        re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)
    ) + r')(?![A-Za-z0-9_+#])', re.I)
    for name, aliases in _SKILL_ALIASES.items()
}
_REQUIRED = re.compile(r'必须|要求|需掌握|需熟悉|须掌握|必备|\b(?:must|required|requirement)\b', re.I)
_BONUS = re.compile(r'加分|优先|\b(?:bonus|preferred|preferable|plus)\b', re.I)
_NEGATIVE = re.compile(r'不要求|无需|不需要|不必|无须|非必需|非必须|\b(?:not required|not necessary|no need)\b', re.I)
_COMPLEX = re.compile(r'除非|否则|如果|若|任选|任意|其中|之一|至少.{0,15}(?:两|二|2)种|\b(?:unless|if|except|at least two|either)\b', re.I)
_OR = re.compile(r'\s*(?:或(?:者)?|\bor\b)\s*', re.I)
_AND = re.compile(r'\s*(?:和|及|与|、|,|，|\band\b|&)\s*', re.I)
_CLAUSE = re.compile(r'[^；;。\n!?！？]+')
_CITIES = {
    '深圳': '深圳', 'shenzhen': '深圳', '上海': '上海', 'shanghai': '上海',
    '北京': '北京', 'beijing': '北京', '广州': '广州', 'guangzhou': '广州',
    '杭州': '杭州', 'hangzhou': '杭州', '成都': '成都', 'chengdu': '成都',
    '南京': '南京', 'nanjing': '南京', '武汉': '武汉', 'wuhan': '武汉',
    '苏州': '苏州', 'suzhou': '苏州', '西安': '西安', "xi'an": '西安',
    '天津': '天津', 'tianjin': '天津', '重庆': '重庆', 'chongqing': '重庆',
    '远程': 'remote', 'remote': 'remote',
}


def _span(field: str, text: str, start: int = 0, end: int | None = None) -> JobSpan:
    end = len(text) if end is None else end
    return JobSpan(field=field, start=start, end=end, quote=text[start:end])


def _canonical(name: str) -> str:
    tokens = tokenize(name)
    return tokens[0] if len(tokens) == 1 else name.casefold()


def _profile_skills(profile: MatchingInput):
    """Keep every supporting location, including nested experience technology."""
    out: dict[str, list[tuple[str, object]]] = {}
    for i, skill in enumerate(profile.skills):
        out.setdefault(_canonical(skill.name), []).append((f'skills[{i}]', skill))
    for section in ['projects', 'work_experience']:
        for i, entry in enumerate(getattr(profile, section)):
            for j, skill in enumerate(entry.technologies):
                out.setdefault(_canonical(skill.name), []).append(
                    (f'{section}[{i}].technologies[{j}]', skill))
    return out


def _matches(text: str):
    found = []
    for name, pattern in _SKILLS.items():
        found.extend((m.start(), m.end(), name) for m in pattern.finditer(text))
    return sorted(found)


def _groups(matches, text: str):
    """Recognize plain A-or-B chains and plain conjunctions, not mixed logic."""
    if len(matches) <= 1:
        return [matches], False
    connectors = [text[a[1]:b[0]] for a, b in zip(matches, matches[1:])]
    is_or = [_OR.fullmatch(c) is not None for c in connectors]
    is_and = [_AND.fullmatch(c) is not None for c in connectors]
    if all(is_or):
        return [matches], False
    if all(is_and):
        return [[m] for m in matches], False
    # Mixed AND/OR or prose between recognized skills has no reliable grouping.
    return [[m] for m in matches], True


def _fully_parsed_clause(text, found):
    # Unknown skills or unrecognized prose cannot disappear from a denominator.
    # Only the finite label/connector grammar below may surround skill names.
    for start, end, _ in reversed(found):
        text = text[:start] + ' ' + text[end:]
    residue = re.sub(
        r'岗位要求|任职要求|要求|必须|必备|需掌握|需熟悉|须掌握|掌握|熟悉|了解|使用|'
        r'加分|优先|(?:\b(?:required|requirement|must|know|bonus|preferred|preferable|is|a|plus)\b)|'
        r'或(?:者)?|和|及|与|(?:\b(?:or|and)\b)|[\s：:,，、&()（）.]+', '', text, flags=re.I)
    return not residue


def _make_skill(alternatives, kind, support, evidence):
    names = list(dict.fromkeys(alternatives))
    matched = []; paths = []; refs = []
    for name in names:
        locations = support.get(_canonical(name), [])
        if locations:
            matched.append(name)
        for path, skill in locations:
            paths.append(path)
            refs.extend(skill.mention.evidence)
    return SkillCheck(alternatives=names, kind=kind,
        status='evidence_found' if matched else 'not_found', matched=matched,
        resume_paths=list(dict.fromkeys(paths)), resume_evidence=refs,
        job_evidence=evidence)


def _skills(profile: MatchingInput, candidate: JobCandidate):
    support = _profile_skills(profile)
    output = []; warnings = []
    observed: set[str] = set()
    recognised_clause = False
    for field in ['title', 'description']:
        text = getattr(candidate.job, field)
        for clause in _CLAUSE.finditer(text):
            raw = clause.group()
            found = _matches(raw)
            if not found:
                if _REQUIRED.search(raw) or _BONUS.search(raw):
                    warnings.append('unparsed_requirement_clause')
                continue
            recognised_clause = True
            observed.update(_canonical(x[2]) for x in found)
            # Negated requirements are not requirements or positive evidence.
            if _NEGATIVE.search(raw):
                warnings.append('negated_skill_clause_ignored')
                continue
            required = bool(_REQUIRED.search(raw)); bonus = bool(_BONUS.search(raw))
            groups, ambiguous = _groups(found, raw)
            if (_COMPLEX.search(raw) or required and bonus or ambiguous
                    or (required or bonus) and not _fully_parsed_clause(raw, found)):
                kind = 'mention'; warnings.append('ambiguous_skill_requirement')
                groups = [[m] for m in found]
            else:
                kind = 'required' if required else 'bonus' if bonus else 'mention'
            if kind == 'mention':
                warnings.append('skill_mentions_not_requirements')
            for group in groups:
                # The qualifier (required/bonus/ambiguous prose) is evidence too.
                # A bare skill-name span cannot justify the classification.
                start = clause.start(); end = clause.end()
                output.append(_make_skill([m[2] for m in group], kind, support,
                    [_span(field, text, start, end)]))
    if not recognised_clause:
        warnings.append('no_recognised_skill_text')
    # Repeated wording adds provenance, not repeated scoring weight.
    deduplicated = {}
    for item in output:
        key = (item.kind, tuple(sorted(_canonical(n) for n in item.alternatives)))
        if key in deduplicated:
            deduplicated[key].job_evidence.extend(item.job_evidence)
        else:
            deduplicated[key] = item
    output = list(deduplicated.values())
    # Metadata alone cannot be promoted to must-have or supplied a fabricated quote.
    if any(_canonical(name) not in observed for name in candidate.job.skills):
        warnings.append('job_skill_metadata_without_text_evidence')
    return output, list(dict.fromkeys(warnings))


def _city(value: str):
    return _CITIES.get(value.strip().casefold().removesuffix('市'))


def _city_check(profile, candidate, hard):
    prefs = profile.preferences.cities
    value = candidate.job.city
    status = 'unknown'; reason = 'city_unspecified_or_unparsed'
    if prefs and value:
        city = _city(value); choices = [_city(c) for c in prefs]
        if city is not None and city in choices:
            status = 'satisfied'; reason = 'explicit_city_alternative_matched'
        elif city is not None and all(c is not None for c in choices):
            status = 'not_satisfied'; reason = 'explicit_city_conflict'
    return ConstraintCheck(name='city', status=status, hard='city' in hard, reason=reason,
        resume_paths=['preferences.cities'] if prefs else [],
        job_evidence=[_span('city', value)] if value else [])


def _quantity_requirement(text: str, kind: str):
    """Parse a small positive-minimum grammar, excluding ranges and alternatives."""
    if not text:
        return None
    if _NEGATIVE.search(text) or _BONUS.search(text) or re.search(r'如果|若|除非|视情况|弹性|可选|\b(?:if|unless|flexible|optional)\b|\d\s*[-—~～至到/]\s*\d|或|任选|\b(?:or|up to|maximum|less than|more than|at most|under|below|over)\b|最多|不超过|少于|以下|至多|不满', text, re.I):
        return None
    if kind == 'attendance':
        patterns = [r'每周\s*(?:至少|不少于|最少)?\s*([1-7])\s*天',
            r'(?<![0-9])([1-7])\s*天\s*/\s*周',
            r'(?<![0-9])(?:at least\s*)?([1-7])\s*days?\s*(?:per\s*week|/\s*week)']
    else:
        patterns = [r'(?:实习(?:时长)?|持续|至少|不少于|最少)\s*(?:至少|不少于|最少)?\s*(\d{1,2})\s*个?月',
            r'(?<![0-9])(?:at least\s*)?(\d{1,2})\s*months?']
    values = [int(m.group(1)) for pattern in patterns for m in re.finditer(pattern, text, re.I)]
    unique = set(values)
    if len(unique) != 1 or min(unique) < 1:
        return None
    return next(iter(unique))


def _availability_check(profile, candidate, hard, kind):
    field = 'available_days_per_week' if kind == 'attendance' else 'available_duration_months'
    value = getattr(profile.preferences, field)
    raw = candidate.job.attendance
    minimum = _quantity_requirement(raw, kind)
    status = 'unknown'; reason = f'{kind}_unspecified_or_unparsed'
    if value is not None and minimum is not None:
        status = 'satisfied' if value >= minimum else 'not_satisfied'
        reason = 'explicit_minimum_met' if status == 'satisfied' else 'explicit_minimum_conflict'
    return ConstraintCheck(name=kind, status=status, hard=kind in hard, reason=reason,
        resume_paths=[f'preferences.{field}'] if value is not None else [],
        job_evidence=[_span('attendance', raw)] if raw else [])


def evaluate_rules(
    profile: MatchingInput, candidate: JobCandidate, *, hard_constraints: list[str],
) -> RuleResult:
    """Return lexical evidence coverage and three-state explicit constraints.

    Coverage is of parsed required groups, not proficiency or job suitability.
    No document, model, filesystem or database is read. Profile evidence must
    have been checked upstream against its original ResumeDocument.
    """
    if not isinstance(profile, MatchingInput) or not isinstance(candidate, JobCandidate):
        raise TypeError('expected_matching_profile_and_job_candidate')
    if (not isinstance(hard_constraints, list)
            or any(not isinstance(k, str) or k not in {'city', 'attendance', 'duration'} for k in hard_constraints)
            or len(set(hard_constraints)) != len(hard_constraints)):
        raise ValueError('invalid_hard_constraints')
    profile = MatchingInput.model_validate(profile)
    candidate = JobCandidate.model_validate(candidate)
    skills, warnings = _skills(profile, candidate)
    constraints = [_city_check(profile, candidate, hard_constraints),
        _availability_check(profile, candidate, hard_constraints, 'attendance'),
        _availability_check(profile, candidate, hard_constraints, 'duration')]
    if candidate.job.education:
        warnings.append('education_requirement_not_evaluated')
    if candidate.job.experience:
        warnings.append('experience_requirement_not_evaluated')
    required = [s for s in skills if s.kind == 'required']
    covered = sum(s.status == 'evidence_found' for s in required)
    return RuleResult(constraints=constraints, skills=skills,
        covered_required=covered, total_required=len(required),
        coverage=covered/len(required) if required else None,
        warnings=list(dict.fromkeys(warnings)))
