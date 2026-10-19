"""Controlled retrieval isolates generation; no real or proxy embedding requests."""

import hashlib
import json
from datetime import UTC, datetime

from rag.corpus import load_corpus
from rag.index import PolicyIndex

from evals.grounding_cases import GROUNDING_CASES, GROUNDING_FIXTURE_VERSION
from evals.policy_runtime import B5Harness


class _ControlledVector:
    def __init__(self, matched=True):
        self.matched = matched

    def embed(self, texts):
        return [[1.0, 0.0] if self.matched else [0.0, 1.0] for _ in texts]


def build_grounding_harness() -> B5Harness:
    """Return full MCP resolvers with real corpus text and controlled ranking/states.

    Each case has a fixed evidence scope for *any* query. This measures answering
    given retrieved evidence, not retrieval accuracy; the 36-query benchmark is separate.
    """
    corpus = load_corpus()
    resolvers = {}
    fixtures = []
    for case in GROUNDING_CASES:
        sections = set(case.sections)
        chunks = [c.model_copy(deep=True) for c in corpus if (c.source, c.title) in sections]
        if len(chunks) != len(sections):
            raise ValueError(f"Grounding fixture has missing/duplicate corpus sections: {case.id}")
        if case.expected_policy_state == "conflict":
            other = chunks[0].model_copy(deep=True)
            other.metadata.version = "controlled-overlapping-version"
            chunks.append(other)
        elif case.expected_policy_state == "inactive":
            for chunk in chunks:
                chunk.metadata.effective_until = datetime(2026, 10, 13, tzinfo=UTC)
        vector = _ControlledVector(case.expected_policy_state != "no_match")
        index = PolicyIndex(chunks, [[1.0, 0.0] for _ in chunks])

        def resolve(index=index, vector=vector, state=case.expected_policy_state):
            if state == "unavailable":
                raise RuntimeError("controlled retrieval failure")
            return index, vector

        resolvers[case.id] = resolve
        fixtures.append(
            {
                "case": case.id,
                "state": case.expected_policy_state,
                "chunks": [c.model_dump(mode="json") for c in chunks],
            }
        )
    corpus_sha = hashlib.sha256(
        "\n".join(c.model_dump_json() for c in corpus).encode("utf-8")
    ).hexdigest()
    fixture_sha = hashlib.sha256(
        json.dumps(
            fixtures,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return B5Harness(
        policy_resolver=lambda: (_ for _ in ()).throw(ValueError("fixture not selected")),
        resolver_overrides=resolvers,
        policy_corpus_sha256=corpus_sha,
        policy_index_sha256=fixture_sha,
        embed_stats={
            "requests": 0,
            "tokens": 0,
            "mode": "controlled-retrieval",
            "fixture_version": GROUNDING_FIXTURE_VERSION,
            "fixture_sha256": fixture_sha,
            "note": "Real corpus text, controlled ranking/faults; no embedding model",
        },
    )
