"""Schema diagnosis uses only authored synthetic documents and scripted replies."""
import copy
import importlib.util
import json
import re
import socket
import traceback
from dataclasses import FrozenInstanceError
from pathlib import Path

import httpx2
import pytest

from job_radar.resume_models import ResumeBlock, ResumeDocument, ResumePage, ResumeProvenance
from job_radar.resume_structure import (
    ClientResponse, MAX_SCHEMA_ISSUES, ResumeDraft, ResumeStructureError,
    SchemaIssue, SYSTEM_PROMPT, build_structure_request, structure_resume,
)
from job_radar.resume_structure_apinebula import (
    APINebulaStructureClient, RateCard, strict_schema,
)
from job_radar.resume_structure_fake import FakeStructureClient

GOLDEN=json.loads((Path(__file__).parent/'fixtures/resume_structure_golden.json').read_text())['cases']
EMPTY=dict(education=[],skills=[],projects=[],work_experience=[],summary=None,warnings=[])
SECRET='SYNTHETIC_PRIVATE_TEXT_NOT_FOR_DIAGNOSTICS'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('socket used'))
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:pytest.fail('socket used'))


def document():
    return ResumeDocument(provenance=ResumeProvenance(source_sha256='a'*64,
        parser_version='synthetic-v1',extractor_version='synthetic-v1'),pages=[
        ResumePage(page_number=i,width=600.0,height=800.0,blocks=[
            ResumeBlock(block_id=f'p{i:03d}-b{j:03d}',text=t,bbox=(0.0,0.0,580.0,20.0))
            for j,t in enumerate(lines,1)]) for i,lines in enumerate(GOLDEN[0]['pages'],1)])


def fact(value,quote=None):
    return dict(value=value,evidence=[dict(page=1,block_id='p001-b001',quote=value if quote is None else quote)])


def caught(data):
    client=FakeStructureClient([ClientResponse(status='completed',text=json.dumps(data,ensure_ascii=False))])
    with pytest.raises(ResumeStructureError) as err:
        structure_resume(document(),client=client,resume_id='synthetic')
    assert len(client.requests)==1
    error=err.value
    assert error.code=='schema_error' and str(error)=='schema_error'
    assert error.evidence_code is None and error.path is None
    return error


def display(error):
    return {'stage':error.schema_stage,'issues':[dict(path=x.path,rule=x.rule) for x in error.schema_issues]}


@pytest.mark.parametrize('data,path,rule',[
    ({**EMPTY,'education':[{'end_date':{**fact('预计 2029 年毕业'),'normalized':'2029'}}]},'draft.education[0].end_date','date_normalization_mismatch'),
    ({**EMPTY,'education':[{'degree':fact('学士','本科')}]},'draft.education[0].degree','fact_value_not_quoted'),
    ({**EMPTY,'skills':[{'name':'Python','mention':{'value':'Python','evidence':[
        {'page':2,'block_id':'p001-b001','quote':'Python'}]}}]},'draft.skills[0].mention.evidence[0]','evidence_page_mismatch'),
    ({**EMPTY,'projects':[{}]},'draft.projects[0]','empty_entry'),
    ({**EMPTY,'education':[{'start_date':{**fact('2025.13'),'normalized':None}}]},'draft.education[0].start_date','invalid_explicit_date'),
    ({**EMPTY,'projects':[{'start_date':fact('2026.01'),'end_date':fact('2025.09')}]},'draft.projects[0]','date_order'),
    ({**EMPTY,'summary':fact(' ')},'draft.summary.value','blank_string'),
    ({**EMPTY,'warnings':[1]},'draft.warnings[0]','string_type'),
    ({**EMPTY,'education':[{'school':{'value':'大学','evidence':[]}}]},'draft.education[0].school.evidence','too_short'),
    ({**EMPTY,'skills':[{'name':'Python','mention':fact('Python'),'context':'expert'}]},'draft.skills[0].context','literal_error'),
    ({**EMPTY,'skills':[{'name':'Python','mention':{'value':'Python','evidence':[
        {'page':'1','block_id':'p001-b001','quote':'Python'}]}}]},'draft.skills[0].mention.evidence[0].page','int_type'),
])
def test_redacted_exact_stage_path_and_rule_for_synthetic_invalid_drafts(data,path,rule):
    error=caught(data)
    assert error.schema_stage=='draft'
    assert SchemaIssue(path=path,rule=rule) in error.schema_issues
    assert len(error.schema_issues)<=MAX_SCHEMA_ISSUES


