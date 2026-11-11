"""Pydantic schemas shared by the store, preparation, graph, verification and API."""

from typing import Literal, NewType, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Severity = Literal["critical", "normal"]
CheckOp = Literal[">=", "<=", ">", "<", "=="]
CheckUnit = Literal["gün", "hafta", "ay", "yıl", "%", "TL"]
FindingStatus = Literal["compliant", "non_compliant", "not_found"]
CodeCheck = Literal["passed", "failed", "unverifiable"]
NoFindingReason = Literal["no_submit", "step_limit", "rate_limited", "llm_error"]
ReviewReason = Literal[
    "quote_not_found",
    "numeric_mismatch",
    "ambiguous_value",
    "unit_conversion_uncertain",
    "unverified_not_found",
    "critical_not_found",
    "missing_proposal",
    "proposal_noncompliant",
    "agent_escalated",
    "no_finding",
    "injection_suspected",
]
ReviewStatus = Literal["running", "awaiting_lawyer", "approved", "changes_requested", "failed"]
AuditEvent = Literal[
    "review_started",
    "clauses_split",
    "injection_flagged",
    "pii_masked",
    "index_built",
    "model_call",
    "tool_call",
    "finding",
    "verification",
    "awaiting_lawyer",
    "finding_decision",
    "proposal_decision",
    "contract_decision",
    "report_created",
    "review_closed",
    "review_failed",
]
InjectionKind = Literal["pattern", "hidden_characters"]
MaskedText = NewType("MaskedText", str)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Check(_Model):
    op: CheckOp
    value: float
    unit: CheckUnit


class Rule(_Model):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    severity: Severity = "normal"
    check: Check | None = None


class _Span(_Model):
    start: int = Field(ge=0)
    end: int

    @model_validator(mode="after")
    def _end_not_before_start(self) -> Self:
        if self.end < self.start:
            raise ValueError("end, start'tan küçük olamaz.")
        return self


class RawClause(_Span):
    """A clause as split from the original contract; `text == original[start:end]`, exactly.

    Used only by clause splitting and injection scanning; never reaches the model, the index,
    the graph or the store.
    """

    clause_id: str = Field(min_length=1)
    clause_no: str | None = None
    text: str


class Clause(_Span):
    """A masked clause, built only after masking.

    `start` / `end` refer to the original text, `text` is masked (and has invisible characters
    removed), so its length may differ from `end - start`.
    """

    clause_id: str = Field(min_length=1)
    clause_no: str | None = None
    text: MaskedText


class Proposal(_Model):
    clause_id: str = Field(min_length=1)
    proposed_text: str
    rationale: str
    code_check: CodeCheck | None = None


class Finding(_Model):
    rule_id: str = Field(min_length=1)
    status: FindingStatus
    clause_id: str | None = None
    quote: str | None = None
    value: float | None = None
    unit: str | None = None
    reasoning: str = ""
    proposal: Proposal | None = None
    escalation_reason: str | None = None
    no_finding_reason: NoFindingReason | None = None
    needs_review: bool = False
    review_reasons: list[ReviewReason] = Field(default_factory=list)

    @model_validator(mode="after")
    def _evidence_required(self) -> Self:
        if self.status in ("compliant", "non_compliant"):
            if not self.clause_id or not self.clause_id.strip():
                raise ValueError(f"{self.status} sonucu için clause_id zorunlu.")
            if not self.quote or not self.quote.strip():
                raise ValueError(f"{self.status} sonucu için quote zorunlu.")
        return self


class InjectionFlag(_Span):
    clause_id: str = Field(min_length=1)
    kind: InjectionKind
    phrase: str
    detail: str | None = None


class AuditRecord(_Model):
    id: int
    ts: str
    review_id: str
    actor: str
    event: AuditEvent
    payload: dict[str, object]


class ReviewRecord(_Model):
    review_id: str
    status: ReviewStatus
    created_at: str
    original_text: str
    provider: str
    model: str
    clauses: list[Clause] = Field(default_factory=list)
    injection_flags: list[InjectionFlag] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    decision: dict[str, object] | None = None
    decided_at: str | None = None
    decided_by: str | None = None
