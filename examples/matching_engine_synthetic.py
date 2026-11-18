"""Offline Stage 2 demo, including an explicitly requested dense fallback."""
from datetime import datetime,timezone
import hashlib
import json
from job_radar.import_models import ImportedJob
from job_radar.matching_models import JobCandidate,SearchPreferences
from job_radar.matching_input import build_matching_input,build_matching_request
from job_radar.resume_models import ResumeBlock,ResumeDocument,ResumePage,ResumeProvenance
from job_radar.resume_profile_rules import build_rule_profile
from job_radar.matching_engine import match_jobs
from job_radar.retrieval_models import EngineConfig


def main():
    text='课程项目：使用 Python 和 FastAPI 实现查询接口。'
    doc=ResumeDocument(provenance=ResumeProvenance(source_sha256=hashlib.sha256(text.encode()).hexdigest(),parser_version='synthetic-v1',extractor_version='synthetic-v1'),pages=[ResumePage(page_number=1,width=600.0,height=800.0,blocks=[ResumeBlock(block_id='p001-b001',text=text,bbox=(20.0,20.0,500.0,40.0))])])
    matching=build_matching_input(build_rule_profile(doc,resume_id='synthetic-engine'),preferences=SearchPreferences(cities=['上海'],available_days_per_week=3))
    jobs=[]
    for i,city,description in [(1,'上海','要求 Python 或 Go。FastAPI 加分。'),(2,'北京','必须 Python。'),(3,'上海','要求 Java。')]:
        job=ImportedJob(source='manual',url=f'https://example.org/jobs/{i}',title='开发实习',city=city,description=description)
        canonical=json.dumps(job.model_dump(),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        jobs.append(JobCandidate(dataset_id='synthetic-v1',job_id=i,job=job,jd_sha256=hashlib.sha256(description.encode()).hexdigest(),fact_sha256=hashlib.sha256(canonical.encode()).hexdigest(),review_status='unconfirmed',provenance=[dict(provenance_id=i,source_file_sha256=str(i)*64,input_format='txt',parser_version='synthetic-v1',imported_at=datetime(2026,11,11,tzinfo=timezone.utc),import_run_id='synthetic-run')]))
    request=build_matching_request(matching,jobs,purpose='debug')
    result=match_jobs(request,config=EngineConfig(hard_constraints=['city']))
    assert result.results[0].key.job_id==1 and [j.key.job_id for j in result.excluded]==[2]
    print('mode='+result.actual_mode+'; status='+result.status)
    print('ranked_job_ids='+','.join(str(j.key.job_id) for j in result.results))
    print('excluded_job_ids='+','.join(str(j.key.job_id) for j in result.excluded))
    print('coverage='+str(result.results[0].rules.coverage)+' (evidence coverage, not proficiency)')
    degraded=match_jobs(request,config=EngineConfig(mode='hybrid',allow_dense_fallback=True,hard_constraints=['city']))
    assert degraded.actual_mode=='bm25_rules' and degraded.status=='degraded'
    print('requested=hybrid; actual='+degraded.actual_mode+'; status='+degraded.status)
    print('dense_reason='+str(degraded.channels[1].reason))
    print('real_embedding_calls=0; database_reads=0; network_calls=0')
    print('review_status=unconfirmed; formal_gate=unchanged')


if __name__=='__main__':main()
