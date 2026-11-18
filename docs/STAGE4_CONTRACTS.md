# Current Stage 4 validator V1 / prompt v3 contract (2026-11-16)

This section supersedes the historical v1/v2 generation and negation-policy notes below. The approved scope is Phase 0–2 only; no real request or credential read is authorized for this delivery. `STAGE4_V3_PROTOCOL.md` and its exact `STAGE4_V3_SYSTEM_PROMPT.txt` are the Phase 0 freeze. `report_prompt_v3.py` contains that exact text as a source constant, not a runtime document loader.

- New generation: exact `match-analysis-prompt-v3` prompt/version/schema gate. MatchReport stays `match-report-v1`; draft schema and report fields/types/ranks/status/review state are unchanged. Only report prompt_version permitted values/default extend to v3. v1/v2 prompt sources remain frozen.
- Whole-draft order: structure → rule → evidence → output text. Every claim completes the same layer before the next layer starts. Failures return fixed codes, retain complete rejection diagnostics only in an explicit private sidecar, keep `draft=null` and usage/reserve, and halt the serial batch. No text/JSON/reference/status repair and still one complete per job.
- Canonical subject is one exact alternative of the current job rule. requirement_kind is independent of support status and must match the actually cited same-subject rule, not just any rule with that kind. Unknown findings use kind/status unknown/unknown and cite JD + scope + the selected rule. Absence checks examine all supplied resume evidence; existing course/personal-project use conflicts with unknown. Plans are not prior use. Explicit gaps require cited same-skill direct negative original evidence; conditions, other people, double negatives and cross-skill/context splicing are not certified. Stage 1–3 rules/ranking remain unchanged.
- Output text: at most 80 original Unicode code points and a single short sentence. Unknown text is exactly `简历未提及 <subject> 使用经历。`. NFKC/casefold/whitespace removal are comparison-copy operations only. Probability/inflation terms are rejected in subject/text for all kind/status values, with no negation/source/advisory exemptions. Control/zero-width and sensitive content fail the output contract rather than being silently cleaned. Exact words/shapes and priority are frozen in the protocol; finite lexical checks do not establish universal entailment.
- New codes: `analysis_advice_requirement_mismatch`, `analysis_unknown_evidence_conflict`, `analysis_text_contract_violation`, `analysis_text_sensitive_content`, `analysis_validation_policy_mismatch`. In v3 `analysis_unknown_as_inability` denotes structured unknown-kind misuse, not old free-text denial matching. Historical diagnostics retain their original meaning/version.
- Historical v1/v2 reports are read-only display with original version/status/text preserved and an explicit not-v3-approved label. They still undergo generic structure, content hashes, claim ID/reference scope and original ranking checks. `validate_report(report, require_current_policy=True)` rejects a historical version with `analysis_validation_policy_mismatch`. Historical display does not replay or copy the removed negation helper; the same v2 version previously had multiple validator snapshots.
- Reporting remains original JSON-to-Markdown display, not structured-facts-plus-local-template V2. Authored SyntheticReportClient responses and the nine-job offline evaluation are fixtures, not model output or real acceptance. The current stop is before Phase 3; real sample/payload/limits/budget require fresh user authorization.

Observed offline baseline and modified results, final independent rollback, precise commands and changed-path hashes belong to this delivery's `VERIFICATION.txt`; no historical test count is reused as a new run.

---

## Historical contract record (retained verbatim)

# Stage 4 frozen match-report-v1 (2026-11-14)

Primary goal: explain retrieved jobs, evidence gaps and actionable next steps with citations, never hiring probability. Authoritative models: report_models.py. Existing models/algorithms/review gates stay unchanged. Initial serial offline gate tests draft JSON -> strict schema -> round-trip; it is not semantic validation or real LLM acceptance. All implementation occurs in this delivery's modified copy.

## Local vs model ownership

