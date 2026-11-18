"""Evidence-preserving, tokenizer-budgeted JD chunks and best-chunk ranking.

The tokenizer is supplied explicitly. This module never loads a model and never
approximates tokens using characters. Sanitized encoder text has a separate
character map; evidence spans always address the unchanged source description.
"""
from __future__ import annotations

import hashlib
import re

from .matching_models import JobCandidate, _plain
from .rag_models import (
    AggregatedChunks, ChunkConfig, ChunkHit, EvidenceChunk, TextMap, TokenBudget,
)
from .retrieval_models import JobKey, JobSpan, RetrievalError, RetrievalHit
from .retrieval_text import key_tuple

# Keep the same sequential minimization patterns as retrieval_text v1. Replaced
# contact spans receive one unmapped space, not invented source coordinates.
_CONTACT_PATTERNS = (
    re.compile(r'(?i)https?://[^\s，。；;]+'),
    re.compile(r'(?i)(?<![a-z0-9_.+-])[a-z0-9_.+-]+@[a-z0-9.-]+\.[a-z]{2,}'),
    re.compile(r'(?<![a-z0-9])(?:\+?\d[ ()-]*){7,15}(?![a-z0-9])', re.I),
)
_SENTENCE = re.compile(r'[。！？!?；;][”’\"\']?|(?:\r?\n)+|\.(?:[ \t]+|$)')


def _sanitize(text: str) -> tuple[str, list[int | None]]:
    positions: list[int | None] = list(range(len(text)))
    for pattern in _CONTACT_PATTERNS:
        parts, maps, cursor = [], [], 0
        for match in pattern.finditer(text):
            parts.extend((text[cursor:match.start()], ' '))
            maps.extend(positions[cursor:match.start()]); maps.append(None)
            cursor = match.end()
        parts.append(text[cursor:]); maps.extend(positions[cursor:])
        text, positions = ''.join(parts), maps
    left = len(text) - len(text.lstrip())
    right = len(text.rstrip())
    return text[left:right], positions[left:right]


def _metadata(budget: TokenBudget) -> tuple[str, str, int]:
    try:
        identity = budget.tokenizer_id, budget.revision, budget.max_tokens
        valid = (all(isinstance(x, str) and x.strip() for x in identity[:2])
                 and type(identity[2]) is int and identity[2] >= 1
                 and callable(budget.count) and callable(budget.offsets))
    except Exception:
        valid = False
    if not valid:
        raise RetrievalError('chunk_tokenizer_metadata')
    return identity


def _count(budget: TokenBudget, text: str, metadata: tuple) -> int:
    try:
        count = budget.count(text, kind='passage')
    except Exception:
        raise RetrievalError('chunk_tokenizer_error') from None
    if _metadata(budget) != metadata:
        raise RetrievalError('chunk_tokenizer_changed')
    if type(count) is not int or count < 1:
        raise RetrievalError('chunk_invalid_token_count')
    return count


def _offsets(budget: TokenBudget, text: str, metadata: tuple) -> list[tuple[int, int]]:
    try:
        offsets = budget.offsets(text)
    except Exception:
        raise RetrievalError('chunk_tokenizer_error') from None
    if _metadata(budget) != metadata:
        raise RetrievalError('chunk_tokenizer_changed')
    if not isinstance(offsets, list) or (text.strip() and not offsets):
        raise RetrievalError('chunk_invalid_token_offsets')
    previous = (-1, -1)
    for pair in offsets:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or any(type(number) is not int for number in pair)):
            raise RetrievalError('chunk_invalid_token_offsets')
        start, end = pair
        # Some fast tokenizers repeat Unicode offsets for subword/byte pieces.
        # Non-decreasing overlapping offsets are valid; backwards spans are not.
        if not 0 <= start < end <= len(text) or start < previous[0] or end < previous[1]:
            raise RetrievalError('chunk_invalid_token_offsets')
        previous = start, end
    return [(start, end) for start, end in offsets]


def _maps(field: str, positions: list[int | None], encoded_start: int) -> list[TextMap]:
    result = []
    index = 0
    while index < len(positions):
        source = positions[index]
        if source is None:
            index += 1
            continue
        end = index + 1
        while end < len(positions) and positions[end] == source + end - index:
            end += 1
        result.append(TextMap(field=field, source_start=source,
                              source_end=source + end-index,
                              encoded_start=encoded_start+index,
                              encoded_end=encoded_start+end))
        index = end
    return result


