"""Network recovery must preserve failures, fees and the tool execution boundary."""

from decimal import Decimal

import pytest
from agent_core.billing import BudgetedClient, CurrencyBudget, EndpointRate, quote_usage
from agent_core.llm import LLMConfig, StreamEnd, TextDelta, ToolCall, Usage
from evals.reconnect import ReconnectingClient, RecoveryBudget

RATE = EndpointRate("test", "test", "CNY", Decimal("0.096"), Decimal("0.34"))


class InterruptedClient:
    config = LLMConfig(api_key="test", model="test")
    request_timeout = 1

    def __init__(self, error=TimeoutError, failures=1):
        self.calls, self.error, self.failures = 0, error, failures

    async def stream_chat(self, messages, tools=None):
        self.calls += 1
        if self.calls <= self.failures:
            yield TextDelta("discard this partial response")
            yield ToolCall("partial", "never_execute", "{}")
            raise self.error("interrupted")
        yield TextDelta("complete")
        yield ToolCall("accepted", "execute_once", "{}")
        yield StreamEnd(Usage(13, 7, 20))


async def test_retry_discards_partial_tools_and_preserves_unknown_fee():
    raw, records, retries, delays = InterruptedClient(), [], [], []
    budget = RecoveryBudget(Decimal("0.02"), "CNY")

    async def sleep(delay):
        delays.append(delay)

    client = ReconnectingClient(
        BudgetedClient(raw, RATE, budget, request_observer=records.append),
        observer=retries.append,
        sleep=sleep,
    )
    client.begin_case("case")
    events = [event async for event in client.stream_chat([])]
    assert [event.name for event in events if isinstance(event, ToolCall)] == ["execute_once"]
    assert [event.text for event in events if isinstance(event, TextDelta)] == ["complete"]
    assert raw.calls == budget.requests == 2 and client.recovered_requests == 1
    assert budget.unknown_usage and budget.unknown_requests == 1 and not budget.exhausted
    assert budget.reserved == Decimal("0.005") and budget.spent > 0
    assert [record["status"] for record in records] == ["started", "unknown", "started", "settled"]
    assert records[1]["billing"]["estimated_amount"] is None
    assert retries[0]["retry"] is True and delays == [2]


@pytest.mark.parametrize("error,failures,expected", [(ValueError, 1, 1), (TimeoutError, 9, 2)])
async def test_nontransport_failures_and_unknown_ceiling_stop_without_partial_events(
    error,
    failures,
    expected,
):
    budget = RecoveryBudget(Decimal("0.1"), "CNY", max_unknown=2)
    raw = InterruptedClient(error, failures)

    async def sleep(delay):
        pass

    client = ReconnectingClient(BudgetedClient(raw, RATE, budget), sleep=sleep)
    seen = []
    with pytest.raises(error):
        async for event in client.stream_chat([]):
            seen.append(event)
    assert seen == [] and raw.calls == expected and budget.unknown_requests == expected


def test_recovery_is_opt_in_and_reservations_are_not_reported_as_known_spend():
    original = CurrencyBudget(Decimal("0.1"), "CNY", 10)
    original.record(None)
    assert original.exhausted
    budget = RecoveryBudget(Decimal("0.005"), "CNY")
    budget.before_request()
    budget.record(quote_usage(RATE, None))
    assert budget.exhausted and budget.spent == 0
    assert budget.summary()["unknown_reserve_total"] == "0.005"
    assert budget.summary()["reserve_is_actual_bill"] is False
