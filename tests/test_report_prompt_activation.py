"""Activation compatibility and strict provider gate; no real requests."""
from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from job_radar.report_analysis import SYSTEM_PROMPT, build_analysis_request, validate_analysis
from job_radar.report_models import MatchReport, PROMPT_VERSION, AnalysisConfig
from job_radar.report_prompt_v1 import SYSTEM_PROMPT_V1
from job_radar.report_prompt_v2 import SYSTEM_PROMPT_V2
from job_radar.report_prompt_v3 import SYSTEM_PROMPT_V3
from job_radar.report_apinebula import APINebulaAnalysisClient, AnalysisProviderError
from job_radar.report_engine import generate_match_report
from job_radar.report_evidence import build_evidence_packet
from job_radar.report_synthetic import synthetic_inputs, synthetic_manifest
from job_radar.rag_index import DerivedIndex
from job_radar.rag_engine import match_jobs_indexed
from job_radar.report_render import render_match_report
from job_radar.report_demo_client import SyntheticReportClient


@pytest.fixture
def case(tmp_path):
    doc,req=synthetic_inputs()
    index=DerivedIndex(tmp_path/'index')
    index.update(req.jobs,manifest=synthetic_manifest(req.jobs))
    result=match_jobs_indexed(req,index=index)
    return doc,req,result


def test_active_prompt_exact_v3_and_version(case):
    doc,req,result=case
    packet=build_evidence_packet(req,result,doc)
    request=build_analysis_request(packet,packet.selected_keys[0])
    assert request.prompt_version==PROMPT_VERSION=='match-analysis-prompt-v3'
    assert request.system_prompt==SYSTEM_PROMPT==SYSTEM_PROMPT_V3
    assert SYSTEM_PROMPT_V2.startswith(SYSTEM_PROMPT_V1)


@pytest.mark.parametrize('old_version',['match-analysis-prompt-v1','match-analysis-prompt-v2'])
def test_read_and_roundtrip_both_report_versions_without_relabeling(case,old_version):
    doc,req,result=case
    report=generate_match_report(req,result,doc,report_id='version-history',
        generated_at=datetime(2026,11,15,tzinfo=timezone.utc),client=SyntheticReportClient())
    data=report.model_dump(mode='json');data['prompt_version']=old_version
    historical=MatchReport.model_validate_json(json.dumps(data))
    assert historical.prompt_version==old_version
    assert MatchReport.model_validate_json(historical.model_dump_json())==historical
    assert old_version in render_match_report(historical)


def test_new_reports_default_v3_but_match_report_schema_still_v1(case):
    doc,req,result=case
    report=generate_match_report(req,result,doc,report_id='new',
        generated_at=datetime(2026,11,15,tzinfo=timezone.utc))
    assert report.prompt_version=='match-analysis-prompt-v3'
    assert report.schema_version=='match-report-v1'


@pytest.mark.parametrize('field,value',[
    ('system_prompt',SYSTEM_PROMPT_V1),('prompt_version','match-analysis-prompt-v1'),
    ('system_prompt',SYSTEM_PROMPT_V2),('prompt_version','match-analysis-prompt-v2'),
])
def test_provider_rejects_old_generation_configuration_without_key_read(case,field,value):
    doc,req,result=case
    packet=build_evidence_packet(req,result,doc)
    request=build_analysis_request(packet,packet.selected_keys[0])
    # Pure payload validation; no constructor, key or network.
    client=APINebulaAnalysisClient.__new__(APINebulaAnalysisClient)
    client._timeout=90.0;client._max_output=8192
    assert client._payload(request)['instructions']==SYSTEM_PROMPT_V3
    with pytest.raises(AnalysisProviderError,match='invalid_analysis_request'):
        client._payload(replace(request,**{field:value}))


def test_single_job_selection_preserves_all_other_ranks_without_client_calls(case):
    doc,req,result=case
    scripted=SyntheticReportClient()
    report=generate_match_report(req,result,doc,report_id='only-one',
        generated_at=datetime(2026,11,15,tzinfo=timezone.utc),
        config=AnalysisConfig(top_n=1),client=scripted)
    assert scripted.calls==1
    assert [a.status for a in report.analyses]==['completed']+['not_analyzed']*(len(report.analyses)-1)
    assert report.packet.retrieval==result
    assert report.packet.selected_keys==[result.matching.results[0].key]

