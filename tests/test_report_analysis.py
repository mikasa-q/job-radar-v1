"""Synthetic analysis wiring/guards only, not measured LLM quality."""
import copy
import json
from dataclasses import replace
import pytest
from job_radar.rag_engine import match_jobs_indexed
from job_radar.rag_index import DerivedIndex
from job_radar.rag_models import canonical_hash
from job_radar.report_analysis import SYSTEM_PROMPT,build_analysis_request,validate_analysis,analyze_job
from job_radar.report_models import *
from job_radar.report_synthetic import synthetic_inputs,synthetic_manifest
from job_radar.retrieval_models import JobKey,JobSpan


def record(kind,path,value,*,key=None,quote=None,resume_ref=None,job_span=None):
    data=dict(kind=kind,path=path,key=key,source_sha256='a'*64,quote=quote,resume_ref=resume_ref,job_span=job_span,value_json=json.dumps(value,ensure_ascii=False,sort_keys=True))
    return EvidenceRecord(evidence_id='e'+canonical_hash({k:(v.model_dump(mode='json') if hasattr(v,'model_dump') else v) for k,v in data.items()})[:24],**data)

@pytest.fixture
def packet(tmp_path):
    doc,req=synthetic_inputs()
    index=DerivedIndex(tmp_path/'index');index.update(req.jobs,manifest=synthetic_manifest(req.jobs))
    result=match_jobs_indexed(req,index=index)
    selected=[r.key for r in result.matching.results[:3]]
    evidence=[record('scope','profile',{'scope':'full_supplied_projection','skills':[s.name for s in req.profile.skills]})]
    for s in req.profile.skills:
        for ref in s.mention.evidence:
            evidence.append(record('resume','profile.skills',{'value':s.mention.value,'context':s.context,'block_text':doc.pages[0].blocks[0].text,'evidence':ref.model_dump()},quote=ref.quote,resume_ref=ref))
    for ranked in result.matching.results:
        job=next(j.job for j in req.jobs if j.job_id==ranked.key.job_id)
        for field in ['title','description']:
            text=getattr(job,field)
            span=JobSpan(field=field,start=0,end=len(text),quote=text)
            evidence.append(record('job','job.'+field,{'text':text},key=ranked.key,quote=text,job_span=span))
        for n,s in enumerate(ranked.rules.skills):
            evidence.append(record('rule',f'rules.skills[{n}]',s.model_dump(mode='json'),key=ranked.key))
        evidence.append(record('retrieval','retrieval.rank',{'rank':ranked.rank},key=ranked.key))
    return EvidencePacket(request_sha256=canonical_hash(req),document_sha256=canonical_hash(doc),profile_sha256=canonical_hash(req.profile),retrieval_sha256=canonical_hash(result),retrieval=result,selected_keys=selected,evidence=evidence)


def refs(packet,key,kind):
    return [e for e in packet.evidence if e.kind==kind and (e.key is None or e.key==key)]


def valid_unknown(packet,key=None):
    key=key or next(e.key for e in packet.evidence if e.kind=='job' and e.key in packet.selected_keys and 'Docker' in (e.quote or ''))
    j=next(e for e in refs(packet,key,'job') if e.job_span.field=='description')
    scope=refs(packet,key,'scope')[0]
    return MatchAnalysisDraft(key=key,claims=[AnalysisClaim(claim_id='c1',kind='unknown',subject='Docker',status='unknown',text='简历未提及 Docker 使用经历。',requirement_kind='bonus',evidence_ids=[j.evidence_id,scope.evidence_id,next(e.evidence_id for e in refs(packet,key,'rule') if 'Docker' in json.loads(e.value_json).get('alternatives',[]))])])


