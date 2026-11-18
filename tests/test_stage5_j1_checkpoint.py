"""J1 prepare/explicit ranking approval/one-shot report; entirely offline."""
import importlib.util
import json
from pathlib import Path
import threading
import httpx2
import pytest
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
s1=module('j1_test_s1',ROOT/'examples/stage5_s1_checkpoint.py')
j1=module('j1_checkpoint',ROOT/'examples/stage5_j1_checkpoint.py')
ev=module('j1_test_v3',ROOT/'examples/stage4_v3_offline_evaluate.py')


def response(text):
    return {'model':'gpt-6.1-sol','status':'completed','output':[{'type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':text}]}],
        'usage':{'input_tokens':1000,'output_tokens':100,'total_tokens':1100}}


def prepare_s1(tmp_path,monkeypatch):
    from test_resume_structure import empty,skill,fact
    import job_radar.api_v1_live as live
    root=tmp_path/'s1';s1.prepare(root)
    tech=[skill('Python'),skill('FastAPI')]
    for s in tech:s['context']='project'
    draft=empty(skills=tech,projects=[{'name':None,'role':None,'nature':fact('课程项目'),
        'start_date':None,'end_date':None,'description':[fact('使用 Python 和 FastAPI 开发接口。')],'technologies':tech}])
    monkeypatch.setattr(live.httpx2,'HTTPTransport',lambda **kwargs:httpx2.MockTransport(lambda request:httpx2.Response(200,json=response(json.dumps(draft,ensure_ascii=False)))))
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-j1-setup')
    assert s1.execute(root)==0
    return root


def authored_draft(root):
    from job_radar.report_models import EvidencePacket,MatchAnalysisDraft
    packet=EvidencePacket.model_validate_json((root/'evidence.json').read_text());key=packet.selected_keys[0]
    return MatchAnalysisDraft(key=key,claims=[ev.claim(packet,key,'Python','supported',number=1),
        ev.claim(packet,key,'FastAPI','supported',number=2),ev.claim(packet,key,'Docker','unknown',number=3)])


def test_prepare_freezes_ranking_drift_without_paid_authorization(tmp_path,monkeypatch):
    s=prepare_s1(tmp_path,monkeypatch);root=tmp_path/'j1';before=json.loads((s/'api-store/state.json').read_text())['content']['billing']
    m=j1.prepare(s,root)
    assert j1.check_files(s,root)==m
    review=json.loads((root/'RANKING_REVIEW.json').read_text())
    assert review['status']=='ranking_changed_requires_human_confirmation' and review['j1_still_first']
    assert [r['key']['job_id'] for r in review['old_ranking']]==[1,2,3]
    assert [r['key']['job_id'] for r in review['new_ranking']]==[1,3,4,2]
    assert '开发接口' in review['new_query']['text']
    assert not m['authorization_appended'] and m['input_allocation']<=20000
    after=json.loads((s/'api-store/state.json').read_text())['content']['billing']
    assert before==after
    with pytest.raises(AssertionError):j1.execute(s,root)
    assert not (root/'J1_LIVE_STARTED').exists()
    with pytest.raises(AssertionError):j1.authorize(s,root,'0'*64)
    assert before==json.loads((s/'api-store/state.json').read_text())['content']['billing']


@pytest.mark.parametrize('outcome',['success','timeout','bad_json','bad_draft','sidecar','result_write','status_during_call'])
def test_report_checkpoint_single_call_same_ledger_stop_and_known_usage(tmp_path,monkeypatch,outcome):
    import os
    import job_radar.api_v1_live as live
    s=prepare_s1(tmp_path,monkeypatch);root=tmp_path/'j1';m=j1.prepare(s,root)
    j1.authorize(s,root,m['ranking_review_sha256'])
    calls=[];started=threading.Event();release=threading.Event();draft=authored_draft(root)
    def handler(request):
        calls.append(request);started.set()
        state=json.loads((s/'api-store/state.json').read_text())['content']['billing']['totals']
        assert state['new_attempts']==2
        if outcome=='status_during_call':assert release.wait(5)
        if outcome=='timeout':raise httpx2.ReadTimeout('synthetic',request=request)
        if outcome=='bad_json':return httpx2.Response(200,text='{invalid')
        return httpx2.Response(200,json=response('invalid draft' if outcome=='bad_draft' else draft.model_dump_json()))
    monkeypatch.setattr(live.httpx2,'HTTPTransport',lambda **kwargs:httpx2.MockTransport(handler))
    orig=j1.write
    def bad_write(path,value):
        if Path(path).name=={'sidecar':'match-report.json','result_write':'RESULT.json'}.get(outcome):raise OSError('synthetic sidecar')
        return orig(path,value)
    monkeypatch.setattr(j1,'write',bad_write)
    original_get=TestClient.get
    def bad_get(self,url,*args,**kwargs):
        if outcome=='status_during_call' and str(url).startswith('/v1/reports/'):
            assert started.wait(5);release.set();raise RuntimeError('synthetic status')
        return original_get(self,url,*args,**kwargs)
    monkeypatch.setattr(TestClient,'get',bad_get);monkeypatch.setenv('OPENAI_API_KEY','synthetic-j1-execute')
    rc=j1.execute(s,root)
    assert rc==(0 if outcome=='success' else 2)
    assert len(calls)==1 and (root/'J1_LIVE_STARTED').exists() and 'OPENAI_API_KEY' not in os.environ
    after=json.loads((root/'billing-after.json').read_text())
    if outcome=='result_write':
        assert after['halted'] and after['new_attempts']==2 and after['actual_cny']=='0.05749458'
        assert after['inflight'] is None and (root/'provider-response-1.json').exists() and (root/'report-resource.json').exists()
        with pytest.raises(FileExistsError):j1.execute(s,root)
        return
    r=json.loads((root/'RESULT.json').read_text())
    assert r['generation_attempts']==2 and r['new_generation_attempts']==1 and r['profile_requests']==0 and r['report_requests']==1
    assert after['retained_reserve_cny']=='0.8029632' and after['new_attempts']==2
    assert r['halted']==(outcome!='success') and r['usage_known']==(outcome not in ['timeout','bad_json'])
    assert r['actual_usage_cost_cny']==('0.00214500' if r['usage_known'] else None)
    if outcome=='success':
        assert r['review']['status']=='passed' and '本报告包含真实模型响应' in (root/'match-report.md').read_text()
        assert r['task_state']=='succeeded'
    if outcome in ['sidecar','status_during_call']:assert r['task_state']=='succeeded' and after['inflight'] is None
    with pytest.raises(FileExistsError):j1.execute(s,root)
    assert len(calls)==1


