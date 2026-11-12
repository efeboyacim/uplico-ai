"""Pydantic schemas shared by the store, preparation, graph, verification and API."""

from collections.abc import Sequence
from typing import Literal, NewType, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Severity = Literal["critical", "normal"]
CheckOp = Literal[">=", "<=", ">", "<", "=="]
CheckUnit = Literal["gün", "hafta", "ay", "yıl", "%", "TL"]
FindingStatus = Literal["compliant", "non_compliant", "not_found"]
CodeCheck = Literal["passed", "failed", "unverifiable"]
NoFindingReason = Literal["no_submit", "step_limit", "rate_limited", "llm_error"]
# Why a run that had already submitted a finding did not end by itself (agent_design.md §6).
InterruptReason = Literal["step_limit", "rate_limited", "llm_error"]
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
    "run_interrupted",
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
# The lawyer's decision values (architecture.md §3 "Avukat kararı").
FindingAction = Literal["accept", "correct"]
ProposalAction = Literal["accept", "edit", "reject"]
ContractDecision = Literal["approve", "request_changes"]
DecidedStatus = Literal["approved", "changes_requested"]


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
    interrupted_by: InterruptReason | None = None
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

    @model_validator(mode="after")
    def _interrupted_or_no_finding(self) -> Self:
        if self.interrupted_by is not None and self.no_finding_reason is not None:
            raise ValueError("interrupted_by ve no_finding_reason birlikte dolu olamaz.")
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


class FindingDecision(_Model):
    """One entry of the lawyer's decision, after masking: `edited_text` is masked.

    Built only from a request that already passed the format rules and the record checks; the
    format rules are the ones of the request model, written once in `src/decision.py`.
    """

    rule_id: str = Field(min_length=1)
    action: FindingAction | None = None
    status: FindingStatus | None = None
    proposal_action: ProposalAction | None = None
    edited_text: MaskedText | None = None

    @model_validator(mode="after")
    def _format_rules(self) -> Self:
        # Imported here: src.decision imports this module at its top.
        from src.decision import entry_format_error

        error = entry_format_error(self.action, self.status, self.proposal_action, self.edited_text)
        if error is not None:
            raise ValueError(error)
        return self


class LawyerDecision(_Model):
    """The lawyer's masked decision: the resume payload and the input of `finalize`."""

    findings: list[FindingDecision] = Field(default_factory=list)
    contract_decision: ContractDecision

    @model_validator(mode="after")
    def _rule_ids_unique(self) -> Self:
        from src.decision import duplicate_rule_id_error

        error = duplicate_rule_id_error([entry.rule_id for entry in self.findings])
        if error is not None:
            raise ValueError(error)
        return self


class FindingOutcome(_Model):
    """The stored final result of one rule; one per rule, in rule order."""

    rule_id: str = Field(min_length=1)
    # From the rule copy in the graph state: a later change to rules.yaml does not change it.
    severity: Severity
    final_status: FindingStatus
    action: FindingAction | None = None
    proposal_action: ProposalAction | None = None
    edited_text: MaskedText | None = None


class ReviewOutcome(_Model):
    """The `reviews.decision` column: the contract decision and the final result of every rule."""

    contract_decision: ContractDecision
    findings: list[FindingOutcome]


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
    decision: ReviewOutcome | None = None
    decided_at: str | None = None
    decided_by: str | None = None


class RuleResult(_Model):
    """One rule in the report: the model's status, the final status and the lawyer's action."""

    rule_id: str
    severity: Severity
    model_status: FindingStatus
    final_status: FindingStatus
    clause_id: str | None
    clause_no: str | None
    quote: str | None
    needs_review: bool
    review_reasons: list[ReviewReason]
    action: FindingAction | None


class ReportProposal(_Model):
    """One proposal in the report; `proposal_action` is null when the lawyer did not decide."""

    rule_id: str
    clause_id: str
    proposed_text: str
    code_check: CodeCheck | None
    proposal_action: ProposalAction | None
    edited_text: str | None


class RequestedChange(_Model):
    """A proposal the lawyer accepted or edited, with its final text."""

    rule_id: str
    clause_id: str
    clause_no: str | None
    source: Literal["accepted", "edited"]
    text: str


class ReportInjectionFlag(_Model):
    clause_id: str
    kind: InjectionKind
    phrase: str


class LawyerReportInjectionFlag(ReportInjectionFlag):
    start: int
    end: int
    detail: str | None


class Report(_Model):
    review_id: str
    created_at: str
    decided_at: str | None
    provider: str
    model: str
    decided_by: str | None
    status: DecidedStatus
    approved_with_noncompliant: bool
    noncompliant_rule_ids: list[str]
    rule_results: list[RuleResult]
    proposals: list[ReportProposal]
    requested_changes: list[RequestedChange]
    # Sequence (covariant): the lawyer report narrows the element type.
    injection_flags: Sequence[ReportInjectionFlag]


class LawyerReport(Report):
    injection_flags: Sequence[LawyerReportInjectionFlag]
