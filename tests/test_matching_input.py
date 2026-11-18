"""Stage 1D: all evidence is synthetic; no real DB, resume or model required."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys

from pydantic import ValidationError
import pytest

from job_radar.import_models import ImportedJob
from job_radar.matching_input import build_matching_input, build_matching_request
from job_radar.matching_models import (
    JobCandidate, JobProvenance, MatchingInput, MatchingRequest, SearchPreferences,
)
from job_radar.resume_models import ResumeBlock, ResumeDocument, ResumePage, ResumeProvenance
from job_radar.resume_profile import ResumeProfile
from job_radar.resume_profile_rules import (
    ProfileEvidenceError, build_rule_profile, validate_profile_evidence,
)
from job_radar.resume_structure import ClientResponse, structure_resume
from job_radar.resume_structure_fake import FakeStructureClient

GOLDEN = json.loads((Path(__file__).parent/'fixtures/resume_structure_golden.json').read_text())['cases']


def document(*pages):
    pages = pages or (["计划学习 Docker", "课程项目：使用 Python 和 FastAPI。"],)
    return ResumeDocument(provenance=ResumeProvenance(
        source_sha256=hashlib.sha256(json.dumps(pages).encode()).hexdigest(),
        parser_version='synthetic-v1', extractor_version='synthetic-text-v1'),
        pages=[ResumePage(page_number=i, width=600.0, height=800.0, blocks=[
            ResumeBlock(block_id=f'p{i:03d}-b{j:03d}', text=text,
                        bbox=(20.0,float(j*20),580.0,float(j*20+15)))
            for j,text in enumerate(lines,1)]) for i,lines in enumerate(pages,1)])


def rules():
    return build_rule_profile(document(), resume_id='synthetic-1b')


def projected():
    return build_matching_input(rules(), preferences=SearchPreferences())


def provenance(**updates):
    return JobProvenance.model_validate(dict(
        provenance_id=1, source_file_sha256='1'*64, input_format='txt',
        parser_version='synthetic-v1', imported_at=datetime(2026,11,11,tzinfo=timezone.utc),
        import_run_id='synthetic-run', **updates))


def job_payload():
    job = ImportedJob(source='manual', title='合成开发实习', url='https://example.org/jobs/1',
                      description='要求 Python 或 Go；Docker 加分。', skills=['Python','Docker'])
    canonical=json.dumps(job.model_dump(),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    return dict(dataset_id='synthetic-evidence-v1',job_id=1,job=job.model_dump(),
                jd_sha256=hashlib.sha256(job.description.encode()).hexdigest(),
                fact_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
                provenance=[provenance().model_dump()],review_status='unconfirmed')


def candidate(**updates):
    return JobCandidate.model_validate({**job_payload(),**updates})


def test_1b_projection_preserves_every_field_and_unknown():
    profile=rules(); prefs=SearchPreferences(); before=profile.model_dump_json()
    output=build_matching_input(profile,preferences=prefs)
    original=profile.model_dump(); actual=output.model_dump()
    assert actual.pop('schema_version')=='matching-input-v1'
    assert actual.pop('profile_schema_version')==original.pop('schema_version')
    assert actual.pop('profile_id')==original.pop('resume_id')
    assert actual.pop('preferences')==prefs.model_dump()
    assert actual==original
    assert output.education==[] and output.summary is None
    assert output.work_experience==[] and output.projects==[]
    assert output.preferences.cities is None
    assert [s.name for s in output.skills]==['Python','FastAPI']
    assert 'not_extracted:education' in output.warnings
    assert profile.model_dump_json()==before


@pytest.mark.parametrize('case',GOLDEN,ids=[c['name'] for c in GOLDEN])
def test_1c_same_projection_all_golden_fields_and_comparison_separate(case):
    doc=document(*case['pages'])
    fake=FakeStructureClient([ClientResponse(status='completed',text=json.dumps(case['response']))])
    result=structure_resume(doc,client=fake,resume_id='synthetic-1c',profile_version=4)
    before=result.model_dump_json()
    output=build_matching_input(result.profile,preferences=SearchPreferences())
    assert output.profile_version==4 and output.profile_id=='synthetic-1c'
    for field in ['source','builder_version','education','skills','projects','work_experience','summary','warnings','review_status']:
        assert getattr(output,field)==getattr(result.profile,field)
    assert output.review_status=='unconfirmed'
    assert 'comparison' not in output.model_dump()
    assert result.model_dump_json()==before and len(fake.requests)==1


def test_rule_only_skill_not_unioned_into_1c():
    doc=document(['课程项目：Python 和 FastAPI。'])
    data={'education':[],'skills':[],'projects':[],'work_experience':[],'summary':None,'warnings':[]}
    result=structure_resume(doc,client=FakeStructureClient([ClientResponse(status='completed',text=json.dumps(data))]),resume_id='empty')
    assert result.comparison.rule_only_skills==['FastAPI','Python']
    assert build_matching_input(result.profile,preferences=SearchPreferences()).skills==[]


@pytest.mark.parametrize('state',['unconfirmed','confirmed','rejected'])
def test_review_status_preserved_not_promoted(state):
    profile=ResumeProfile.model_validate({**rules().model_dump(),'review_status':state})
    assert build_matching_input(profile,preferences=SearchPreferences()).review_status==state


@pytest.mark.parametrize('value',[{}, {'cities':['上海','深圳'],'target_roles':['后端','AI工程'],
    'available_days_per_week':3,'available_duration_months':6}])
def test_explicit_preferences_and_roundtrip(value):
    prefs=SearchPreferences.model_validate(value)
    out=build_matching_input(rules(),preferences=prefs)
    assert out.preferences==prefs
    assert MatchingInput.model_validate_json(out.model_dump_json())==out


@pytest.mark.parametrize('updates',[
    {'cities':[]},{'cities':['']},{'cities':['  ']},{'cities':'上海'},
    {'target_roles':[]},{'target_roles':[' ']},{'target_roles':[3]},
    {'available_days_per_week':0},{'available_days_per_week':8},
    {'available_days_per_week':True},{'available_days_per_week':'3'},
    {'available_duration_months':0},{'available_duration_months':-1},
    {'available_duration_months':1.5},{'available_duration_months':False},
    {'available_duration_months':'6'},{'expected_city':'北京'},
])
def test_invalid_preferences_rejected(updates):
    with pytest.raises(ValidationError): SearchPreferences.model_validate(updates)


@pytest.mark.parametrize('updates',[
    {'schema_version':'v2'},{'profile_schema_version':'v2'},{'profile_id':''},
    {'profile_id':' '},{'profile_version':0},{'profile_version':True},
    {'profile_version':'1'},{'builder_version':''},{'review_status':'automatic'},
    {'warnings':[' ']},{'skills':[{}]},{'source':{}},{'score':100},
])
def test_invalid_matching_projection_rejected(updates):
    with pytest.raises(ValidationError): MatchingInput.model_validate({**projected().model_dump(),**updates})


def test_nested_bypass_caught_at_projection_and_request_boundaries():
    profile=rules(); profile.skills[0].mention.evidence.clear()
    with pytest.raises(ValidationError): build_matching_input(profile,preferences=SearchPreferences())
    inp=projected(); inp.skills[0].mention.evidence.clear()
    with pytest.raises(ValidationError): build_matching_request(inp,[],purpose='debug')
    with pytest.raises(ValidationError): MatchingRequest(purpose='debug',profile=inp,jobs=[])
    prefs=SearchPreferences(); prefs.cities=['北京']; prefs.cities.append(' ')
    with pytest.raises(ValidationError): build_matching_input(rules(),preferences=prefs)


@pytest.mark.parametrize('updates',[{'profile_version':0},{'schema_version':'v2'}, {'warnings':[' ']},
                                   {'source':{}},{'extra':'invented'}])
def test_model_copy_cannot_bypass_projection(updates):
    profile=rules().model_copy(update=updates)
    with pytest.raises(ValidationError): build_matching_input(profile,preferences=SearchPreferences())


def test_models_and_projection_detach_all_nested_inputs():
    profile=rules(); prefs=SearchPreferences(cities=['深圳'])
    inp=build_matching_input(profile,preferences=prefs); job=candidate()
    before=inp.model_dump_json(); job_before=job.model_dump_json()
    req=build_matching_request(inp,[job],purpose='debug')
    req.profile.skills[0].mention.evidence[0].quote='changed'
    req.profile.preferences.cities.append('北京')
    req.jobs[0].job.skills.append('Rust')
    req.jobs[0].provenance.clear()
    assert inp.model_dump_json()==before and job.model_dump_json()==job_before
    inp.skills[0].mention.evidence[0].quote='different'
    inp.preferences.cities.append('广州')
    assert profile.skills[0].mention.evidence[0].quote!='different'
    assert prefs.cities==['深圳']


@pytest.mark.parametrize('kind,code',[
    ('source','source_mismatch'),('quote','quote_mismatch'),('page','missing_page'),
    ('block','missing_block'),('negative','non_positive_skill_context'),
])
def test_upstream_evidence_gate_is_explicit(kind,code):
    doc=document(); profile=rules()
    if kind=='source': profile.source.source_sha256='f'*64
    elif kind=='quote': profile.skills[0].mention.evidence[0].quote='Python invented text'
    elif kind=='page':
        ref=profile.skills[0].mention.evidence[0]
        profile.skills[0].mention.evidence[0]=ref.model_copy(update={'page':2,'block_id':'p002-b001'})
    elif kind=='block': profile.skills[0].mention.evidence[0].block_id='p001-b099'
    else: doc.pages[0].blocks[1].text='计划学习 Python 和 FastAPI。'
    with pytest.raises(ProfileEvidenceError) as caught: validate_profile_evidence(profile,doc)
    assert caught.value.code==code


def test_projection_does_not_claim_document_evidence_was_rechecked():
    profile=rules(); profile.skills[0].mention.evidence[0].quote='Python invented text'
    output=build_matching_input(profile,preferences=SearchPreferences())
    assert output.skills[0].mention.evidence[0].quote=='Python invented text'
    assert 'evidence_validated' not in output.model_dump()
    with pytest.raises(ProfileEvidenceError): validate_profile_evidence(profile,document())


def test_candidate_preserves_full_jd_unknowns_and_multiple_provenance_versions():
    data=job_payload(); second=copy.deepcopy(data['provenance'][0])
    second.update(provenance_id=2,source_file_sha256='2'*64,input_format='html')
    data['provenance'].append(second)
    job=JobCandidate.model_validate(data)
    assert job.job.description==data['job']['description']
    assert job.job.education==job.job.experience==job.job.attendance==''
    assert job.job.skills==['Python','Docker'] # mentions, not must-have labels
    assert [p.provenance_id for p in job.provenance]==[1,2]
    assert JobCandidate.model_validate_json(job.model_dump_json())==job
    assert job.model_dump()['job']==data['job']


@pytest.mark.parametrize('source',['manual','boss'])
def test_hash_recipe_matches_existing_store(source):
    from job_radar.dataset_store import job_fingerprints
    job=ImportedJob(source=source,title='合成岗位',url='https://www.zhipin.com/job_detail/synthetic.html' if source=='boss' else 'https://example.org/jobs/1',description='合成 Python JD。')
    hashes=job_fingerprints(job)
    got=JobCandidate.model_validate({**job_payload(),'job':job.model_dump(),**hashes})
    assert (got.jd_sha256,got.fact_sha256)==(hashes['jd_sha256'],hashes['fact_sha256'])


@pytest.mark.parametrize('updates',[
    {'dataset_id':''},{'dataset_id':' '},{'job_id':0},{'job_id':True},
    {'job_id':'1'},{'schema_version':'v2'},{'review_status':'automatic'},
    {'provenance':[]},{'jd_sha256':'f'*64},{'fact_sha256':'f'*64},
    {'jd_sha256':'a'*63},{'fact_sha256':'G'*64},{'score':90},
])
def test_invalid_candidate_rejected(updates):
    with pytest.raises(ValidationError): candidate(**updates)


@pytest.mark.parametrize('field,value',[
    ('description','changed JD'),('city','上海'),('skills',['Python']),
    ('warnings',['missing_city']),('description_kind','summary'),('source','unknown'),
])
def test_changed_job_fact_or_jd_not_accepted_with_old_hashes(field,value):
    data=job_payload(); data['job'][field]=value
    with pytest.raises(ValidationError): JobCandidate.model_validate(data)


@pytest.mark.parametrize('updates',[
    {'provenance_id':0},{'source_file_sha256':'x'*64},{'input_format':'pdf'},
    {'parser_version':''},{'import_run_id':' '},{'source_file':'/tmp/private'},
    {'imported_at':datetime(2026,1,1)},
    {'imported_at':datetime(2026,1,1,tzinfo=timezone(timedelta(hours=8)))},
])
def test_invalid_provenance_rejected(updates):
    with pytest.raises(ValidationError): JobProvenance.model_validate({**provenance().model_dump(),**updates})


@pytest.mark.parametrize('same_id',[True,False])
def test_duplicate_provenance_rejected(same_id):
    data=job_payload(); second=copy.deepcopy(data['provenance'][0])
    if same_id: second['source_file_sha256']='2'*64
    else: second['provenance_id']=2
    data['provenance'].append(second)
    with pytest.raises(ValidationError,match='duplicate_provenance'): JobCandidate.model_validate(data)


@pytest.mark.parametrize('profile_state',['unconfirmed','confirmed','rejected'])
@pytest.mark.parametrize('job_state',['unconfirmed','confirmed','rejected'])
@pytest.mark.parametrize('purpose',['debug','formal'])
def test_all_review_state_combinations(profile_state,job_state,purpose):
    inp=MatchingInput.model_validate({**projected().model_dump(),'review_status':profile_state})
    job=candidate(review_status=job_state)
    if purpose=='formal' and (profile_state!='confirmed' or job_state!='confirmed'):
        with pytest.raises(ValidationError,match='formal_requires_confirmed'):
            build_matching_request(inp,[job],purpose=purpose)
    else:
        req=build_matching_request(inp,[job],purpose=purpose)
        assert req.profile.review_status==profile_state
        assert req.jobs[0].review_status==job_state
        assert MatchingRequest.model_validate_json(req.model_dump_json())==req


def test_empty_jobs_is_valid_unranked_request_not_failed_match():
    req=build_matching_request(projected(),[],purpose='debug')
    assert req.jobs==[] and 'score' not in req.model_dump()
    data=projected().model_dump(); data['review_status']='confirmed'
    assert build_matching_request(MatchingInput.model_validate(data),[],purpose='formal').jobs==[]


def test_no_sorting_filtering_or_deduplication():
    first=candidate(); data=job_payload(); data['job_id']=2
    data['job']['url']='https://example.org/jobs/2'
    data['fact_sha256']=hashlib.sha256(json.dumps(data['job'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    second=JobCandidate.model_validate(data)
    req=build_matching_request(projected(),[second,first],purpose='debug')
    assert [x.job_id for x in req.jobs]==[2,1]
    assert [x.review_status for x in req.jobs]==['unconfirmed','unconfirmed']
    for obj in [req,req.profile,*req.jobs]:
        assert not {'score','rank','matched_skills','embedding','requirements','hire_probability'} & obj.model_dump().keys()


@pytest.mark.parametrize('duplicate',['id','url'])
def test_duplicate_job_identity_rejected(duplicate):
    first=candidate(); second=candidate()
    if duplicate=='url': second.job_id=2
    with pytest.raises(ValidationError,match='duplicate_job_identity'):
        build_matching_request(projected(),[first,second],purpose='debug')


def test_job_ids_are_namespaced_by_dataset():
    req=build_matching_request(projected(),[candidate(),candidate(dataset_id='another-dataset')],purpose='debug')
    assert len(req.jobs)==2


@pytest.mark.parametrize('kind',['hash','nested_job','source_list'])
def test_mutated_candidate_rechecked_at_assembly(kind):
    job=candidate()
    if kind=='hash': job=job.model_copy(update={'jd_sha256':'f'*64})
    elif kind=='nested_job': job.job.skills.append('invented')
    else: job.provenance.clear()
    with pytest.raises(ValidationError): build_matching_request(projected(),[job],purpose='debug')
    with pytest.raises(ValidationError): MatchingRequest(profile=projected(),jobs=[job],purpose='debug')


@pytest.mark.parametrize('call',[
    lambda:build_matching_input({},preferences=SearchPreferences()),
    lambda:build_matching_input(rules(),preferences={}),
    lambda:build_matching_request({},[],purpose='debug'),
    lambda:build_matching_request(projected(),(),purpose='debug'),
    lambda:build_matching_request(projected(),[{}],purpose='debug'),
])
def test_wrong_public_function_types_fail(call):
    with pytest.raises(TypeError): call()


@pytest.mark.parametrize('updates',[{'purpose':'automatic'},{'schema_version':'v2'}, {'score':1}, {'profile':{}}, {'jobs':[{}]}])
def test_request_strict_shape(updates):
    data=build_matching_request(projected(),[],purpose='debug').model_dump()
    with pytest.raises(ValidationError): MatchingRequest.model_validate({**data,**updates})


def test_purpose_must_be_explicit():
    with pytest.raises(TypeError): build_matching_request(projected(),[])
    with pytest.raises(ValidationError): MatchingRequest(profile=projected(),jobs=[])


def test_deterministic_side_effect_free_conversion(monkeypatch):
    profile=rules(); prefs=SearchPreferences(); job=candidate()
    def blocked(*args,**kwargs): raise AssertionError('Unexpected I/O')
    monkeypatch.setattr(socket,'socket',blocked)
    monkeypatch.setattr(sqlite3,'connect',blocked)
    monkeypatch.setattr(Path,'read_bytes',blocked)
    monkeypatch.setattr(Path,'read_text',blocked)
    monkeypatch.setattr('builtins.open',blocked)
    monkeypatch.setattr(FakeStructureClient,'complete',blocked)
    left=build_matching_request(build_matching_input(profile,preferences=prefs),[job],purpose='debug')
    right=build_matching_request(build_matching_input(profile,preferences=prefs),[job],purpose='debug')
    assert left.model_dump_json()==right.model_dump_json()
    assert profile.review_status=='unconfirmed' and job.review_status=='unconfirmed'


def test_importing_stage1d_does_not_import_io_layers():
    script="import sys;import job_radar.matching_input;assert not any(m in sys.modules for m in ['job_radar.storage','job_radar.dataset_store','job_radar.api','job_radar.resume_structure_apinebula','openai']);print('IMPORT_OK')"
    result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True,check=True)
    assert result.stdout=='IMPORT_OK\n'


def test_synthetic_demo():
    result=subprocess.run([sys.executable,'-B',str(Path(__file__).parents[1]/'examples/matching_input_synthetic.py')],capture_output=True,text=True,check=True)
    assert 'request=matching-request-v1; purpose=debug; jobs=1 (unranked)' in result.stdout
    assert 'formal=formal_requires_confirmed' in result.stdout
    assert 'upstream_tampered_quote=quote_mismatch' in result.stdout
    assert 'database_reads=0; network_calls=0' in result.stdout


@pytest.mark.parametrize('value',['2026-11-11T00:00:00Z','2026-11-11T00:00:00+00:00'])
def test_provenance_explicit_utc_iso_wire_field(value):
    data=provenance().model_dump();data['imported_at']=value
    got=JobProvenance.model_validate(data)
    assert got.imported_at==datetime(2026,11,11,tzinfo=timezone.utc)
    assert JobProvenance.model_validate_json(got.model_dump_json())==got


@pytest.mark.parametrize('value',[0,True,123.5,'2026-11-11','2026-11-11T00:00:00','2026-11-11T08:00:00+08:00','not-a-time'])
def test_provenance_rejects_non_utc_wire_timestamp(value):
    with pytest.raises(ValidationError): JobProvenance.model_validate({**provenance().model_dump(),'imported_at':value})


def test_assignment_retains_nested_types_and_rechecks_at_next_boundary():
    job=candidate();job.job_id=2
    assert isinstance(job.job,ImportedJob) and isinstance(job.provenance[0],JobProvenance)
    inp=projected(); inp.profile_version=2
    assert isinstance(inp.preferences,SearchPreferences)
    req=build_matching_request(inp,[job],purpose='debug')
    req.purpose='debug'
    assert isinstance(req.profile,MatchingInput) and isinstance(req.jobs[0],JobCandidate)
    with pytest.raises(ValidationError): req.purpose='formal'
    # Cross-field assignment can leave a model invalid; discard after failure.
    with pytest.raises(ValidationError): MatchingRequest.model_validate(req.model_dump())


def test_nested_extra_injected_by_model_copy_not_silently_dropped():
    inp=projected()
    mutated=inp.source.model_copy(update={'invented':'x'})
    inp=inp.model_copy(update={'source':mutated})
    with pytest.raises(ValidationError):build_matching_request(inp,[],purpose='debug')


def test_root_instances_revalidate_instead_of_skipping_checks():
    req=build_matching_request(projected(),[candidate()],purpose='debug')
    req.jobs[0].job.description='mutated after construction'
    with pytest.raises(ValidationError):MatchingRequest.model_validate(req)
    inp=projected(); inp.skills[0].mention.evidence.clear()
    with pytest.raises(ValidationError):MatchingInput.model_validate(inp)
    job=candidate();job.provenance.clear()
    with pytest.raises(ValidationError):JobCandidate.model_validate(job)
