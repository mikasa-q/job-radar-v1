"""Entirely synthetic stage 3 fixtures, not human annotations."""
from datetime import datetime, timezone
import hashlib
import json
from job_radar.import_models import ImportedJob
from job_radar.matching_models import JobCandidate, SearchPreferences
from job_radar.matching_input import build_matching_input, build_matching_request
from job_radar.resume_models import ResumeDocument, ResumeProvenance, ResumeBlock, ResumePage
from job_radar.resume_profile_rules import build_rule_profile
from job_radar.rag_models import IndexManifest, canonical_hash


def synthetic_job(index=1, *, description=None, city='上海', dataset='synthetic-stage3'):
    descriptions = ['岗位职责：使用 Python 和 FastAPI 开发后端接口。要求熟悉 SQL 数据库。',
        '岗位职责：使用 Swift 开发 iOS 界面。要求掌握 SwiftUI。',
        '岗位职责：使用 SQL 分析业务数据。熟悉 Python 加分。',
        '岗位职责：使用 Java 编写服务。需要 Spring 开发经验。']
    record = ImportedJob(source='manual', title=['Python 后端','iOS 开发','数据分析','Java 开发'][(index-1)%4],
        url=f'https://example.org/synthetic/{index}', description=description or descriptions[(index-1)%4], city=city)
    return JobCandidate(dataset_id=dataset, job_id=index, job=record,
        jd_sha256=hashlib.sha256(record.description.encode()).hexdigest(),
        fact_sha256=canonical_hash(record), review_status='unconfirmed',
        provenance=[dict(provenance_id=index, source_file_sha256='a'*64, input_format='txt',
            parser_version='synthetic-v1', imported_at=datetime(2026,11,12,tzinfo=timezone.utc), import_run_id='synthetic')])


def synthetic_request(jobs=None, *, text='课程项目：使用 Python 和 FastAPI 开发接口。'):
    doc = ResumeDocument(provenance=ResumeProvenance(source_sha256=hashlib.sha256(text.encode()).hexdigest(),
        parser_version='synthetic-v1', extractor_version='synthetic-v1'), pages=[ResumePage(page_number=1,
        width=600.0,height=800.0,blocks=[ResumeBlock(block_id='p001-b001',text=text,bbox=(10.0,10.0,500.0,30.0))])])
    profile = build_matching_input(build_rule_profile(doc, resume_id='synthetic-stage3'),preferences=SearchPreferences())
    return build_matching_request(profile, jobs if jobs is not None else [synthetic_job(i) for i in range(1,5)], purpose='debug')


def synthetic_manifest(jobs, *, generation='synthetic-g1', **kwargs):
    ordered = sorted(jobs,key=lambda j:(j.dataset_id,j.job_id))
    return IndexManifest(generation=generation, snapshot_sha256=canonical_hash([j.model_dump(mode='json') for j in ordered]), **kwargs)
