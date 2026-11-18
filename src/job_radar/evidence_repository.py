"""Independent read-only adapter for the V1 evidence dataset, not legacy jobs.

No source files are opened and no review state is promoted. Dataset identity is
supplied by the caller; row/hash/source associations are checked in a snapshot.
"""
from __future__ import annotations
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from pydantic import ValidationError
from .matching_models import JobCandidate, MatchingModel
from .retrieval_models import RetrievalError


class _DatasetIdentity(MatchingModel):
    dataset_id: str


def read_job_candidates(path: str | Path, *, dataset_id: str) -> list[JobCandidate]:
    identity=_DatasetIdentity(dataset_id=dataset_id)
    path=Path(path).expanduser().resolve(strict=True)
    if not path.is_file():raise RetrievalError('invalid_dataset_path')
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as connection:
            connection.row_factory=sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            if connection.execute('PRAGMA application_id').fetchone()[0]!=0x4A525631 or connection.execute('PRAGMA user_version').fetchone()[0]!=1:
                raise RetrievalError('incompatible_dataset')
            if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise RetrievalError('dataset_foreign_key_error')
            rows=connection.execute('SELECT * FROM imported_jobs ORDER BY id').fetchall()
            source_rows=connection.execute('SELECT * FROM job_provenance ORDER BY job_id,id').fetchall()
            groups={}
            for source in source_rows:
                groups.setdefault(source['job_id'],[]).append(dict(
                    provenance_id=source['id'],source_file_sha256=source['source_file_sha256'],
                    input_format=source['input_format'],parser_version=source['parser_version'],
                    imported_at=source['imported_at'],import_run_id=source['import_run_id']))
            result=[]
            scalars=('source','title','url','description','description_kind','company','city','salary','education','experience','attendance','posted_at')
            for row in rows:
                job={name:row[name] for name in scalars}
                for name in ('source_tags','skills','warnings'):
                    job[name]=json.loads(row[name+'_json'])
                result.append(JobCandidate(dataset_id=identity.dataset_id,job_id=row['id'],job=job,
                    jd_sha256=row['jd_sha256'],fact_sha256=row['fact_sha256'],
                    provenance=groups.get(row['id'],[]),review_status=row['review_status']))
            return result
    except (sqlite3.Error,ValidationError,ValueError,KeyError,IndexError,TypeError) as error:
        if isinstance(error,RetrievalError):raise
        raise RetrievalError('invalid_evidence_dataset') from None
