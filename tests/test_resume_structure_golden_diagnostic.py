"""Synthetic exact-golden diagnostics. No equivalence relaxation or live calls."""
import copy
import importlib.util
import json
import socket
from pathlib import Path

import pytest

from job_radar.resume_structure import ClientResponse, PROMPT_VERSION, SYSTEM_PROMPT, structure_resume
from job_radar.resume_structure_fake import FakeStructureClient

ROOT=Path(__file__).parents[1]
CASES=json.loads((ROOT/'tests/fixtures/resume_structure_golden.json').read_text())['cases']


def load_demo():
    spec=importlib.util.spec_from_file_location('golden_diagnostic_demo',ROOT/'examples/resume_structure_apinebula_synthetic.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('socket used'))
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:pytest.fail('socket used'))


def fake(drafts):
    return FakeStructureClient([ClientResponse(status='completed',text=json.dumps(d,ensure_ascii=False)) for d in drafts])


def test_prompt_v3_clarifies_original_golden_not_embed_answers():
    assert PROMPT_VERSION=='resume-structure-prompt-v3'
    for phrase in ['education.degree 仅摘录学历层次','education.status 仅摘录就读状态',
                   '顶层 skills 汇总所有正向技能','不互相替代','首次出现顺序',
                   '去掉标签和用作分隔符的句末标点','不改写正文','没有正向自述时为 null']:
        assert phrase in SYSTEM_PROMPT
    for word in ['合成大学','合成查询系统','喜欢编写可复核的测试','full_education_course_internship']:
        assert word not in SYSTEM_PROMPT


@pytest.mark.parametrize('case',CASES,ids=[c['name'] for c in CASES])
def test_all_original_goldens_exactly_pass_unchanged(case):
    demo=load_demo();client=fake([case['response']])
    result=structure_resume(demo.synthetic_document(case['pages']),client=client,resume_id='synthetic',timeout_seconds=90)
    assert demo.shape(result)==case['expected']
    assert demo.golden_diagnostic(demo.shape(result),case['expected'])=={'issues':[]}
    assert len(client.requests)==1 and client.requests[0].system_prompt==SYSTEM_PROMPT
    assert result.profile.review_status=='unconfirmed'


@pytest.mark.parametrize('variation,path,rule',[
    ('summary_punctuation','golden.summary','text_span_mismatch'),
    ('skill_order','golden.skills','list_order_mismatch'),
    ('status_span','golden.education_status[0]','value_mismatch'),
    ('missing_top_skills','golden.skills','list_length_mismatch'),
    ('missing_summary','golden.summary','presence_mismatch'),
    ('project_nature_missing','golden.project_nature[0]','presence_mismatch'),
    ('work_type_missing','golden.employment_type[0]','presence_mismatch'),
    ('missing_education','golden.counts.education','count_mismatch'),
])
def test_locally_valid_variants_still_fail_exact_golden_stop_one(variation,path,rule,capsys):
    demo=load_demo();draft=copy.deepcopy(CASES[0]['response'])
    if variation=='summary_punctuation':draft['summary']['value']+='。'
    elif variation=='skill_order':draft['skills'].reverse()
    elif variation=='status_span':draft['education'][0]['status']['value']='本科在读'
    elif variation=='missing_top_skills':draft['skills']=[]
    elif variation=='missing_summary':draft['summary']=None
    elif variation=='project_nature_missing':draft['projects'][0]['nature']=None
    elif variation=='work_type_missing':draft['work_experience'][0]['employment_type']=None
    elif variation=='missing_education':draft['education']=[]
    before=copy.deepcopy(draft);client=fake([draft,CASES[1]['response'],CASES[2]['response']])
    assert demo.exercise(CASES,client)==1
    assert len(client.requests)==1 and draft==before
    text=capsys.readouterr().out
    assert 'result=golden_mismatch; stopped' in text
    diag=json.loads(next(l.split('=',1)[1] for l in text.splitlines() if l.startswith('golden_diagnostic=')))
    assert {'path':path,'rule':rule} in diag['issues']
    assert CASES[1]['name'] not in text and CASES[2]['name'] not in text
    for secret in ['本科在读','喜欢编写可复核的测试','合成大学','quote','actual','expected']:
        assert secret not in text


@pytest.mark.parametrize('field', ['skills','schools','education_status','project_nature','employment_type','summary','counts'])
def test_diagnostic_only_fixed_paths_never_values(field):
    demo=load_demo();want=copy.deepcopy(CASES[0]['expected']);got=copy.deepcopy(want)
    got[field]='SYNTHETIC_SECRET' if field=='summary' else ['SYNTHETIC_SECRET']
    text=json.dumps(demo.golden_diagnostic(got,want))
    assert 'SYNTHETIC_SECRET' not in text and 'golden.'+field in text


def test_unknown_extra_keys_not_echoed():
    demo=load_demo();want=copy.deepcopy(CASES[0]['expected']);got={**want,'SECRET_KEY':'SECRET_VALUE'}
    assert demo.golden_diagnostic(got,want)=={'issues':[{'path':'golden','rule':'shape_keys_mismatch'}]}


def test_diagnostic_is_bounded_and_inputs_not_mutated():
    demo=load_demo();want={k:['expected']*100 for k in demo.GOLDEN_FIELDS};got={k:['SECRET']*100 for k in demo.GOLDEN_FIELDS}
    want['summary']='expected';got['summary']='SECRET';want['counts']=[1,2,3];got['counts']=[9,9,9]
    before=copy.deepcopy((got,want));diag=demo.golden_diagnostic(got,want)
    assert len(diag['issues'])==8 and (got,want)==before
    for row in diag['issues']:
        assert set(row)=={'path','rule'} and len(row['path'])<=64
    assert 'SECRET' not in json.dumps(diag)


@pytest.mark.parametrize('field', ['skills','counts','summary'])
def test_diagnostic_malformed_types_are_stable_codes(field):
    demo=load_demo();want=copy.deepcopy(CASES[0]['expected']);got=copy.deepcopy(want);got[field]={'SECRET':'SECRET'}
    assert demo.golden_diagnostic(got,want)=={'issues':[{'path':'golden.'+field,'rule':'type_mismatch'}]}


def test_schema_and_evidence_failures_still_stop_before_golden(capsys):
    demo=load_demo();draft=copy.deepcopy(CASES[0]['response'])
    draft['education'][0]['end_date']['normalized']='2029'
    client=fake([draft,CASES[1]['response']])
    assert demo.exercise(CASES,client)==1 and len(client.requests)==1
    text=capsys.readouterr().out
    assert 'schema_diagnostic=' in text and 'date_normalization_mismatch' in text
    assert 'golden_diagnostic=' not in text


def test_all_three_success_still_unlock_in_order(capsys):
    demo=load_demo();client=fake([case['response'] for case in CASES])
    assert demo.exercise(CASES,client)==0 and len(client.requests)==3
    text=capsys.readouterr().out
    assert text.count('result=validated')==3 and 'diagnostic=' not in text
    for req in client.requests:assert req.timeout_seconds==90 and req.prompt_version==PROMPT_VERSION
