"""Frozen v3 black-box matrix; authored evidence is not real model acceptance."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from job_radar.report_analysis import validate_analysis, analyze_job, build_analysis_request, SYSTEM_PROMPT
from job_radar.report_core import parse_analysis
from job_radar.report_evidence import validate_evidence_packet
from job_radar.report_models import AnalysisClaim, AnalysisResponse, EvidencePacket, EvidenceRecord, MatchAnalysisDraft, MatchReport, ReportError
from job_radar.report_engine import generate_match_report
from job_radar.report_render import validate_report, render_match_report
from job_radar.report_demo_client import SyntheticReportClient
from job_radar.rag_models import canonical_hash
from job_radar.resume_profile import EvidenceRef

spec=importlib.util.spec_from_file_location('v3_eval',Path(__file__).parents[1]/'examples/stage4_v3_offline_evaluate.py')
ev=importlib.util.module_from_spec(spec);spec.loader.exec_module(ev)

@pytest.fixture
def case(tmp_path):return ev.make_case(tmp_path/'index')
def single(packet,subject='Python',status='supported',**kwargs):
 key=packet.selected_keys[0]
 return MatchAnalysisDraft(key=key,claims=[ev.claim(packet,key,subject,status,**kwargs)])
def error(d,p,code):
 with pytest.raises(ReportError,match='^'+code+'$'):validate_analysis(d,p,d.key)

@pytest.mark.parametrize('word',ev.INFLATION)
@pytest.mark.parametrize('prefix',['','不代表','并非不','引文写道：'])
def test_every_inflation_literal_no_negation_or_quote_exemption(case,word,prefix):
 p=case[3];error(ev.alter(single(p),text=prefix+word+'。'),p,'analysis_unsupported_experience')
@pytest.mark.parametrize('word',ev.PROBABILITY)
def test_every_probability_literal(case,word):
 p=case[3];error(ev.alter(single(p),text=word),p,'analysis_forbidden_probability')
@pytest.mark.parametrize('word',ev.MARKERS)
def test_every_contract_marker(case,word):
 p=case[3];error(ev.alter(single(p),text=word+'。'),p,'analysis_text_contract_violation')
@pytest.mark.parametrize('text,code',[
 ('ＰＲＯＤＵＣＴＩＯＮ　ＥＸＰＥＲＩＥＮＣＥ。','analysis_unsupported_experience'),
 ('生产\n经验。','analysis_unsupported_experience'),('生 产 经 验。','analysis_unsupported_experience'),
 ('生\u200b产经验。','analysis_text_contract_violation'),('Python\u202e。','analysis_text_contract_violation'),
 ('Python\t。','analysis_text_contract_violation'),('Python；开发。','analysis_text_contract_violation'),
 ('Python。另句。','analysis_text_contract_violation'),('Python\u2028开发。','analysis_text_contract_violation'),
 ('Python;开发。','analysis_text_contract_violation'),('界'*81,'analysis_text_contract_violation')])
def test_unicode_control_split_sentence_and_length(case,text,code):
 p=case[3];error(ev.alter(single(p),text=text),p,code)

def test_eighty_unicode_not_bytes_and_no_rewriting(case):
 p=case[3];d=ev.alter(single(p),text='界'*79+'。');before=d.model_dump_json()
 assert validate_analysis(d,p,d.key)==d and d.model_dump_json()==before
@pytest.mark.parametrize('text',['简历未提及Docker使用经历。','简历未提及 Docker。','不会 Docker。','不能据此断言不会 Docker。','简历未提及 Docker 使用经历。 '])
def test_unknown_exact_contract(case,text):
 p=case[3];error(ev.alter(single(p,'Docker','unknown'),text=text),p,'analysis_text_contract_violation')
@pytest.mark.parametrize('kind',['skill_gap','match_interpretation'])
def test_unknown_structural_business_category(case,kind):
 p=case[3];error(ev.alter(single(p,'Docker','unknown'),kind=kind),p,'analysis_unknown_as_inability')
@pytest.mark.parametrize('subject',['python',' Python','Python ','Python 和 FastAPI','熟练度','生产经验'])
def test_subject_canonical_exact(case,subject):
 p=case[3];error(ev.alter(single(p),subject=subject),p,'analysis_requirement_kind_mismatch')

def test_later_rule_error_precedes_earlier_text_and_bad_reference(case):
 p=case[3];a=ev.alter(single(p),text='精通 Python。',evidence_ids=['e'+'0'*24]).claims[0]
 b=ev.claim(p,p.selected_keys[0],'Docker','unknown',number=2);b.requirement_kind='required'
 error(MatchAnalysisDraft(key=p.selected_keys[0],claims=[a,b]),p,'analysis_requirement_kind_mismatch')

def test_later_evidence_error_precedes_earlier_text(case):
 p=case[3];a=ev.alter(single(p),text='精通 Python。').claims[0]
 b=ev.claim(p,p.selected_keys[0],'Docker','unknown',number=2);b.evidence_ids=['e'+'0'*24]
 error(MatchAnalysisDraft(key=p.selected_keys[0],claims=[a,b]),p,'analysis_evidence_scope')

def test_unknown_checks_uncited_all_source_usage(case):
 p=case[3];d=single(p,'Python','unknown');assert all(next(e for e in p.evidence if e.evidence_id==i).kind!='resume' for i in d.claims[0].evidence_ids)
 error(d,p,'analysis_unknown_evidence_conflict')
@pytest.mark.parametrize('source,status,code',[
 ('计划学习 Docker。','unknown',None),('项目使用 Docker。','unknown','analysis_unknown_evidence_conflict'),
 ('从未使用 Docker。','unknown','analysis_unknown_evidence_conflict'),('也许用过 Docker。','unknown','analysis_unknown_evidence_conflict'),
 ('计划学习 Docker。','explicit_gap','analysis_gap_requires_negative_evidence'),
 ('从未使用 Docker。','explicit_gap',None),('个人说明：从未使用 Docker。','explicit_gap',None),
 ('本人未使用 Docker。','explicit_gap',None),('I have not used Docker.','explicit_gap',None),
 ('并非从未使用 Docker。','explicit_gap','analysis_gap_requires_negative_evidence'),
 ('如果从未使用 Docker。','explicit_gap','analysis_gap_requires_negative_evidence'),
 ('如果没有项目经验，从未使用 Docker。','explicit_gap','analysis_gap_requires_negative_evidence'),
 ('如果没有项目经验\n从未使用 Docker。','explicit_gap','analysis_gap_requires_negative_evidence'),
 ('同事说：从未使用 Docker。','explicit_gap','analysis_gap_requires_negative_evidence'),
 ('从未使用 Java，项目使用 Docker。','explicit_gap','analysis_gap_requires_negative_evidence'),
 ('从未使用 Java。项目使用 Docker。','explicit_gap','analysis_gap_requires_negative_evidence'),
 ('未来计划：未使用 Docker。','explicit_gap','analysis_gap_requires_negative_evidence'),
])
def test_full_original_context_negative_plan_ambiguity(case,source,status,code):
 p=case[3];key=p.selected_keys[0];d=single(p,'Docker','unknown')
 data=dict(kind='resume',path='document.pages[0].blocks[p001-b002]',key=None,source_sha256='a'*64,quote=source,resume_ref=EvidenceRef(page=1,block_id='p001-b002',quote=source),job_span=None,value_json=json.dumps({'block_text':source},ensure_ascii=False))
 e=EvidenceRecord(evidence_id='e'+canonical_hash({k:v.model_dump(mode='json') if hasattr(v,'model_dump') else v for k,v in data.items()})[:24],**data)
 p=p.model_copy(deep=True);p.evidence.append(e)
 if status=='explicit_gap':d=ev.alter(d,kind='skill_gap',status='explicit_gap',text='简历注明从未使用 Docker。',evidence_ids=d.claims[0].evidence_ids+[e.evidence_id])
 if code:error(d,p,code)
 else:assert validate_analysis(d,p,key)==d

def test_source_quote_without_block_context_cannot_establish_negative(case):
 p=case[3];d=single(p,'Docker','unknown');quote='从未使用 Docker。'
 ref=EvidenceRef(page=1,block_id='p001-b002',quote=quote)
 data=dict(kind='resume',path='profile.summary',key=None,source_sha256='a'*64,quote=quote,resume_ref=ref,job_span=None,value_json=json.dumps({'block_text':'同事说：从未使用 Docker。'},ensure_ascii=False))
 e=EvidenceRecord(evidence_id='e'+canonical_hash({k:v.model_dump(mode='json') if hasattr(v,'model_dump') else v for k,v in data.items()})[:24],**data)
 p=p.model_copy(deep=True);p.evidence.append(e)
 d=ev.alter(d,kind='skill_gap',status='explicit_gap',text='简历注明从未使用 Docker。',evidence_ids=d.claims[0].evidence_ids+[e.evidence_id])
 error(d,p,'analysis_gap_requires_negative_evidence')

@pytest.mark.parametrize('text',['精通 Python。','不代表生产经验。','Expert Python。'])
def test_advisory_also_checks_inflation(case,text):
 p=case[3];d=single(p);a=d.claims[0];c=a.model_dump();c.update(claim_id='c2',kind='recommendation',status='advisory',text=text,related_claim_ids=['c1'])
 error(MatchAnalysisDraft(key=d.key,claims=[a,AnalysisClaim.model_validate(c)]),p,'analysis_unsupported_experience')
@pytest.mark.parametrize('word',['必须','必需','必补','硬性','must','required'])
def test_optional_advice_even_negated_mandatory(case,word):
 p=case[3];d=single(p,'Docker','unknown');c=d.claims[0].model_dump();c.update(claim_id='c2',kind='recommendation',status='advisory',text=f'无需视为{word}。',related_claim_ids=['c1'])
 error(MatchAnalysisDraft(key=d.key,claims=[d.claims[0],AnalysisClaim.model_validate(c)]),p,'analysis_optional_as_mandatory')

def test_selected_rule_kind_not_any_other_same_subject_kind(tmp_path):
 _,_,_,p=ev.make_case(tmp_path/'multi',descriptions=['要求掌握 Python。Python 加分。'])
 d=single(p,rule_kind='bonus');d=ev.alter(d,requirement_kind='required')
 error(d,p,'analysis_requirement_kind_mismatch')

def test_missing_or_cross_subject_rule(case):
 p=case[3];d=single(p);without=[i for i in d.claims[0].evidence_ids if next(e for e in p.evidence if e.evidence_id==i).kind!='rule']
 error(ev.alter(d,evidence_ids=without),p,'analysis_invalid_rule_reference')
 wrong=ev.rule_ref(p,d.key,'Docker')[1]
 error(ev.alter(d,evidence_ids=without+[wrong.evidence_id]),p,'analysis_invalid_rule_reference')

@pytest.mark.parametrize('mutation',['quote','value_json','source_sha256','resume_ref'])
def test_packet_inplace_mutation_caught(case,mutation):
 p=case[3].model_copy(deep=True);d=single(p);e=next(e for e in p.evidence if e.kind=='resume')
 if mutation=='quote':e.__dict__['quote']='changed'
 elif mutation=='value_json':e.__dict__['value_json']='{}'
 elif mutation=='source_sha256':e.__dict__['source_sha256']='0'*64
 else:e.resume_ref.__dict__['page']=2
 error(d,p,'analysis_invalid_packet')

def test_recomputed_content_id_still_rejected_by_original_rebuild(case):
 doc,req,result,p=case;p=p.model_copy(deep=True);index=next(i for i,e in enumerate(p.evidence) if e.kind=='scope');data=p.evidence[index].model_dump();data['value_json']='{}';data['evidence_id']='e'+canonical_hash({k:v for k,v in data.items() if k!='evidence_id'})[:24];p.evidence[index]=EvidenceRecord.model_validate(data)
 with pytest.raises(ReportError,match='evidence_packet_mismatch'):validate_evidence_packet(p,req,result,doc)

@pytest.mark.parametrize('old',['match-analysis-prompt-v1','match-analysis-prompt-v2'])
def test_history_keeps_non_v3_text_and_rejects_current_certification(case,old):
 doc,req,result,p=case;r=generate_match_report(req,result,doc,report_id='history',generated_at=datetime(2026,11,16,tzinfo=timezone.utc),client=SyntheticReportClient())
 data=r.model_dump(mode='json');data['prompt_version']=old;data['analyses'][0]['draft']['claims'][0]['text']='旧限定：不代表生产经验。';h=MatchReport.model_validate(data);before=h.model_dump_json()
 assert '历史只读，不代表已通过v3' in render_match_report(h);assert h.model_dump_json()==before
 with pytest.raises(ReportError,match='analysis_validation_policy_mismatch'):validate_report(h,require_current_policy=True)
 h.analyses[0].draft.claims[0].evidence_ids=['e'+'0'*24]
 with pytest.raises(ReportError,match='analysis_evidence_scope'):validate_report(h)

def test_frozen_prompt_bytes_and_schema_scope():
 path=Path(__file__).parents[1]/'docs/STAGE4_V3_SYSTEM_PROMPT.txt';assert SYSTEM_PROMPT==path.read_text()
 from job_radar.report_models import PROMPT_VERSION, REPORT_VERSION
 assert PROMPT_VERSION=='match-analysis-prompt-v3' and REPORT_VERSION=='match-report-v1'
 assert set(AnalysisClaim.model_fields)=={'claim_id','kind','subject','status','text','requirement_kind','evidence_ids','related_claim_ids'}
 assert AnalysisClaim.model_json_schema()['properties']['text']['maxLength']==1600

@pytest.mark.parametrize('code',['analysis_unknown_evidence_conflict','analysis_text_contract_violation','analysis_text_sensitive_content','analysis_advice_requirement_mismatch','analysis_validation_policy_mismatch'])
def test_new_error_codes_whitelisted_once_halt_and_audit(case,code):
 p=case[3]
 class Fake:
  calls=0;halted=None
  def complete(self,request):self.calls+=1;raise ReportError(code)
  def halt(self,code):self.halted=code
 c=Fake();out=analyze_job(p,p.selected_keys[0],client=c)
 assert out.error_code==code and out.draft is None and c.calls==1 and c.halted=='analysis_validation_failed'


def test_evaluation_dual_labels_nine_jobs_and_not_run(tmp_path):
 result=ev.evaluate(tmp_path/'evaluation')
 assert result['gate_passed']
 assert result['labels']['policy']['FP']==result['labels']['policy']['FN']==0
 assert result['labels']['semantic']['FP']>0
 assert len(result['samples'])==9 and all(s['real_status']=='not_run' and s['offline_error'] is None for s in result['samples'])
 assert all(s['finding_coverage']==1 and s['unsupported_added_claims']==0 for s in result['samples'])
 assert result['real_calls']==result['credential_reads']==0 and result['supplier_completion_rate'] is None
 assert all(r['layers']['text']=='not_evaluated' for r in result['rows'] if r['first_error_layer'] in ['structure','rule','evidence'])
 assert result['by_expected_layer']['structure']['policy']['FPR'] is None
 assert result['sample_acceptance']['accepted']==result['sample_acceptance']['total']==9
 assert result['sample_acceptance']['rate']==1
 assert result['finding_summary']['expected_count']==result['finding_summary']['matched_count']==24
 assert result['finding_summary']['coverage']==1 and result['finding_summary']['unsupported_added_claims']==0
 for layer in ev.LAYERS:
  excluded=sum(r['layers'][layer]=='not_evaluated' for r in result['rows'])
  for label in ('semantic','policy'):
   values=result['per_layer_evaluated'][layer][label]
   assert values['excluded_not_evaluated']==excluded
   assert sum(values[k] for k in ('TP','FP','FN','TN'))==values['evaluated']==len(result['rows'])-excluded
 assert result['per_layer_evaluated']['structure']['policy']['TP']==4
 assert result['per_layer_evaluated']['text']['semantic']['FP']==36

@pytest.mark.parametrize('field,mutate',[
 ('duplicate_id',lambda d:d['claims'].append(dict(d['claims'][0]))),
 ('duplicate_ref',lambda d:d['claims'][0]['evidence_ids'].append(d['claims'][0]['evidence_ids'][0])),
 ('self_related',lambda d:d['claims'][0].update(related_claim_ids=['c1'])),
 ('unknown_explicit',lambda d:d['claims'][0].update(kind='unknown',status='explicit_gap')),
 ('extra_field',lambda d:d['claims'][0].update(score=10)),
])
def test_structure_mutations_first_fixed_schema(case,field,mutate):
 p=case[3];obj=single(p).model_dump();mutate(obj)
 with pytest.raises(ReportError,match='analysis_schema_error'):parse_analysis(json.dumps(obj,ensure_ascii=False))

@pytest.mark.parametrize('text',['person@example.com','https://example.org','/Users/alice/private.txt','C:\\Users\\alice\\secret.pdf','13812345678'])
def test_sensitive_output_rejection_not_silent_cleaning(case,text):
 p=case[3];d=single(p);base=d.claims[0].model_dump();base.update(claim_id='c2',kind='recommendation',status='advisory',text=text,related_claim_ids=['c1']);draft=MatchAnalysisDraft(key=d.key,claims=[d.claims[0],AnalysisClaim.model_validate(base)])
 code=ev.decision(draft,p,d.key)
 # URL/email/file punctuation may hit single-sentence contract first, but an
 # otherwise single-token phone reaches the dedicated sensitive-content code.
 assert code in {'analysis_text_contract_violation','analysis_text_sensitive_content'}
 if text=='13812345678':assert code=='analysis_text_sensitive_content'
 assert draft.claims[1].text==text

def test_source_contains_inflation_not_output_exemption(tmp_path):
 _,_,_,p=ev.make_case(tmp_path/'inflated',text='课程项目：使用 Python 和 FastAPI 开发生产系统。')
 d=single(p);error(ev.alter(d,text='课程项目使用 Python 开发生产系统。'),p,'analysis_unsupported_experience')

def test_advice_category_mismatch_same_subject_with_two_rule_kinds(tmp_path):
 _,_,_,p=ev.make_case(tmp_path/'advice',descriptions=['要求掌握 Python。Python 加分。'])
 d=single(p,rule_kind='required');c=ev.claim(p,d.key,'Python','supported',number=2,rule_kind='bonus').model_dump();c.update(kind='recommendation',status='advisory',text='可补充 Python 使用记录。',related_claim_ids=['c1'])
 error(MatchAnalysisDraft(key=d.key,claims=[d.claims[0],AnalysisClaim.model_validate(c)]),p,'analysis_advice_requirement_mismatch')

@pytest.mark.parametrize('code,change',[
 ('analysis_unknown_evidence_conflict',dict(subject='Python',status='unknown',kind='unknown',text='简历未提及 Python 使用经历。')),
 ('analysis_text_contract_violation',dict(text='界'*81)),
 ('analysis_unsupported_experience',dict(text='不代表生产经验。')),
])
def test_rejection_sidecar_preserves_new_policy_body_and_usage(case,code,change):
 from job_radar.report_models import CallAudit
 p=case[3];d=ev.alter(single(p,'Python','unknown') if code=='analysis_unknown_evidence_conflict' else single(p),**change);raw=d.model_dump_json(indent=2);events=[]
 class Fake:
  calls=0;halted=None
  def complete(self,request):
   self.calls+=1
   return AnalysisResponse(status='completed',text=raw,audit=CallAudit(provider='authored',model='no-model',attempts=1,input_tokens=2,output_tokens=3,total_tokens=5,reserved_units='0.1',billing_unit='CNY'))
  def halt(self,code):self.halted=code
 c=Fake();out=analyze_job(p,d.key,client=c,rejection_sink=events.append)
 assert c.calls==1 and out.error_code==code and out.draft is None and out.audit.total_tokens==5 and out.audit.reserved_units=='0.1'
 assert events[0].raw_draft_text==raw and events[0].parsed_draft==d.model_dump(mode='json')
 assert 'raw_draft_text' not in out.model_dump_json() and c.halted=='analysis_validation_failed'


def test_plan_not_supported_and_sql_negative_not_unknown(tmp_path):
 doc,req,result,p=ev.make_case(tmp_path/'s3',ev.SAMPLES['S3'])
 key=next(r.key for r in result.matching.results if r.key.job_id==1)
 d=MatchAnalysisDraft(key=key,claims=[ev.claim(p,key,'Docker','unknown')]);d=ev.alter(d,kind='strength',status='supported',text='已使用 Docker。')
 error(d,p,'analysis_unsupported_positive')
 key=next(r.key for r in result.matching.results if r.key.job_id==3)
 d=MatchAnalysisDraft(key=key,claims=[ev.claim(p,key,'SQL','unknown')]);error(d,p,'analysis_unknown_evidence_conflict')


def test_finding_coverage_drops_when_frozen_expected_finding_missing():
 expected=ev.EXPECTED['S1'][1]
 measured=ev.finding_metrics(expected,expected[:-1])
 assert measured['finding_coverage']==pytest.approx(2/3)
 assert measured['expected_count']==3 and measured['matched_count']==2
 assert measured['unsupported_added_claims']==0
 assert measured['missing_findings']==[['Docker','unknown','bonus']]


def test_finding_added_without_frozen_expectation_counts_unsupported():
 expected=ev.EXPECTED['S1'][1]
 measured=ev.finding_metrics(expected,expected+[['Rust','supported','required']])
 assert measured['finding_coverage']==1 and measured['unsupported_added_claims']==1
 assert measured['unexpected_findings']==[['Rust','supported','required']]
