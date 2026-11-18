"""Tiny synthetic evidence fixtures. No model intelligence or human quality labels."""
from datetime import datetime, timezone
import hashlib
from .import_models import ImportedJob
from .matching_models import JobCandidate, SearchPreferences
from .matching_input import build_matching_input,build_matching_request
from .resume_models import ResumeDocument,ResumeProvenance,ResumeBlock,ResumePage
from .resume_profile_rules import build_rule_profile
from .rag_models import IndexManifest, canonical_hash

def synthetic_inputs(*,text='课程项目：使用 Python 和 FastAPI 开发接口。',descriptions=None):
    doc=ResumeDocument(provenance=ResumeProvenance(source_sha256=hashlib.sha256(text.encode()).hexdigest(),parser_version='synthetic-v1',extractor_version='synthetic-v1'),pages=[ResumePage(page_number=1,width=600.0,height=800.0,blocks=[ResumeBlock(block_id='p001-b001',text=text,bbox=(10.0,10.0,500.0,30.0))])])
    profile=build_matching_input(build_rule_profile(doc,resume_id='synthetic-stage4'),preferences=SearchPreferences())
    jobs=[]
    for n,desc in enumerate(descriptions if descriptions is not None else ['要求掌握 Python。熟悉 Docker 加分。','要求掌握 FastAPI 或 Django。','要求掌握 SQL。Python 加分。','要求掌握 Swift。'],1):
        job=ImportedJob(source='manual',title=f'合成开发岗位{n}',url=f'https://example.org/stage4/{n}',description=desc,city='上海')
        jobs.append(JobCandidate(dataset_id='synthetic-stage4',job_id=n,job=job,jd_sha256=hashlib.sha256(desc.encode()).hexdigest(),fact_sha256=canonical_hash(job),review_status='unconfirmed',provenance=[dict(provenance_id=n,source_file_sha256='a'*64,input_format='txt',parser_version='synthetic-v1',imported_at=datetime(2026,11,14,tzinfo=timezone.utc),import_run_id='synthetic')]))
    return doc,build_matching_request(profile,jobs,purpose='debug')

def synthetic_manifest(jobs):
    return IndexManifest(generation='synthetic-stage4-g1',snapshot_sha256=canonical_hash([j.model_dump(mode='json') for j in sorted(jobs,key=lambda j:(j.dataset_id,j.job_id))]))
