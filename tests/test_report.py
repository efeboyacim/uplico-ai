import pytest

from src.models import (
    Clause,
    Finding,
    FindingOutcome,
    InjectionFlag,
    MaskedText,
    Proposal,
    ReviewOutcome,
    ReviewRecord,
)
from src.report import build_lawyer_report, build_report


def _proposal(clause_id: str = "c2", text: str = "30 gün") -> Proposal:
    return Proposal(clause_id=clause_id, proposed_text=text, rationale="gizli", code_check="passed")


def _finding(
    rule_id: str,
    status: str = "non_compliant",
    clause_id: str | None = "c1",
    proposal: Proposal | None = None,
) -> Finding:
    quote = "alıntı" if status != "not_found" else None
    return Finding.model_validate(
        {
            "rule_id": rule_id,
            "status": status,
            "clause_id": clause_id if status != "not_found" else None,
            "quote": quote,
            "reasoning": "gerekçe",
            "proposal": proposal,
            "needs_review": True,
            "review_reasons": ["agent_escalated"],
        }
    )


def _outcome(rule_id: str, final_status: str = "non_compliant", **fields: object) -> FindingOutcome:
    return FindingOutcome.model_validate(
        {"rule_id": rule_id, "severity": "normal", "final_status": final_status, **fields}
    )


def _record(
    findings: list[Finding],
    outcomes: list[FindingOutcome],
    contract_decision: str = "approve",
    **fields: object,
) -> ReviewRecord:
    status = "approved" if contract_decision == "approve" else "changes_requested"
    return ReviewRecord.model_validate(
        {
            "review_id": "r1",
            "status": status,
            "created_at": "2026-01-01T00:00:00Z",
            "original_text": "asıl metin",
            "provider": "groq",
            "model": "m",
            "clauses": [
                Clause(start=0, end=3, clause_id="c1", clause_no="1", text=MaskedText("bir")),
                Clause(start=3, end=6, clause_id="c2", clause_no="2", text=MaskedText("iki")),
                Clause(start=6, end=9, clause_id="c3", clause_no=None, text=MaskedText("üç")),
            ],
            "findings": findings,
            "decision": ReviewOutcome(contract_decision=contract_decision, findings=outcomes)
            if outcomes or findings
            else None,
            "decided_at": "2026-01-02T00:00:00Z",
            "decided_by": "Av. Acme",
            **fields,
        }
    )


def _three() -> ReviewRecord:
    """a: non_compliant, draft accepted; b: compliant, no draft; c: non_compliant, draft edited."""
    return _record(
        [
            _finding("a", proposal=_proposal("c2", "A metni")),
            _finding("b", status="compliant", clause_id="c3"),
            _finding("c", proposal=_proposal("c3", "C metni")),
        ],
        [
            _outcome("a", action="accept", proposal_action="accept"),
            _outcome("b", "compliant", action="accept"),
            _outcome("c", action="accept", proposal_action="edit", edited_text="C düzenlendi"),
        ],
    )


def test_report_lists_follow_finding_order_and_header_comes_from_record() -> None:
    report = build_report(_three())
    assert [r.rule_id for r in report.rule_results] == ["a", "b", "c"]
    assert [p.rule_id for p in report.proposals] == ["a", "c"]
    assert [c.rule_id for c in report.requested_changes] == ["a", "c"]
    assert report.noncompliant_rule_ids == ["a", "c"]
    assert (report.review_id, report.status) == ("r1", "approved")
    assert (report.created_at, report.decided_at) == (
        "2026-01-01T00:00:00Z",
        "2026-01-02T00:00:00Z",
    )
    assert (report.provider, report.model, report.decided_by) == ("groq", "m", "Av. Acme")


def test_report_rule_result_carries_model_status_final_status_and_clause_no() -> None:
    record = _record(
        [_finding("a", clause_id="c2"), _finding("b", status="not_found")],
        [_outcome("a", "compliant", action="correct"), _outcome("b", "not_found")],
    )
    a, b = build_report(record).rule_results
    assert (a.model_status, a.final_status, a.action) == ("non_compliant", "compliant", "correct")
    assert (a.clause_id, a.clause_no, a.quote) == ("c2", "2", "alıntı")
    assert (a.needs_review, a.review_reasons, a.severity) == (True, ["agent_escalated"], "normal")
    assert (b.clause_id, b.clause_no, b.quote) == (None, None, None)


def test_report_undecided_draft_in_proposals_with_null_action_not_in_requested_changes() -> None:
    record = _record(
        [_finding("a", proposal=_proposal())],
        [_outcome("a", proposal_action=None)],
    )
    report = build_report(record)
    [proposal] = report.proposals
    assert proposal.proposal_action is None and proposal.edited_text is None
    assert report.requested_changes == []