def test_profile_stage_distinct_from_draft_for_blank_warning():
    data={**EMPTY,'warnings':[' ']}
    assert ResumeDraft.model_validate(data)
    error=caught(data)
    assert display(error)=={'stage':'profile','issues':[{'path':'profile.warnings','rule':'blank_warning'}]}


@pytest.mark.parametrize('value',[None,[],1,'synthetic text',True])
def test_wrong_root_shape_diagnosed_without_echo(value):
    error=caught(value)
    assert error.schema_stage=='draft'
    assert error.schema_issues[0].path=='draft'
    assert error.schema_issues[0].rule in {'model_type','dict_type'}


def test_missing_key_preserves_known_schema_path():
    data=copy.deepcopy(EMPTY);del data['education']
    assert display(caught(data))=={'stage':'draft','issues':[{'path':'draft.education','rule':'missing'}]}


@pytest.mark.parametrize('data',[
    {**EMPTY,SECRET:SECRET},
    {**EMPTY,'education':[{'school':{'value':SECRET,'evidence':[]}}]},
    {**EMPTY,'skills':[{'name':'Python','mention':fact('Python'),SECRET:SECRET}]},
    {**EMPTY,'skills':[{'name':'Python','mention':{'value':SECRET,'evidence':[
        {'page':SECRET,'block_id':SECRET,'quote':SECRET}]}}]},
])
def test_private_inputs_and_extra_field_names_never_appear_in_diagnostic_or_traceback(data):
    error=caught(data)
    assert SECRET not in json.dumps(display(error))
    assert SECRET not in ''.join(traceback.format_exception(error))
    assert SECRET not in repr(error)
    if SECRET in data or (data.get('skills') and SECRET in data['skills'][0]):
        assert any('<unknown_field>' in issue.path for issue in error.schema_issues)


def test_many_errors_have_bounded_immutable_diagnostics():
    data={**EMPTY,'education':[{'school':{'value':str(i),'evidence':[]}} for i in range(30)]}
    error=caught(data)
    assert len(error.schema_issues)==MAX_SCHEMA_ISSUES==8
    assert isinstance(error.schema_issues,tuple)
    with pytest.raises(FrozenInstanceError):error.schema_issues[0].path='changed'


def test_other_error_codes_have_no_schema_diagnostic():
    client=FakeStructureClient([ClientResponse(status='completed',text='invalid json')])
    with pytest.raises(ResumeStructureError) as err:structure_resume(document(),client=client,resume_id='synthetic')
    assert err.value.code=='invalid_json'
    assert err.value.schema_stage is None and err.value.schema_issues==()


def _load_demo():
    p=Path(__file__).parents[1]/'examples/resume_structure_apinebula_synthetic.py'
    spec=importlib.util.spec_from_file_location('schema_diagnostic_demo',p)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('profile_stage',[False,True])
def test_runner_prints_redacted_stage_and_stops_at_first_schema_failure(profile_stage,capsys):
    data=copy.deepcopy(GOLDEN[0]['response'])
    if profile_stage:data['warnings']=[' ']
    else:data['education'][0]['end_date']['normalized']='2029'
    client=FakeStructureClient([ClientResponse(status='completed',text=json.dumps(data))])
    assert _load_demo().exercise(GOLDEN,client)==1
    assert len(client.requests)==1
    text=capsys.readouterr().out
    assert text.count('schema_diagnostic=')==1
    report=json.loads(text.split('schema_diagnostic=')[1].strip())
    assert report['stage']==('profile' if profile_stage else 'draft')
    assert report['issues'][0]['rule']==('blank_warning' if profile_stage else 'date_normalization_mismatch')
    assert '预计' not in text and '合成大学' not in text
    assert GOLDEN[1]['name'] not in text


