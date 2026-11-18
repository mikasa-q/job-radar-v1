"""Independent, opt-in MatchReport provider adapter; no live work at import.

A new batch requires explicit rates, budget and input/output allocations. The
input gate is serialized UTF-8 bytes plus an allowance, NOT exact billable token
counting. Every attempted request consumes its full reservation, with zero
retries. Local semantic/evidence validation stays outside this transport layer.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from decimal import Decimal, InvalidOperation

import httpx2
import openai

from .report_models import (AnalysisRequest, AnalysisResponse, CallAudit,
                            MatchAnalysisDraft, PROMPT_VERSION, ReportError, strict_json)
from .retrieval_models import JobKey
from .retrieval_text import retrieval_text
from .resume_structure_apinebula import RateCard

BASE_URL = 'https://apinebula.ai/v1'
MODEL = 'gpt-6.1-sol'
REASONING_EFFORT = 'low'
SDK_VERSION = '3.49.0'
TIMEOUT_SECONDS = 90.0
MAX_WIRE_BYTES = 1024 * 1024
LOCAL_OVERHEAD_ALLOWANCE = 1024
_LOCAL_FIELDS = frozenset({'quote','resume_ref','job_span','resume_evidence','job_evidence',
    'provenance','evidence','url','source_file','source_path','file_path','source_sha256',
    'jd_sha256','fact_sha256','source_file_sha256','span','mapping','encoding_text',
    'encoding_sha256','block_id','page','start','end'})


class AnalysisProviderError(ReportError):
    """Fixed, bounded code only; never provider body or generated content."""


def _integer(value, minimum=0, maximum=None):
    return type(value) is int and value >= minimum and (maximum is None or value <= maximum)


def _budget(value):
    try:
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError
        result = Decimal(value)
        if not result.is_finite() or result <= 0 or not -18 <= result.adjusted() <= 12:
            raise ValueError
        return result
    except (ValueError, InvalidOperation):
        raise AnalysisProviderError('invalid_budget') from None


def strict_schema() -> dict:
    """Provider-only conversion: keep original local schema unchanged."""
    def visit(node):
        if isinstance(node, list):
            return [visit(item) for item in node]
        if not isinstance(node, dict):
            return node
        value = {key: visit(item) for key, item in node.items() if key not in {'default', 'title'}}
        if value.get('type') == 'object':
            value['additionalProperties'] = False
            value['required'] = list(value.get('properties', {}))
        return value
    return visit(MatchAnalysisDraft.model_json_schema())


def _safe_strings(value):
    """Check minimized data text recursively, including decoded value objects."""
    if isinstance(value, str):
        return retrieval_text(value) == value and not re.search(r'(?i)(?:file://|/(?:Users|home|private|tmp|var|Volumes|etc|opt|Applications|Library|usr|root)/|[A-Za-z]:\\)', value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _safe_strings(k) and _safe_strings(v) for k, v in value.items())
    if isinstance(value, list):
        return all(_safe_strings(item) for item in value)
    return value is None or type(value) in (int, bool) or (type(value) is float and math.isfinite(value))


def _minimized(value):
    if isinstance(value,dict):
        return not set(value).intersection(_LOCAL_FIELDS) and all(_minimized(v) for v in value.values())
    if isinstance(value,list): return all(_minimized(v) for v in value)
    return True


def _check_packet(data):
    """Transport shape check; references are still resolved by local analyzer."""
    if not isinstance(data, dict) or set(data) != {'schema_version','key','purpose','context','evidence','limitations'}:
        raise ValueError
    if data['schema_version'] != 'match-analysis-input-v1' or data['purpose'] not in {'debug','formal'}:
        raise ValueError
    key = JobKey.model_validate(data['key'])
    context=data['context']
    if (not isinstance(context,dict) or set(context)!={'title','rank','bm25','dense','fusion_score','rules','channels','retrieval_status','actual_mode'}
            or (context['title'] is not None and not isinstance(context['title'],str))
            or not _integer(context['rank'],1,1000)
            or type(context['fusion_score']) not in (int,float) or not math.isfinite(context['fusion_score']) or context['fusion_score']<0
            or not isinstance(context['rules'],dict) or not isinstance(context['channels'],list)
            or any(context[channel] is not None and not isinstance(context[channel],dict) for channel in ('bm25','dense'))
            or context['retrieval_status'] not in {'ok','degraded','no_query','no_candidates','no_hits'}
            or context['actual_mode'] not in {'bm25_rules','hybrid'}
            or not isinstance(data['limitations'],list)):
        raise ValueError
    if not all(isinstance(v, str) for v in data['limitations']):
        raise ValueError
    records = data['evidence']
    if not isinstance(records, list) or not 0 < len(records) <= 10000:
        raise ValueError
    ids = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {'evidence_id','kind','path','key','content'}:
            raise ValueError
        ident = record['evidence_id']
        if not isinstance(ident, str) or not re.fullmatch(r'e[0-9a-f]{24}',ident) or ident in ids:
            raise ValueError
        ids.add(ident)
        if record['kind'] not in {'resume','job','rule','retrieval','preference','scope'}:
            raise ValueError
        if (not isinstance(record['path'],str) or not 0 < len(record['path']) <= 1024
                or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.\[\]:-]*',record['path']) is None):
            raise ValueError
        if record['key'] is not None and JobKey.model_validate(record['key']) != key:
            raise ValueError
        if record['kind'] in {'job','rule','retrieval'} and record['key'] is None:
            raise ValueError
    if not _minimized(data['context']) or any(not _minimized(r['content']) for r in records):
        raise ValueError
    # Structural IDs/paths already have typed/grammar checks. Do not treat
    # digit runs inside hashes or block IDs as phone numbers. Only content
    # strings undergo contact minimization verification.
    if (not _safe_strings(data['context']) or not _safe_strings(data['limitations'])
            or any(not _safe_strings(r['content']) for r in records)):
        raise ValueError
    if (not _safe_strings(key.dataset_id) or '/' in key.dataset_id or '\\' in key.dataset_id
            or any(r['kind'] in {'resume','preference','scope'} and r['key'] is not None for r in records)):
        raise ValueError


class APINebulaAnalysisClient:
    """A serialized new batch, explicit limits, fixed endpoint, zero retries.

    ``budget_usd`` is compared with RateCard.usd_reserve_per_unit, which is an
    explicit budget-allocation conversion, not an asserted exchange rate.
    Transport timeout/deadline checks are not a hard process-kill guarantee.
    """
    def __init__(self, rates: RateCard, *, budget_usd: str,
                 max_input_tokens: int, max_output_tokens: int,
                 max_attempts: int = 3, timeout_seconds: float = TIMEOUT_SECONDS,
                 _transport=None):
        if not isinstance(rates, RateCard):
            raise AnalysisProviderError('tariff_required')
        # Frozen RateCard can still be maliciously mutated via object.__setattr__.
        try:
            rates = RateCard(**vars(rates))
        except Exception:
            raise AnalysisProviderError('invalid_tariff') from None
        budget = _budget(budget_usd)
        if (not _integer(max_input_tokens,1,1024*1024)
                or not _integer(max_output_tokens,1,65536)
                or not _integer(max_attempts,1,20)
                or type(timeout_seconds) not in (int,float)
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= TIMEOUT_SECONDS):
            raise AnalysisProviderError('invalid_limits')
        if openai.__version__ != SDK_VERSION:
            raise AnalysisProviderError('sdk_version_mismatch')
        key = os.environ.get('OPENAI_API_KEY','')
        if not key.strip():
            raise AnalysisProviderError('missing_api_key')
        http = httpx2.Client(transport=_transport,trust_env=False,follow_redirects=False,timeout=timeout_seconds)
        try:
            self._sdk = openai.OpenAI(api_key=key,base_url=BASE_URL,max_retries=0,
                organization='',project='',http_client=http,timeout=timeout_seconds)
        except Exception:
            http.close()
            raise AnalysisProviderError('sdk_configuration_error') from None
        self._rates = rates
        self._budget = budget
        self._max_input = max_input_tokens
        self._max_output = max_output_tokens
        self._max_attempts = max_attempts
        self._timeout = float(timeout_seconds)
        self._lock = threading.Lock()
        self._closed = False
        self._halted = None
        self._checks = self._attempts = 0
        self._reserved_usd = Decimal(0)
        self._reserved_units = Decimal(0)
        self._reported_units = Decimal(0)
        self._usage = []
        self._last_audit = None

    @property
    def audit(self):
        with self._lock:
            return {'provider':'apinebula','model':MODEL,'reasoning':REASONING_EFFORT,
                'local_count_method':'serialized_utf8_bytes_plus_1024_allowance',
                'local_count_checks':self._checks,'count_api_requests':0,
                'generation_attempts':self._attempts,'max_attempts':self._max_attempts,
                'max_input_allocation':self._max_input,'max_output_tokens':self._max_output,
                'billing_unit':self._rates.unit,'reserved_usd':str(self._reserved_usd),
                'reserved_units':str(self._reserved_units),
                'reported_generation_cost_ceiling_units':str(self._reported_units) if len(self._usage)==self._attempts else None,
                'usage_known':len(self._usage)==self._attempts,'halted':self._halted,
                'usage':[dict(item) for item in self._usage]}

    @property
    def last_call_audit(self):
        with self._lock:
            return None if self._last_audit is None else CallAudit.model_validate(self._last_audit.model_dump())

    def halt(self, code='analysis_validation_failed'):
        """Allow the orchestrator to stop the batch after local validation fails."""
        if code not in {'analysis_validation_failed','analysis_batch_stopped'}:
            raise AnalysisProviderError('invalid_halt_code')
        with self._lock:
            self._halted = code

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._sdk.close()

    def _remaining(self, deadline):
        value = deadline-time.monotonic()
        if value <= 0:
            raise TimeoutError('provider_timeout')
        return min(value,self._timeout)

    def _read(self, response, deadline):
        body = bytearray()
        for chunk in response.iter_bytes(chunk_size=4096):
            self._remaining(deadline)
            if len(body)+len(chunk)>MAX_WIRE_BYTES:
                raise AnalysisProviderError('provider_body_limit')
            body.extend(chunk)
        self._remaining(deadline)
        try:
            value = strict_json(body.decode('utf-8'))
            if not isinstance(value,dict): raise ValueError
            return value
        except (ValueError,UnicodeError,TypeError,RecursionError):
            raise AnalysisProviderError('invalid_provider_json') from None

    def _payload(self, request):
        from .report_analysis import SYSTEM_PROMPT
        try:
            if (not isinstance(request,AnalysisRequest)
                    or request.system_prompt != SYSTEM_PROMPT
                    or request.prompt_version != PROMPT_VERSION
                    or strict_json(request.output_schema_json) != MatchAnalysisDraft.model_json_schema()
                    or type(request.timeout_seconds) not in (float,int)
                    or not math.isfinite(request.timeout_seconds)
                    or not 0 < request.timeout_seconds <= self._timeout
                    or not _integer(request.max_output_bytes,256,1024*1024)
                    or not isinstance(request.packet_json,str)
                    or len(request.packet_json.encode('utf-8'))>1024*1024):
                raise ValueError
            data = strict_json(request.packet_json)
            _check_packet(data)
        except (ValueError,TypeError,AttributeError,KeyError,UnicodeError,RecursionError,OverflowError):
            raise AnalysisProviderError('invalid_analysis_request') from None
        return {'model':MODEL,'instructions':request.system_prompt,'input':request.packet_json,
            'reasoning':{'effort':REASONING_EFFORT},
            'text':{'format':{'type':'json_schema','name':'match_analysis_draft','strict':True,'schema':strict_schema()}},
            'truncation':'disabled','max_output_tokens':self._max_output,
            'store':False,'tools':[],'stream':False}

    def complete(self, request: AnalysisRequest) -> AnalysisResponse:
        with self._lock:
            if self._closed: raise AnalysisProviderError('client_closed')
            if self._halted: raise AnalysisProviderError('run_halted')
            self._last_audit = None
            started = time.monotonic()
            try:
                response=self._complete(request,started)
                elapsed=max(0.0,time.monotonic()-started)
                if self._last_audit is not None:
                    self._last_audit.elapsed_seconds=elapsed
                    response=AnalysisResponse(status=response.status,text=response.text,audit=self._last_audit)
                return response
            except (TimeoutError,openai.APITimeoutError):
                self._halted='provider_timeout'
                raise TimeoutError('provider_timeout') from None
            except AnalysisProviderError as error:
                self._halted=error.code
                raise AnalysisProviderError(error.code) from None
            except openai.APIStatusError as error:
                self._halted=f'provider_http_{error.status_code}'
                raise AnalysisProviderError(self._halted) from None
            except Exception:
                self._halted='provider_error'
                raise AnalysisProviderError('provider_error') from None
            finally:
                if self._last_audit is not None:
                    self._last_audit.elapsed_seconds=max(0.0,time.monotonic()-started)

    def _complete(self,request,started):
        payload=self._payload(request)
        # This includes the complete outgoing body, prompt, schema and fixed flags.
        allocated=len(json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8'))+LOCAL_OVERHEAD_ALLOWANCE
        self._checks+=1
        if allocated>self._max_input: raise AnalysisProviderError('input_allocation_limit')
        ceiling_units=self._rates.generation_units(self._max_input,self._max_output)
        ceiling_usd=ceiling_units*Decimal(self._rates.usd_reserve_per_unit)
        if self._attempts>=self._max_attempts: raise AnalysisProviderError('attempt_limit')
        if self._reserved_usd+ceiling_usd>self._budget: raise AnalysisProviderError('budget_limit')
        deadline=started+min(request.timeout_seconds,self._timeout)
        timeout=self._remaining(deadline)
        self._attempts+=1
        self._reserved_units+=ceiling_units
        self._reserved_usd+=ceiling_usd
        self._last_audit=CallAudit(provider='apinebula',model=MODEL,attempts=1,
            reserved_units=str(ceiling_units),billing_unit=self._rates.unit)
        with self._sdk.responses.with_streaming_response.create(**payload,timeout=timeout) as response:
            result=self._read(response,deadline)
        self._record_usage(result,allocated)
        if result.get('model')!=MODEL: raise AnalysisProviderError('response_model_mismatch')
        status=result.get('status')
        if status=='incomplete':
            self._halted='incomplete_response'
            return AnalysisResponse(status='incomplete',audit=self._last_audit)
        if status!='completed': raise AnalysisProviderError('invalid_provider_status')
        output=result.get('output')
        if not isinstance(output,list): raise AnalysisProviderError('invalid_provider_output')
        texts=[]
        for item in output:
            if not isinstance(item,dict): raise AnalysisProviderError('invalid_provider_output')
            if item.get('type')=='reasoning': continue
            if (item.get('type')!='message' or item.get('role')!='assistant'
                    or item.get('status')!='completed' or not isinstance(item.get('content'),list)):
                raise AnalysisProviderError('invalid_provider_output')
            for part in item['content']:
                if not isinstance(part,dict): raise AnalysisProviderError('invalid_provider_output')
                if part.get('type')=='refusal':
                    self._halted='model_refused'
                    return AnalysisResponse(status='refused',audit=self._last_audit)
                if part.get('type')!='output_text' or not isinstance(part.get('text'),str):
                    raise AnalysisProviderError('invalid_provider_output')
                texts.append(part['text'])
        if len(texts)!=1: raise AnalysisProviderError('invalid_provider_output')
        try:
            size=len(texts[0].encode('utf-8'))
        except UnicodeError: raise AnalysisProviderError('invalid_provider_output') from None
        if size>request.max_output_bytes: raise AnalysisProviderError('output_byte_limit')
        return AnalysisResponse(status='completed',text=texts[0],audit=self._last_audit)

    def _record_usage(self,result,allocated):
        value=result.get('usage')
        if not isinstance(value,dict): raise AnalysisProviderError('unknown_usage')
        inp,out,total=(value.get(k) for k in ('input_tokens','output_tokens','total_tokens'))
        if not all(_integer(v) for v in (inp,out,total)) or inp<1 or total!=inp+out:
            raise AnalysisProviderError('unknown_usage')
        if inp>self._max_input or out>self._max_output: raise AnalysisProviderError('usage_limit')
        self._reported_units+=self._rates.generation_units(inp,out)
        self._usage.append(dict(local_input_allocation=allocated,input_tokens=inp,output_tokens=out,total_tokens=total))
        values=self._last_audit.model_dump()
        values.update(input_tokens=inp,output_tokens=out,total_tokens=total)
        self._last_audit=CallAudit.model_validate(values)
