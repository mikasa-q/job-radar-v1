"""Dependency-free BM25 over the frozen Stage 2 lexical projection.

This is a weighted-token BM25 baseline, not BM25F. Title counts and lengths are
multiplied by title_weight; the body uses unit weight. IDF is always positive:
log(1 + (N - df + .5) / (df + .5)). Query terms contribute once each. No matching
requirements, embeddings, files, clocks or database calls are involved here.
"""
from __future__ import annotations

from collections import Counter
import math

from .matching_models import _plain
from .retrieval_models import (
    EngineConfig, RetrievalDocument, RetrievalError, RetrievalHit, RetrievalQuery,
)
from .retrieval_text import key_tuple


def _terms(values: list[str]) -> list[str]:
    # Projection owns tokenization/aliases; do not create a second tokenizer.
    if any(not term.strip() for term in values):
        raise RetrievalError('invalid_lexical_term')
    return values


def retrieve_bm25(
    query: RetrievalQuery, documents: list[RetrievalDocument], config: EngineConfig,
) -> list[RetrievalHit]:
    """Return positive lexical hits, score-descending then stable job identity.

    Inputs are detached and revalidated, including mutable nested values and
    model_copy bypasses. All document identities are validated before scoring.
    Scores only express lexical relevance; matched_terms are not requirements
    or proof of skill proficiency. recall_k limits this channel, not top_k.
    """
    if not isinstance(query, RetrievalQuery):
        raise TypeError('expected_retrieval_query')
    if not isinstance(config, EngineConfig):
        raise TypeError('expected_engine_config')
    if not isinstance(documents, list) or any(
        not isinstance(item, RetrievalDocument) for item in documents
    ):
        raise TypeError('expected_retrieval_documents')
    query = RetrievalQuery.model_validate(_plain(query))
    config = EngineConfig.model_validate(_plain(config))
    documents = [RetrievalDocument.model_validate(_plain(item)) for item in documents]
    if len(documents) > config.max_jobs:
        raise RetrievalError('too_many_jobs')
    if len(query.text) > config.max_text_chars:
        raise RetrievalError('query_text_too_long')
    identities = [key_tuple(item.key) for item in documents]
    if len(set(identities)) != len(identities):
        raise RetrievalError('duplicate_document_identity')
    terms = list(dict.fromkeys(_terms(query.terms)))
    frequencies = []
    for item in documents:
        if len(item.text) > config.max_text_chars:
            raise RetrievalError('job_text_too_long')
        body = Counter(_terms(item.terms))
        title = Counter(_terms(item.title_terms))
        counts = {term: body[term] + config.title_weight * title[term]
                  for term in body.keys() | title.keys()}
        frequencies.append({term: count for term, count in counts.items() if count > 0})
    if not documents or not terms or not query.text.strip():
        return []
    # fsum avoids term-set/hash order or input-order changing normalization
    # through ordinary floating-point accumulation of fractional title weights.
    lengths = [math.fsum(freq.values()) for freq in frequencies]
    average_length = math.fsum(lengths) / len(documents)
    if average_length == 0:
        return []
    corpus_size = len(documents)
    df = {term: sum(term in freq for freq in frequencies) for term in terms}
    idf = {term: math.log1p((corpus_size - count + 0.5) / (count + 0.5))
           for term, count in df.items()}
    scored = []
    for document, frequency, length in zip(documents, frequencies, lengths):
        matched = [term for term in terms if term in frequency]
        if not matched:
            continue
        normalization = config.k1 * (1 - config.b + config.b * length / average_length)
        score = math.fsum(
            idf[term] * frequency[term] * (config.k1 + 1)
            / (frequency[term] + normalization)
            for term in matched
        )
        if not math.isfinite(score):
            raise RetrievalError('nonfinite_bm25_score')
        if score > 0:
            scored.append((document.key, score, matched))
    scored.sort(key=lambda row: (-row[1], key_tuple(row[0])))
    return [RetrievalHit(key=key, channel='bm25', score=score, rank=rank,
                         matched_terms=matched)
            for rank, (key, score, matched) in enumerate(scored[:config.recall_k], 1)]