def _wire_constraints(value,node,root):
    """Test-only evaluation of this schema's exact keyword surface, not generic JSONSchema."""
    supported={'$ref','$defs','type','anyOf','properties','required','additionalProperties',
               'description','items','enum','minimum','minLength','minItems','pattern'}
    assert set(node)<=supported,set(node)-supported
    if '$ref' in node:return _wire_constraints(value,root['$defs'][node['$ref'].split('/')[-1]],root)
    if 'anyOf' in node:return any(_wire_constraints(value,choice,root) for choice in node['anyOf'])
    t=node.get('type')
    if t=='null':return value is None
    if t=='object':
        return (isinstance(value,dict) and set(node.get('required',[]))<=set(value)
            and (node.get('additionalProperties') is not False or set(value)<=set(node['properties']))
            and all(_wire_constraints(v,node['properties'][k],root) for k,v in value.items()))
    if t=='array':return (isinstance(value,list) and len(value)>=node.get('minItems',0)
        and all(_wire_constraints(v,node['items'],root) for v in value))
    if t=='string':return (isinstance(value,str) and len(value)>=node.get('minLength',0)
        and ('pattern' not in node or re.search(node['pattern'],value) is not None)
        and ('enum' not in node or value in node['enum']))
    if t=='integer':return type(value) is int and value>=node.get('minimum',float('-inf'))
    raise AssertionError('unsupported test keyword/type')


@pytest.mark.parametrize('change',['date_normalized','fact_rewrite','page_mismatch','empty_entry','warning_blank'])
def test_wire_schema_can_pass_while_local_custom_validator_fails(change):
    data=ResumeDraft.model_validate(GOLDEN[0]['response']).model_dump()
    if change=='date_normalized':data['education'][0]['end_date']['normalized']='2029'
    if change=='fact_rewrite':data['education'][0]['degree']['value']='学士'
    if change=='page_mismatch':data['skills'][0]['mention']['evidence'][0]['page']=2
    if change=='empty_entry':
        data['projects']=[dict(start_date=None,end_date=None,name=None,role=None,nature=None,description=[],technologies=[])]
    if change=='warning_blank':data['warnings']=[' ']
    wire=strict_schema()
    assert _wire_constraints(data,wire,wire)
    assert caught(data).schema_stage==('profile' if change=='warning_blank' else 'draft')


def test_unknown_custom_message_and_dynamic_error_type_not_echoed():
    from pydantic import ValidationError
    from pydantic_core import PydanticCustomError
    from job_radar.resume_structure import _schema_failure
    error=ValidationError.from_exception_data('Synthetic',[{
        'type':'value_error','loc':(SECRET,),'input':SECRET,'ctx':{'error':ValueError(SECRET)}
    },{'type':PydanticCustomError(SECRET,SECRET),'loc':('summary',),'input':SECRET}])
    error=_schema_failure(error,'draft')
    assert SECRET not in json.dumps(display(error))
    assert {x.rule for x in error.schema_issues}=={'custom_validation_error','validation_error'}


def test_provider_configuration_payload_schema_and_prompt_unchanged(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-never-real')
    client=APINebulaStructureClient(RateCard('synthetic','CNY','0.78','3.90','1','0.975'),
        _transport=httpx2.MockTransport(lambda req:pytest.fail('HTTP not expected')))
    try:
        assert client._sdk.timeout==90.0 and client._sdk.max_retries==0
        payload=client._payload(build_structure_request(document(),timeout_seconds=90))
        assert payload['instructions']==SYSTEM_PROMPT
        assert payload['model']=='gpt-6.1-sol' and payload['reasoning']=={'effort':'low'}
        assert payload['text']['format']['schema']==strict_schema()
        assert client.audit['generation_attempts']==0 and client.audit['count_api_requests']==0
    finally:client.close()
