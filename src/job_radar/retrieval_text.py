"""Versioned deterministic projections and tokens; no files or model calls."""
from __future__ import annotations
import re
from .matching_models import MatchingInput, MatchingRequest
from .retrieval_models import EngineConfig, JobKey, RetrievalDocument, RetrievalError, RetrievalQuery

# Explicit aliases only, shared by lexical and rule modules; no substring skills.
ALIASES = {'python3':'python','py.test':'pytest','tool-calling':'tool calling'}
_TOKEN = re.compile(r'tool[ -]calling|python\s+3|[a-z0-9]+(?:[.+#-][a-z0-9]+)*(?:\+\+|#)?|[\u4e00-\u9fff]+',re.I)


def tokenize(text: str) -> list[str]:
    terms=[]
    for m in _TOKEN.finditer(text):
        token=m.group().lower()
        if re.fullmatch(r'[\u4e00-\u9fff]+',token):
            # Character bigrams give a dependency-free Chinese baseline.
            terms.extend(token[i:i+2] for i in range(max(1,len(token)-1)))
        else:
            terms.append(ALIASES.get(token, 'python' if token=='python 3' else token))
    return terms


def retrieval_text(text: str) -> str:
    """Remove recognizable contact/URL spans from encoder input, not evidence.

    This is a bounded minimization rule, not a claim of full anonymization.
    Original field evidence remains separately attached for local inspection.
    """
    text=re.sub(r'(?i)https?://[^\s，。；;]+', ' ', text)
    text=re.sub(r'(?i)(?<![a-z0-9_.+-])[a-z0-9_.+-]+@[a-z0-9.-]+\.[a-z]{2,}', ' ', text)
    text=re.sub(r'(?<![a-z0-9])(?:\+?\d[ ()-]*){7,15}(?![a-z0-9])', ' ', text, flags=re.I)
    return text.strip()


def key_tuple(key: JobKey) -> tuple[str,int]:
    return key.dataset_id,key.job_id


def project_request(request: MatchingRequest, config: EngineConfig) -> tuple[RetrievalQuery,list[RetrievalDocument]]:
    request=MatchingRequest.model_validate(request)
    config=EngineConfig.model_validate(config)
    if len(request.jobs)>config.max_jobs:raise RetrievalError('too_many_jobs')
    p=request.profile
    texts=[];paths=[];refs=[]
    def add(text,path,evidence=()):
        text=retrieval_text(text)
        if text:
            if text not in texts:texts.append(text)
            paths.append(path);refs.extend(evidence)
    for i,skill in enumerate(p.skills):add(skill.name,f'skills[{i}]',skill.mention.evidence)
    for kind in ['projects','work_experience']:
        for i,entry in enumerate(getattr(p,kind)):
            for j,description in enumerate(entry.description):
                add(description.value,f'{kind}[{i}].description[{j}]',description.evidence)
            for j,skill in enumerate(entry.technologies):
                add(skill.name,f'{kind}[{i}].technologies[{j}]',skill.mention.evidence)
    for i,role in enumerate(p.preferences.target_roles or []):add(role,f'preferences.target_roles[{i}]')
    text='\n'.join(texts)
    if len(text)>config.max_text_chars:raise RetrievalError('query_text_too_long')
    query=(RetrievalQuery(text=text,terms=list(dict.fromkeys(tokenize(text))),resume_paths=paths,evidence=refs)
           if text else RetrievalQuery.empty())
    docs=[]
    for job in request.jobs:
        title=retrieval_text(job.job.title)
        description=retrieval_text(job.job.description)
        full=title+'\n'+description
        if not full.strip():raise RetrievalError('empty_job_retrieval_text')
        if len(full)>config.max_text_chars:raise RetrievalError('job_text_too_long')
        docs.append(RetrievalDocument(key=JobKey(dataset_id=job.dataset_id,job_id=job.job_id),
            title=job.job.title,text=full,title_terms=tokenize(title),
            terms=tokenize(description),fact_sha256=job.fact_sha256))
    return query,docs
