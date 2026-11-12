import pytest
from pydantic import ValidationError

from src.decision import (
    MAX_EDITED_TEXT_CHARS,
    DecisionIssue,
    build_outcome,
    check_decision,
    decided_status,
    decision_events,
    duplicate_rule_id_error,
    entry_format_error,
)
from src.models import (
    Finding,
    FindingDecision,
    LawyerDecision,
    MaskedText,
    Proposal,
    Rule,
)

RULES = [
    Rule(id="notice", text="Bildirim en az 30 gün.", severity="critical"),
    Rule(id="penalty", text="Ceza en fazla %10."),
    Rule(id="law", text="Türk hukuku."),
]


def _proposal() -> Proposal:
    return Proposal(clause_id="c2", proposed_text="30 gün", rationale="r", code_check="passed")


def _findings() -> list[Finding]:
    """notice: queued, with a draft; penalty: queued, no draft; law: not queued, with a draft."""
    return [
        Finding(
            rule_id="notice",
            status="non_compliant",
            clause_id="c2",
            quote="15 gün",
            proposal=_proposal(),
            needs_review=True,
            review_reasons=["numeric_mismatch"],
        ),
        Finding(
            rule_id="penalty",
            status="not_found",
            needs_review=True,
            review_reasons=["unverified_not_found"],
        ),
        Finding(
            rule_id="law",
            status="non_compliant",
            clause_id="c3",
            quote="Alman hukuku",
            proposal=_proposal(),
        ),
    ]


def _entry(rule_id: str, **fields: object) -> FindingDecision:
    return FindingDecision.model_validate({"rule_id": rule_id, **fields})


def _complete() -> list[FindingDecision]:
    return [
        _entry("notice", action="accept", proposal_action="accept"),
        _entry("penalty", action="accept"),
    ]


# --- Format rules ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "status", "proposal_action", "edited_text"),
    [
        (None, None, None, None),
        ("correct", None, None, None),
        ("accept", "compliant", None, None),
        (None, "compliant", "accept", None),
        (None, None, "edit", None),
        (None, None, "edit", ""),
        (None, None, "edit", "  \n\t"),
        (None, None, "edit", "\u200b"),
        (None, None, "edit", "\u200b \u200d\ufeff "),
        (None, None, "accept", "metin"),
        (None, None, "reject", "metin"),
        ("accept", None, None, "metin"),
    ],
    ids=[
        "nothing",
        "correct-without-status",
        "accept-with-status",
        "status-without-action",
        "edit-without-text",
        "edit-empty",
        "edit-blank",
        "edit-only-invisible",
        "edit-invisible-and-space",
        "accept-with-text",
        "reject-with-text",
        "text-without-proposal-action",
    ],
)
def test_entry_format_error_breaking_entries_fixed_message(
    action: str | None, status: str | None, proposal_action: str | None, edited_text: str | None
) -> None:
    message = entry_format_error(action, status, proposal_action, edited_text)  # type: ignore[arg-type]
    assert message is not None
    assert "metin" not in message  # fixed text: the input never appears in it


@pytest.mark.parametrize(
    ("action", "status", "proposal_action", "edited_text"),
    [
        ("accept", None, None, None),
        ("correct", "not_found", None, None),
        (None, None, "reject", None),
        (None, None, "edit", "\u200b30 gün"),
        ("correct", "compliant", "edit", "yeni metin"),
    ],
)
def test_entry_format_error_valid_entries_none(
    action: str | None, status: str | None, proposal_action: str | None, edited_text: str | None
) -> None:
    assert entry_format_error(action, status, proposal_action, edited_text) is None  # type: ignore[arg-type]


def test_duplicate_rule_id_error() -> None:
    assert duplicate_rule_id_error(["a", "b"]) is None
    assert duplicate_rule_id_error([]) is None
    assert duplicate_rule_id_error(["a", "b", "a"]) is not None


