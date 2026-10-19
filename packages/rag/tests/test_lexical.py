import math

import pytest
from rag.chunk import Chunk
from rag.lexical import BM25Index, RankedHit, chunk_id, reciprocal_rank_fusion, tokenize


def chunk(name, text):
    return Chunk(source=name + ".md", title=name, text=text)


def test_tokenizer_keeps_sku_numbers_and_chinese_bigrams_without_a_dictionary():
    assert tokenize("Ａ１００１, ROUND_HALF_UP 0.75 待发货") == [
        "a1001", "round_half_up", "0.75", "待发", "发货",
    ]
    assert tokenize("退货，政策") == ["退货", "政策"]
    assert tokenize("😀?!") == []


def test_bm25_matches_hand_calculated_positive_idf_and_does_not_count_query_twice():
    index = BM25Index([chunk("a", "stock stock"), chunk("b", "price")])
    hit = index.search("stock")[0]
    idf = math.log1p((2 - 1 + 0.5) / (1 + 0.5))
    expected = idf * 2 * (1.2 + 1) / (2 + 1.2 * (1 - 0.75 + 0.75 * 2 / 1.5))
    assert hit.score == pytest.approx(expected)
    assert index.search("stock stock")[0].score == hit.score
    assert hit.components == {"bm25": hit.score} and hit.ranks == {"bm25": 1}


def test_bm25_returns_no_unrelated_evidence_and_restricts_scope_before_top_k():
    a, b = chunk("a", "stock stock"), chunk("b", "stock price")
    index = BM25Index([a, b])
    assert index.search("unknown") == []
    assert index.search(" ") == []
    assert index.search("stock", allowed=set()) == []
    assert [hit.chunk for hit in index.search("stock", k=1, allowed={chunk_id(b)})] == [b]
    assert BM25Index([]).search("stock") == []


def test_bm25_uses_bodies_only_and_deterministic_ties():
    a, b = chunk("a", "same"), chunk("b", "same")
    forward, reverse = BM25Index([a, b]), BM25Index([b, a])
    assert forward.search("a") == []
    assert [hit.chunk for hit in forward.search("same")] == [
        hit.chunk for hit in reverse.search("same")
    ]
    with pytest.raises(ValueError, match="duplicate"):
        BM25Index([a, a])


@pytest.mark.parametrize("changes", [{"k1": 0}, {"k1": float("inf")},
                                     {"k1": True}, {"b": -0.1}, {"b": float("nan")}])
def test_invalid_bm25_parameters_are_rejected(changes):
    with pytest.raises(ValueError):
        BM25Index([], **changes)


def test_rrf_combines_ranks_and_preserves_scores_in_distinct_spaces():
    a, b, c = [chunk(name, name) for name in "abc"]
    result = reciprocal_rank_fusion({
        "vector": [RankedHit(a, 0.9), RankedHit(b, 0.8)],
        "bm25": [RankedHit(b, 14.0), RankedHit(c, 12.0)],
    })
    assert result[0].chunk == b
    assert result[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert result[0].components == {"vector": 0.8, "bm25": 14.0}
    assert result[0].ranks == {"vector": 2, "bm25": 1}
    assert len(result) == 3 and reciprocal_rank_fusion({}) == []
    with pytest.raises(ValueError):
        reciprocal_rank_fusion({"vector": [RankedHit(a, 1), RankedHit(a, 0.8)]})
    with pytest.raises(ValueError):
        reciprocal_rank_fusion({"vector": [RankedHit(a, float("nan"))]})


@pytest.mark.parametrize("changes", [{"k": 0}, {"k": True}, {"rank_constant": -1},
                                     {"rank_constant": float("nan")}])
def test_invalid_rrf_parameters_are_rejected(changes):
    with pytest.raises(ValueError):
        reciprocal_rank_fusion({}, **changes)