def valid_strength(packet):
    for ranked in packet.retrieval.matching.results:
        if ranked.key not in packet.selected_keys:continue
        for check in ranked.rules.skills:
            if check.status=='evidence_found':
                subject=check.matched[0]
                rule=next(e for e in refs(packet,ranked.key,'rule') if json.loads(e.value_json)==check.model_dump(mode='json'))
                resume=next(e for e in refs(packet,ranked.key,'resume') if e.resume_ref in check.resume_evidence)
                jd=next(e for e in refs(packet,ranked.key,'job') if e.job_span.field=='description')
                return MatchAnalysisDraft(key=ranked.key,claims=[AnalysisClaim(claim_id='c1',kind='strength',subject=subject,status='supported',text=f'当前课程项目材料中有{subject}使用证据。',requirement_kind=check.kind,evidence_ids=[rule.evidence_id,resume.evidence_id,jd.evidence_id])])
    raise AssertionError('fixture_has_no_support')


def changed_claim(draft,**patch):
    value=draft.model_dump();value['claims'][0].update(patch)
    return MatchAnalysisDraft.model_validate(value)


def test_request_single_job_and_deterministic(packet):
    key=packet.selected_keys[0]
    a=build_analysis_request(packet,key);b=build_analysis_request(packet,key)
    assert a==b
    data=json.loads(a.packet_json)
    assert set(data)=={'schema_version','key','purpose','context','evidence','limitations'}
    assert data['schema_version']=='match-analysis-input-v1'
    assert data['key']==key.model_dump()
    assert all(e['key'] in [None,key.model_dump()] for e in data['evidence'])
    assert json.loads(a.output_schema_json)==MatchAnalysisDraft.model_json_schema()
    assert a.prompt_version==PROMPT_VERSION
    assert '不是录用裁判' in a.system_prompt and 'OR已有一个分支证据' in SYSTEM_PROMPT


def test_wire_minimizes_nested_contact_paths_metadata(packet):
    key=packet.selected_keys[0]
    record=refs(packet,key,'scope')[0]
    new_value=json.dumps({'text':'邮箱 person@example.com 电话 +86 13812345678 https://secret.example/test /Users/alice/private/file.txt C:\\Users\\alice\\secret.pdf','quote':'PRIVATE-QUOTE','resume_ref':{'quote':'PRIVATE-REF'},'nested':{'text':'https://secret.example/t mail@example.com'},'safe':'Python course project','provenance':{'absolute':'/Users/alice/secret'}},ensure_ascii=False)
    replacement=globals()['record']('scope',record.path,json.loads(new_value))
    packet.evidence[packet.evidence.index(record)]=replacement
    a=build_analysis_request(packet,key)
    for forbidden in ['person@example.com','13812345678','secret.example','/Users/alice','PRIVATE-QUOTE','PRIVATE-REF','mail@example.com','C:\\\\Users']:
        assert forbidden not in a.packet_json
    assert 'Python course project' in a.packet_json
    assert 'quote' not in json.loads(a.packet_json)['evidence'][0]['content']
    assert record.quote is None


def test_no_request_mutation(packet):
    before=packet.model_dump_json()
    build_analysis_request(packet,packet.selected_keys[0])
    assert packet.model_dump_json()==before


def test_input_limit_counts_schema_prompt(packet):
    with pytest.raises(ReportError,match='analysis_input_limit'):
        build_analysis_request(packet,packet.selected_keys[0],config=AnalysisConfig(max_input_bytes=256))


def test_unselected_key_fails(packet):
    with pytest.raises(ReportError,match='analysis_key_not_selected'):
        build_analysis_request(packet,JobKey(dataset_id='unselected',job_id=80))


def test_unsafe_path_is_rejected(packet):
    packet.evidence[0]=record('scope','/Users/private/secret',{'scope':'supplied'})
    with pytest.raises(ReportError,match='analysis_unsafe_evidence_path'):
        build_analysis_request(packet,packet.selected_keys[0])


def test_valid_unknown_and_strength(packet):
    unknown=valid_unknown(packet)
    assert validate_analysis(unknown,packet,unknown.key)==unknown
    strength=valid_strength(packet)
    assert validate_analysis(strength,packet,strength.key)==strength


