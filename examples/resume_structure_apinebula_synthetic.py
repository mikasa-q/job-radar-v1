"""Only the three repository-authored synthetic fixtures; offline by default.

--live opts into the already-confirmed provider, but requires explicit group
prices and USD conversion. No arbitrary resume paths or credentials accepted.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import httpx2

from job_radar.resume_models import ResumeDocument, ResumePage, ResumeBlock, ResumeProvenance
from job_radar.resume_structure import ResumeDraft, ResumeStructureError, structure_resume
from job_radar.resume_structure_apinebula import (
    APINebulaError, APINebulaStructureClient, RateCard, MODEL, TIMEOUT_SECONDS,
)

FIXTURE = Path(__file__).resolve().parents[1]/'tests/fixtures/resume_structure_golden.json'


def synthetic_document(pages):
    return ResumeDocument(provenance=ResumeProvenance(
        source_sha256=hashlib.sha256(json.dumps(pages,ensure_ascii=False).encode()).hexdigest(),
        parser_version='synthetic-v1',extractor_version='synthetic-text-v1'),
        pages=[ResumePage(page_number=i,width=600.0,height=800.0,blocks=[
            ResumeBlock(block_id=f'p{i:03d}-b{j:03d}',text=text,bbox=(0.0,0.0,580.0,20.0))
            for j,text in enumerate(lines,1)]) for i,lines in enumerate(pages,1)])


def shape(result):
    p=result.profile
    val=lambda v:None if v is None else v.value
    return {'skills':[s.name for s in p.skills],
            'schools':[val(e.school) for e in p.education],
            'education_status':[val(e.status) for e in p.education],
            'project_nature':[val(e.nature) for e in p.projects],
            'employment_type':[val(e.employment_type) for e in p.work_experience],
            'summary':val(p.summary),
            'counts':[len(p.education),len(p.projects),len(p.work_experience)]}



# This is a diagnostic for the *unchanged exact comparison*, not an equivalence
# matcher or a response normalizer. Only fixed paths/rules leave this function.
GOLDEN_FIELDS = ('skills', 'schools', 'education_status', 'project_nature',
                 'employment_type', 'summary', 'counts')
MAX_GOLDEN_ISSUES = 8


def golden_diagnostic(actual, expected):
    issues = []
    def add(path, rule):
        if len(issues) < MAX_GOLDEN_ISSUES:
            issues.append({'path':path, 'rule':rule})
    if (not isinstance(actual,dict) or not isinstance(expected,dict)
            or set(actual)!=set(GOLDEN_FIELDS) or set(expected)!=set(GOLDEN_FIELDS)):
        return {'issues':[{'path':'golden', 'rule':'shape_keys_mismatch'}]}
    for field in GOLDEN_FIELDS:
        got, want = actual[field], expected[field]
        if got == want:
            continue
        path = 'golden.'+field
        if field == 'summary':
            if got is None or want is None:
                add(path,'presence_mismatch')
            elif type(got) is not str or type(want) is not str:
                add(path,'type_mismatch')
            else:
                add(path,'text_span_mismatch')
            continue
        if type(got) is not list or type(want) is not list:
            add(path,'type_mismatch')
            continue
        if len(got) != len(want):
            add(path,'list_length_mismatch')
        if field == 'counts':
            for index,label in enumerate(('education','projects','work_experience')):
                if index < len(got) and index < len(want) and got[index] != want[index]:
                    add(path+'.'+label,'count_mismatch')
            continue
        if (len(got)==len(want) and all(v is None or type(v) is str for v in got+want)
                and Counter(got)==Counter(want)):
            add(path,'list_order_mismatch')
            continue
        for index,(g,w) in enumerate(zip(got[:MAX_GOLDEN_ISSUES],want[:MAX_GOLDEN_ISSUES])):
            if g != w:
                add(path+'['+str(index)+']','presence_mismatch' if g is None or w is None else 'value_mismatch')
        if not any(row['path'].startswith(path) for row in issues) and len(issues)<MAX_GOLDEN_ISSUES:
            add(path,'list_value_mismatch')
    return {'issues':issues}


def exercise(cases,client):
    for case in cases:
        try:
            result=structure_resume(synthetic_document(case['pages']),client=client,
                                    resume_id='synthetic-'+case['name'],
                                    timeout_seconds=TIMEOUT_SECONDS)
            actual=shape(result)
            if actual!=case['expected']:
                print('case='+case['name']+' result=golden_mismatch; stopped')
                print('golden_diagnostic='+json.dumps(golden_diagnostic(actual,case['expected']),sort_keys=True))
                return 1
            print('case='+case['name']+' result=validated review_status='+result.profile.review_status)
        except ResumeStructureError as error:
            print('case='+case['name']+' result='+error.code+'; stopped')
            if error.code == 'schema_error':
                print('schema_diagnostic='+json.dumps({
                    'stage':error.schema_stage,
                    'issues':[{'path':issue.path,'rule':issue.rule}
                              for issue in error.schema_issues],
                },sort_keys=True))
            return 1
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    for name in ('group','unit','input-rate','output-rate','usd-reserve-per-unit','cache-create-rate'):
        parser.add_argument('--'+name)
    args=parser.parse_args(argv)
    cases=json.loads(FIXTURE.read_text())['cases']
    if len(cases)!=3:
        print('preflight=synthetic_fixture_count; real_requests=0');return 2
    client=None
    if args.live:
        values=[args.group,args.unit,args.input_rate,args.output_rate,args.usd_reserve_per_unit,args.cache_create_rate]
        if any(v is None for v in values):
            print('preflight=tariff_required; real_requests=0');return 2
        try:
            rates=RateCard(*values)
            client=APINebulaStructureClient(rates)
        except APINebulaError as error:
            print('preflight='+error.code+'; real_requests=0');return 2
        print('mode=live; synthetic_resumes_only; provider=apinebula; model='+MODEL+'; reasoning=low; timeout_seconds='+str(TIMEOUT_SECONDS))
    else:
        # Explicit fake key and rates for in-memory HTTP only, never production.
        rates=RateCard('synthetic','synthetic-unit','0.78','3.9','1')
        outputs=iter(cases)
        def handler(req):
            draft=ResumeDraft.model_validate(next(outputs)['response']).model_dump()
            return httpx2.Response(200,json={'model':MODEL,'status':'completed',
                'output':[{'type':'message','role':'assistant','status':'completed',
                    'content':[{'type':'output_text','text':json.dumps(draft,ensure_ascii=False)}]}],
                'usage':{'input_tokens':1000,'output_tokens':100,'total_tokens':1100}})
        with patch.dict('os.environ',{'OPENAI_API_KEY':'synthetic-offline-key'}):
            client=APINebulaStructureClient(rates,_transport=httpx2.MockTransport(handler))
        print('mode=offline; real_requests=0; scripted_responses_not_model_quality')
    try:
        return exercise(cases,client)
    finally:
        print('audit='+json.dumps(client.audit,ensure_ascii=False,sort_keys=True))
        client.close()


if __name__=='__main__':
    raise SystemExit(main())