def chunk_job(candidate: JobCandidate, *, budget: TokenBudget,
              config: ChunkConfig | None = None) -> list[EvidenceChunk]:
    """Split paragraphs/sentences, then long sentences at tokenizer boundaries.

    Title, role prefix and special tokens are included in every measured chunk.
    The effective ceiling is min(config.max_tokens, tokenizer.max_tokens).
    Token overlap uses actual tokenizer offsets and always advances. A title or
    indivisible token that exhausts the budget produces an explicit error.
    """
    candidate = JobCandidate.model_validate(_plain(candidate))
    config = ChunkConfig() if config is None else ChunkConfig.model_validate(_plain(config))
    metadata = _metadata(budget)
    ceiling = min(config.max_tokens, metadata[2])
    title, title_map = _sanitize(candidate.job.title)
    body, body_map = _sanitize(candidate.job.description)
    if not body.strip():
        return []
    prefix = title + '\n'
    if _count(budget, prefix, metadata) >= ceiling:
        raise RetrievalError('chunk_title_budget_exhausted')
    offsets = _offsets(budget, body, metadata)
    ends = sorted({end for _, end in offsets} | {len(body)})
    sentence_ends = sorted({match.end() for match in _SENTENCE.finditer(body)} | {len(body)})
    chunks = []
    start = 0
    covered_end = 0
    while start < len(body):
        while start < len(body) and body[start].isspace():
            start += 1
        if start >= len(body):
            break
        candidates = [end for end in ends if end > start]
        if not candidates:
            raise RetrievalError('chunk_invalid_token_offsets')
        # Token counts can change at a substring boundary. Binary probing need
        # not find a mathematically maximal chunk; every selected text is counted
        # again, and no source suffix is discarded.
        lo, hi, best = 0, len(candidates)-1, None
        while lo <= hi:
            middle = (lo+hi)//2
            end = candidates[middle]
            if _count(budget, prefix+body[start:end], metadata) <= ceiling:
                best = end; lo = middle+1
            else:
                hi = middle-1
        if best is None or best <= covered_end:
            if start < covered_end:
                start = covered_end
                continue  # Drop overlap rather than emit a duplicate-only chunk.
            raise RetrievalError('chunk_token_budget_exhausted')
        natural = [end for end in sentence_ends if max(start, covered_end) < end <= best]
        end = natural[-1] if natural else best
        # A sentence boundary is not guaranteed to be a token boundary. Recheck
        # its exact encoding instead of assuming the prior count applies.
        if _count(budget, prefix+body[start:end], metadata) > ceiling:
            end = best
        while end > start and body[end-1].isspace():
            end -= 1
        if end <= covered_end:
            end = best
            while end > start and body[end-1].isspace():
                end -= 1
            if end <= covered_end and start < covered_end:
                start = covered_end
                continue
        if end <= start or end <= covered_end:
            raise RetrievalError('chunk_no_progress')
        piece = body[start:end]
        selected = body_map[start:end]
        source_positions = [position for position in selected if position is not None]
        if not source_positions:
            raise RetrievalError('chunk_no_source_evidence')
        source_start, source_end = min(source_positions), max(source_positions)+1
        encoded = prefix + piece
        count = _count(budget, encoded, metadata)
        if count > ceiling:
            raise RetrievalError('chunk_token_budget_exhausted')
        chunks.append(EvidenceChunk(
            key=JobKey(dataset_id=candidate.dataset_id, job_id=candidate.job_id),
            jd_sha256=candidate.jd_sha256, ordinal=len(chunks),
            span=JobSpan(field='description', start=source_start, end=source_end,
                         quote=candidate.job.description[source_start:source_end]),
            encoding_text=encoded,
            encoding_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
            token_count=count,
            mapping=_maps('title', title_map, 0)+_maps('description', selected, len(prefix)),
        ))
        if len(chunks) > config.max_chunks_per_job:
            raise RetrievalError('chunk_limit_exceeded')
        covered_end = end
        if end >= len(body):
            break
        overlap_tokens = [(a, b) for a, b in offsets if start < a < end and b <= end]
        next_start = end
        if config.overlap_tokens and overlap_tokens:
            # Count real offset entries, not unique characters. Repeated Unicode
            # spans can represent multiple tokens; avoid exceeding the requested
            # overlap by choosing a later complete start boundary if necessary.
            take = min(config.overlap_tokens, len(overlap_tokens))
            cutoff = overlap_tokens[-take][0]
            starts = sorted({a for a, _ in overlap_tokens if a >= cutoff})
            for overlap_start in starts:
                if sum(a >= overlap_start for a, _ in overlap_tokens) <= config.overlap_tokens:
                    next_start = overlap_start
                    break
        if next_start <= start:
            raise RetrievalError('chunk_no_progress')
        start = next_start
    if _metadata(budget) != metadata:
        raise RetrievalError('chunk_tokenizer_changed')
    return chunks


def aggregate_chunks(hits: list[ChunkHit], *, recall_k: int) -> AggregatedChunks:
    """Return one job per position with the maximum chunk score, never a sum."""
    if type(recall_k) is not int or not 1 <= recall_k <= 10000:
        raise RetrievalError('chunk_invalid_recall_k')
    if not isinstance(hits, list) or any(not isinstance(hit, ChunkHit) for hit in hits):
        raise TypeError('expected_chunk_hits')
    checked = [ChunkHit.model_validate(_plain(hit)) for hit in hits]
    best, hashes, chunks = {}, {}, {}
    for hit in checked:
        identity = key_tuple(hit.chunk.key)
        if identity in hashes and hashes[identity] != hit.chunk.jd_sha256:
            raise RetrievalError('chunk_conflicting_job_hash')
        hashes[identity] = hit.chunk.jd_sha256
        chunk_id = identity, hit.chunk.ordinal
        payload = hit.chunk.model_dump_json()
        if chunk_id in chunks and chunks[chunk_id] != payload:
            raise RetrievalError('chunk_conflicting_identity')
        chunks[chunk_id] = payload
        prior = best.get(identity)
        if prior is None or (-hit.score, hit.chunk.ordinal, hit.rank) < (-prior.score, prior.chunk.ordinal, prior.rank):
            best[identity] = hit
    ordered = sorted(best.values(), key=lambda hit: (-hit.score, key_tuple(hit.chunk.key)))[:recall_k]
    return AggregatedChunks(
        hits=[RetrievalHit(key=hit.chunk.key, channel='dense', score=hit.score, rank=rank)
              for rank, hit in enumerate(ordered, 1)],
        evidence=[ChunkHit(chunk=hit.chunk, score=hit.score, rank=rank)
                  for rank, hit in enumerate(ordered, 1)],
    )