def test_wrong_job_key(packet):
    draft=valid_unknown(packet)
    with pytest.raises(ReportError,match='analysis_key_mismatch'):
        validate_analysis(draft,packet,next(k for k in packet.selected_keys if k!=draft.key))


def test_cross_job_evidence(packet):
    draft=valid_unknown(packet)
    other=next(e for e in packet.evidence if e.kind=='job' and e.key!=draft.key)
    forged=changed_claim(draft,evidence_ids=[other.evidence_id,refs(packet,draft.key,'scope')[0].evidence_id])
    with pytest.raises(ReportError,match='analysis_evidence_scope'):
        validate_analysis(forged,packet,draft.key)


def test_unknown_id(packet):
    draft=valid_unknown(packet)
    forged=changed_claim(draft,evidence_ids=['e'+'0'*24])
    with pytest.raises(ReportError,match='analysis_evidence_scope'):
        validate_analysis(forged,packet,draft.key)

@pytest.mark.parametrize('text',['录用概率为80%','匹配程度100％','胜任率高','Confidence 0.99','成功率百分之九十'])
def test_probability_language_rejected(packet,text):
    draft=changed_claim(valid_unknown(packet),text=text)
    with pytest.raises(ReportError,match='analysis_forbidden_probability'):
        validate_analysis(draft,packet,draft.key)

@pytest.mark.parametrize('text',['候选人不会Docker。','不具备Docker能力。','候选人缺乏Docker技能。','Not capable of Docker.'])
def test_unknown_never_inability(packet,text):
    draft=changed_claim(valid_unknown(packet),text=text)
    with pytest.raises(ReportError,match='analysis_text_contract_violation'):
        validate_analysis(draft,packet,draft.key)


def test_unknown_requires_scope(packet):
    d=valid_unknown(packet)
    d=changed_claim(d,evidence_ids=[i for i in d.claims[0].evidence_ids if i != refs(packet,d.key,'scope')[0].evidence_id])
    with pytest.raises(ReportError,match='analysis_unknown_requires_scope'):
        validate_analysis(d,packet,d.key)


def test_no_rule_or_resume_is_not_supported(packet):
    d=valid_strength(packet)
    for kind in ['rule','resume','job']:
        keep=[i for i in d.claims[0].evidence_ids if next(e for e in packet.evidence if e.evidence_id==i).kind!=kind]
        forged=changed_claim(d,evidence_ids=keep)
        with pytest.raises(ReportError,match='analysis_invalid_rule_reference' if kind=='rule' else 'analysis_unsupported_positive'):
            validate_analysis(forged,packet,d.key)


def test_requirement_bonus_cannot_be_required(packet):
    d=valid_strength(packet)
    other='bonus' if d.claims[0].requirement_kind=='required' else 'required'
    with pytest.raises(ReportError,match='analysis_requirement_kind_mismatch'):
        validate_analysis(changed_claim(d,requirement_kind=other),packet,d.key)

@pytest.mark.parametrize('text',['精通Python。','具备三年生产经验。','Python expert with production experience.','具备2 years of experience.'])
def test_course_mention_not_inflated(packet,text):
    d=valid_strength(packet)
    with pytest.raises(ReportError,match='analysis_unsupported_experience'):
        validate_analysis(changed_claim(d,text=text),packet,d.key)


def test_absence_not_explicit_gap(packet):
    d=changed_claim(valid_unknown(packet),kind='skill_gap',status='explicit_gap',text='Docker差距明确。')
    with pytest.raises(ReportError,match='analysis_gap_requires_negative_evidence'):
        validate_analysis(d,packet,d.key)


