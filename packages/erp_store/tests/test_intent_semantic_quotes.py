"""Quote location must preserve exact evidence and reject ambiguous or forged spans."""

from copy import deepcopy

import pytest
from erp_store.intent_semantic import locate_proposal
from erp_store.intent_state import source_message

TEXT = "帮客户甲列份采购清单：机械键盘要四个，预算1500元，备注『不要提交😀』。"


def candidate(text=TEXT):
    def evidence(quote):
        return {"message_id": "turn", "quote": quote}

    def field(value):
        return {"value": value, "evidence": evidence(value)}

    return {
        "schema_version": 1,
        "operation": "create",
        "action": evidence("列"),
        "customer": field("客户甲"),
        "note": field("不要提交😀"),
        "budget_limit": {"value": "1500", "evidence": evidence("1500元")},
        "items": [
            {"product": field("机械键盘"), "quantity": {"value": 4, "evidence": evidence("四个")}}
        ],
        "unresolved": [],
    }


def test_unique_quotes_located_without_changing_original_or_mutating_input():
    data = candidate()
    original = deepcopy(data)
    result = locate_proposal(source_message(TEXT, "turn"), data)
    assert data == original
    assert result.note.evidence.quote == "不要提交😀"
    assert result.note.evidence.start == TEXT.index("不要提交😀")
    assert result.note.evidence.end == TEXT.index("不要提交😀") + 5
    assert result.budget_limit.evidence.quote == "1500元"
    assert result.items[0].quantity.value == 4


@pytest.mark.parametrize(
    "change",
    [
        "wrong_offset",
        "half_offset",
        "foreign_message",
        "missing_quote",
        "bare_money",
        "invented_count",
        "unknown_property",
        "boolean_offset",
    ],
)
def test_untrusted_quote_failures_cannot_be_repaired(change):
    data = candidate()
    evidence = data["items"][0]["product"]["evidence"]
    if change == "wrong_offset":
        evidence.update(start=0, end=4)
    elif change == "half_offset":
        evidence["start"] = TEXT.index("机械键盘")
    elif change == "foreign_message":
        evidence["message_id"] = "another-turn"
    elif change == "missing_quote":
        evidence["quote"] = "不存在的商品"
    elif change == "bare_money":
        data["budget_limit"]["evidence"]["quote"] = "1500"
    elif change == "invented_count":
        data["items"][0]["quantity"]["value"] = 40
    elif change == "unknown_property":
        evidence["verified"] = True
    elif change == "boolean_offset":
        evidence.update(start=True, end=4)
    with pytest.raises(ValueError):
        locate_proposal(source_message(TEXT, "turn"), data)


def test_duplicate_quote_requires_position_and_cannot_bind_quoted_product():
    text = TEXT.replace("不要提交😀", "机械键盘😀")
    data = candidate()
    data["note"] = None
    with pytest.raises(ValueError, match="重复"):
        locate_proposal(source_message(text, "turn"), data)
    product = data["items"][0]["product"]["evidence"]
    product.update(start=text.index("机械键盘"), end=text.index("机械键盘") + 4)
    assert locate_proposal(source_message(text, "turn"), data).items[0].product.value == "机械键盘"
    product.update(start=text.rindex("机械键盘"), end=text.rindex("机械键盘") + 4)
    with pytest.raises(ValueError, match="引文"):
        locate_proposal(source_message(text, "turn"), data)


def test_real_source_words_cannot_be_invented_as_an_unspecified_note():
    text = "帮客户甲列份采购清单：机械键盘要四个，预算1500元。"
    data = candidate()
    data["note"] = {"value": "采购清单", "evidence": {"message_id": "turn", "quote": "采购清单"}}
    with pytest.raises(ValueError, match="明确的备注字段"):
        locate_proposal(source_message(text, "turn"), data)
    data["note"] = None
    assert locate_proposal(source_message(text, "turn"), data).note is None


@pytest.mark.parametrize("value", ["不要", "不要提交"])
def test_note_evidence_cannot_drop_the_rest_of_a_real_note(value):
    data = candidate()
    data["note"] = {"value": value, "evidence": {"message_id": "turn", "quote": value}}
    with pytest.raises(ValueError, match="完整备注字段"):
        locate_proposal(source_message(TEXT, "turn"), data)


def test_explicit_unquoted_note_is_still_supported():
    text = TEXT.replace("『不要提交😀』", "不要提交😀")
    assert locate_proposal(source_message(text, "turn"), candidate()).note.value == "不要提交😀"
