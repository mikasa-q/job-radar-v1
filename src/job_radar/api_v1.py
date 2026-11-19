"""Versioned API composition. Importing this module opens no files or providers."""
from __future__ import annotations
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import create_app as create_legacy_app
from .api_v1_models import Envelope, Accepted, ApiError, ERROR_HTTP, Ready
from .api_v1_pdf_report import PDF_REPORT_ROUTE
from .api_v1_runtime import OfflineService, ServiceSettings
from .api_v1_store import ServiceError

MAX_UPLOAD = 10*1024*1024
MAX_MULTIPART = MAX_UPLOAD+64*1024
MAX_JSON = 256*1024


class BodyLimit:
    """Count actual received bytes before multipart spooling or JSON parsing."""
    def __init__(self, app):self.app=app

    async def __call__(self, scope, receive, send):
        if scope['type']!='http' or not scope.get('path','').startswith('/v1'):
            return await self.app(scope,receive,send)
        scope.setdefault('state',{})['request_id']='req_'+uuid.uuid4().hex
        limit=MAX_MULTIPART if scope['path'] in {'/v1/resumes', PDF_REPORT_ROUTE} else MAX_JSON
        # No await downstream until the complete bounded body is available.
        chunks=[];total=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            if message['type']!='http.request':continue
            block=message.get('body',b'');total+=len(block)
            if total>limit:
                response=JSONResponse(status_code=413,headers={'Cache-Control':'no-store',
                    'X-Request-ID':scope['state']['request_id']},content=Envelope(
                    request_id=scope['state']['request_id'],error=ApiError(code='payload_too_large')).model_dump(mode='json'))
                return await response(scope,receive,send)
            chunks.append(block)
            if not message.get('more_body'):break
        delivered=False
        async def bounded_receive():
            nonlocal delivered
            if delivered:return await receive()
            delivered=True
            return {'type':'http.request','body':b''.join(chunks),'more_body':False}
        started=False
        async def response_send(message):
            nonlocal started
            if message['type']=='http.response.start':
                started=True
                message.setdefault('headers',[]).extend([
                    (b'x-request-id',scope['state']['request_id'].encode()),
                    (b'cache-control',b'no-store')])
            await send(message)
        try:
            await self.app(scope,bounded_receive,response_send)
        except Exception:
            if started:raise
            response=JSONResponse(status_code=500,content=Envelope(
                request_id=scope['state']['request_id'],error=ApiError(code='internal_error')).model_dump(mode='json'))
            await response(scope,bounded_receive,response_send)


def create_app(settings: ServiceSettings | None = None, *, legacy_database_path=None, mock_backend=None, live_backend=None):
    # Default store is created only inside lifespan, never at module import.
    settings=settings or ServiceSettings(
        store_root=Path(os.getenv('JOBRADAR_API_STORE','~/.jobradar/api-v1')).expanduser(),
        evidence_path=Path(os.environ['JOBRADAR_EVIDENCE_DB']) if 'JOBRADAR_EVIDENCE_DB' in os.environ else None,
        dataset_id=os.getenv('JOBRADAR_DATASET_ID','v1-evidence'),
        index_root=Path(os.environ['JOBRADAR_INDEX_ROOT']) if 'JOBRADAR_INDEX_ROOT' in os.environ else None)
    app=create_legacy_app(legacy_database_path)
    service=OfflineService(settings, mock_backend=mock_backend, live_backend=live_backend)
    @asynccontextmanager
    async def lifespan(app):
        service.start()
        app.state.offline_service=service
        try:yield
        finally:service.close()
    app.router.lifespan_context=lifespan
    app.title='JobRadar V1 整合 API（真实适配：离线验收）'
    app.version='1.1.0'
    app.add_middleware(BodyLimit)

    def wrap(request,data=None,error=None):
        rid=getattr(request.state,'request_id','req_'+uuid.uuid4().hex)
        return Envelope(request_id=rid,data=data,error=error).model_dump(mode='json')

    @app.exception_handler(ServiceError)
    async def service_error(request,exc):
        return JSONResponse(status_code=ERROR_HTTP[exc.code],content=wrap(request,error=ApiError(code=exc.code,fields=exc.fields)))

    # Preserve old /jobs errors, including its original validation envelope.
    original_validation=app.exception_handlers[RequestValidationError]
    original_http=app.exception_handlers[StarletteHTTPException]
    @app.exception_handler(RequestValidationError)
    async def invalid(request,exc):
        if not request.url.path.startswith('/v1'):return await original_validation(request,exc)
        allowed={'body','query','path','header','mode','profile_id','match_id','candidate_keys',
                 'dataset_id','job_id','retrieval','preferences','purpose','top_n','limit','offset',
                 'format','resume_id','report_id','file'}
        fields=['.'.join(str(x) if isinstance(x,int) or str(x) in allowed else '__field__'
                         for x in e['loc']) for e in exc.errors()][:8]
        return JSONResponse(status_code=422,content=wrap(request,error=ApiError(code='invalid_request',fields=fields)))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request,exc):
        if not request.url.path.startswith('/v1'):return await original_http(request,exc)
        code={404:'resource_not_found',405:'method_not_allowed'}.get(exc.status_code,'invalid_request')
        return JSONResponse(status_code=ERROR_HTTP[code],content=wrap(request,error=ApiError(code=code)))

    @app.exception_handler(ResponseValidationError)
    async def invalid_response(request,exc):
        return JSONResponse(status_code=500,content=wrap(request,error=ApiError(code='internal_error')))

    @app.exception_handler(OSError)
    async def storage_error(request,exc):
        if not request.url.path.startswith('/v1'):
            return JSONResponse(status_code=500,content=wrap(request,error=ApiError(code='internal_error')))
        return JSONResponse(status_code=503,content=wrap(request,error=ApiError(code='store_unavailable')))

    @app.exception_handler(Exception)
    async def unexpected(request,exc):
        return JSONResponse(status_code=500,content=wrap(request,error=ApiError(code='internal_error')))

    def typed(data, model):
        return model.model_validate_json(json.dumps(data,ensure_ascii=False))

    responses={str(status):{'model':Envelope[dict]} for status in set(ERROR_HTTP.values())}

    @app.get('/v1/ready',response_model=Envelope[Ready],responses=responses)
    def ready(request:Request):
        state=service.ready()
        return JSONResponse(status_code=200 if state['status']=='ready' else 503,content=wrap(request,data=state))

    def accepted(record):
        plural={'profile':'profiles','match':'matches','report':'reports'}[record['kind']]
        return Accepted(resource_id=record['resource_id'],state=record['state'],
                        status_url='/v1/'+plural+'/'+record['resource_id']).model_dump(mode='json')

    from .api_v1_resumes import register_routes as resume_routes
    from .api_v1_matching import register_routes as matching_routes
    from .api_v1_reports import register_routes as report_routes
    from .api_v1_pdf_report import register_routes as pdf_report_routes
    for register in (resume_routes, matching_routes, report_routes):
        register(app,service,wrap=wrap,typed=typed,accepted=accepted,responses=responses)
    pdf_report_routes(app, service, wrap=wrap, accepted=accepted, responses=responses)
    return app


app=create_app()