Local EvidencePacket binds request/document/profile/retrieval hashes, the original IndexedMatchingResult, top-N ranked-prefix selection (default 3, max20), evidence records and coded limitations. MatchReport locally controls ID/UTC timestamp/version/hashes/status/review_status=unconfirmed. Model returns MatchAnalysisDraft(key, claims) only; each claim has id/kind/subject/status/text/requirement_kind/evidence_ids/related_claim_ids. Claims: interpretation, strength, gap, unknown, recommendation. Unknown is not absence of ability. Recommendations are advisory and target non-recommendation claims. No score/probability/confidence fields.

Every evidence record has stable e+24hex ID, kind, source path/hash, optional job key, original quote and ResumeRef/JobSpan, canonical value_json. IDs derived locally from complete record sans ID. Resume/ref and JD/span resolve against original material, not sanitized positions. Evidence IDs are references, not trusted credentials. Lexical/schema checks do not prove semantic entailment.

## A: report_evidence.py + tests/test_report_evidence.py

`build_evidence_packet(request: MatchingRequest, result: IndexedMatchingResult, document: ResumeDocument, *, config: AnalysisConfig|None=None) -> EvidencePacket`.
`validate_evidence_packet(packet, request, result, document, *, config=None) -> EvidencePacket`: rebuild canonical packet and exact compare, reject mutation/stale/cross-job references. Reconstruct ResumeProfile from MatchingInput and validate_profile_evidence with original document. Check source/identity/purpose/review fields, result candidate hashes/provenance/review, recompute rule results, check all job spans and chunk coordinates/hash. For lexical actual mode, compare rank/BM25/fusion to original match_jobs without requesting embeddings (include appropriate config handling). Hybrid input snapshot binding cannot attest original dense query computation; preserve explicit limitation, do not invent authenticity.
Evidence should include verified projected resume facts with context/full block, JD full description/title/other relevant source fields (not only best chunk), per-skill/constraint rules, retrieval decomposition, explicit preferences, and scope record for full supplied projected profile (absence not original document/ability absence). Job evidence per full field and rule spans; rules include JD/resume supporting refs. Stable deterministic IDs and detached copies. No I/O/credentials/provider/reranking.

## B: report_analysis.py + tests/test_report_analysis.py

`SYSTEM_PROMPT`; `build_analysis_request(packet: EvidencePacket, key: JobKey, *, config: AnalysisConfig|None=None) -> AnalysisRequest`.
`validate_analysis(draft: MatchAnalysisDraft, packet: EvidencePacket, key: JobKey) -> MatchAnalysisDraft`.
`analyze_job(packet, key, *, client: AnalysisClient, config=None) -> JobAnalysis`.
Wire JSON must be {schema_version:'match-analysis-input-v1', key, purpose, context, evidence, limitations}; context is selected job title and existing rank/scores/rules/channel statuses minimally projected. Only global/that-job records. Wire evidence retains IDs/kinds/path/key but sends sanitized textual content, no original quote/ref/source URLs/file paths, no contact data or whole local packet. Use retrieval_text minimization on every outward string value; no raw JSON nested quote escape leakage. Limits include schema+prompt+data; no silent truncation. Request schema equals MatchAnalysisDraft.model_json_schema(). One client.complete only; fixed errors, no retry/repair. Validate scope/key/evidence linkage; supported strengths require job+resume and reliable matching rule support; explicit gaps require explicit negative resume context (not mere rule not_found); unknown absence needs job+scope; advice linked to actual finding and relevant job evidence. Reject invented percentages/probabilities, unsupported proficiency/experience assertions via bounded conservative guards; these are not universal semantic verification. Facts vs interpretations vs advice distinguished. Fake responses prove wiring only. Exceptions use codes, not raw generated/provider text.

## C: report_apinebula.py + tests/test_report_apinebula.py