def test_explicit_negative_and_future_boundary(packet):
    d=valid_unknown(packet)
    for text,success in [('从未使用 Docker。',True),('计划学习 Docker。',False)]:
        ref=__import__('job_radar.resume_profile',fromlist=['EvidenceRef']).EvidenceRef(page=1,block_id='p001-b001',quote=text)
        e=record('resume','profile.summary',{'value':text},quote=text,resume_ref=ref)
        p=EvidencePacket.model_validate(packet);p.evidence.append(e)
        gap=changed_claim(d,kind='skill_gap',status='explicit_gap',text='简历注明从未使用 Docker。',evidence_ids=[i for i in d.claims[0].evidence_ids if i != refs(packet,d.key,'scope')[0].evidence_id]+[e.evidence_id])
        if success:assert validate_analysis(gap,p,gap.key)==gap
        else:
            with pytest.raises(ReportError,match='analysis_gap_requires_negative_evidence'):validate_analysis(gap,p,gap.key)


def test_advice_requires_same_finding_and_evidence(packet):
    d=valid_unknown(packet)
    c=AnalysisClaim(claim_id='c2',kind='recommendation',subject='Docker',status='advisory',text='可补充 Docker 使用记录。',requirement_kind='bonus',evidence_ids=d.claims[0].evidence_ids,related_claim_ids=['c1'])
    value=MatchAnalysisDraft(key=d.key,claims=[d.claims[0],c])
    assert validate_analysis(value,packet,d.key)==value
    c.subject='Java'
    with pytest.raises(ReportError,match='analysis_requirement_kind_mismatch'):
        validate_analysis(MatchAnalysisDraft(key=d.key,claims=[d.claims[0],c]),packet,d.key)


class Client:
    def __init__(self,response=None,error=None):self.calls=0;self.response=response;self.error=error
    def complete(self,request):
        self.calls+=1
        if self.error:raise self.error
        return self.response


def test_analyze_once_success_with_audit(packet):
    draft=valid_unknown(packet)
    audit=CallAudit(provider='scripted',model='synthetic-no-intelligence',attempts=1,input_tokens=10,output_tokens=20,total_tokens=30)
    c=Client(AnalysisResponse(status='completed',text=draft.model_dump_json(),audit=audit))
    result=analyze_job(packet,draft.key,client=c)
    assert c.calls==1 and result.status=='completed' and result.audit==audit

@pytest.mark.parametrize('status',['refused','incomplete'])
def test_incomplete_response_is_failed_no_retry(packet,status):
    c=Client(AnalysisResponse(status=status))
    result=analyze_job(packet,packet.selected_keys[0],client=c)
    assert c.calls==1 and result.status=='failed' and result.error_code=='analysis_'+status and result.draft is None

@pytest.mark.parametrize('error,code',[(TimeoutError('private'),'analysis_client_timeout'),(RuntimeError('secret source text'),'analysis_client_error'),(ReportError('analysis_input_limit'),'analysis_input_limit'),(ReportError('BADSECRET'),'analysis_client_error')])
def test_client_errors_are_fixed_without_retry(packet,error,code):
    c=Client(error=error)
    result=analyze_job(packet,packet.selected_keys[0],client=c)
    assert c.calls==1 and result.error_code==code and result.draft is None
    assert 'secret' not in result.model_dump_json()

@pytest.mark.parametrize('response',[None,{'status':'completed','text':'{}'},AnalysisResponse(status='completed',text='```json\n{}\n```'),AnalysisResponse(status='completed',text='{}')])
def test_bad_response_is_not_repaired(packet,response):
    c=Client(response)
    result=analyze_job(packet,packet.selected_keys[0],client=c)
    assert c.calls==1 and result.status=='failed' and result.draft is None


def test_preflight_limit_zero_client_calls(packet):
    c=Client()
    result=analyze_job(packet,packet.selected_keys[0],client=c,config=AnalysisConfig(max_input_bytes=256))
    assert c.calls==0 and result.error_code=='analysis_input_limit'


def test_report_error_lookalike_does_not_expose_arbitrary_code(packet):
    c=Client(error=ReportError('analysis_private_payload'))
    result=analyze_job(packet,valid_unknown(packet).key,client=c)
    assert result.error_code=='analysis_client_error'
    assert 'private_payload' not in result.model_dump_json()


