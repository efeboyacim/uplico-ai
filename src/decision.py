"""The lawyer's decision: pure functions only (no I/O, no logging).

- Format rules (`entry_format_error`, `duplicate_rule_id_error`): checked without the record; the
  request model (`src/api.py`) and the internal model (`src/models.py`) both call them.
- Record checks (`check_decision`): the entries against the review's stored findings; run before
  masking, on the request model.
- The final result (`build_outcome`) and the decision and closing audit rows (`decision_events`),
  written by `finalize` in one `Store.complete_review` call.

Every message is fixed text: none carries an input value (architecture.md §3 "Avukat kararı").
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, get_args

from src.models import (
    AuditEvent,
    ContractDecision,
    DecidedStatus,
    Finding,
    FindingAction,
    FindingOutcome,
    FindingStatus,
    LawyerDecision,
    ProposalAction,
    ReviewOutcome,
    Rule,
)
from src.text import strip_invisible

MAX_EDITED_TEXT_CHARS = 20_000
LAWYER_ACTOR_PREFIX = "lawyer:"
SYSTEM_ACTOR = "system"

_DECIDED_STATUS: dict[ContractDecision, DecidedStatus] = {
    "approve": "approved",
    "request_changes": "changes_requested",
}
_FINDING_STATUSES: tuple[FindingStatus, ...] = get_args(FindingStatus)

# One audit row for `Store.complete_review`: (actor, event, payload).
EventRow = tuple[str, AuditEvent, dict[str, object]]


# --- Format rules ---------------------------------------------------------------------------------


def entry_format_error(
    action: FindingAction | None,
    status: FindingStatus | None,
    proposal_action: ProposalAction | None,
    edited_text: str | None,
) -> str | None:
    """The fixed message of the first format rule one entry breaks, or None."""
    if action is None and proposal_action is None:
        return "Her girdide action ya da proposal_action olmalı."
    if action == "correct" and status is None:
        return "action correct ise status zorunlu."
    if action != "correct" and status is not None:
        return "status yalnızca action correct ile verilir."
    if proposal_action == "edit":
        # Invisible characters do not count: masking strips them, and an all-invisible text
        # would be saved as an empty edit.
        if edited_text is None or not strip_invisible(edited_text).strip():
            return "proposal_action edit ise boş olmayan edited_text zorunlu."
    elif edited_text is not None:
        return "edited_text yalnızca proposal_action edit ile verilir."
    return None


def duplicate_rule_id_error(rule_ids: Sequence[str]) -> str | None:
    if len(set(rule_ids)) != len(rule_ids):
        return "Her rule_id en fazla bir kez verilebilir."
    return None


# --- Record checks --------------------------------------------------------------------------------


class DecisionEntry(Protocol):
    """What the record checks read of an entry: the request model and the internal model."""

    @property
    def rule_id(self) -> str: ...

    @property
    def action(self) -> FindingAction | None: ...

    @property
    def proposal_action(self) -> ProposalAction | None: ...


@dataclass(frozen=True)
class DecisionIssue:
    """One record check that failed; `loc` is relative to the request body."""

    type: str
    loc: tuple[str | int, ...]
    msg: str


def check_decision(
    entries: Sequence[DecisionEntry], findings: Sequence[Finding]
) -> list[DecisionIssue]:
    """The record checks of a decision against the review's stored findings.

    Looks only at `rule_id`, `action` and `proposal_action`, so it runs before masking. An entry
    is located by its index; a queued finding without an entry by its rule id.
    """
    issues: list[DecisionIssue] = []
    by_rule = {finding.rule_id: finding for finding in findings}
    given: dict[str, tuple[int, DecisionEntry]] = {}
    for index, entry in enumerate(entries):
        finding = by_rule.get(entry.rule_id)
        if finding is None:
            issues.append(
                DecisionIssue(
                    "unknown_rule_id",
                    ("findings", index, "rule_id"),
                    "Bu incelemede bu rule_id ile bir bulgu yok.",
                )
            )
            continue
        given[entry.rule_id] = (index, entry)
        if entry.proposal_action is not None and finding.proposal is None:
            issues.append(
                DecisionIssue(
                    "proposal_without_draft",
                    ("findings", index, "proposal_action"),
                    "Taslağı olmayan bulguya proposal_action verilemez.",
                )
            )
    for finding in findings:
        if not finding.needs_review:
            continue
        found = given.get(finding.rule_id)
        decided = found[1] if found is not None else None
        where: str | int = found[0] if found is not None else finding.rule_id
        if decided is None or decided.action is None:
            issues.append(
                DecisionIssue(
                    "missing_action",
                    ("findings", where, "action"),
                    "Avukat kuyruğundaki her bulgu için action zorunlu.",
                )
            )
        if finding.proposal is not None and (decided is None or decided.proposal_action is None):
            issues.append(
                DecisionIssue(
                    "missing_proposal_action",
                    ("findings", where, "proposal_action"),
                    "Avukat kuyruğundaki taslaklı her bulgu için proposal_action zorunlu.",
                )
            )
    return issues


# --- Final result and audit rows ------------------------------------------------------------------


def decided_status(contract_decision: ContractDecision) -> DecidedStatus:
    return _DECIDED_STATUS[contract_decision]


def build_outcome(
    decision: LawyerDecision, findings: Sequence[Finding], rules: Sequence[Rule]
) -> ReviewOutcome:
    """The final result of every rule, in rule order.

    A finding's final status is the lawyer's `status` when the action is `correct`, the model's
    otherwise (`accept` or no decision). A finding the lawyer did not touch gets an outcome too.
    """
    by_rule = {finding.rule_id: finding for finding in findings}
    rule_ids = {rule.id for rule in rules}
    if set(by_rule) != rule_ids or len(by_rule) != len(findings):
        raise ValueError("Bulgular kural setiyle birebir eşleşmiyor.")
    entries = {entry.rule_id: entry for entry in decision.findings}
    if not set(entries) <= rule_ids:
        raise ValueError("Kararda kural setinde olmayan bir rule_id var.")
    outcomes: list[FindingOutcome] = []
    for rule in rules:
        finding = by_rule[rule.id]
        entry = entries.get(rule.id)
        final_status = finding.status
        if entry is not None and entry.action == "correct" and entry.status is not None:
            final_status = entry.status
        outcomes.append(
            FindingOutcome(
                rule_id=rule.id,
                severity=rule.severity,
                final_status=final_status,
                action=entry.action if entry is not None else None,
                proposal_action=entry.proposal_action if entry is not None else None,
                edited_text=entry.edited_text if entry is not None else None,
            )
        )
    return ReviewOutcome(contract_decision=decision.contract_decision, findings=outcomes)


def decision_events(
    decision: LawyerDecision,
    outcome: ReviewOutcome,
    lawyer_name: str,
    status: DecidedStatus,
) -> list[EventRow]:
    """The decision and closing audit rows, in the order they are written.

    Per entry, in rule order: `finding_decision` when an action was given, `proposal_decision`
    when a proposal action was given (a finding without a decision gets no row). Then
    `contract_decision` (both by `lawyer:<name>`), `report_created` and `review_closed` (`system`).
    """
    lawyer = f"{LAWYER_ACTOR_PREFIX}{lawyer_name}"
    entries = {entry.rule_id: entry for entry in decision.findings}
    rows: list[EventRow] = []
    for result in outcome.findings:
        entry = entries.get(result.rule_id)
        if entry is None:
            continue
        if entry.action is not None:
            rows.append(
                (
                    lawyer,
                    "finding_decision",
                    {
                        "rule_id": entry.rule_id,
                        "action": entry.action,
                        "status": entry.status if entry.action == "correct" else None,
                        "final_status": result.final_status,
                    },
                )
            )
        if entry.proposal_action is not None:
            rows.append(
                (
                    lawyer,
                    "proposal_decision",
                    {
                        "rule_id": entry.rule_id,
                        "proposal_action": entry.proposal_action,
                        "edited_text": entry.edited_text,
                    },
                )
            )
    rows.append(
        (
            lawyer,
            "contract_decision",
            {"contract_decision": decision.contract_decision, "status": status},
        )
    )
    counts = Counter(result.final_status for result in outcome.findings)
    rows.append(
        (
            SYSTEM_ACTOR,
            "report_created",
            {"final_status_counts": {s: counts.get(s, 0) for s in _FINDING_STATUSES}},
        )
    )
    rows.append((SYSTEM_ACTOR, "review_closed", {"status": status}))
    return rows
