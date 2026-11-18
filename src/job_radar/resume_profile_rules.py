"""Pure, conservative skill baseline and document-bound evidence validation.

No files, clocks, network, database, random IDs, LLMs or matching calls here.
The caller supplies resume_id. Ambiguous blocks are omitted, never repaired.
"""
from __future__ import annotations

import re
from collections.abc import Iterator

from pydantic import BaseModel

from .parser import SKILLS, extract_skills
from .resume_models import ResumeDocument
from .resume_profile import (
    EvidenceRef, ProfileSource, ResumeProfile, SkillEvidence, TextFact,
)

PROFILE_RULES_VERSION = "resume-profile-rules-v1"
# Deliberately small, explicit aliases. No fuzzy spelling correction.
_ALIASES = {name: (name,) for name in SKILLS}
_ALIASES["Python"] = ("Python", "Python3", "Python 3")
_ALIASES["pytest"] = ("pytest", "py.test")
_ALIASES["Tool Calling"] = ("Tool Calling", "Tool-Calling")
_PATTERNS = {
    name: re.compile(r"(?<![a-z0-9_])(?:" + "|".join(
        re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)
    ) + r")(?![a-z0-9_])", re.IGNORECASE)
    for name, aliases in _ALIASES.items()
}
_BLOCKED = re.compile(
    r"不(?:会|懂|熟悉|熟练|掌握|了解|使用|曾)|未(?:曾|使用|接触|掌握|学习|学过|熟悉|熟练)|"
    r"没有|从未|缺乏|尚未|暂无|没用过|还没|无.{0,12}(?:经验|基础|接触)|"
    r"计划|打算|准备学|希望学|希望掌握|想学习|将学习|拟学习|未来|待学习|待掌握|待提升|"
    r"岗位要求|招聘要求|任职要求|招聘|求职目标|期望岗位|"
    r"\b(?:no|not|never|without|lack|lacking|plan|planning|planned|intend|future|will|want|require[ds]?|requirements?)\b|"
    r"\b(?:don|doesn|didn|haven|hasn|hadn|isn|aren|wasn|weren|can|couldn|wouldn)['’]t\b",
    re.IGNORECASE,
)
_RISK_HEADING = re.compile(
    r"^(?:#+\s*)?(?:学习计划|未来计划|计划学习|计划|待学习|待掌握|岗位要求|招聘要求|任职要求|"
    r"求职目标|期望岗位|未掌握|未使用|暂未使用|不会|尚未掌握|暂无经验|待学习|待掌握|"
    r"learning plan|future plans?|requirements?|job requirements?|not used|not yet learned)(?:\s*[:：].*|\s*)$",
    re.IGNORECASE,
)
_SAFE_HEADING = re.compile(
    r"^(?:#+\s*)?(?:教育经历|教育背景|项目经历|项目经验|课程项目|个人项目|工作经历|实习经历|"
    r"专业技能|技能|技术栈|个人总结|自我评价|education|projects?|work experience|internships?|skills|summary)\s*[:：]?\s*$",
    re.IGNORECASE,
)


class ProfileEvidenceError(ValueError):
    def __init__(self, code: str, path: str):
        self.code, self.path = code, path
        # Do not put resume text in error messages or logs.
        super().__init__(f"{code}: {path}")


def source_from_document(document: ResumeDocument) -> ProfileSource:
    return ProfileSource(
        document_schema_version=document.schema_version,
        source_sha256=document.provenance.source_sha256,
        parser_version=document.provenance.parser_version,
        extractor_version=document.provenance.extractor_version,
    )


def _block_contexts(document: ResumeDocument):
    """A risky heading persists across blocks/pages until an explicit safe heading.

    Negation/future/requirements anywhere in a block excludes the *entire* block,
    including an otherwise positive clause. This trades recall for traceability.
    """
    risky_section = False
    for page in document.pages:
        for block in page.blocks:
            first_line = next((line.strip() for line in block.text.splitlines() if line.strip()), "")
            if _SAFE_HEADING.fullmatch(first_line):
                risky_section = False
            if _RISK_HEADING.match(first_line):
                risky_section = True
            blocked = risky_section or bool(_BLOCKED.search(block.text))
            yield page.page_number, block, blocked


def _context(text: str) -> str:
    if re.search(r"课程项目|course project", text, re.IGNORECASE):
        return "course"
    if re.search(r"实习|internship", text, re.IGNORECASE):
        return "internship"
    if re.search(r"项目|\bproject\b", text, re.IGNORECASE):
        return "project"
    if re.search(r"工作|任职|work experience", text, re.IGNORECASE):
        return "work"
    if re.search(r"技能|技术栈|\bskills\b", text, re.IGNORECASE):
        return "skill_list"
    return "unspecified"


