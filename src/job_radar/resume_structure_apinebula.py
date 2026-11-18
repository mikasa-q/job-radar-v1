"""Explicit APINebula Responses adapter; no calls on import or construction.

Tariffs must be supplied in the token group's billing unit, with an explicit
USD budget reservation per billing unit. Public list prices are not used as account-specific prices.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx2
import openai

from .resume_structure import (
    ClientResponse, DEFAULT_MAX_BLOCKS, DEFAULT_MAX_INPUT_BYTES,
    DEFAULT_MAX_OUTPUT_BYTES, PROMPT_VERSION, ResumeDraft, StructureRequest,
    SYSTEM_PROMPT,
)

BASE_URL = "https://apinebula.ai/v1"
MODEL = "gpt-6.1-sol"
REASONING_EFFORT = "low"
SDK_VERSION = "3.49.0"
MAX_INPUT_TOKENS = 12000
MAX_OUTPUT_TOKENS = 8192
MAX_ATTEMPTS = 3
BUDGET_USD = Decimal("0.20")
TIMEOUT_SECONDS = 90.0
# JSON escaping/envelope overhead is separate from the 128 KiB model text cap.
MAX_WIRE_BYTES = 1024 * 1024
LOCAL_OVERHEAD_ALLOWANCE = 1024


class APINebulaError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _decimal(value, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise APINebulaError("invalid_tariff")
    try:
        result = Decimal(value)
        if not result.is_finite() or result < 0 or (positive and result == 0):
            raise ValueError
        # Bound exponent/size as well as finiteness, before arithmetic.
        if len(value) > 64 or (result != 0 and not -18 <= result.adjusted() <= 12):
            raise ValueError
    except (InvalidOperation, ValueError):
        raise APINebulaError("invalid_tariff") from None
    return result


@dataclass(frozen=True)
class RateCard:
    """Explicit account tariff, never guessed from currency symbols.

    Numeric values are decimal strings; usd_reserve_per_unit reserves USD budget per
    billing unit; it is a conservative allocation, not a claimed FX rate.
    """
    group: str
    unit: str
    input_per_million: str
    output_per_million: str
    usd_reserve_per_unit: str
    cache_create_per_million: str | None = None

    def __post_init__(self):
        for label in (self.group, self.unit):
            if not isinstance(label, str) or not label.strip() or len(label) > 80:
                raise APINebulaError("invalid_tariff")
        _decimal(self.input_per_million, positive=True)
        _decimal(self.output_per_million, positive=True)
        _decimal(self.usd_reserve_per_unit, positive=True)
        if self.cache_create_per_million is not None:
            _decimal(self.cache_create_per_million, positive=True)

    def generation_units(self, input_tokens: int, output_tokens: int) -> Decimal:
        return ((Decimal(input_tokens) * (_decimal(self.input_per_million)
                    + _decimal(self.cache_create_per_million or "0"))
                 + Decimal(output_tokens) * _decimal(self.output_per_million))
                / Decimal(1000000))



def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise APINebulaError("invalid_provider_json")
        result[key] = value
    return result


def _constant(value):
    raise APINebulaError("invalid_provider_json")


def strict_schema() -> dict:
    """Require all nested fields; retain nullability and local constraints.

    Local 1B/1C-1 models stay unchanged and still validate the response.
    """
    def visit(node):
        if isinstance(node, list):
            return [visit(item) for item in node]
        if not isinstance(node, dict):
            return node
        converted = {key: visit(value) for key, value in node.items()
                     if key not in {"default", "title"}}
        if converted.get("type") == "object":
            converted["additionalProperties"] = False
            converted["required"] = list(converted.get("properties", {}))
        return converted
    return visit(ResumeDraft.model_json_schema())


def _integer(value, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


class APINebulaStructureClient:
    """One run, at most 3 local counts + 3 generations, serialized across threads.

    Counting is local only. Every attempted generation reserves the full
    12000/8192 ceiling, without refunds. Any uncertain result halts the
    run. SDK timeout is an inactivity/transport timeout, not a hard process kill.
    """
    def __init__(self, rates: RateCard, *, _transport=None):
        if not isinstance(rates, RateCard):
            raise APINebulaError("tariff_required")
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key.strip():
            raise APINebulaError("missing_api_key")
        if openai.__version__ != SDK_VERSION:
            raise APINebulaError("sdk_version_mismatch")
        # Disable redirects and env proxies; only this fixed host receives auth.
        http = httpx2.Client(transport=_transport, trust_env=False,
                             follow_redirects=False, timeout=TIMEOUT_SECONDS)
        try:
            self._sdk = openai.OpenAI(
                api_key=key, base_url=BASE_URL, max_retries=0,
                organization="", project="", http_client=http,
                timeout=TIMEOUT_SECONDS,
            )
        except Exception:
            http.close()
            raise APINebulaError("sdk_configuration_error") from None
        self._rates = rates
        self._lock = threading.Lock()
        self._counts = self._generations = 0
        self._reserved = Decimal(0)
        self._reported_units = Decimal(0)
        self._usage: list[dict] = []
        self._halted: str | None = None
        self._closed = False

    @property
    def audit(self) -> dict:
        # No token, raw request/response, exception body or model-generated text.
        return {"provider": "apinebula", "model": MODEL, "reasoning": REASONING_EFFORT,
                "local_count_checks": self._counts, "count_api_requests": 0,
                "generation_attempts": self._generations, "billing_unit": self._rates.unit,
                "reserved_usd": str(self._reserved),
                "reported_generation_cost_ceiling_units": (str(self._reported_units)
                    if len(self._usage) == self._generations else None),
                "usage_known": len(self._usage) == self._generations,
                "local_count_method": "serialized_utf8_bytes_plus_1024_allowance",
                "halted": self._halted, "usage": [dict(row) for row in self._usage]}

    def close(self):
        with self._lock:
            self._closed = True
            self._sdk.close()

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("provider_timeout")
        return min(TIMEOUT_SECONDS, remaining)

    def _read(self, response, limit: int, deadline: float) -> dict:
        body = bytearray()
        for chunk in response.iter_bytes(chunk_size=4096):
            self._remaining(deadline)
            if len(body) + len(chunk) > limit:
                raise APINebulaError("provider_body_limit")
            body.extend(chunk)
        self._remaining(deadline)
        try:
            result = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs,
                                parse_constant=_constant)
            if not isinstance(result, dict):
                raise ValueError
        except (UnicodeError, ValueError, RecursionError):
            raise APINebulaError("invalid_provider_json") from None
        return result

    def _payload(self, request: StructureRequest) -> dict:
        if not isinstance(request, StructureRequest):
            raise APINebulaError("invalid_request")
        try:
            valid = (request.system_prompt == SYSTEM_PROMPT
                     and request.prompt_version == PROMPT_VERSION
                     and json.loads(request.output_schema_json) == ResumeDraft.model_json_schema()
                     and type(request.timeout_seconds) in (int, float)
                     and math.isfinite(request.timeout_seconds)
                     and 0 < request.timeout_seconds <= TIMEOUT_SECONDS
                     and _integer(request.max_output_bytes, minimum=1)
                     and request.max_output_bytes <= DEFAULT_MAX_OUTPUT_BYTES
                     and len(request.blocks_json.encode("utf-8")) <= DEFAULT_MAX_INPUT_BYTES)
            blocks = json.loads(request.blocks_json, object_pairs_hook=_pairs,
                                parse_constant=_constant)
            valid = valid and set(blocks) == {"blocks"} and isinstance(blocks["blocks"], list)
            valid = valid and 0 < len(blocks["blocks"]) <= DEFAULT_MAX_BLOCKS
            for block in blocks["blocks"]:
                valid = valid and (isinstance(block, dict)
                    and set(block) == {"page", "block_id", "text"}
                    and _integer(block["page"], minimum=1)
                    and isinstance(block["block_id"], str)
                    and isinstance(block["text"], str) and bool(block["text"].strip()))
            if not valid:
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
            raise APINebulaError("invalid_request") from None
        return {"model": MODEL, "instructions": request.system_prompt,
                "input": request.blocks_json, "reasoning": {"effort": REASONING_EFFORT},
                "text": {"format": {"type": "json_schema", "name": "resume_draft",
                                    "strict": True, "schema": strict_schema()}},
                "truncation": "disabled"}

    def complete(self, request: StructureRequest) -> ClientResponse:
        with self._lock:
            if self._closed:
                raise APINebulaError("client_closed")
            if self._halted:
                raise APINebulaError("run_halted")
            try:
                return self._complete(request)
            except (TimeoutError, openai.APITimeoutError):
                self._halted = "provider_timeout"
                raise TimeoutError("provider_timeout") from None
            except APINebulaError as error:
                self._halted = error.code
                raise APINebulaError(error.code) from None
            except openai.APIStatusError as error:
                self._halted = f"provider_http_{error.status_code}"
                raise APINebulaError(self._halted) from None
            except Exception:
                self._halted = "provider_error"
                raise APINebulaError("provider_error") from None

    def _complete(self, request: StructureRequest) -> ClientResponse:
        payload = self._payload(request)
        # Byte-based conservative local allocation includes the *entire* wire
        # JSON (prompt/schema/data) plus template allowance; not exact billing
        # tokens. It needs no tokenizer download and makes no count API call.
        count = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + LOCAL_OVERHEAD_ALLOWANCE
        self._counts += 1
        if count > MAX_INPUT_TOKENS:
            raise APINebulaError("input_token_limit")
        ceiling_units = self._rates.generation_units(MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS)
        ceiling = ceiling_units * _decimal(self._rates.usd_reserve_per_unit)
        if self._generations >= MAX_ATTEMPTS:
            raise APINebulaError("attempt_limit")
        if self._reserved + ceiling > BUDGET_USD:
            raise APINebulaError("budget_limit")
        deadline = time.monotonic() + min(request.timeout_seconds, TIMEOUT_SECONDS)
        timeout = self._remaining(deadline)
        self._generations += 1
        self._reserved += ceiling
        with self._sdk.responses.with_streaming_response.create(
            **payload, max_output_tokens=MAX_OUTPUT_TOKENS, store=False,
            tools=[], stream=False, timeout=timeout,
        ) as response:
            result = self._read(response, MAX_WIRE_BYTES, deadline)
        self._record_usage(result, count)
        if result.get("model") != MODEL:
            raise APINebulaError("response_model_mismatch")
        status = result.get("status")
        if status == "incomplete":
            self._halted = "incomplete_response"
            return ClientResponse(status="incomplete")
        if status != "completed":
            raise APINebulaError("invalid_provider_status")
        items = result.get("output")
        if not isinstance(items, list):
            raise APINebulaError("invalid_provider_output")
        texts = []
        for item in items:
            if not isinstance(item, dict):
                raise APINebulaError("invalid_provider_output")
            if item.get("type") == "reasoning":
                continue
            if (item.get("type") != "message" or item.get("role") != "assistant"
                    or item.get("status") != "completed" or not isinstance(item.get("content"), list)):
                raise APINebulaError("invalid_provider_output")
            for part in item["content"]:
                if not isinstance(part, dict):
                    raise APINebulaError("invalid_provider_output")
                if part.get("type") == "refusal":
                    self._halted = "model_refused"
                    return ClientResponse(status="refused")
                if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                    raise APINebulaError("invalid_provider_output")
                texts.append(part["text"])
        if len(texts) != 1:
            raise APINebulaError("invalid_provider_output")
        try:
            size = len(texts[0].encode("utf-8"))
        except UnicodeError:
            raise APINebulaError("invalid_provider_output") from None
        if size > request.max_output_bytes:
            raise APINebulaError("output_byte_limit")
        return ClientResponse(status="completed", text=texts[0])

    def _record_usage(self, result: dict, counted: int):
        usage = result.get("usage")
        if not isinstance(usage, dict):
            raise APINebulaError("unknown_usage")
        inp, out, total = (usage.get(key) for key in ("input_tokens", "output_tokens", "total_tokens"))
        if not all(_integer(v) for v in (inp, out, total)) or inp < 1 or total != inp + out:
            raise APINebulaError("unknown_usage")
        if inp > MAX_INPUT_TOKENS or out > MAX_OUTPUT_TOKENS:
            raise APINebulaError("usage_limit")
        self._reported_units += self._rates.generation_units(inp, out)
        self._usage.append({"local_input_allocation": counted, "input_tokens": inp,
                            "output_tokens": out, "total_tokens": total})
