"""Pure, synthetic rule checks. A hit is evidence, not a capability claim."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import sqlite3

from pydantic import ValidationError
import pytest

from job_radar.import_models import ImportedJob
from job_radar.matching_input import build_matching_input
from job_radar.matching_models import JobCandidate, JobProvenance, SearchPreferences
from job_radar.matching_rules import evaluate_rules
from job_radar.resume_profile import (
    EvidenceRef, ProfileSource, ResumeProfile, SkillEvidence, TextFact,
)
from job_radar.retrieval_models import RuleResult


def profile(names=('Python',), **prefs):
    skills = [SkillEvidence(name=name, context='project', mention=TextFact(value=name,
        evidence=[EvidenceRef(page=1, block_id=f'p001-b{i:03d}', quote=f'项目中使用{name}。')]))
        for i, name in enumerate(names, 1)]
    resume = ResumeProfile(resume_id='synthetic-rules', profile_version=2,
        source=ProfileSource(source_sha256='a'*64, parser_version='synthetic-v1',
            extractor_version='synthetic-v1'), skills=skills, warnings=['synthetic'])
    return build_matching_input(resume, preferences=SearchPreferences(**prefs))


def candidate(description='要求 Python 或 Go；Docker 加分。', **updates):
    raw = dict(source='manual', title='开发实习', url='https://example.org/jobs/1',
        description=description, city='', education='', experience='', attendance='', skills=[])
    raw.update(updates)
    job = ImportedJob.model_validate(raw)
    canonical = json.dumps(job.model_dump(),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    return JobCandidate(dataset_id='synthetic-rules', job_id=1, job=job,
        jd_sha256=hashlib.sha256(job.description.encode()).hexdigest(),
        fact_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
        provenance=[JobProvenance(provenance_id=1, source_file_sha256='b'*64,
            input_format='txt',parser_version='synthetic-v1',
            imported_at=datetime(2026,11,11,tzinfo=timezone.utc), import_run_id='synthetic-run')],
        review_status='unconfirmed')


def run(description='要求 Python 或 Go；Docker 加分。', names=('Python',), prefs=None, hard=None, **job):
    return evaluate_rules(profile(names, **(prefs or {})), candidate(description, **job),
                          hard_constraints=[] if hard is None else hard)


def test_or_group_and_bonus_are_not_two_required_skills():
    result = run()
    assert result.total_required == result.covered_required == 1
    assert result.coverage == 1.0
    required, bonus = result.skills
    assert required.alternatives == ['Python','Go']
    assert required.matched == ['Python'] and required.status == 'evidence_found'
    assert bonus.kind == 'bonus' and bonus.alternatives == ['Docker']
    assert bonus.status == 'not_found'


def test_requirement_and_bonus_quotes_keep_the_classifying_clause():
    result = run('要求 Python 和 Go；Docker 加分。')
    assert [s.job_evidence[0].quote for s in result.skills] == [
        '要求 Python 和 Go', '要求 Python 和 Go', 'Docker 加分']
    assert [s.kind for s in result.skills] == ['required', 'required', 'bonus']


@pytest.mark.parametrize('prefix', [
    'no more than', 'not more than', 'at most', 'under', 'below',
])
@pytest.mark.parametrize('unit,kind', [('3 days per week', 'attendance'), ('3 months', 'duration')])
def test_english_upper_bound_cannot_become_a_hard_minimum(prefix, unit, kind):
    result = run(attendance=f'{prefix} {unit}',
        prefs={'available_days_per_week': 1, 'available_duration_months': 1},
        hard=[kind])
    check = next(c for c in result.constraints if c.name == kind)
    assert check.status == 'unknown' and check.hard is True


@pytest.mark.parametrize('raw', [
    '每周3天优先，实习3个月加分', '3 days per week preferred, 3 months preferred',
    'more than 3 days per week, more than 3 months', 'over 3 days per week, over 3 months',
])
def test_optional_or_strict_inequality_quantities_remain_unknown(raw):
    result = run(attendance=raw,
        prefs={'available_days_per_week': 1, 'available_duration_months': 1},
        hard=['attendance', 'duration'])
    assert all(c.status == 'unknown' for c in result.constraints[1:])


@pytest.mark.parametrize('text', [
    '要求 Python 和 Docker。','必须 Python、Docker。','Required Python and Docker.',
    '要求 Python, Docker。','要求 Python & Docker。','要求 Python 与 Docker。',
])
def test_and_groups_count_separately(text):
    result = run(text)
    assert result.total_required == 2 and result.covered_required == 1
    assert result.coverage == 0.5


@pytest.mark.parametrize('text', [
    'Python or Go required.', '必须 Python 或者 Go。', 'Python 或 Go 必备。',
])
def test_or_chains_preserve_one_denominator(text):
    result = run(text, names=('Go',))
    assert result.total_required == result.covered_required == 1
    assert result.skills[0].alternatives == ['Python','Go']


@pytest.mark.parametrize('text', [
    'Python 优先。', 'Docker 加分。', 'Python preferred.', 'Python is a plus.',
])
def test_bonus_never_changes_required_coverage(text):
    result = run(text)
    assert result.coverage is None and result.total_required == 0
    assert all(s.kind == 'bonus' for s in result.skills)


@pytest.mark.parametrize('text', [
    '不要求 Python。','无需 Docker。','不需要 Python。','Python not required.',
    'No need Python.', 'Python 非必须。',
])
def test_negative_requirements_ignored_not_promoted(text):
    result = run(text)
    assert result.skills == [] and result.coverage is None
    assert 'negated_skill_clause_ignored' in result.warnings


@pytest.mark.parametrize('text', [
    '要求 Python 或 Go 和 Docker。', '要求 Python，加分 Docker。',
    '如果掌握 Python 则必须 Docker。','必须 Python 项目以及 Docker 部署。',
    '要求至少两种 Python 或 Go。',
])
def test_complex_clauses_not_given_required_semantics(text):
    result = run(text)
    assert result.coverage is None and result.total_required == 0
    assert all(s.kind == 'mention' for s in result.skills)
    assert 'ambiguous_skill_requirement' in result.warnings


@pytest.mark.parametrize('text', ['开发Python接口。','熟悉 Python。','Python工程师'])
def test_unlabelled_mentions_not_requirements(text):
    result = run(text)
    assert result.total_required == 0 and result.coverage is None
    assert all(s.kind == 'mention' for s in result.skills)


@pytest.mark.parametrize('text', ['Pythonista','Pythonic','Golang','GoLang','MySQLish','ReactNative'])
def test_skills_use_ascii_token_boundaries(text):
    result = run(text)
    assert result.skills == []


@pytest.mark.parametrize('raw,canonical', [
    ('Python3','Python'),('Python 3','Python'),('py.test','pytest'),
    ('Tool-Calling','Tool Calling'),('C++','C++'),('C#','C#'),
])
def test_explicit_aliases_and_technical_tokens(raw, canonical):
    result = run(f'要求 {raw}。',names=(raw,))
    assert result.skills[0].alternatives == [canonical]
    assert result.skills[0].status == 'evidence_found'
    assert result.coverage == 1.0


def test_repeated_requirement_does_not_weight_denominator_twice():
    result = run('要求 Python 或 Go。必须 Go 或 Python。要求 Docker。')
    assert result.total_required == 2 and result.covered_required == 1
    assert len(result.skills[0].job_evidence) == 2


def test_no_required_evidence_is_not_inability():
    result = run('要求 Docker。', names=())
    assert result.coverage == 0.0
    assert result.skills[0].status == 'not_found'
    dumped = result.model_dump_json()
    assert '不会' not in dumped and 'incapable' not in dumped and 'proficiency' not in dumped


def test_metadata_only_skills_not_given_fake_quote():
    result = run('负责开发接口。', skills=['Python'])
    assert result.skills == [] and result.coverage is None
    assert 'job_skill_metadata_without_text_evidence' in result.warnings


def test_all_evidence_spans_reconstruct_from_supplied_jd_fields():
    p = profile(('Python','Docker'), cities=['深圳'], available_days_per_week=3,
                available_duration_months=6)
    job = candidate('要求 Python 或 Go；Docker 加分。', city='深圳',attendance='每周至少3天，实习至少6个月')
    result = evaluate_rules(p,job,hard_constraints=['city','attendance','duration'])
    for check in [*result.constraints,*result.skills]:
        for span in check.job_evidence:
            field = getattr(job.job,span.field)
            assert field[span.start:span.end] == span.quote
        for evidence in check.resume_evidence:
            assert evidence.page == 1 and evidence.quote.startswith('项目中使用')
    assert result.skills[0].resume_paths == ['skills[0]']
    assert result.skills[1].resume_paths == ['skills[1]']


@pytest.mark.parametrize('city,choices,status', [
    ('深圳',['深圳'],'satisfied'),('深圳市',['Shenzhen'],'satisfied'),
    ('Shanghai',['上海','深圳'],'satisfied'),('北京',['深圳'],'not_satisfied'),
    ('上海',['Shenzhen'],'not_satisfied'),('深圳',['不明城市'],'unknown'),
    ('深圳/上海',['深圳'],'unknown'),('', ['深圳'],'unknown'),
    ('深圳',None,'unknown'),('remote',['远程'],'satisfied'),
    ('深圳',['上海','深圳'],'satisfied'),
])
def test_city_explicit_alternatives_and_unknown(city, choices, status):
    result = run(city=city,prefs={'cities':choices},hard=['city'])
    check = result.constraints[0]
    assert check.status == status and check.hard is True


@pytest.mark.parametrize('attendance,available,status', [
    ('每周至少3天',3,'satisfied'),('每周3天',5,'satisfied'),
    ('3天/周',2,'not_satisfied'),('at least 4 days per week',3,'not_satisfied'),
    ('3 days/week',3,'satisfied'),('每周3-5天',3,'unknown'),
    ('每周三天',3,'unknown'),('每周至少3天',None,'unknown'),
    ('不要求每周3天',3,'unknown'),('最多每周3天',3,'unknown'),
    ('每周3天以下',3,'unknown'),('每周3天或4天',3,'unknown'),
    ('每周3天，每周4天',4,'unknown'),('',3,'unknown'),
])
def test_attendance_three_state(attendance, available, status):
    result = run(attendance=attendance,prefs={'available_days_per_week':available})
    check = result.constraints[1]
    assert check.status == status and check.hard is False


@pytest.mark.parametrize('attendance,available,status', [
    ('实习至少6个月',6,'satisfied'),('实习3个月',2,'not_satisfied'),
    ('至少3个月',6,'satisfied'),('at least 6 months',3,'not_satisfied'),
    ('6 months',6,'satisfied'),('实习3-6个月',6,'unknown'),
    ('实习六个月',6,'unknown'),('至少6个月',None,'unknown'),
    ('不要求实习6个月',6,'unknown'),('最多6个月',6,'unknown'),
    ('实习3个月或6个月',6,'unknown'),('1年',6,'unknown'),
    ('',6,'unknown'),
])
def test_duration_three_state(attendance, available, status):
    result = run(attendance=attendance,prefs={'available_duration_months':available})
    assert result.constraints[2].status == status


def test_hard_is_only_explicit_field_and_unknown_stays_unknown():
    result = run(city='北京',attendance='每周至少4天，实习至少6个月',
        prefs={'cities':['深圳'],'available_days_per_week':3},hard=['city','duration'])
    assert [(x.name,x.hard,x.status) for x in result.constraints] == [
        ('city',True,'not_satisfied'),('attendance',False,'not_satisfied'),
        ('duration',True,'unknown')]


def test_education_and_years_not_inferred_from_school_or_projects():
    result = run(education='本科毕业',experience='3年工作经验')
    assert 'education_requirement_not_evaluated' in result.warnings
    assert 'experience_requirement_not_evaluated' in result.warnings
    assert all(x.name not in {'education','years'} for x in result.constraints)


def test_nested_project_technology_preserves_resume_paths():
    p = profile(())
    skill = profile().skills[0].model_dump()
    data = p.model_dump(); data['projects'] = [{'technologies':[skill]}]
    p = type(p).model_validate(data)
    result = evaluate_rules(p,candidate('要求 Python。'),hard_constraints=[])
    assert result.skills[0].resume_paths == ['projects[0].technologies[0]']
    assert result.skills[0].resume_evidence


def test_multiple_resume_locations_retained_for_one_skill():
    p = profile(('Python','Python'))
    result = evaluate_rules(p,candidate('要求 Python。'),hard_constraints=[])
    assert result.skills[0].resume_paths == ['skills[0]','skills[1]']
    assert len(result.skills[0].resume_evidence)==2


@pytest.mark.parametrize('hard', [None,(),['unknown'],['city','city'],[True]])
def test_invalid_hard_constraints_rejected(hard):
    with pytest.raises(ValueError,match='invalid_hard_constraints'):
        evaluate_rules(profile(),candidate(),hard_constraints=hard)


def test_tampered_nested_models_are_revalidated():
    p=profile();p.skills[0].mention.evidence.clear()
    with pytest.raises(ValidationError):evaluate_rules(p,candidate(),hard_constraints=[])
    job=candidate();job.job.description='changed'
    with pytest.raises(ValidationError):evaluate_rules(profile(),job,hard_constraints=[])


def test_inputs_not_modified_and_output_detached():
    p=profile();job=candidate();before=(p.model_dump_json(),job.model_dump_json())
    result=evaluate_rules(p,job,hard_constraints=['city'])
    assert before==(p.model_dump_json(),job.model_dump_json())
    result.skills[0].resume_evidence[0].quote='changed'
    assert p.skills[0].mention.evidence[0].quote!='changed'
    assert RuleResult.model_validate_json(evaluate_rules(p,job,hard_constraints=[]).model_dump_json())
    assert p.review_status==job.review_status=='unconfirmed'


def test_no_io_and_deterministic(monkeypatch):
    p=profile();job=candidate()
    def blocked(*a,**kw):raise AssertionError('Unexpected I/O')
    monkeypatch.setattr(socket,'socket',blocked);monkeypatch.setattr(sqlite3,'connect',blocked)
    monkeypatch.setattr(Path,'read_text',blocked);monkeypatch.setattr(Path,'read_bytes',blocked)
    monkeypatch.setattr('builtins.open',blocked)
    assert evaluate_rules(p,job,hard_constraints=[]).model_dump_json()==evaluate_rules(p,job,hard_constraints=[]).model_dump_json()


@pytest.mark.parametrize('text', [
    '要求 Python 和 Erlang。','要求 Python 和 Docker 任选一种。',
    '要求 Python 和 Docker 两项之一。','要求 Python 并具备沟通能力。',
    '必须掌握 Python 或 Go 其中一种。',
])
def test_partial_or_unparsed_grammar_not_full_coverage(text):
    result=run(text)
    assert result.total_required==0 and result.coverage is None
    assert 'ambiguous_skill_requirement' in result.warnings


@pytest.mark.parametrize('text',[
    '如果有课每周3天','每周3天可选','每周3天视情况而定','optional 3 days per week',
])
def test_conditional_availability_not_false_satisfaction(text):
    result=run(attendance=text,prefs={'available_days_per_week':3})
    assert result.constraints[1].status=='unknown'


def test_wholly_unparsed_requirements_are_visible():
    result=run('要求 Erlang。')
    assert result.skills==[] and result.coverage is None
    assert 'unparsed_requirement_clause' in result.warnings
    assert 'no_recognised_skill_text' in result.warnings


@pytest.mark.parametrize('text',['13 days/week','23天/周','每周13天'])
def test_attendance_does_not_match_suffix_of_invalid_number(text):
    result=run(attendance=text,prefs={'available_days_per_week':7})
    assert result.constraints[1].status=='unknown'