def _walk(model: BaseModel, path: str = "profile") -> Iterator[tuple[str, BaseModel]]:
    yield path, model
    for name in type(model).model_fields:
        value = getattr(model, name)
        if isinstance(value, BaseModel):
            yield from _walk(value, f"{path}.{name}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, BaseModel):
                    yield from _walk(item, f"{path}.{name}[{index}]")


def validate_profile_evidence(profile: ResumeProfile, document: ResumeDocument) -> ResumeProfile:
    """Return a fully revalidated copy; review_status is preserved, not promoted.

    This verifies identity, lexical evidence and conservative skill exclusions.
    It does not prove authorship, factual truth, or general semantic entailment.
    """
    # Revalidate dumps, not existing instances: model_copy/update and mutable lists
    # can otherwise bypass Pydantic's normal construction checks.
    document = ResumeDocument.model_validate(document.model_dump())
    profile = ResumeProfile.model_validate(profile.model_dump())
    if profile.source != source_from_document(document):
        raise ProfileEvidenceError("source_mismatch", "profile.source")
    blocks = {(page, block.block_id): (block, blocked)
              for page, block, blocked in _block_contexts(document)}
    pages = {page.page_number for page in document.pages}
    for path, item in _walk(profile):
        if isinstance(item, EvidenceRef):
            if item.page not in pages:
                raise ProfileEvidenceError("missing_page", path)
            found = blocks.get((item.page, item.block_id))
            if found is None:
                raise ProfileEvidenceError("missing_block", path)
            if item.quote not in found[0].text:
                raise ProfileEvidenceError("quote_mismatch", path)
        if isinstance(item, SkillEvidence):
            pattern = _PATTERNS.get(item.name)
            if pattern is not None:
                valid_name = pattern.fullmatch(item.mention.value) is not None
            else:
                is_known_alias = any(p.fullmatch(item.name) is not None for p in _PATTERNS.values())
                valid_name = not is_known_alias and item.name.casefold() == item.mention.value.casefold()
            if not valid_name:
                raise ProfileEvidenceError("skill_name_mismatch", path)
            # A substring of Pythonista is not an occurrence of the Python token.
            mention_pattern = re.compile(
                r"(?<![a-z0-9_])" + re.escape(item.mention.value) + r"(?![a-z0-9_])",
                re.IGNORECASE,
            )
            # Check every supplied reference, not just one convenient short quote.
            for ref in item.mention.evidence:
                if mention_pattern.search(ref.quote) is None:
                    raise ProfileEvidenceError("skill_mention_mismatch", path)
                found = blocks.get((ref.page, ref.block_id))
                if found is not None and ref.quote in found[0].text:
                    # A short quote must not cut the suffix off Pythonista and
                    # manufacture a standalone Python token. Check boundaries
                    # in the original block, not just the quoted substring.
                    text = found[0].text
                    valid_occurrence = False
                    start = text.find(ref.quote)
                    tokens = list(mention_pattern.finditer(text))
                    while start >= 0:
                        end = start + len(ref.quote)
                        if any(start <= token.start() and token.end() <= end for token in tokens):
                            valid_occurrence = True
                            break
                        start = text.find(ref.quote, start + 1)
                    if not valid_occurrence:
                        raise ProfileEvidenceError("skill_mention_mismatch", path)
                if found is not None and found[1]:
                    raise ProfileEvidenceError("non_positive_skill_context", path)
    return profile


def build_rule_profile(document: ResumeDocument, *, resume_id: str) -> ResumeProfile:
    """Extract only known, positive skill mentions. Other fields remain unknown.

    One mention per canonical skill per block. Repeated skills in different
    blocks retain separate evidence. This is not a general resume parser.
    """
    document = ResumeDocument.model_validate(document.model_dump())
    skills = []
    warnings = ["rules_skills_only", "not_extracted:education", "not_extracted:projects",
                "not_extracted:work_experience", "not_extracted:summary"]
    warnings.extend(document.warnings)
    for page in document.pages:
        warnings.extend(f"page_{page.page_number}:{warning}" for warning in page.warnings)
    for page, block, blocked in _block_contexts(document):
        # Keep old word-list extraction as a baseline, adding only explicit aliases.
        candidates = set(extract_skills(block.text))
        matches = []
        for name, pattern in _PATTERNS.items():
            match = pattern.search(block.text)
            if match is not None:
                candidates.add(name)
                matches.append((match.start(), name, match.group()))
        if blocked:
            if candidates:
                warnings.append(f"excluded_skill_context:{block.block_id}")
            continue
        for _, name, original in sorted(matches):
            skills.append(SkillEvidence(
                name=name,
                mention=TextFact(value=original, evidence=[EvidenceRef(
                    page=page, block_id=block.block_id, quote=block.text,
                )]),
                context=_context(block.text),
            ))
    if not skills:
        warnings.append("no_positive_skill_evidence")
    profile = ResumeProfile(
        resume_id=resume_id, source=source_from_document(document),
        builder_version=PROFILE_RULES_VERSION, skills=skills,
        warnings=list(dict.fromkeys(warnings)),
    )
    return validate_profile_evidence(profile, document)
