import pytest
from rag.citations import check_citations, citation_label, text_digest


def ref():
    text = "退款在审核通过后 3 个工作日内原路退回。"
    return {
        "source": "return-policy.md",
        "title": "退款流程与时效",
        "version": "v1",
        "text": text,
        "text_sha256": text_digest(text),
    }


def test_citation_identity_and_whitespace_only_quote_changes():
    r = ref()
    failures, cited = check_citations(citation_label(r) + "「审核通过后3个工作日内原路退回」", [r])
    assert failures == [] and cited == {(r["source"], r["title"])}


@pytest.mark.parametrize(
    "quote", ["审核通过后 1 个工作日内原路退回", "提交后 3 个工作日内原路退回", "退款"]
)
def test_changed_numbers_conditions_and_too_short_quotes_fail(quote):
    assert (
        "citation_not_verbatim"
        in check_citations(citation_label(ref()) + f"「{quote}」", [ref()])[0]
    )


def test_missing_quotes_and_unknown_sources_or_versions_fail():
    r = ref()
    assert "citation_format" in check_citations(citation_label(r), [r])[0]
    text = citation_label({**r, "version": "v2"}) + f"「{r['text']}」"
    assert "citation_unknown_reference" in check_citations(text, [r])[0]
    assert "citation_unknown_reference" in check_citations(text, [])[0]


def test_tampered_and_conflicting_evidence_fail_closed():
    r = ref()
    text = citation_label(r) + f"「{r['text']}」"
    assert "citation_evidence_hash" in check_citations(text, [{**r, "text": "错误正文"}])[0]
    assert "citation_invalid_evidence" in check_citations(text, [{}])[0]
    assert (
        "citation_conflicting_reference" in check_citations(text, [r, {**r, "text": "其他正文"}])[0]
    )


def test_nested_quotes_in_original_policy_are_preserved_and_checked():
    r = ref()
    r["text"] = "仅「待付款」或「待发货」的订单可直接取消。"
    r["text_sha256"] = text_digest(r["text"])
    text = citation_label(r) + "「" + r["text"] + "」"
    assert check_citations(text, [r])[0] == []
    assert "citation_not_verbatim" in check_citations(text.replace("待付款", "已付款"), [r])[0]
    assert "citation_format" in check_citations(text[:-1], [r])[0]
