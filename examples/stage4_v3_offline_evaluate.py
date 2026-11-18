"""Authored offline v3 evaluation, not real model quality or paid acceptance.

Run with PYTHONPATH=src python examples/stage4_v3_offline_evaluate.py OUTPUT_DIR.
No provider is constructed; inputs/claims below are deliberately authored fixtures.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import tempfile

from job_radar.report_analysis import validate_analysis, build_analysis_request
from job_radar.report_core import parse_analysis
from job_radar.report_evidence import build_evidence_packet
from job_radar.report_models import AnalysisClaim, MatchAnalysisDraft, ReportError
from job_radar.report_synthetic import synthetic_inputs, synthetic_manifest
from job_radar.rag_engine import match_jobs_indexed
from job_radar.rag_index import DerivedIndex
from job_radar.rag_models import canonical_hash

POOL=['要求掌握 Python 和 FastAPI。熟悉 Docker 加分。','要求掌握 Python 或 Java。熟悉 Git 加分。','要求掌握 Python 和 SQL。熟悉 Redis 加分。']
SAMPLES={
 'S1':'课程项目：使用 Python 和 FastAPI 开发接口。',
 'S2':'个人项目：使用 Python 和 Git 编写命令行工具。',
 'S3':'课程项目：使用 Python 和 FastAPI 编写示例接口。学习计划：计划学习 Docker。个人说明：从未使用 SQL。',
}
# Independently frozen manual expectations, not generated from current rules.
EXPECTED={
 'S1':{1:[['Python','supported','required'],['FastAPI','supported','required'],['Docker','unknown','bonus']],2:[['Python','supported','required'],['Git','unknown','bonus']],3:[['Python','supported','required'],['SQL','unknown','required'],['Redis','unknown','bonus']]},
 'S2':{1:[['Python','supported','required'],['FastAPI','unknown','required'],['Docker','unknown','bonus']],2:[['Python','supported','required'],['Git','supported','bonus']],3:[['Python','supported','required'],['SQL','unknown','required'],['Redis','unknown','bonus']]},
 'S3':{1:[['Python','supported','required'],['FastAPI','supported','required'],['Docker','unknown','bonus']],2:[['Python','supported','required'],['Git','unknown','bonus']],3:[['Python','supported','required'],['SQL','explicit_gap','required'],['Redis','unknown','bonus']]},
}
INFLATION=['精通','熟练','专家','资深','生产经验','生产环境经验','正式工作','生产系统','expert','proficient','senior','production experience','production system','三年经验','2年开发经验','3 years of experience']
PROBABILITY=['80%','百分之八十','录用概率','胜任率','胜任概率','成功率','可信度','置信度','hire probability','hiring probability','chance of hiring','chance of being hired','confidence']
MARKERS=['匹配度高','匹配度低','高度匹配','胜任','满足岗位要求','能力不足','不代表','不等于','不等同于','不意味着','不能据此','仅说明','限于','不足以','无法确定','尚不能','does not imply','not equivalent']
LAYERS=['structure','rule','evidence','text']
CODE_LAYER={**{c:'structure' for c in ['analysis_output_limit','analysis_invalid_json','analysis_schema_error','analysis_key_mismatch']},**{c:'rule' for c in ['analysis_requirement_kind_mismatch','analysis_unknown_as_inability','analysis_alternative_already_supported','analysis_advice_requirement_mismatch']}}
def code_layer(code):
 if code is None:return None
 return CODE_LAYER.get(code,'text' if code in ['analysis_forbidden_probability','analysis_unsupported_experience','analysis_optional_as_mandatory','analysis_text_contract_violation','analysis_text_sensitive_content'] else 'evidence')

def make_case(directory,text=SAMPLES['S1'],descriptions=None):
 doc,req=synthetic_inputs(text=text,descriptions=POOL if descriptions is None else descriptions)
 if text==SAMPLES['S3']:
  # Frozen S3 is three distinct original blocks, not a single context that
  # lets a future/negative marker suppress a separate positive course fact.
  from job_radar.resume_models import ResumeBlock, ResumePage
  from job_radar.resume_profile_rules import build_rule_profile
  from job_radar.matching_input import build_matching_input, build_matching_request
  from job_radar.matching_models import SearchPreferences
  blocks=[ResumeBlock(block_id=f'p001-b00{i}',text=part,bbox=(10.0,float(i*40),500.0,float(i*40+20))) for i,part in enumerate(['课程项目：使用 Python 和 FastAPI 编写示例接口。','学习计划：计划学习 Docker。','个人说明：从未使用 SQL。'],1)]
  doc=doc.model_copy(update={'pages':[ResumePage(page_number=1,width=600.0,height=800.0,blocks=blocks)]})
  profile=build_matching_input(build_rule_profile(doc,resume_id='synthetic-stage4'),preferences=SearchPreferences())
  req=build_matching_request(profile,req.jobs,purpose='debug')
 idx=DerivedIndex(Path(directory));idx.update(req.jobs,manifest=synthetic_manifest(req.jobs))
 result=match_jobs_indexed(req,index=idx);packet=build_evidence_packet(req,result,doc)
 return doc,req,result,packet

def rule_ref(packet,key,subject,kind=None):
 row=next(r for r in packet.retrieval.matching.results if r.key==key)
 rule=next(s for s in row.rules.skills if subject in s.alternatives and (kind is None or s.kind==kind))
 record=next(e for e in packet.evidence if e.kind=='rule' and e.key==key and json.loads(e.value_json)==rule.model_dump(mode='json'))
 return rule,record

def claim(packet,key,subject,status,*,number=1,text=None,kind=None,rule_kind=None):
 rule,ref=rule_ref(packet,key,subject,rule_kind)
 jd=next(e for e in packet.evidence if e.kind=='job' and e.key==key and e.path=='job.description')
 ids=[jd.evidence_id,ref.evidence_id]
 if status=='supported':
  source=next(e for e in packet.evidence if e.kind=='resume' and e.resume_ref in rule.resume_evidence)
  ids.append(source.evidence_id)
  default=f'课程项目使用 {subject}。'
 elif status=='unknown':
  ids.append(next(e.evidence_id for e in packet.evidence if e.kind=='scope'));default=f'简历未提及 {subject} 使用经历。'
 else:
  source=next(e for e in packet.evidence if e.kind=='resume' and f'从未使用 {subject}' in (e.quote or ''))
  ids.append(source.evidence_id);default=f'简历注明从未使用 {subject}。'
 return AnalysisClaim(claim_id=f'c{number}',kind=kind or {'supported':'strength','unknown':'unknown','explicit_gap':'skill_gap'}[status],subject=subject,status=status,text=text or default,requirement_kind=rule.kind,evidence_ids=ids)

def alter(draft,**changes):
 d=draft.model_dump();d['claims'][0].update(changes);return MatchAnalysisDraft.model_validate(d)

def decision(draft,packet,key):
 try:
  if isinstance(draft,str):draft=parse_analysis(draft)
  validate_analysis(draft,packet,key)
 except ReportError as e:return e.code
 return None

def metrics(rows,label):
 out=dict(TP=0,FP=0,FN=0,TN=0)
 for row in rows:
  expected=row[label];rejected=row['error_code'] is not None
  out['TP' if expected and rejected else 'FN' if expected else 'FP' if rejected else 'TN']+=1
 out['FPR']=out['FP']/(out['FP']+out['TN']) if out['FP']+out['TN'] else None
 out['FNR']=out['FN']/(out['TP']+out['FN']) if out['TP']+out['FN'] else None
 return out

def finding_metrics(expected, actual):
 """Compare against independently frozen expectations, never current rule output."""
 wanted={tuple(item) for item in expected};found={tuple(item) for item in actual}
 matched=wanted & found
 return dict(expected_count=len(wanted), actual_count=len(found), matched_count=len(matched),
  finding_coverage=len(matched)/len(wanted) if wanted else None,
  unsupported_added_claims=len(found-wanted),
  missing_findings=[list(item) for item in sorted(wanted-found)],
  unexpected_findings=[list(item) for item in sorted(found-wanted)])

def layer_metrics(rows, layer, label):
 """Actual evaluated-layer confusion matrix; later unrun checks are excluded.

 Per-layer manual label is expected_layer == layer AND that label's should-
 reject flag. A sample expected to fail another layer is not an attack on this
 layer. Actual rejection means the observed first error is in this layer.
 """
 entered=[row for row in rows if row['layers'][layer]!='not_evaluated']
 projected=[{'expected_here':row['expected_layer']==layer and row[label],
             'error_code':row['error_code'] if row['first_error_layer']==layer else None}
            for row in entered]
 return dict(evaluated=len(entered), excluded_not_evaluated=len(rows)-len(entered),
             **metrics(projected,'expected_here'))

def evaluate(directory):
 output=Path(directory);output.mkdir(parents=True,exist_ok=False)
 rows=[];sample_rows=[]
 with tempfile.TemporaryDirectory(prefix='jobradar-v3-offline-') as tmp:
  _,_,_,packet=make_case(Path(tmp)/'attacks');key=packet.selected_keys[0]
  base=MatchAnalysisDraft(key=key,claims=[claim(packet,key,'Python','supported')])
  unknown=MatchAnalysisDraft(key=key,claims=[claim(packet,key,'Docker','unknown')])
  def add(name,group,draft,expected,semantic,layer,expected_code=None):
   code=decision(draft,packet,key);first=code_layer(code)
   states={l:('not_evaluated' if first and LAYERS.index(l)>LAYERS.index(first) else 'rejected' if l==first else 'passed') for l in LAYERS}
   rows.append(dict(name=name,group=group,source='authored_fixture',expected_policy_reject=expected,expected_semantic_reject=semantic,expected_layer=layer,error_code=code,first_error_layer=first,layers=states,expected_code=expected_code,matched_expectation=(bool(code)==expected and (expected_code is None or code==expected_code))))
  add('supported-short','positive',base,False,False,'text');add('unknown-short','positive',unknown,False,False,'text')
  for word in INFLATION:
   for prefix,semantic in [('',True),('不代表',False)]:
    add(prefix+word,'inflation',alter(base,text=prefix+word+'。'),True,semantic,'text','analysis_unsupported_experience')
  for word in PROBABILITY:add(word,'probability',alter(base,text=word),True,True,'text','analysis_forbidden_probability')
  for word in MARKERS:add(word,'contract',alter(base,text=word+'。'),True,word in {'匹配度高','匹配度低','高度匹配','胜任','满足岗位要求','能力不足'},'text','analysis_text_contract_violation')
  for text in ['生 产 经 验。','ＰＲＯＤＵＣＴＩＯＮ　ＥＸＰＥＲＩＥＮＣＥ。','PROFICIENT。','生产\n经验。']:
   add(text,'obfuscation',alter(base,text=text),True,True,'text','analysis_unsupported_experience')
  for text in ['生\u200b产经验。','Python\u202e。','Python\t。','Python；开发。','Python。另句。','界'*81]:
   add(repr(text),'contract',alter(base,text=text),True,False,'text','analysis_text_contract_violation')
  add('wrong-kind','rule',alter(base,requirement_kind='bonus'),True,True,'rule','analysis_requirement_kind_mismatch')
  add('unknown-as-gap','rule',alter(unknown,kind='skill_gap'),True,True,'rule','analysis_unknown_as_inability')
  add('invalid-id','evidence',alter(base,evidence_ids=['e'+'0'*24]),True,True,'evidence','analysis_evidence_scope')
  add('existing-use-unknown','evidence',MatchAnalysisDraft(key=key,claims=[claim(packet,key,'Python','unknown')]),True,True,'evidence','analysis_unknown_evidence_conflict')
  add('unknown-as-inability','contract',alter(unknown,text='不会 Docker。'),True,True,'text','analysis_text_contract_violation')
  for raw in ['{bad','{"key":1,"key":2}','{"x":NaN}','```json\n{}\n```']:
   add(raw,'structure',raw,True,True,'structure','analysis_invalid_json')
  # Independent historical labels: old negated limitations have no positive
  # experience assertion, even though deliberately rejected by the v3 contract.
  fixture=Path(__file__).resolve().parents[1]/'tests/fixtures/stage4_negation'
  retained=MatchAnalysisDraft.model_validate_json((fixture/'real-v2-rejected-draft.json').read_text())
  rp=__import__('job_radar.report_models',fromlist=['EvidencePacket']).EvidencePacket.model_validate_json((fixture/'s1-j1-packet.json').read_text())
  for i in [0,1]:
   d=MatchAnalysisDraft(key=retained.key,claims=[retained.claims[i]]);code=decision(d,rp,d.key)
   rows.append(dict(name=f'historical-c{i+1}',group='historical-isolated',source='retained_real_response',expected_policy_reject=True,expected_semantic_reject=False,expected_layer='text',error_code=code,first_error_layer=code_layer(code),layers={l:'rejected' if l=='text' else 'passed' for l in LAYERS},expected_code='analysis_unsupported_experience',matched_expectation=code=='analysis_unsupported_experience'))
  for sample,text in SAMPLES.items():
   doc,req,result,p=make_case(Path(tmp)/sample,text)
   for rank in result.matching.results:
    key=rank.key;claims=[];expected=EXPECTED[sample][key.job_id]
    for rule in rank.rules.skills:
     # OR selects its supported branch; unused Java is not an additional gap.
     subject=rule.matched[0] if rule.status=='evidence_found' else rule.alternatives[0]
     status='supported' if rule.status=='evidence_found' else 'explicit_gap' if sample=='S3' and subject=='SQL' else 'unknown'
     fact=claim(p,key,subject,status,number=len(claims)+1,rule_kind=rule.kind)
     if status=='supported':fact.text=('个人项目' if sample=='S2' else '课程项目')+f'使用 {subject}。'
     claims.append(fact)
    draft=MatchAnalysisDraft(key=key,claims=claims);code=decision(draft,p,key)
    request=build_analysis_request(p,key)
    actual=[[c.subject,c.status,c.requirement_kind] for c in claims]
    finding_result=finding_metrics(expected,actual)
    sample_rows.append(dict(sample=sample,job_id=key.job_id,rank=rank.rank,source='authored_fixture',real_status='not_run',offline_error=code,expected_findings=expected,actual_findings=actual,**finding_result,status_distribution=dict(Counter(c.status for c in claims)),packet_sha256=canonical_hash(p),wire_sha256=hashlib.sha256(request.packet_json.encode()).hexdigest(),draft=draft.model_dump(mode='json')))
 result=dict(schema_version='stage4-v3-offline-evaluation-v1',real_calls=0,credential_reads=0,new_cost_cny='0',real_samples_status={s:'not_run' for s in SAMPLES},supplier_completion_rate=None,source='authored fixtures and retained raw; not general semantic accuracy',labels={'semantic':metrics(rows,'expected_semantic_reject'),'policy':metrics(rows,'expected_policy_reject')},rows=rows,samples=sample_rows)
 result['by_group']={g:{label:metrics([r for r in rows if r['group']==g],key) for label,key in [('semantic','expected_semantic_reject'),('policy','expected_policy_reject')]} for g in sorted({r['group'] for r in rows})}
 result['by_expected_layer']={l:{label:metrics([r for r in rows if r['expected_layer']==l],key) for label,key in [('semantic','expected_semantic_reject'),('policy','expected_policy_reject')]} for l in LAYERS}
 result['per_layer_evaluated']={l:{label:layer_metrics(rows,l,key) for label,key in [('semantic','expected_semantic_reject'),('policy','expected_policy_reject')]} for l in LAYERS}
 result['per_layer_label_definition']='expected_layer == layer AND expected_label_reject; actual rejection is first_error_layer == layer; not_evaluated excluded from all four counts'
 accepted=sum(s['offline_error'] is None for s in sample_rows)
 result['sample_acceptance']=dict(accepted=accepted,total=len(sample_rows),rate=accepted/len(sample_rows) if sample_rows else None,source='authored_fixture_not_real_model')
 wanted=sum(s['expected_count'] for s in sample_rows);matched=sum(s['matched_count'] for s in sample_rows)
 result['finding_summary']=dict(expected_count=wanted,actual_count=sum(s['actual_count'] for s in sample_rows),matched_count=matched,coverage=matched/wanted if wanted else None,unsupported_added_claims=sum(s['unsupported_added_claims'] for s in sample_rows))
 result['first_error_distribution']=dict(Counter(r['error_code'] or 'accepted' for r in rows));result['runtime_errors']=[]
 result['policy_conservative_cost']=[r['name'] for r in rows if not r['expected_semantic_reject'] and r['error_code']]
 result['policy_false_negatives']=[r['name'] for r in rows if r['expected_policy_reject'] and not r['error_code']]
 result['policy_false_positives']=[r['name'] for r in rows if not r['expected_policy_reject'] and r['error_code']]
 result['semantic_false_negatives']=[r['name'] for r in rows if r['expected_semantic_reject'] and not r['error_code']]
 result['cross_table']=dict(Counter(f"semantic_reject={r['expected_semantic_reject']},policy_reject={r['expected_policy_reject']},actual_reject={bool(r['error_code'])}" for r in rows))
 result['gate_passed']=all(r['matched_expectation'] for r in rows) and len(sample_rows)==9 and all(s['offline_error'] is None and s['finding_coverage']==1 and s['unsupported_added_claims']==0 for s in sample_rows)
 jp=output/'evaluation.json';mp=output/'evaluation.md';jp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 lines=['# v3 离线双标签评估','', '人工 authored fixture 与历史 raw；真实模型调用 0，费用 0；S1/S2/S3 真实验收均 not_run。','',f"- gate_passed: {result['gate_passed']}",f"- 语义标签：{result['labels']['semantic']}",f"- 协议标签：{result['labels']['policy']}",'- 分母 0 以 null/unknown 表示；保守 policy 拒绝不冒称语义误报归零。','- 后层未执行标 not_evaluated；有限攻击集成绩不外推一般准确率。','','## 逐项首错','|名称|组|语义应拒|协议应拒|实际首错|','|---|---|---|---|---|']
 lines += [f"|{r['name'].replace(chr(10),' ')}|{r['group']}|{r['expected_semantic_reject']}|{r['expected_policy_reject']}|{r['error_code'] or 'accepted'}|" for r in rows]
 lines += ['','## 实际逐层统计（排除未执行）','层标签=expected_layer等于本层且对应人工应拒；actual=首错发生在本层。未执行后层不计TP/FP/FN/TN。','|层|标签|实际进入|未执行排除|TP|FP|FN|TN|','|---|---|---|---|---|---|---|---|']
 lines += [f"|{layer}|{label}|{values['evaluated']}|{values['excluded_not_evaluated']}|{values['TP']}|{values['FP']}|{values['FN']}|{values['TN']}|" for layer,labels in result['per_layer_evaluated'].items() for label,values in labels.items()]
 lines += ['',f"- 九岗离线接纳：{result['sample_acceptance']}",f"- 独立期望finding汇总：{result['finding_summary']}"]
 lines += ['','## 三份样本九岗（人工短事实）','|样本|岗位|原排名|离线结果|预期 finding 覆盖|真实状态|','|---|---|---|---|---|---|']+[f"|{s['sample']}|{s['job_id']}|{s['rank']}|{s['offline_error'] or 'accepted'}|{s['finding_coverage']}|not_run|" for s in sample_rows]
 mp.write_text('\n'.join(lines)+'\n');assert json.loads(jp.read_text())==result
 return result

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('output');args=parser.parse_args();result=evaluate(args.output)
 print(json.dumps({'gate_passed':result['gate_passed'],'policy':result['labels']['policy'],'semantic':result['labels']['semantic'],'samples':len(result['samples']),'real_calls':0},ensure_ascii=False));raise SystemExit(0 if result['gate_passed'] else 1)