def test_advice_cannot_share_scope_only(packet):
    d=valid_unknown(packet)
    title=next(e for e in refs(packet,d.key,'job') if e.job_span.field=='title')
    scope=refs(packet,d.key,'scope')[0]
    c=AnalysisClaim(claim_id='c2',kind='recommendation',subject='Docker',status='advisory',text='可补充 Docker 使用记录。',requirement_kind='bonus',evidence_ids=[title.evidence_id,scope.evidence_id,d.claims[0].evidence_ids[-1]],related_claim_ids=['c1'])
    with pytest.raises(ReportError,match='analysis_advice_requires_finding_evidence'):
        validate_analysis(MatchAnalysisDraft(key=d.key,claims=[d.claims[0],c]),packet,d.key)


def test_subject_not_in_jd_cannot_be_unknown_gap(packet):
    d=changed_claim(valid_unknown(packet),subject='UnmentionedTechnology',text='该技能情况未知。')
    with pytest.raises(ReportError,match='analysis_requirement_kind_mismatch'):
        validate_analysis(d,packet,d.key)


def test_empty_query_does_not_invent_analysis_key(tmp_path):
    doc,req=synthetic_inputs(text='暂无正向技能。')
    idx=DerivedIndex(tmp_path/'index');idx.update(req.jobs,manifest=synthetic_manifest(req.jobs))
    result=match_jobs_indexed(req,index=idx)
    p=EvidencePacket(request_sha256=canonical_hash(req),document_sha256=canonical_hash(doc),profile_sha256=canonical_hash(req.profile),retrieval_sha256=canonical_hash(result),retrieval=result,selected_keys=[])
    client=Client()
    value=analyze_job(p,JobKey(dataset_id='synthetic-stage4',job_id=1),client=client)
    assert value.status=='failed' and value.error_code=='analysis_key_not_selected' and client.calls==0


def test_real_evidence_builder_interoperates(tmp_path):
    from job_radar.report_evidence import build_evidence_packet
    doc,req=synthetic_inputs()
    idx=DerivedIndex(tmp_path/'index');idx.update(req.jobs,manifest=synthetic_manifest(req.jobs))
    result=match_jobs_indexed(req,index=idx)
    p=build_evidence_packet(req,result,doc)
    for key in p.selected_keys:
        request=build_analysis_request(p,key)
        data=json.loads(request.packet_json)
        assert any(e['kind']=='scope' for e in data['evidence'])
        assert all(e['key'] is None or e['key']==key.model_dump() for e in data['evidence'])
    d=valid_strength(p)
    assert validate_analysis(d,p,d.key)==d
    d=valid_unknown(p)
    assert validate_analysis(d,p,d.key)==d


def test_unknown_does_not_permit_proficiency_inflation(packet):
    d=changed_claim(valid_unknown(packet),text='精通Docker，但其能力未知。')
    with pytest.raises(ReportError,match='analysis_unsupported_experience'):
        validate_analysis(d,packet,d.key)


def test_other_skill_negation_does_not_establish_explicit_gap(packet):
    from job_radar.resume_profile import EvidenceRef
    d=valid_unknown(packet)
    text='从未使用 Java。项目中使用 Docker。'
    ref=EvidenceRef(page=1,block_id='p001-b001',quote=text)
    e=record('resume','document.pages[0].blocks[p001-b001]',{'block_text':text},quote=text,resume_ref=ref)
    p=EvidencePacket.model_validate(packet);p.evidence.append(e)
    gap=changed_claim(d,kind='skill_gap',status='explicit_gap',text='简历注明从未使用 Docker。',evidence_ids=[i for i in d.claims[0].evidence_ids if i != refs(packet,d.key,'scope')[0].evidence_id]+[e.evidence_id])
    with pytest.raises(ReportError,match='analysis_gap_requires_negative_evidence'):
        validate_analysis(gap,p,gap.key)