def test_internal_model_calls_the_same_format_rules() -> None:
    with pytest.raises(ValidationError):
        _entry("notice", action="correct")
    with pytest.raises(ValidationError):
        _entry("notice", proposal_action="reject", edited_text="metin")
    with pytest.raises(ValidationError):
        _entry("notice", proposal_action="edit", edited_text="\u200b")
    with pytest.raises(ValidationError):
        _entry("notice", action="accept", note="bilinmeyen alan")
    with pytest.raises(ValidationError):
        LawyerDecision(
            findings=[_entry("notice", action="accept"), _entry("notice", action="accept")],
            contract_decision="approve",
        )
    with pytest.raises(ValidationError):
        LawyerDecision.model_validate({"findings": [], "contract_decision": "reject"})
    assert LawyerDecision(contract_decision="approve").findings == []


def test_max_edited_text_chars_is_twenty_thousand() -> None:
    assert MAX_EDITED_TEXT_CHARS == 20_000


# --- Record checks --------------------------------------------------------------------------------


def test_check_decision_complete_decision_no_issue() -> None:
    assert check_decision(_complete(), _findings()) == []
    # A finding outside the queue may be decided too, by action or by proposal action alone.
    extra = [*_complete(), _entry("law", proposal_action="reject")]
    assert check_decision(extra, _findings()) == []


def test_check_decision_unknown_rule_id() -> None:
    entries = [*_complete(), _entry("ghost", action="accept")]
    assert check_decision(entries, _findings()) == [
        DecisionIssue(
            "unknown_rule_id",
            ("findings", 2, "rule_id"),
            "Bu incelemede bu rule_id ile bir bulgu yok.",
        )
    ]


def test_check_decision_proposal_action_on_finding_without_draft() -> None:
    entries = [
        _entry("notice", action="accept", proposal_action="accept"),
        _entry("penalty", action="accept", proposal_action="reject"),
    ]
    [issue] = check_decision(entries, _findings())
    assert issue.type == "proposal_without_draft"
    assert issue.loc == ("findings", 1, "proposal_action")


def test_check_decision_queued_finding_without_action() -> None:
    entries = [_entry("notice", proposal_action="accept"), _entry("penalty", action="accept")]
    [issue] = check_decision(entries, _findings())
    assert issue.type == "missing_action"
    assert issue.loc == ("findings", 0, "action")


def test_check_decision_queued_finding_not_sent_located_by_rule_id() -> None:
    [issue] = check_decision(
        [_entry("notice", action="accept", proposal_action="accept")], _findings()
    )
    assert issue.type == "missing_action"
    assert issue.loc == ("findings", "penalty", "action")


def test_check_decision_queued_draft_without_proposal_action() -> None:
    entries = [_entry("notice", action="accept"), _entry("penalty", action="accept")]
    [issue] = check_decision(entries, _findings())
    assert issue.type == "missing_proposal_action"
    assert issue.loc == ("findings", 0, "proposal_action")


def test_check_decision_nothing_sent_every_queued_requirement_listed() -> None:
    issues = check_decision([], _findings())
    assert [(i.type, i.loc) for i in issues] == [
        ("missing_action", ("findings", "notice", "action")),
        ("missing_proposal_action", ("findings", "notice", "proposal_action")),
        ("missing_action", ("findings", "penalty", "action")),
    ]


def test_check_decision_messages_fixed_and_free_of_input() -> None:
    entries = [
        _entry("GIZLI-KURAL", action="accept"),
        _entry("law", proposal_action="edit", edited_text="GIZLI-METIN"),
    ]
    issues = check_decision(entries, _findings())
    assert issues
    assert all("GIZLI" not in i.msg for i in issues)


# --- Final result ---------------------------------------------------------------------------------


def test_build_outcome_correct_accept_and_untouched_in_rule_order() -> None:
    decision = LawyerDecision(
        findings=[
            _entry("penalty", action="correct", status="non_compliant"),
            _entry(
                "notice",
                action="accept",
                proposal_action="edit",
                edited_text=MaskedText("[KISI_1] 30 gün"),
            ),
        ],
        contract_decision="request_changes",
    )
    outcome = build_outcome(decision, _findings(), RULES)
    assert outcome.contract_decision == "request_changes"
    assert [o.rule_id for o in outcome.findings] == ["notice", "penalty", "law"]
    notice, penalty, law = outcome.findings
    # accept: the model's status stays.
    assert notice.final_status == "non_compliant"
    assert notice.action == "accept"
    assert notice.proposal_action == "edit"
    assert notice.edited_text == "[KISI_1] 30 gün"
    # correct: the lawyer's status.
    assert penalty.final_status == "non_compliant"
    assert penalty.action == "correct"
    # Untouched: still an outcome, no decision, the model's status.
    assert law.final_status == "non_compliant"
    assert law.action is None and law.proposal_action is None and law.edited_text is None
    # Severity comes from the given rule copy.
    assert [o.severity for o in outcome.findings] == ["critical", "normal", "normal"]