def test_report_edited_draft_text_is_edited_text_accepted_draft_is_model_text() -> None:
    changes = {c.rule_id: c for c in build_report(_three()).requested_changes}
    assert (changes["a"].source, changes["a"].text) == ("accepted", "A metni")
    assert (changes["c"].source, changes["c"].text) == ("edited", "C düzenlendi")
    proposals = {p.rule_id: p for p in build_report(_three()).proposals}
    assert (
        proposals["c"].edited_text == "C düzenlendi" and proposals["c"].proposed_text == "C metni"
    )
    assert proposals["a"].edited_text is None


def test_report_requested_change_clause_comes_from_the_proposal_not_the_finding() -> None:
    [change] = build_report(_three()).requested_changes[:1]
    # Rule a's finding is in c1, its draft targets c2.
    assert (change.clause_id, change.clause_no) == ("c2", "2")
    [rule_a] = build_report(_three()).rule_results[:1]
    assert (rule_a.clause_id, rule_a.clause_no) == ("c1", "1")


def test_report_rejected_draft_not_in_requested_changes() -> None:
    record = _record(
        [_finding("a", proposal=_proposal())],
        [_outcome("a", action="accept", proposal_action="reject")],
    )
    report = build_report(record)
    assert [p.proposal_action for p in report.proposals] == ["reject"]
    assert report.requested_changes == []


def test_report_accepted_draft_of_finding_corrected_to_compliant_not_listed() -> None:
    record = _record(
        [_finding("a", proposal=_proposal())],
        [_outcome("a", "compliant", action="correct", proposal_action="accept")],
    )
    report = build_report(record)
    assert len(report.proposals) == 1
    assert report.requested_changes == []
    assert report.noncompliant_rule_ids == []


def test_report_approved_with_noncompliant_true_only_when_approved_and_noncompliant() -> None:
    approved = build_report(_three())
    assert approved.approved_with_noncompliant is True
    asked = _record(_three().findings, _three().decision.findings, "request_changes")  # type: ignore[union-attr]
    changes_requested = build_report(asked)
    assert changes_requested.approved_with_noncompliant is False
    assert changes_requested.noncompliant_rule_ids == ["a", "c"]
    clean = _record([_finding("b", status="compliant")], [_outcome("b", "compliant")])
    assert build_report(clean).approved_with_noncompliant is False


def test_report_approve_with_requested_changes_always_warns() -> None:
    report = build_report(_three())
    assert report.status == "approved" and report.requested_changes
    assert report.approved_with_noncompliant is True


def test_report_noncompliant_without_draft_listed_but_no_requested_change() -> None:
    record = _record([_finding("a")], [_outcome("a", action="accept")])
    report = build_report(record)
    assert report.noncompliant_rule_ids == ["a"]
    assert report.proposals == [] and report.requested_changes == []


def test_report_injection_flags_viewer_has_no_positions_lawyer_has() -> None:
    flag = InjectionFlag(
        start=1, end=5, clause_id="c1", kind="pattern", phrase="ifade", detail="ayrıntı"
    )
    record = _three().model_copy(update={"injection_flags": [flag]})
    [viewer_flag] = build_report(record).injection_flags
    assert viewer_flag.model_dump() == {"clause_id": "c1", "kind": "pattern", "phrase": "ifade"}
    [lawyer_flag] = build_lawyer_report(record).injection_flags
    assert lawyer_flag.model_dump() == {
        "clause_id": "c1",
        "kind": "pattern",
        "phrase": "ifade",
        "start": 1,
        "end": 5,
        "detail": "ayrıntı",
    }


def test_lawyer_report_equals_report_apart_from_flag_positions() -> None:
    record = _three()
    assert build_lawyer_report(record).model_dump() == build_report(record).model_dump()


def test_report_text_fields_stay_masked_and_hidden_fields_are_left_out() -> None:
    dumped = build_report(_three()).model_dump_json()
    assert "gizli" not in dumped  # the draft's rationale
    assert "gerekçe" not in dumped  # the finding's reasoning
    assert "asıl metin" not in dumped and "bir" not in dumped  # contract and clause text


def test_report_record_without_decision_raises_value_error() -> None:
    record = _record([_finding("a")], [_outcome("a")]).model_copy(update={"decision": None})
    for build in (build_report, build_lawyer_report):
        with pytest.raises(ValueError, match="son karar"):
            build(record)


@pytest.mark.parametrize("status", ["running", "awaiting_lawyer", "failed"])
def test_report_status_not_decided_raises_value_error(status: str) -> None:
    record = _three().model_copy(update={"status": status})
    for build in (build_report, build_lawyer_report):
        with pytest.raises(ValueError):
            build(record)


def test_report_finding_without_outcome_raises_value_error_without_data() -> None:
    record = _record([_finding("a"), _finding("b")], [_outcome("a")])
    for build in (build_report, build_lawyer_report):
        with pytest.raises(ValueError) as error:
            build(record)
        assert "b" not in str(error.value).split() and "alıntı" not in str(error.value)
