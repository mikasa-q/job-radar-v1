"""Runnable synthetic BM25 tuning/ablation demo, never human quality evidence."""
from pathlib import Path
import argparse
import json
from stage3_synthetic import synthetic_job,synthetic_manifest
from job_radar.rag_models import EvaluationSet,EvalQuery,EvalJudgment,ExperimentSpec,canonical_hash
from job_radar.rag_evaluation import run_experiments
from job_radar.retrieval_text import tokenize,retrieval_text
from job_radar.retrieval_models import JobKey,RetrievalQuery,EngineConfig
from job_radar.retrieval_bm25 import retrieve_bm25
from job_radar.rag_index import DerivedIndex


def main(output):
    jobs=[synthetic_job(i) for i in range(1,5)]
    queries=[EvalQuery(query_id='backend',text='Python FastAPI 接口',profile_group='synth-backend',split='dev',source='synthetic'),
        EvalQuery(query_id='mobile',text='Swift iOS SwiftUI',profile_group='synth-mobile',split='dev',source='synthetic'),
        EvalQuery(query_id='data',text='SQL Python 数据分析',profile_group='synth-data',split='dev',source='synthetic')]
    expected=[[2,0,1,0],[0,2,0,0],[1,0,2,0]]
    labels=[EvalJudgment(query_id=q.query_id,key=JobKey(dataset_id=j.dataset_id,job_id=j.job_id),
        job_group=f'synthetic-job-{j.job_id}',grade=expected[qi][ji],status='synthetic_expected',
        evidence=[f'machine-authored:job-{j.job_id}'],review_note='Not human annotation.')
        for qi,q in enumerate(queries) for ji,j in enumerate(jobs)]
    dataset=EvaluationSet(data_version='synthetic-bm25-demo-v1',queries=queries,judgments=labels)
    configs={'stage2-default':EngineConfig(),'bm25-title1':EngineConfig(k1=.9,b=.5,title_weight=1.),
        'bm25-title0':EngineConfig(k1=1.2,b=.75,title_weight=0.)}
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    idx=DerivedIndex(output/'derived');idx.update(jobs,manifest=synthetic_manifest(jobs))
    docs=idx.documents([label.key for label in labels[:len(jobs)]])
    specs=[ExperimentSpec(experiment_id=name,config_sha256=canonical_hash(cfg),dataset_sha256=canonical_hash(dataset),
        index_sha256=canonical_hash(idx.manifest()),variant='stage2_default' if name=='stage2-default' else 'tuned_bm25') for name,cfg in configs.items()]
    def ranker(query,spec):
        text=retrieval_text(query.text)
        return [h.key for h in retrieve_bm25(RetrievalQuery(text=text,terms=tokenize(text)),docs,configs[spec.experiment_id])]
    reports=run_experiments(dataset,specs,ranker,split='dev')
    (output/'dataset.json').write_text(dataset.model_dump_json(indent=2))
    (output/'report.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2))
    (output/'parameters.json').write_text(json.dumps({k:v.model_dump() for k,v in configs.items()},indent=2))
    print(json.dumps({'experiments':len(specs),'queries':len(queries),'human_quality_ready':False,
        'synthetic':True,'report':str(output/'report.json')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);main(parser.parse_args().output)
