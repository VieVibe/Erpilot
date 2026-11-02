"""Versioned intent data and lossless source scopes; no model or ERP side effects."""

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PARSER_VERSION = "order-rules-5"
POLICY_VERSION = "order-stage-1"
CONTRACT = "draft-intent-2"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceMessage(StrictModel):
    message_id: str
    text: str = Field(min_length=1, max_length=32000)
    sha256: str

    @model_validator(mode="after")
    def check_digest(self):
        if self.sha256 != digest(self.text):
            raise ValueError("source digest mismatch")
        return self


class SourceSpan(StrictModel):
    message_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str

    def verify(self, message):
        return (self.message_id == message.message_id and self.start < self.end
                <= len(message.text) and message.text[self.start:self.end] == self.quote)


class Intent(StrictModel):
    domain: Literal["order_draft", "order", "general"]
    operation: Literal["read", "create", "revise", "refresh_quote", "request_submit",
                       "assess_cancel", "request_cancel"]
    dialogue_act: Literal["request", "select_candidate", "supply_slot", "withdraw"] = "request"
    modality: Literal["asserted", "negated", "conditional", "quoted", "uncertain"] = "asserted"


class Slot(StrictModel):
    field: str
    value: Any
    operation: Literal["set", "clear", "preserve", "add"] = "set"
    evidence: list[SourceSpan] = Field(default_factory=list)


class Unresolved(StrictModel):
    code: str
    slot_path: str = ""
    question: str


class IntentProposal(StrictModel):
    """Candidates cannot supply execution permissions, verification or session IDs."""

    intents: list[Intent] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)
    unresolved: list[Unresolved] = Field(default_factory=list)


class VerifiedIntentFrame(IntentProposal):
    schema_version: Literal[2] = 2
    parser_version: str = PARSER_VERSION
    policy_version: str = POLICY_VERSION
    request_id: str
    session_id: str
    source_messages: list[SourceMessage]
    decision: Literal["read_prerequisite", "clarify", "ready", "blocked", "unsupported"]
    allowed_next_tools: list[str] = Field(default_factory=list)


class ScopedText(StrictModel):
    raw_text: str
    command_text: str
    protected: list[tuple[int, int]]
    issues: list[str]


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scan_source(text):
    """One pass, Python Unicode [start,end) offsets; all quoted/code text is data.

    The command view keeps its length. An unmatched boundary is retained as an
    issue rather than silently accepting a truncated write instruction.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 32000:
        raise ValueError("用户要求须为 1~32000 字符，不能截断后解析")
    closers = {"「": "」", "『": "』", "“": "”", "‘": "’", '"': '"', "'": "'"}
    output, stack, spans, issues = list(text), [], [], []
    index, start, code = 0, 0, None
    while index < len(text):
        char = text[index]
        if code:
            if text.startswith(code, index):
                output[index:index + len(code)] = " " * len(code)
                index += len(code)
                spans.append((start, index))
                code = None
            else:
                output[index] = " "
                index += 1
            continue
        if stack:
            output[index] = " "
            if char == stack[-1]:
                stack.pop()
                if not stack:
                    spans.append((start, index + 1))
            elif char in closers:
                stack.append(closers[char])
        elif char == "`":
            start = index
            code = "```" if text.startswith("```", index) else "`"
            output[index:index + len(code)] = " " * len(code)
            index += len(code)
            continue
        elif char in closers:
            start = index
            stack.append(closers[char])
            output[index] = " "
        elif char in {"」", "』", "”", "’"}:
            issues.append("unmatched_quote")
        index += 1
    if stack or code:
        spans.append((start, len(text)))
        issues.append("unclosed_boundary")
    return ScopedText(raw_text=text, command_text="".join(output), protected=spans, issues=issues)


def utf16_offset(text, offset):
    if type(offset) is not int or not 0 <= offset <= len(text):
        raise ValueError("invalid Unicode offset")
    return len(text[:offset].encode("utf-16-le")) // 2
