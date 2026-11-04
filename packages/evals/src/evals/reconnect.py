"""Opt-in transport recovery for acceptance; defaults elsewhere stay fail-closed."""

import asyncio
from contextlib import aclosing
from decimal import Decimal

import httpx2
from agent_core.billing import CurrencyBudget
from openai import APIConnectionError, APIStatusError


class RecoveryBudget(CurrencyBudget):
    def __init__(
        self, limit, currency, *, max_requests=160, max_unknown=8, unknown_reserve=Decimal("0.005")
    ):
        super().__init__(limit, currency, max_requests)
        if max_unknown < 1 or not unknown_reserve.is_finite() or unknown_reserve <= 0:
            raise ValueError("positive unknown-request bounds required")
        self.max_unknown = max_unknown
        self.unknown_reserve = unknown_reserve
        self.unknown_requests = 0

    @property
    def reserved(self):
        return self.unknown_reserve * self.unknown_requests

    @property
    def exhausted(self):
        return (
            self.requests >= self.max_requests
            or self.unknown_requests >= self.max_unknown
            or self.spent + self.reserved >= self.limit
        )

    def reason(self):
        return (
            f"known={self.spent}, unknown reserve={self.reserved}, limit={self.limit} "
            f"{self.currency}; requests={self.requests}/{self.max_requests}; "
            f"unknown={self.unknown_requests}/{self.max_unknown}"
        )

    def record(self, estimate):
        super().record(estimate)
        if estimate is None or estimate.estimated_amount is None:
            self.unknown_requests += 1

    def summary(self):
        return {
            "currency": self.currency,
            "limit": str(self.limit),
            "known_spent": str(self.spent),
            "unknown_usage": self.unknown_usage,
            "unknown_requests": self.unknown_requests,
            "requests": self.requests,
            "unknown_reserve_per_request": str(self.unknown_reserve),
            "unknown_reserve_total": str(self.reserved),
            "reserve_is_actual_bill": False,
            "max_unknown": self.max_unknown,
            "max_requests": self.max_requests,
        }


def transient(error):
    if isinstance(error, APIStatusError):
        return error.status_code in {408, 429, 500, 502, 503, 504}
    return isinstance(error, (APIConnectionError, TimeoutError, httpx2.TransportError))


class ReconnectingClient:
    """Buffer each completion so partial failed attempts never reach tool execution."""

    def __init__(self, client, *, attempts=3, delays=(2, 4), observer=None, sleep=asyncio.sleep):
        if attempts < 1 or len(delays) != attempts - 1 or any(v < 0 for v in delays):
            raise ValueError("one non-negative delay per retry required")
        self.client, self.attempts, self.delays = client, attempts, delays
        self.observer, self.sleep = observer, sleep
        self.logical_requests = 0
        self.recovered_requests = 0
        self.failed_attempts = 0

    @property
    def config(self):
        return self.client.config

    @property
    def request_timeout(self):
        return self.client.request_timeout

    def begin_case(self, case_id):
        self.client.begin_case(case_id)

    async def stream_chat(self, messages, tools=None):
        self.logical_requests += 1
        for attempt in range(1, self.attempts + 1):
            try:
                async with aclosing(self.client.stream_chat(messages, tools=tools)) as stream:
                    events = [event async for event in stream]
            except Exception as error:
                self.failed_attempts += 1
                retry = (
                    transient(error)
                    and attempt < self.attempts
                    and not self.client.budget.exhausted
                )
                delay = self.delays[attempt - 1] if retry else 0
                if self.observer:
                    self.observer(
                        {
                            "logical_request": self.logical_requests,
                            "attempt": attempt,
                            "physical_request": self.client.budget.requests,
                            "error_type": type(error).__name__,
                            "http_status": getattr(error, "status_code", None),
                            "retry": retry,
                            "delay_seconds": delay,
                        }
                    )
                if not retry:
                    raise
                await self.sleep(delay)
            else:
                if attempt > 1:
                    self.recovered_requests += 1
                for event in events:
                    yield event
                return
