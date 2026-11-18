"""Scripted client for offline control-flow tests, not a language model.

Responses are supplied by the caller. This client neither extracts facts nor
checks a golden answer. A successful fake response proves no model accuracy.
"""
from __future__ import annotations

from collections.abc import Sequence

from .resume_structure import ClientResponse, StructureRequest


class FakeStructureClient:
    def __init__(self, responses: Sequence[ClientResponse | Exception]):
        self._responses = tuple(
            ClientResponse.model_validate(item.model_dump())
            if isinstance(item, ClientResponse) else item
            for item in responses
        )
        self.requests: list[StructureRequest] = []

    def complete(self, request: StructureRequest) -> ClientResponse:
        index = len(self.requests)
        self.requests.append(request)
        if index >= len(self._responses):
            raise RuntimeError("fake_responses_exhausted")
        response = self._responses[index]
        if isinstance(response, Exception):
            raise response
        # Rebuild at use time, so one invocation's caller cannot mutate the script.
        return ClientResponse.model_validate(response.model_dump())