def test_satisfied_or_alternative_not_missing_requirement(tmp_path):
    from job_radar.report_evidence import build_evidence_packet
    doc,req=synthetic_inputs(descriptions=['要求掌握 Python 或 Java。'])
    idx=DerivedIndex(tmp_path/'index');idx.update(req.jobs,manifest=synthetic_manifest(req.jobs))
    packet=build_evidence_packet(req,match_jobs_indexed(req,index=idx),doc)
    row=next(r for r in packet.retrieval.matching.results if any(len(s.alternatives)>1 and s.status=='evidence_found' for s in r.rules.skills))
    check=next(s for s in row.rules.skills if len(s.alternatives)>1 and s.status=='evidence_found')
    subject=next(a for a in check.alternatives if a not in check.matched)
    jd=next(e for e in refs(packet,row.key,'job') if e.job_span.field=='description')
    scope=refs(packet,row.key,'scope')[0]
    claim=AnalysisClaim(claim_id='c1',kind='skill_gap',subject=subject,status='explicit_gap',text=f'简历注明从未使用 {subject}。',requirement_kind=check.kind,evidence_ids=[jd.evidence_id,scope.evidence_id])
    with pytest.raises(ReportError,match='analysis_alternative_already_supported'):
        validate_analysis(MatchAnalysisDraft(key=row.key,claims=[claim]),packet,row.key)


def test_bonus_advice_not_rewritten_as_mandatory(packet):
    d=valid_unknown(packet)
    claim=AnalysisClaim(claim_id='c2',kind='recommendation',subject='Docker',status='advisory',text='必须补齐Docker才可以投递。',requirement_kind='bonus',evidence_ids=d.claims[0].evidence_ids,related_claim_ids=['c1'])
    with pytest.raises(ReportError,match='analysis_optional_as_mandatory'):
        validate_analysis(MatchAnalysisDraft(key=d.key,claims=[d.claims[0],claim]),packet,d.key)


def test_transport_failure_preserves_audit_and_halts(packet):
    from job_radar.report_models import CallAudit
    class Client:
        last_call_audit=CallAudit(provider='mock',model='synthetic',attempts=1,reserved_units='0.1',billing_unit='CNY')
        halted=None
        def complete(self,request):raise TimeoutError('secret timeout detail')
        def halt(self,code):self.halted=code
    c=Client();out=analyze_job(packet,packet.selected_keys[0],client=c)
    assert out.error_code=='analysis_client_timeout'
    assert out.audit.reserved_units=='0.1' and out.audit.input_tokens is None
    assert c.halted=='analysis_validation_failed'
    assert 'secret' not in out.model_dump_json()

@pytest.mark.parametrize('code,expected',[('unknown_usage','analysis_provider_unknown_usage'),('budget_limit','analysis_provider_budget_limit'),('provider_http_429','analysis_provider_http_429'),('analysis_secret_token','analysis_client_error'),('private_secret','analysis_client_error')])
def test_provider_error_fixed_code_mapping(packet,code,expected):
    class Client:
        def complete(self,request):raise ReportError(code)
    out=analyze_job(packet,packet.selected_keys[0],client=Client())
    assert out.error_code==expected


def test_negation_wrong_skill_comma_rejected(packet):
    d=valid_unknown(packet)
    text='不会Java，使用Docker开发项目。'
    ref=EvidenceRef(page=1,block_id='p001-b001',quote=text)
    e=record('resume','document.pages[0].blocks[p001-b001]',{'block_text':text},quote=text,resume_ref=ref)
    p=EvidencePacket.model_validate(packet);p.evidence.append(e)
    gap=changed_claim(d,kind='skill_gap',status='explicit_gap',text='简历注明从未使用 Docker。',evidence_ids=[i for i in d.claims[0].evidence_ids if i != refs(packet,d.key,'scope')[0].evidence_id]+[e.evidence_id])
    with pytest.raises(ReportError,match='analysis_gap_requires_negative_evidence'):validate_analysis(gap,p,gap.key)