Independent `APINebulaAnalysisClient` implements AnalysisClient; no reuse/relaxing ResumeDraft-specific existing adapter. Imports/env behavior: module import no key/network; constructor explicitly takes rates/budget/input/output limits/attempt limit and reads OPENAI_API_KEY only then. Reference existing fixed SDK3.49.0/httpx2 Responses strict-schema conversion, zero SDK/application retries, fixed host/model/low, tools=[] store=False stream=False truncation=disabled, no redirects/proxies, bounded bytes/deadline, validated usage/model/completed envelope. Default max attempts3, transport timeout90, no count API; explicit batch budget and rates are required (no old monetary authorization inheritance). Payload must enforce new AnalysisRequest prompt/version/schema and wire shape, not arbitrary instructions. Audit exposes only counters/usage/reserved budget/fixed codes. Failure takes attempt/full reserve; unknown usage stops batch; strict/local failures must stop engine batch. Pure MockTransport tests only, no live runner or credentials access. C may import B's SYSTEM_PROMPT lazily, not edit B/shared files.

## Primary integration

`generate_match_report(request,result,document,*,report_id,generated_at,client=None,config=None)` builds+revalidates packet, preserves all ranks and excluded/channel states; serial at most selected count, stop remaining after failure, retain retrieval and explicit failed/not_analyzed status; client=None has zero network. Validate draft citations before report assembly and revalidate before deterministic Markdown rendering. JSON is the canonical source; export both without overwriting originals. Empty/no-query/no-hit: local report, no client call. Shared contract/schema/core/engine/docs/examples/NOTES/dependencies owned only by primary.

Acceptance: mechanical tests, provider MockTransport, unchanged old regression, independent rollback. Real model quality and paid batch require separate confirmed configuration, limits and budget. No FastAPI/reranker/retrieval tuning/new scraping/database changes.

## Post-freeze reviewed additions

EvidenceRecord content-derived ID is checked at model boundaries (`e` + canonical_hash(record excluding ID)[:24]); not a signature. report_core assembly and renderer call validate_analysis again. Wire record uses `content` (decoded/minimized value_json), never raw value_json. Locally generated evidence path permits colon/hyphen spans/block IDs; structural IDs are not contact-cleaned. Optional provider last_call_audit/halt preserve failure reservation and terminate reused batches after local failure. Renderer escapes source text; new directory-only export does not overwrite input. Report synthetic demo uses authored findings only.


## Opt-in rejection sidecar extension (2026-11-15)

analyze_job/generate_match_report accept optional rejection_sink (default None). Only a rejected completed-response body is sent to this explicitly local sink; strict MatchReport and JobAnalysis schemas remain unchanged and failed draft stays null. report_diagnostics captures complete bounded raw response, parsed valid draft, hashes/key/version/error, and per-claim expected kinds vs actual with exact subject matching and rule evidence IDs. Validation predicates remain unchanged. FileRejectionSink creates a private new directory with exclusive files and reopens each; sink failures fail-stop with a fixed code. General logs/exceptions and wire data still contain no generated body. report_prompt_v2 is an inactive candidate only; current PROMPT_VERSION remains v1 pending user approval.


## Approved v2 activation (2026-11-15)

Active prompt/version now v2; original v1 text frozen in report_prompt_v1.py. New generation gate only accepts exact active v2 prompt/version. MatchReport remains match-report-v1 and prompt_version permits v1 or v2 for historical read/roundtrip without relabeling; no other report or draft schema fields change. Domain validators and scores remain unchanged. Single authorized live scope is S1/J1 only, top_n1/max_attempts1, same fail-stop diagnostics and zero retries; total budget CNY1.00 includes previous spend/reserve.


## Inflation-only local negation repair (2026-11-16)

The unsupported-experience lexical guard now evaluates each inflation occurrence with bounded explicit negation scope. Clause/contrast boundaries reset scope; double negation and unrecognized ambiguity remain conservative failures. No claim-wide exemption. Exact v2 prompt/version, report/draft schemas, request-builder and every other guard are unchanged. The retained real draft's c1/c2 limitation passes; its c3 still fails unchanged unknown-denial validation. Real one-shot S1/J1 is separately authorized after offline/regression/rollback gates, no retries or other jobs, cumulative CNY1.00.
