"""Read-only HTTP access to the database produced by the existing CLI."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Query, Request
from fastapi.responses import JSONResponse

from .schemas import HealthResponse, JobResponse, JobsPage, JobsStats
from .storage import DatabaseUnavailable, get_job, job_stats, query_jobs


logger = logging.getLogger(__name__)
DEFAULT_DATABASE = Path(__file__).resolve().parents[2] / "data" / "jobs.db"


def get_database_path(request: Request) -> Path:
    return request.app.state.database_path


Database = Annotated[Path, Depends(get_database_path)]


def create_app(database_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(
        title="JobRadar 岗位查询 API", version="0.3.0",
        description="查询已入库的岗位；请求不会触发网页采集。",
    )
    configured_path = database_path if database_path is not None else os.getenv(
        "JOBRADAR_DB", str(DEFAULT_DATABASE)
    )
    app.state.database_path = Path(configured_path).expanduser().resolve()

    @app.exception_handler(DatabaseUnavailable)
    async def database_unavailable(request: Request, exc: DatabaseUnavailable):
        logger.error("Job database query failed", exc_info=exc)
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.get("/health", response_model=HealthResponse, tags=["服务"])
    def health():
        """Liveness only: a healthy process need not have an initialized DB."""
        return {"status": "ok"}

    @app.get("/jobs", response_model=JobsPage, tags=["岗位"])
    def read_jobs(
        database: Database,
        city: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        keyword: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
        skill: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        source: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        return query_jobs(database, city=city, keyword=keyword, skill=skill,
                          source=source, limit=limit, offset=offset)

    # Register this static path before /jobs/{job_id}.
    @app.get("/jobs/stats", response_model=JobsStats, tags=["岗位"])
    def read_stats(database: Database):
        return job_stats(database)

    @app.get("/jobs/{job_id}", response_model=JobResponse, tags=["岗位"])
    def read_job(database: Database, job_id: Annotated[int, PathParam(ge=1)]):
        job = get_job(database, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="岗位不存在。")
        return job

    return app


app = create_app()