def test_authorization_diagnostic_failure_halts_without_generation(tmp_path,monkeypatch):
    s=prepare_s1(tmp_path,monkeypatch);root=tmp_path/'j1';m=j1.prepare(s,root)
    original=j1.write
    def fault(path,value):
        if Path(path).name=='billing-authorized.json':raise OSError('synthetic authorization sidecar')
        return original(path,value)
    monkeypatch.setattr(j1,'write',fault)
    with pytest.raises(OSError):j1.authorize(s,root,m['ranking_review_sha256'])
    totals=json.loads((s/'api-store/state.json').read_text())['content']['billing']['totals']
    assert totals['halted'] and totals['new_attempts']==1 and totals['actual_cny']=='0.05534958'
    assert not (root/'J1_LIVE_STARTED').exists()


def test_fixed_refreeze_rejects_new_order_even_with_refrozen_hash(tmp_path,monkeypatch):
    from copy import deepcopy
    s=prepare_s1(tmp_path,monkeypatch);root=tmp_path/'j1';m=j1.prepare(s,root)
    ledger_bytes=(s/'api-store/state.json').read_bytes()
    row=json.loads((root/'match-resource.json').read_text());saved=row['payload']
    original_jobs=json.loads((s/'original-jobs.json').read_text())
    old_request=json.loads((j1.ORIGINAL/'request.json').read_text())
    old_retrieval=json.loads((j1.ORIGINAL/'retrieval.json').read_text())
    assert all(j1.accepted_checkpoint(saved,original_jobs,old_request,old_retrieval).values())
    assert m['accepted_hit_order']==[1,3,4,2]
    # Guard does not rely solely on mutable artifact hashes: changing a valid
    # common rule/coverage, original candidate facts or J1 target is rejected.
    for changed in ['skill','coverage','fact','target']:
        value=deepcopy(saved)
        if changed=='skill':value['retrieval']['matching']['results'][0]['rules']['skills'][0]['status']='not_found'
        elif changed=='coverage':value['retrieval']['matching']['results'][0]['rules']['coverage']=0.5
        elif changed=='fact':value['request']['jobs'][0]['job']['title']='changed candidate fact'
        else:value['retrieval']['matching']['results']=value['retrieval']['matching']['results'][1:]+value['retrieval']['matching']['results'][:1]
        with pytest.raises(AssertionError,match='accepted_checkpoint_drift'):
            j1.accepted_checkpoint(value,original_jobs,old_request,old_retrieval)
    # Supply a new internally consistent artifact/review hash for another order.
    # authorize must reject before it opens or appends to the shared journal.
    rows=saved['retrieval']['matching']['results'];rows[1],rows[2]=rows[2],rows[1]
    for rank,item in enumerate(rows,1):item['rank']=rank;item['bm25']['rank']=rank
    j1.write(root/'match-resource.json',row)
    review=json.loads((root/'RANKING_REVIEW.json').read_text())
    review['new_ranking']=j1.ranks(saved['retrieval']);j1.write(root/'RANKING_REVIEW.json',review)
    for name in ['match-resource.json','RANKING_REVIEW.json']:m['artifact_sha256'][name]=j1.digest(root/name)
    m['ranking_review_sha256']=j1.digest(root/'RANKING_REVIEW.json');j1.write(root/'MANIFEST.json',m)
    with pytest.raises(AssertionError,match='accepted_checkpoint_drift'):
        j1.authorize(s,root,m['ranking_review_sha256'])
    assert (s/'api-store/state.json').read_bytes()==ledger_bytes
    assert not (root/'J1_LIVE_STARTED').exists() and not (root/'billing-authorized.json').exists()
