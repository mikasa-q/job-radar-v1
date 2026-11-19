"""One-request PDF → rule profile → indexed match → report orchestration."""
from __future__ import annotations

import time
from typing import Annotated, Literal
import uuid

from fastapi import Header, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .api_v1_models import Accepted, Envelope, ProfileCreate, ReportCreate
from .api_v1_reports import markdown_download
from .api_v1_resumes import PDF_REQUEST_BODY, read_pdf_upload
from .api_v1_store import ServiceError
from .retrieval_models import EngineConfig

PDF_REPORT_ROUTE = '/v1/reports/from-pdf'


def _wait_for_stage(service, row):
    # The orchestrator runs in the request's thread, never in the single-task
    # executor it waits on. A timeout does not resubmit or cancel accepted work.
    deadline = time.monotonic() + 30
    while row['state'] in ('queued', 'running'):
        if time.monotonic() >= deadline:
            raise ServiceError('execution_timeout')
        time.sleep(.025)
        row = service.store.get(row['resource_id'])
    if row['state'] != 'succeeded':
        raise ServiceError(row['error']['code'])
    return row


def report_from_pdf(service, raw: bytes, key: str, mode: Literal['offline', 'llm']):
    token = service.store.token(PDF_REPORT_ROUTE, key)
    if mode == 'llm':
        # Reject absent/disabled backends before extracting the PDF. Exact
        # context + full-wire approval is rechecked by service.report after
        # ranking. Don't rerun a paid profile or reserve anything here.
        service.require_report_backend()
        service.billing_backend()
    # Derive server-owned stage keys. Both modes share the rule profile;
    # each match and report can be replayed without rerunning a paid task.
    resume = service.upload(raw, 'pdf-report-resume-' + token)
    profile = service.profile(resume['resource_id'], ProfileCreate(), 'pdf-report-profile-' + token)
    profile = _wait_for_stage(service, profile)
    # A Live match carries only its first ranked result in the report snapshot;
    # ranking itself still considers all jobs. This bounds the existing wire
    # preflight (only analyze Top1) without truncating the selected job's evidence.
    config = EngineConfig(top_k=1) if mode == 'llm' else EngineConfig()
    match = service.match_dataset(profile['resource_id'], f'pdf-report-{mode}-match-' + token, config=config)
    match = _wait_for_stage(service, match)
    return service.report(ReportCreate(match_id=match['resource_id'], mode=mode, top_n=1 if mode == 'llm' else 3),
                          f'pdf-report-{mode}-' + token)


def register_routes(app, service, *, wrap, accepted, responses):
    @app.post(PDF_REPORT_ROUTE, status_code=202, response_model=Envelope[Accepted],
              summary='上传 PDF，一站式生成岗位报告',
              description='提交一个 PDF 即可。默认离线，直接下载 Markdown。显式 LLM 仅分析首岗，且须已有精确请求审批。',
              openapi_extra=PDF_REQUEST_BODY,
              responses={**responses,
                         202: {'description': '已接收的 LLM 报告任务'},
                         201: {'content': {'text/markdown': {'schema': {'type': 'string'}}},
                               'description': '已完成的离线 Markdown 报告'}})
    async def create_pdf_report(
        request: Request,
        mode: Annotated[Literal['offline', 'llm'], Query(description='默认离线，不调用 LLM')] = 'offline',
        idempotency_key: Annotated[str | None, Header(description='可选；重试时复用同一值')] = None,
    ):
        key = 'pdf-' + uuid.uuid4().hex if idempotency_key is None else idempotency_key
        raw = await read_pdf_upload(request)
        row = await run_in_threadpool(report_from_pdf, service, raw, key, mode)
        headers = {'Idempotency-Key': key, 'Location': '/v1/reports/' + row['resource_id']}
        if mode == 'llm':
            # Only this report task is exposed. Pending or terminal replay is
            # the current resource snapshot; never polls/sends a second call.
            return JSONResponse(status_code=202, headers=headers, content=wrap(request, data=accepted(row)))
        row = await run_in_threadpool(_wait_for_stage, service, row)
        return markdown_download(row, status_code=201, headers=headers)
