"""Citation identity and verbatim-quote checks, not a semantic entailment judge."""

import hashlib
import re

_CITATION_START = re.compile(r"【([^【】｜\n]+)｜([^【】｜\n]+)｜([^【】｜\n]+)】\s*「")


def citation_label(reference: dict) -> str:
    return f"【{reference['source']}｜{reference['title']}｜{reference['version']}】"


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def check_citations(text: str, references: list[dict]) -> tuple[list[str], set[tuple[str, str]]]:
    """Validate every citation against returned evidence; do not infer answerability.

    Whitespace in a quotation may change. Wording, numbers and punctuation may not.
    A valid quote proves provenance only; support for a claim needs separate review.
    """
    failures = []
    known = {}
    for ref in references:
        if not isinstance(ref, dict) or not all(
            isinstance(ref.get(key), str) and ref[key]
            for key in ("source", "title", "version", "text", "text_sha256")
        ):
            failures.append("citation_invalid_evidence")
            continue
        key = (ref["source"], ref["title"], ref["version"])
        if key in known and known[key]["text"] != ref["text"]:
            failures.append("citation_conflicting_reference")
        known[key] = ref
    cited = set()
    matches = []
    cursor = 0
    outside = []
    while match := _CITATION_START.search(text, cursor):
        outside.append(text[cursor:match.start()])
        depth = 1
        end = match.end()
        while end < len(text) and depth:
            if text[end] == "「":
                depth += 1
            elif text[end] == "」":
                depth -= 1
            end += 1
        if depth:
            failures.append("citation_format")
            cursor = len(text)
            break
        matches.append((*match.groups(), text[match.end():end - 1]))
        cursor = end
    outside.append(text[cursor:])
    if any("【" in part or "】" in part for part in outside):
        failures.append("citation_format")
    for source, title, version, quote in matches:
        ref = known.get((source, title, version))
        if ref is None:
            failures.append("citation_unknown_reference")
            continue
        if ref.get("text_sha256") != text_digest(ref["text"]):
            failures.append("citation_evidence_hash")
            continue
        compact = _compact(quote)
        if len(compact) < 6 or compact not in _compact(ref["text"]):
            failures.append("citation_not_verbatim")
            continue
        cited.add((source, title))
    return list(dict.fromkeys(failures)), cited