def test_build_outcome_correct_to_model_status_is_valid() -> None:
    decision = LawyerDecision(
        findings=[_entry("penalty", action="correct", status="not_found")],
        contract_decision="approve",
    )
    outcome = build_outcome(decision, _findings(), RULES)
    assert outcome.findings[1].final_status == "not_found"
    assert outcome.findings[1].action == "correct"


def test_build_outcome_severity_from_given_rules_not_current_file() -> None:
    changed = [RULES[0].model_copy(update={"severity": "normal"}), *RULES[1:]]
    decision = LawyerDecision(contract_decision="approve")
    assert build_outcome(decision, _findings(), changed).findings[0].severity == "normal"


def test_build_outcome_findings_not_matching_rules_raise() -> None:
    decision = LawyerDecision(contract_decision="approve")
    with pytest.raises(ValueError):
        build_outcome(decision, _findings()[:2], RULES)
    with pytest.raises(ValueError):
        build_outcome(
            LawyerDecision(
                findings=[_entry("ghost", action="accept")], contract_decision="approve"
            ),
            _findings(),
            RULES,
        )


def test_decided_status_maps_contract_decision() -> None:
    assert decided_status("approve") == "approved"
    assert decided_status("request_changes") == "changes_requested"


# --- Audit rows -----------------------------------------------------------------------------------


def test_decision_events_order_payloads_and_actors() -> None:
    decision = LawyerDecision(
        # Given out of rule order: the rows still follow the rule order.
        findings=[
            _entry("law", proposal_action="reject"),
            _entry("penalty", action="correct", status="compliant"),
            _entry(
                "notice",
                action="accept",
                proposal_action="edit",
                edited_text=MaskedText("[KISI_1] 30 gün"),
            ),
        ],
        contract_decision="approve",
    )
    outcome = build_outcome(decision, _findings(), RULES)
    rows = decision_events(decision, outcome, "Av. Acme", "approved")
    lawyer = "lawyer:Av. Acme"
    assert rows == [
        (
            lawyer,
            "finding_decision",
            {
                "rule_id": "notice",
                "action": "accept",
                "status": None,
                "final_status": "non_compliant",
            },
        ),
        (
            lawyer,
            "proposal_decision",
            {"rule_id": "notice", "proposal_action": "edit", "edited_text": "[KISI_1] 30 gün"},
        ),
        (
            lawyer,
            "finding_decision",
            {
                "rule_id": "penalty",
                "action": "correct",
                "status": "compliant",
                "final_status": "compliant",
            },
        ),
        (
            lawyer,
            "proposal_decision",
            {"rule_id": "law", "proposal_action": "reject", "edited_text": None},
        ),
        (lawyer, "contract_decision", {"contract_decision": "approve", "status": "approved"}),
        (
            "system",
            "report_created",
            {"final_status_counts": {"compliant": 1, "non_compliant": 2, "not_found": 0}},
        ),
        ("system", "review_closed", {"status": "approved"}),
    ]


def test_decision_events_no_entries_only_contract_and_closing_rows() -> None:
    decision = LawyerDecision(contract_decision="request_changes")
    outcome = build_outcome(decision, _findings(), RULES)
    rows = decision_events(decision, outcome, "Av. Acme", "changes_requested")
    assert [(actor, event) for actor, event, _ in rows] == [
        ("lawyer:Av. Acme", "contract_decision"),
        ("system", "report_created"),
        ("system", "review_closed"),
    ]
    assert rows[1][2] == {
        "final_status_counts": {"compliant": 0, "non_compliant": 2, "not_found": 1}
    }
