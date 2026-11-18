"""Synthetic durable billing evidence: no provider, credentials or real samples."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from job_radar.api_v1_billing import (Approval, AllowedCall, BillingJournal, SEED,
    SEED_SHA256, initialize, validate_billing, recover_billing, complete_in_state)
from job_radar.api_v1_store import ResourceStore, ServiceError
from job_radar.rag_models import canonical_hash


def wire(kind):
    return {'model': 'gpt-6.1-sol', 'input': f'synthetic-{kind}',
            'instructions': 'synthetic', 'reasoning': {'effort': 'low'},
            'max_output_tokens': 8192, 'store': False, 'tools': [],
            'stream': False, 'truncation': 'disabled',
            'text': {'format': {'type':'json_schema','strict':True,'schema':{}}}}


def resource(store, kind, suffix='first'):
    value = {'synthetic': kind, 'sample': suffix}; snapshot = {'material': suffix}
    row, _ = store.create('/synthetic/' + kind, 'synthetic-key-' + kind + suffix,
                          value, kind, 'llm', snapshot=snapshot)
    context = canonical_hash({'kind': kind, 'input': value, 'snapshot': snapshot})
    return row['resource_id'], context, wire(kind)


def approval(store, plans, **kwargs):
    import hashlib
    seed=store.root.parent/'synthetic-history.json'
    seed.write_text(json.dumps(SEED))
    values = dict(root=str(store.root.resolve()), scope='synthetic-offline-batch',
        seed_sha256=SEED_SHA256,
        seed_refs={str(seed.resolve()):hashlib.sha256(seed.read_bytes()).hexdigest()},
        expires_at=datetime.now(timezone.utc)+timedelta(hours=1),
        max_calls=2,
        allowed=[AllowedCall(kind=k, context_sha256=c, payload_sha256=canonical_hash(p),
                             input_limit={'profile':12000,'report':20000}[k])
                 for k, rid, c, p in plans])
    values.update(kwargs)
    return Approval(**values)


@pytest.fixture
def setup(tmp_path):
    store=ResourceStore(tmp_path/'store');store.open()
    plans=[(kind, *resource(store,kind)) for kind in ('profile','report')]
    ledger=initialize(store,approval(store,plans))
    yield store,ledger,plans
    store.close()


def reserve(ledger,plan):
    k,r,c,p=plan
    return ledger.reserve(r,k,c,p)


def usage(inp=1000,out=100):
    return {'input_tokens':inp,'output_tokens':out,'total_tokens':inp+out}


def settle(ledger,aid,inp=1000,out=100):
    ledger.settle(aid,usage(inp,out), {'synthetic_raw':'retained', 'usage':usage(inp,out)})


def test_explicit_initialization_and_imported_seed_once(setup):
    store,ledger,plans=setup
    state=ledger.status()
    assert state['actual_cny']=='0.05320458'
    assert state['retained_reserve_cny']=='0.6829056'
    assert state['pending_usage_ceiling_cny']=='0'
    assert state['new_attempts']==0 and ledger.ready()
    assert store.state['schema_version']=='api-store-v1.1'
    assert state['approval']['seed_sha256']==canonical_hash(SEED)
    before=deepcopy(store.state)
    with pytest.raises(ServiceError,match='snapshot_conflict'):
        initialize(store,approval(store,plans))
    assert store.state==before


def test_missing_billing_never_autoseeds(tmp_path):
    store=ResourceStore(tmp_path/'store');store.open()
    try:
        ledger=BillingJournal(store)
        assert not ledger.ready() and 'billing' not in store.state
        with pytest.raises(ServiceError,match='llm_disabled'):ledger.status()
        store.state['schema_version']='api-store-v1.1'
        with pytest.raises(ServiceError,match='snapshot_corrupt'):validate_billing(store.state,store.root)
    finally:store.close()


@pytest.mark.parametrize('field,value',[
    ('root','relative'),('seed_sha256','0'*64),('seed_refs',{}),
    ('seed_refs',{'relative':'a'*64}),('seed_refs',{'/synthetic':'bad'}),
    ('expires_at',datetime(2030,1,1)),('max_calls',True),('max_calls',3),
    ('model','other'),('reasoning','high'),('report_prompt','v2')])
def test_approval_strict_binding_validation(tmp_path,field,value):
    store=ResourceStore(tmp_path/'store');store.open()
    try:
        plans=[('profile',*resource(store,'profile'))]
        with pytest.raises(ValidationError):approval(store,plans,**{field:value})
    finally:store.close()


def test_approval_root_and_expiry_fail_closed(tmp_path):
    store=ResourceStore(tmp_path/'store');store.open()
    try:
        plans=[('profile',*resource(store,'profile'))]
        for args in ({'root':str(tmp_path/'other')},
                     {'expires_at':datetime.now(timezone.utc)-timedelta(seconds=1)}):
            with pytest.raises(ServiceError,match='llm_disabled'):
                initialize(store,approval(store,plans,**args))
            assert 'billing' not in store.state
    finally:store.close()


def test_check_is_read_only_and_all_bindings_required(setup):
    store,ledger,plans=setup;k,r,c,p=plans[0];before=deepcopy(store.state)
    ledger.check(k,c,p)
    for kind,ctx,body in [('report',c,p),(k,'f'*64,p),(k,c,{**p,'input':'changed'})]:
        with pytest.raises(ServiceError,match='core_rejected'):ledger.check(kind,ctx,body)
    assert store.state==before
    with pytest.raises(ServiceError,match='snapshot_conflict'):
        ledger.reserve('prf_'+'f'*32,k,c,p)
    assert store.state==before


def test_reserve_retains_double_occupancy_and_persistent_send_intent(setup):
    store,ledger,plans=setup;aid=reserve(ledger,plans[0]);s=ledger.status()
    assert s['new_attempts']==1 and s['inflight']==aid
    assert s['retained_reserve_cny']=='0.7359144'
    assert s['pending_usage_ceiling_cny']=='0.0530088'
    assert s['actual_cny']=='0.05320458' and not ledger.ready()
    disk=json.loads((store.root/'state.json').read_text())['content']['billing']
    assert disk['events'][0]['type']=='send_intent'
    assert disk['totals']==store.state['billing']['totals']
    assert disk['events'][0]['reservation_cny']=='0.0530088'


def test_profile_and_report_share_fees_attempts_and_retained_reserve(setup):
    _,ledger,plans=setup
    first=reserve(ledger,plans[0]);settle(ledger,first,12000,8192);ledger.complete(first)
    second=reserve(ledger,plans[1]);settle(ledger,second,20000,8192);ledger.complete(second)
    s=ledger.status()
    assert s['new_attempts']==2 and s['inflight'] is None and not ledger.ready()
    assert s['actual_cny']=='0.17326218'
    assert s['retained_reserve_cny']=='0.8029632'
    assert Decimal(s['pending_usage_ceiling_cny'])==0
    assert Decimal(s['actual_cny'])+Decimal(s['retained_reserve_cny'])==Decimal('0.97622538')
    with pytest.raises(ServiceError,match='attempt_limit'):reserve(ledger,plans[0])


def test_inflight_survives_usage_until_complete(setup):
    store,ledger,plans=setup;aid=reserve(ledger,plans[0]);settle(ledger,aid)
    s=ledger.status()
    assert s['inflight']==aid and s['attempts'][aid]['state']=='settled'
    assert s['actual_cny']=='0.05534958'
    assert Decimal(s['pending_usage_ceiling_cny'])==0
    with pytest.raises(ServiceError,match='budget_halted'):reserve(ledger,plans[1])
    with pytest.raises(ServiceError,match='budget_halted'):settle(ledger,aid)
    ledger.complete(aid)
    assert ledger.status()['inflight'] is None and ledger.ready()
    before=deepcopy(store.state)
    with pytest.raises(ServiceError,match='budget_halted'):ledger.complete(aid)
    assert store.state==before


def test_approved_call_not_repeatable_with_new_resource_or_key(setup):
    store,ledger,plans=setup;aid=reserve(ledger,plans[0]);settle(ledger,aid);ledger.complete(aid)
    kind,rid,ctx,payload=plans[0]
    with pytest.raises(ServiceError,match='attempt_limit'):ledger.check(kind,ctx,payload)
    value=deepcopy(store.state['records'][rid]['input']);snapshot=deepcopy(store.state['records'][rid]['snapshot'])
    new,_=store.create('/synthetic/profile','a-completely-new-key',value,'profile','llm',snapshot=snapshot)
    with pytest.raises(ServiceError,match='attempt_limit'):
        ledger.reserve(new['resource_id'],kind,ctx,payload)
    assert ledger.status()['new_attempts']==1


def test_two_thread_reservations_have_one_durable_winner(setup):
    _,ledger,plans=setup
    def run(p):
        try:return ('ok',reserve(ledger,p))
        except ServiceError as e:return ('error',e.code)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,plans))
    assert [x[0] for x in results].count('ok')==1
    assert ('error','budget_halted') in results
    assert ledger.status()['new_attempts']==1


@pytest.mark.parametrize('value',[None,{}, {'input_tokens':1000,'output_tokens':100},
    {'input_tokens':True,'output_tokens':0,'total_tokens':1},
    {'input_tokens':1.5,'output_tokens':0,'total_tokens':1.5},
    {'input_tokens':-1,'output_tokens':1,'total_tokens':0},
    {'input_tokens':100,'output_tokens':1,'total_tokens':102}])
def test_unknown_invalid_usage_halts_without_zeroing_pending(setup,value):
    _,ledger,plans=setup;aid=reserve(ledger,plans[0])
    with pytest.raises(ServiceError,match='upstream_error'):ledger.settle(aid,value,{'synthetic':True})
    s=ledger.status()
    assert s['halted'] and s['actual_cny']=='0.05320458'
    assert s['pending_usage_ceiling_cny']=='0.0530088'
    assert s['retained_reserve_cny']=='0.7359144' and s['new_attempts']==1
    assert s['events'][-1]['type']=='halt'
    with pytest.raises(ServiceError,match='budget_halted'):reserve(ledger,plans[1])


@pytest.mark.parametrize('inp,out',[(12001,1),(1000,8193),(1000000,1000000)])
def test_out_of_limit_usage_records_real_known_cost_then_halts(setup,inp,out):
    _,ledger,plans=setup;aid=reserve(ledger,plans[0])
    with pytest.raises(ServiceError,match='upstream_error'):settle(ledger,aid,inp,out)
    s=ledger.status();expected=Decimal('.05320458')+(Decimal(inp)*Decimal('1.755')+Decimal(out)*Decimal('3.9'))/1000000
    assert Decimal(s['actual_cny'])==expected and s['halted']
    assert Decimal(s['pending_usage_ceiling_cny'])==0
    assert s['retained_reserve_cny']=='0.7359144'


@pytest.mark.parametrize('raw',['x'*(1024*1024), {'x':float('nan')}, ['wrong type']])
def test_bad_raw_diagnostics_are_bounded_but_usage_still_recorded(setup,raw):
    store,ledger,plans=setup;aid=reserve(ledger,plans[0])
    with pytest.raises(ServiceError,match='upstream_error'):ledger.settle(aid,usage(),raw)
    s=ledger.status();assert s['halted'] and s['actual_cny']=='0.05534958'
    saved=s['events'][1]['raw'];assert saved['diagnostic_omitted'] is True
    assert len((store.root/'state.json').read_bytes())<1024*1024


def test_known_usage_then_domain_failure_never_refunds(setup):
    _,ledger,plans=setup;aid=reserve(ledger,plans[0]);settle(ledger,aid)
    before=ledger.status();ledger.halt('core_rejected');ledger.halt('upstream_error')
    after=ledger.status()
    for key in ('actual_cny','retained_reserve_cny','pending_usage_ceiling_cny','new_attempts'):
        assert after[key]==before[key]
    assert after['halt_code']=='core_rejected' and after['inflight']==aid
    assert len(after['events'])==len(before['events'])+1
    with pytest.raises(ServiceError,match='budget_halted'):ledger.complete(aid)


@pytest.mark.parametrize('settled',[False,True])
def test_restart_retains_facts_marks_resource_interrupted_and_halts(setup,settled):
    store,ledger,plans=setup;aid=reserve(ledger,plans[0])
    if settled:settle(ledger,aid)
    before=ledger.status();store.close();reopened=ResourceStore(store.root);reopened.open()
    try:
        after=BillingJournal(reopened).status()
        for key in ('actual_cny','retained_reserve_cny','pending_usage_ceiling_cny','new_attempts','inflight'):
            assert after[key]==before[key]
        assert after['halted'] and after['halt_code']=='task_interrupted'
        assert reopened.get(plans[0][1])['state']=='interrupted'
        assert after['events'][-1]['type']=='halt'
        with pytest.raises(ServiceError,match='budget_halted'):
            reserve(BillingJournal(reopened),plans[1])
    finally:reopened.close()


def test_completed_fact_survives_restart_without_new_charge(setup):
    store,ledger,plans=setup;kind,rid,ctx,p=plans[0]
    store.transition(rid,'running');aid=reserve(ledger,plans[0]);settle(ledger,aid)
    store.transition(rid,'succeeded',payload={'synthetic':True},
                     billing_finish=lambda s:complete_in_state(s,aid))
    before=ledger.status();store.close();reopened=ResourceStore(store.root);reopened.open()
    try:
        s=BillingJournal(reopened).status();assert s==before
        assert reopened.get(rid)['state']=='succeeded'
    finally:reopened.close()


@pytest.mark.parametrize('mutation',[
    lambda b:b['seed'].update(historical_actual_cny='0'),
    lambda b:b['totals'].update(actual_cny='NaN'),
    lambda b:b['totals'].update(actual_cny=.05320458),
    lambda b:b['totals'].update(new_attempts=True),
    lambda b:b['events'][0].update(reservation_cny='0'),
    lambda b:b['events'][0].update(payload_sha256='f'*64),
    lambda b:b['approval'].update(root='/a-different-root'),
    lambda b:b['approval'].update(seed_sha256='f'*64),
])
def test_event_amount_seed_root_and_counter_tamper_rejected(setup,mutation):
    store,ledger,plans=setup;reserve(ledger,plans[0]);mutation(store.state['billing'])
    with pytest.raises(ServiceError,match='snapshot_corrupt'):validate_billing(store.state,store.root)
    assert not ledger.ready()


def test_even_rehashed_false_settlement_amount_is_rejected(setup):
    store,ledger,plans=setup;aid=reserve(ledger,plans[0]);settle(ledger,aid)
    event=store.state['billing']['events'][1];event['actual_cny']='0'
    event['sha256']=canonical_hash({k:v for k,v in event.items() if k!='sha256'})
    with pytest.raises(ServiceError,match='snapshot_corrupt'):validate_billing(store.state,store.root)


def test_status_is_detached(setup):
    _,ledger,plans=setup;s=ledger.status();s['approval']['scope']='changed'
    assert ledger.status()['approval']['scope']=='synthetic-offline-batch'


@pytest.mark.parametrize('operation',['reserve','settle','complete','halt'])
def test_write_failure_returns_no_permit_and_stops_instance(setup,monkeypatch,operation):
    store,ledger,plans=setup;aid=None
    if operation in ('settle','complete'):aid=reserve(ledger,plans[0])
    if operation=='complete':settle(ledger,aid)
    before=deepcopy(store.state)
    def fail():raise OSError('synthetic-fsync-error')
    monkeypatch.setattr(store,'_save',fail)
    action={'reserve':lambda:reserve(ledger,plans[0]),
            'settle':lambda:settle(ledger,aid),
            'complete':lambda:ledger.complete(aid),
            'halt':lambda:ledger.halt()}[operation]
    with pytest.raises(ServiceError,match='store_unavailable'):action()
    assert store.state==before and store.write_failed and not ledger.ready()



def test_budget_exact_cap_allows_and_minimal_overflow_rejects():
    from job_radar.api_v1_billing import _fits
    # The test exercises the exact arithmetic used by both preflight and
    # event replay; frozen historical amounts never change in a real journal.
    assert _fits(Decimal('.1'), Decimal('.5'), Decimal('.1'), Decimal('.15'))
    assert not _fits(Decimal('.1'), Decimal('.500000000001'), Decimal('.1'), Decimal('.15'))
    assert not _fits(Decimal('.1'), Decimal('.5'), Decimal('.100000000001'), Decimal('.15'))
    assert _fits(Decimal('.05320458'), Decimal('.6829056'), Decimal(0), Decimal('.0530088'))
    assert not _fits(Decimal('.05320458'), Decimal('.9'), Decimal(0), Decimal('.0530088'))


def test_distinct_two_report_calls_hit_shared_double_occupancy_budget(tmp_path):
    store=ResourceStore(tmp_path/'store');store.open()
    try:
        plans=[('report',*resource(store,'report',suffix=str(i))) for i in range(2)]
        ledger=initialize(store,approval(store,plans))
        aid=reserve(ledger,plans[0]);settle(ledger,aid,20000,8192);ledger.complete(aid)
        before=deepcopy(store.state)
        with pytest.raises(ServiceError,match='budget_exhausted'):reserve(ledger,plans[1])
        assert store.state==before
        s=ledger.status()
        assert s['new_attempts']==1 and Decimal(s['retained_reserve_cny'])==Decimal('.7499544')
    finally:store.close()


@pytest.mark.parametrize('operation',['reserve','settle'])
def test_replace_then_fsync_failure_recovers_uncertain_facts_without_resend(setup,monkeypatch,operation):
    store,ledger,plans=setup;aid=None
    if operation=='settle':aid=reserve(ledger,plans[0])
    original=store._save
    def uncertain():
        original()
        raise OSError('synthetic-directory-fsync-after-replace')
    monkeypatch.setattr(store,'_save',uncertain)
    with pytest.raises(ServiceError,match='store_unavailable'):
        if operation=='reserve':reserve(ledger,plans[0])
        else:settle(ledger,aid)
    assert store.write_failed
    store.close();again=ResourceStore(store.root);again.open()
    try:
        restored=BillingJournal(again);s=restored.status()
        assert s['new_attempts']==1 and s['halted'] and s['inflight'] is not None
        assert s['retained_reserve_cny']=='0.7359144'
        if operation=='reserve':
            assert s['actual_cny']=='0.05320458' and s['pending_usage_ceiling_cny']=='0.0530088'
        else:
            assert s['actual_cny']=='0.05534958' and Decimal(s['pending_usage_ceiling_cny'])==0
        with pytest.raises(ServiceError,match='budget_halted'):reserve(restored,plans[1])
    finally:again.close()


def test_completed_nonempty_billing_survives_reopen_without_refund(setup):
    store,ledger,plans=setup;aid=reserve(ledger,plans[0]);settle(ledger,aid);ledger.complete(aid)
    billing_before=deepcopy(store.state['billing'])
    store.close();again=ResourceStore(store.root);again.open()
    try:assert again.state['billing']==billing_before
    finally:again.close()


def test_single_instance_lock_does_not_reset_nonempty_billing(setup):
    store,ledger,plans=setup;reserve(ledger,plans[0]);before=deepcopy(store.state['billing'])
    other=ResourceStore(store.root)
    with pytest.raises(ServiceError,match='store_unavailable'):other.open()
    assert store.state['billing']==before


def test_approval_expiry_after_initialization_gates_reserve(setup,monkeypatch):
    import job_radar.api_v1_billing as billing
    store,ledger,plans=setup
    later=datetime.now(timezone.utc)+timedelta(days=1)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return later
    monkeypatch.setattr(billing,'datetime',Clock)
    assert not ledger.ready()
    before=deepcopy(store.state)
    with pytest.raises(ServiceError,match='llm_disabled'):reserve(ledger,plans[0])
    assert store.state==before


@pytest.mark.parametrize('patch',[
    {'model':'wrong'}, {'reasoning':{'effort':'high'}}, {'max_output_tokens':1},
    {'max_output_tokens':True}, {'truncation':'auto'}, {'store':True},
    {'stream':True}, {'tools':[{'type':'extra'}]},
    {'text':{'format':{'type':'json_schema','strict':False,'schema':{}}}},
])
def test_even_explicit_bad_wire_allowlist_does_not_authorize_configuration_drift(tmp_path,patch):
    store=ResourceStore(tmp_path/'store');store.open()
    try:
        kind,rid,ctx,payload=('profile',*resource(store,'profile'))
        payload.update(patch);plan=(kind,rid,ctx,payload)
        ledger=initialize(store,approval(store,[plan]))
        before=deepcopy(store.state)
        with pytest.raises(ServiceError,match='core_rejected'):reserve(ledger,plan)
        assert store.state==before and ledger.status()['new_attempts']==0
    finally:store.close()


def test_require_ready_classifies_batch_exhaustion(setup):
    _,ledger,plans=setup
    for plan in plans:
        aid=reserve(ledger,plan);settle(ledger,aid);ledger.complete(aid)
    with pytest.raises(ServiceError,match='attempt_limit'):ledger.require_ready()
    assert not ledger.ready()
