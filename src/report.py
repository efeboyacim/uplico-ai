"""The report: a pure function of the stored final result (`docs/architecture.md` §3 "Rapor").

No I/O, no logging, no PII map. Both builders return masked text; the lawyer variant adds the
flag positions and `src.api` puts the raw values back.
"""

from typing import Literal

from src.models import (
    Finding,
    FindingOutcome,
    InjectionFlag,
    LawyerReport,
    LawyerReportInjectionFlag,
    Report,
    ReportInjectionFlag,
    ReportProposal,
    RequestedChange,
    ReviewRecord,
    RuleResult,
)

_MSG_NOT_DECIDED = "Rapor için kayıtta son karar bulunmalı."
_MSG_OUTCOME_MISSING = "Rapor için her bulgunun son sonucu bulunmalı."


def _outcomes(record: ReviewRecord) -> dict[str, FindingOutcome]:
    if record.decision is None or record.status not in ("approved", "changes_requested"):
        raise ValueError(_MSG_NOT_DECIDED)
    outcomes = {o.rule_id: o for o in record.decision.findings}
    if any(f.rule_id not in outcomes for f in record.findings):
        raise ValueError(_MSG_OUTCOME_MISSING)
    return outcomes


def _clause_nos(record: ReviewRecord) -> dict[str, str | None]:
    return {c.clause_id: c.clause_no for c in record.clauses}


def _rule_result(
    finding: Finding, outcome: FindingOutcome, clause_nos: dict[str, str | None]
) -> RuleResult:
    clause_no = clause_nos.get(finding.clause_id) if finding.clause_id else None
    return RuleResult(
        rule_id=finding.rule_id,
        severity=outcome.severity,
        model_status=finding.status,
        final_status=outcome.final_status,
        clause_id=finding.clause_id,
        clause_no=clause_no,
        quote=finding.quote,
        needs_review=finding.needs_review,
        review_reasons=list(finding.review_reasons),
        action=outcome.action,
    )


def _proposal(finding: Finding, outcome: FindingOutcome) -> ReportProposal | None:
    if finding.proposal is None:
        return None
    return ReportProposal(
        rule_id=finding.rule_id,
        clause_id=finding.proposal.clause_id,
        proposed_text=finding.proposal.proposed_text,
        code_check=finding.proposal.code_check,
        proposal_action=outcome.proposal_action,
        edited_text=outcome.edited_text,
    )


def _requested_change(
    finding: Finding, outcome: FindingOutcome, clause_nos: dict[str, str | None]
) -> RequestedChange | None:
    proposal = finding.proposal
    if proposal is None or outcome.final_status != "non_compliant":
        return None
    source: Literal["accepted", "edited"]
    text: str | None
    if outcome.proposal_action == "accept":
        source, text = "accepted", proposal.proposed_text
    elif outcome.proposal_action == "edit":
        source, text = "edited", outcome.edited_text
    else:
        return None
    if text is None:
        raise ValueError(_MSG_OUTCOME_MISSING)
    return RequestedChange(
        rule_id=finding.rule_id,
        clause_id=proposal.clause_id,
        clause_no=clause_nos.get(proposal.clause_id),
        source=source,
        text=text,
    )


def _flag(flag: InjectionFlag) -> ReportInjectionFlag:
    return ReportInjectionFlag(clause_id=flag.clause_id, kind=flag.kind, phrase=flag.phrase)


def _lawyer_flag(flag: InjectionFlag) -> LawyerReportInjectionFlag:
    return LawyerReportInjectionFlag(
        clause_id=flag.clause_id,
        kind=flag.kind,
        phrase=flag.phrase,
        start=flag.start,
        end=flag.end,
        detail=flag.detail,
    )


def _sections(record: ReviewRecord) -> dict[str, object]:
    """Every field shared by both reports, built from the stored final result."""
    outcomes = _outcomes(record)
    clause_nos = _clause_nos(record)
    pairs = [(f, outcomes[f.rule_id]) for f in record.findings]
    proposals = [p for f, o in pairs if (p := _proposal(f, o)) is not None]
    changes = [c for f, o in pairs if (c := _requested_change(f, o, clause_nos)) is not None]
    noncompliant = [f.rule_id for f, o in pairs if o.final_status == "non_compliant"]
    return {
        "review_id": record.review_id,
        "created_at": record.created_at,
        "decided_at": record.decided_at,
        "provider": record.provider,
        "model": record.model,
        "decided_by": record.decided_by,
        "status": record.status,
        "approved_with_noncompliant": record.status == "approved" and bool(noncompliant),
        "noncompliant_rule_ids": noncompliant,
        "rule_results": [_rule_result(f, o, clause_nos) for f, o in pairs],
        "proposals": proposals,
        "requested_changes": changes,
    }


def build_report(record: ReviewRecord) -> Report:
    """The masked report; raises `ValueError` when the record has no complete final result."""
    return Report.model_validate(
        {**_sections(record), "injection_flags": [_flag(f) for f in record.injection_flags]}
    )


def build_lawyer_report(record: ReviewRecord) -> LawyerReport:
    """The masked report plus flag positions; `src.api` unmasks it for the lawyer."""
    return LawyerReport.model_validate(
        {**_sections(record), "injection_flags": [_lawyer_flag(f) for f in record.injection_flags]}
    )
