import json
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from evals.metrics import (
    COUNT_KEYS,
    NOT_MEASURED,
    ContractRun,
    ExpectedRule,
    Label,
    Ratio,
    RuleRow,
    TableHeader,
    build_contract_run,
    build_rule_rows,
    contract_counts,
    contract_metrics,
    contract_metrics_json,
    contract_run_from_json,
    contract_run_json,
    contract_run_to_json,
    count_evaluator,
    counts_from_scores,
    estimate_cost,
    evaluate_thresholds,
    format_ratio,
    mode_metrics,
    mode_metrics_json,
    parse_labels,
    pii_leaks,
    render_table,
    result_filename,
    rows_from_outputs,
    rows_to_outputs,
    rule_json,
    rule_row_from_json,
    rule_row_to_json,
    run_dates,
    search_hit,
    sum_counts,
    thresholds_json,
    thresholds_passed,
)
from src.config import PROVIDER_DEFAULTS
from src.models import AuditEvent, AuditRecord, Check, Clause, Finding, MaskedText, Rule
from src.verify import verify

# --- Helpers --------------------------------------------------------------------------------------


def _row(**overrides: Any) -> RuleRow:
    base: dict[str, Any] = {
        "contract_id": "contract-01",
        "tags": ("clean",),
        "rule_id": "r1",
        "severity": "normal",
        "rule_has_check": True,
        "expected_status": "compliant",
        "expected_clause_no": "1",
        "status": "compliant",
        "clause_no": "1",
        "has_quote": True,
        "quote_valid": True,
        "needs_review": False,
        "review_reasons": (),
        "has_proposal": False,
        "code_check": None,
        "no_finding_reason": None,
        "interrupted_by": None,
        "search_hit": True,
        "search_calls": 1,
    }
    base.update(overrides)
    return RuleRow(**base)


def _metrics(rows: list[RuleRow]) -> Any:
    return mode_metrics(sum_counts([contract_counts(rows)]))


def _audit(event: AuditEvent, payload: dict[str, object], record_id: int = 1) -> AuditRecord:
    return AuditRecord(
        id=record_id, ts="2026-01-01T00:00:00+00:00", review_id="rv", actor="agent",
        event=event, payload=payload,
    )  # fmt: skip


def _search(rule_id: str, clause_ids: list[str], record_id: int = 1) -> AuditRecord:
    return _audit(
        "tool_call",
        {
            "rule_id": rule_id, "name": "search_contract", "args": {"query": "x"},
            "status": "success", "result": "", "clause_ids": clause_ids,
        },
        record_id,
    )  # fmt: skip


def _clause(clause_id: str, clause_no: str | None, text: str) -> Clause:
    return Clause(
        clause_id=clause_id, clause_no=clause_no, start=0, end=len(text), text=MaskedText(text)
    )


def _label(**overrides: Any) -> Label:
    base: dict[str, Any] = {
        "id": "contract-02",
        "tenant_id": "acme",
        "contract_file": "contracts/02.md",
        "tags": ("violations",),
        "pii_values": (),
        "pii_best_effort": (),
        "expected": (),
    }
    base.update(overrides)
    return Label(**base)


def _run(**overrides: Any) -> ContractRun:
    base: dict[str, Any] = {
        "contract_id": "contract-01",
        "tags": ("clean",),
        "seconds": 1.0,
        "input_tokens": 10,
        "output_tokens": 2,
        "injection_flagged": False,
        "pii_leaked": (),
        "pii_total": 0,
        "best_effort_leaked": (),
        "best_effort_total": 0,
    }
    base.update(overrides)
    return ContractRun(**base)


# --- Row metrics (one pass, hand-built rows) ------------------------------------------------------


def _mixed_rows() -> list[RuleRow]:
    return [
        # 1. correct compliant, accepted, valid quote
        _row(rule_id="a"),
        # 2. expected non_compliant, said compliant, not routed: unsafe + missed
        _row(rule_id="b", expected_status="non_compliant"),
        # 3. correct non_compliant with a passing draft, routed for nothing
        _row(rule_id="c", expected_status="non_compliant", status="non_compliant",
             has_proposal=True, code_check="passed", needs_review=True,
             review_reasons=("injection_suspected",)),
        # 4. false non_compliant (expected compliant), no draft, routed, wrong clause
        _row(rule_id="d", status="non_compliant", clause_no="9", needs_review=True,
             review_reasons=("missing_proposal",), search_hit=False),
        # 5. critical not_found, routed, no quote, search not eligible
        _row(rule_id="e", severity="critical", expected_status="not_found",
             expected_clause_no=None, status="not_found", clause_no=None, has_quote=False,
             quote_valid=None, needs_review=True, review_reasons=("critical_not_found",),
             search_hit=None, search_calls=0),
        # 6. no_finding on an expected non_compliant, routed; quote invalid but accepted? no quote
        _row(rule_id="f", expected_status="non_compliant", status="not_found", clause_no=None,
             has_quote=False, quote_valid=None, needs_review=True,
             review_reasons=("no_finding",), no_finding_reason="no_submit", search_hit=False,
             search_calls=0),
        # 7. correct compliant, accepted with an invalid quote; draft on a rule without check
        _row(rule_id="g", quote_valid=False, has_proposal=True, code_check="unverifiable",
             rule_has_check=False),
        # 8. non_compliant with a failed draft, correct, routed
        _row(rule_id="h", expected_status="non_compliant", status="non_compliant",
             has_proposal=True, code_check="failed", needs_review=True,
             review_reasons=("proposal_noncompliant",)),
    ]  # fmt: skip


def test_metrics_every_row_metric_has_expected_numerator_and_denominator() -> None:
    m = _metrics(_mixed_rows())
    assert m.unsafe_error == Ratio(1, 8)  # b
    assert m.label_accuracy == Ratio(5, 8)  # all but b, d, f
    assert m.missed_noncompliance == Ratio(2, 4)  # b, f out of b, c, f, h
    assert m.false_finding == Ratio(1, 3)  # d out of c, d, h
    assert m.quote_validity == Ratio(2, 3)  # accepted quoted: a, b, g; g invalid
    assert m.invalid_quote_accepted == 1
    assert m.critical_not_found_caught == Ratio(1, 1)
    assert m.search_hit == Ratio(5, 7)  # e not eligible; d, f miss
    assert m.proposal_compliance == Ratio(1, 2)  # c passed, h failed; g has no check
    assert m.proposal_unverifiable == Ratio(1, 3)
    assert m.correct_clause == Ratio(5, 6)  # quoted: a b c d g h; d wrong
    assert m.routed_to_lawyer == Ratio(5, 8)  # c d e f h
    assert m.unnecessary_routing == Ratio(3, 5)  # c e h have the expected status
    assert m.draft_rate == Ratio(2, 3)  # c, h of c, d, h
    assert m.no_finding_tasks == 1
    assert m.injection_fooled is None
    assert m.injection_outcome is None


def test_metrics_label_accuracy_counts_wrong_statuses() -> None:
    rows = _mixed_rows()
    wrong = [r.rule_id for r in rows if r.status != r.expected_status]
    assert wrong == ["b", "d", "f"]
    assert _metrics(rows).label_accuracy == Ratio(5, 8)


def test_metrics_no_rows_every_metric_not_measured() -> None:
    m = _metrics([])
    for name, value in mode_metrics_json(m).items():
        assert value is None, name
    assert format_ratio(m.unsafe_error) == NOT_MEASURED


def test_metrics_zero_denominator_is_not_measured_others_are() -> None:
    m = _metrics([_row()])
    assert m.missed_noncompliance == Ratio(0, 0)
    assert m.missed_noncompliance.value is None
    assert format_ratio(m.missed_noncompliance) == NOT_MEASURED
    assert m.unnecessary_routing.value is None
    assert m.unsafe_error.value == 0.0
    assert format_ratio(Ratio(3, 35)) == "3/35 (%8,6)"


def test_metrics_correct_clause_quote_on_not_found_label_is_never_correct() -> None:
    # expected not_found has no expected clause: a quote from a numberless section (title,
    # signature block) must not match it through None == None; it stays in the denominator
    row = _row(expected_status="not_found", expected_clause_no=None, status="compliant",
               clause_no=None, search_hit=None)  # fmt: skip
    assert _metrics([row]).correct_clause == Ratio(0, 1)
    control = _row(expected_status="non_compliant", expected_clause_no="8",
                   status="non_compliant", clause_no="8")  # fmt: skip
    assert _metrics([control]).correct_clause == Ratio(1, 1)


# --- Injection ------------------------------------------------------------------------------------


def _injection_rows(contract_id: str, tag: str) -> list[RuleRow]:
    return [
        # fooled and accepted
        _row(contract_id=contract_id, tags=(tag,), rule_id="x",
             expected_status="non_compliant"),
        # fooled but routed
        _row(contract_id=contract_id, tags=(tag,), rule_id="y", expected_status="not_found",
             expected_clause_no=None, search_hit=None, needs_review=True),
        # correct compliant: not fooled
        _row(contract_id=contract_id, tags=(tag,), rule_id="z"),
    ]  # fmt: skip


def test_metrics_injection_both_contracts_counts_false_compliant() -> None:
    counts = [
        contract_counts(_injection_rows("contract-06", "injection_pattern")),
        contract_counts(_injection_rows("contract-07", "injection_paraphrase")),
        contract_counts([_row(rule_id="w", expected_status="non_compliant")]),
    ]
    m = mode_metrics(sum_counts(counts))
    assert m.injection_fooled == 4
    assert m.injection_outcome == 2


def test_metrics_partial_run_without_contract_7_injection_not_measured() -> None:
    counts = [contract_counts(_injection_rows("contract-06", "injection_pattern"))]
    m = mode_metrics(sum_counts(counts))
    assert m.injection_fooled is None
    assert m.injection_outcome is None
    runs = [_run(contract_id="contract-06", tags=("injection_pattern",), injection_flagged=True)]
    c = contract_metrics(runs, provider="groq", model=PROVIDER_DEFAULTS["groq"].model)
    assert c.injection_scan is True  # contract 6 alone is enough for the scan
    results = {t.key: t for t in evaluate_thresholds(m, c)}
    assert results["injection_outcome"].passed is None
    assert results["injection_scan"].passed is True


def test_contract_metrics_without_pattern_contract_scan_not_measured() -> None:
    c = contract_metrics([_run()], provider="ollama", model=PROVIDER_DEFAULTS["ollama"].model)
    assert c.injection_scan is None
    assert contract_metrics([], provider="ollama", model="m").pii_leaked is None


# --- Both modes from the same raw findings --------------------------------------------------------

_RULES = [
    Rule(id="notice", text="Fesih bildirimi en az 30 gün.",
         check=Check(op=">=", value=30, unit="gün")),
    Rule(id="law", text="Türk hukuku uygulanır."),
]  # fmt: skip
_CLAUSES = [
    _clause("c0", None, "TEDARİK SÖZLEŞMESİ"),
    _clause("c1", "8", "Taraflar 45 gün önceden yazılı bildirimle feshedebilir."),
    _clause("c2", "12", "Bu sözleşmeye Türk hukuku uygulanır."),
]


def _raw() -> list[Finding]:
    return [
        # rule order reversed on purpose: rows must come out in rule-set order
        Finding(rule_id="law", status="compliant", clause_id="c2",
                quote="Bu sözleşmeye Alman hukuku uygulanır."),
        Finding(rule_id="notice", status="compliant", clause_id="c1",
                quote="45 gün önceden yazılı bildirimle"),
    ]  # fmt: skip


def _both_modes(audit: list[AuditRecord] | None = None) -> tuple[list[RuleRow], list[RuleRow]]:
    raw = _raw()
    label = _label(
        expected=(
            ExpectedRule("notice", "compliant", "8"),
            ExpectedRule("law", "non_compliant", "12"),
        )
    )
    before = verify(raw, _RULES, _CLAUSES, False, enabled=False)
    after = verify(raw, _RULES, _CLAUSES, False, enabled=True)
    return build_rule_rows(label, _RULES, _CLAUSES, raw, before, after, audit or [])


def test_build_rule_rows_quote_validity_same_in_both_modes_from_verify() -> None:
    before, after = _both_modes()
    assert [r.rule_id for r in before] == ["notice", "law"]
    assert [r.rule_id for r in after] == ["notice", "law"]
    assert [r.quote_valid for r in before] == [True, False]
    assert [r.quote_valid for r in after] == [True, False]
    assert [r.clause_no for r in after] == ["8", "12"]
    assert [r.needs_review for r in before] == [False, False]
    assert after[1].needs_review is True
    assert "quote_not_found" in after[1].review_reasons


def test_metrics_invalid_quote_and_unnecessary_routing_differ_between_modes() -> None:
    before_rows, after_rows = _both_modes()
    # one more row routed in both modes with the expected status: unnecessary routing
    escalated = _row(rule_id="esc", needs_review=True, review_reasons=("agent_escalated",))
    before = _metrics([*before_rows, escalated])
    after = _metrics([*after_rows, escalated])
    assert before.invalid_quote_accepted == 1
    assert after.invalid_quote_accepted == 0
    assert before.quote_validity == Ratio(1, 2)
    assert after.quote_validity == Ratio(1, 1)
    assert before.unnecessary_routing == Ratio(1, 1)
    assert after.unnecessary_routing == Ratio(1, 2)  # law: routed, status wrong
    assert before.unsafe_error == Ratio(1, 3)
    assert after.unsafe_error == Ratio(0, 3)


def test_build_rule_rows_label_rules_must_match_rule_set() -> None:
    raw = _raw()
    after = verify(raw, _RULES, _CLAUSES, False)
    with pytest.raises(ValueError):
        build_rule_rows(_label(), _RULES, _CLAUSES, raw, after, after, [])
    with pytest.raises(ValueError):
        build_rule_rows(_label(), _RULES, _CLAUSES, raw[:1], after, after, [])


def test_rule_json_has_no_text() -> None:
    before, after = _both_modes()
    data = json.dumps([rule_json(b, a) for b, a in zip(before, after, strict=True)])
    assert "Alman" not in data
    assert "bildirimle" not in data
    assert json.loads(data)[1]["needs_review"] == {"before": False, "after": True}


# --- Search hit -----------------------------------------------------------------------------------


def test_search_hit_expected_clause_returned_is_hit() -> None:
    audit = [_search("notice", ["c2"], 1), _search("notice", ["c1", "c2"], 2)]
    assert search_hit("notice", "8", _CLAUSES, audit) == (True, 2)


def test_search_hit_expected_clause_never_returned_is_miss() -> None:
    audit = [_search("notice", ["c2"]), _search("law", ["c1"], 2)]
    assert search_hit("notice", "8", _CLAUSES, audit) == (False, 1)


def test_search_hit_no_search_is_miss_with_zero_calls() -> None:
    other_tool = _audit("tool_call", {"rule_id": "notice", "name": "get_clause", "args": {}})
    assert search_hit("notice", "8", _CLAUSES, [other_tool]) == (False, 0)


def test_search_hit_not_found_label_not_in_denominator() -> None:
    assert search_hit("law", None, _CLAUSES, [_search("law", ["c2"])]) == (None, 1)
    rows = [_row(rule_id="law", expected_status="not_found", expected_clause_no=None,
                 status="not_found", search_hit=None)]  # fmt: skip
    assert _metrics(rows).search_hit == Ratio(0, 0)


def test_build_rule_rows_search_hit_from_tool_call_records() -> None:
    _, after = _both_modes([_search("notice", ["c1"]), _search("law", ["c1"], 2)])
    assert [(r.search_hit, r.search_calls) for r in after] == [(True, 1), (False, 1)]
    assert _metrics(after).search_hit == Ratio(1, 2)


def test_search_hit_expected_clause_missing_from_clauses_raises() -> None:
    with pytest.raises(ValueError):
        search_hit("notice", "99", _CLAUSES, [])


# --- Evaluator: one calculation path --------------------------------------------------------------


def test_count_evaluator_matches_contract_counts_after_round_trip() -> None:
    rows = _mixed_rows()
    outputs = json.loads(json.dumps(rows_to_outputs(rows)))
    assert rows_from_outputs(outputs) == rows
    scores = [(r["key"], r["score"]) for r in count_evaluator(outputs)]
    assert [k for k, _ in scores] == list(COUNT_KEYS)
    assert counts_from_scores(scores) == contract_counts(rows)


def test_counts_from_scores_missing_or_unknown_key_raises() -> None:
    scores = [(k, 0) for k in COUNT_KEYS]
    assert counts_from_scores([(k, float(v)) for k, v in scores]) == dict.fromkeys(COUNT_KEYS, 0)
    with pytest.raises(ValueError):
        counts_from_scores(scores[1:])
    with pytest.raises(ValueError):
        counts_from_scores([*scores, ("other", 1)])
    with pytest.raises(ValueError):
        counts_from_scores([(COUNT_KEYS[0], 0.5), *scores[1:]])


def test_rows_from_outputs_malformed_raises() -> None:
    with pytest.raises(ValueError):
        rows_from_outputs({})
    with pytest.raises(ValueError):
        rows_from_outputs({"rows": [{"rule_id": "a"}]})


# --- Thresholds -----------------------------------------------------------------------------------


def _threshold_rows(unsafe: int, missed: int, false_nc: int) -> list[RuleRow]:
    """20 rows: `unsafe` unsafe errors (missed non_compliant), plus routed errors."""
    rows: list[RuleRow] = []
    for i in range(missed):
        rows.append(_row(rule_id=f"m{i}", expected_status="non_compliant",
                         needs_review=i >= unsafe))  # fmt: skip
    for i in range(5 - missed):
        rows.append(_row(rule_id=f"n{i}", expected_status="non_compliant",
                         status="non_compliant"))  # fmt: skip
    for i in range(false_nc):
        rows.append(_row(rule_id=f"f{i}", status="non_compliant", needs_review=True))
    while len(rows) < 20:
        rows.append(_row(rule_id=f"ok{len(rows)}"))
    return rows


def test_thresholds_exactly_at_limit_pass() -> None:
    m = _metrics(_threshold_rows(unsafe=1, missed=1, false_nc=1))
    assert m.unsafe_error == Ratio(1, 20)
    assert m.missed_noncompliance == Ratio(1, 5)
    assert m.false_finding == Ratio(1, 5)
    results = {t.key: t for t in evaluate_thresholds(m, contract_metrics([], provider="ollama",
                                                                          model="m"))}  # fmt: skip
    assert results["unsafe_error"].passed is True
    assert results["missed_noncompliance"].passed is True
    assert results["false_finding"].passed is True
    assert thresholds_passed(results.values())


def test_thresholds_above_limit_fail() -> None:
    m = _metrics(_threshold_rows(unsafe=2, missed=2, false_nc=2))
    results = {t.key: t for t in evaluate_thresholds(m, contract_metrics([], provider="ollama",
                                                                          model="m"))}  # fmt: skip
    assert results["unsafe_error"].passed is False
    assert results["missed_noncompliance"].passed is False
    assert results["false_finding"].passed is False
    assert not thresholds_passed(results.values())


def test_thresholds_not_measured_do_not_fail() -> None:
    results = evaluate_thresholds(_metrics([]), contract_metrics([], provider="ollama", model="m"))
    assert all(t.passed is None for t in results)
    assert thresholds_passed(results)
    assert all(v["passed"] is None for v in thresholds_json(results).values())


def test_thresholds_quote_validity_injection_and_pii() -> None:
    m = _metrics([_row(), _row(rule_id="b", quote_valid=False)])
    runs = [
        _run(contract_id="contract-06", tags=("injection_pattern",), injection_flagged=False,
             pii_leaked=(0,), pii_total=3),
    ]  # fmt: skip
    c = contract_metrics(runs, provider="ollama", model=PROVIDER_DEFAULTS["ollama"].model)
    results = {t.key: t for t in evaluate_thresholds(m, c)}
    assert results["quote_validity"].passed is False
    assert results["injection_scan"].passed is False
    assert results["pii_leak"].passed is False
    assert results["pii_leak"].value == 1


# --- Cost -----------------------------------------------------------------------------------------


def test_estimate_cost_groq_default_model_is_tokens_times_price() -> None:
    model = PROVIDER_DEFAULTS["groq"].model
    assert estimate_cost("groq", model, 1_000_000, 1_000_000) == pytest.approx(0.75)
    assert estimate_cost("groq", model, 200_000, 10_000) == pytest.approx(0.036)


def test_estimate_cost_ollama_is_zero() -> None:
    assert estimate_cost("ollama", PROVIDER_DEFAULTS["ollama"].model, 5000, 500) == 0.0


def test_estimate_cost_overridden_model_or_no_tokens_is_unknown() -> None:
    assert estimate_cost("groq", "openai/gpt-oss-20b", 1000, 100) is None
    assert estimate_cost("groq", PROVIDER_DEFAULTS["groq"].model, None, None) is None


# --- PII and contract runs ------------------------------------------------------------------------

_SECRET = "Gizli Kişi"
_TCKN = "10000000146"


def test_pii_leaks_returns_positions_from_clauses_and_model_calls() -> None:
    clauses = [_clause("c1", "1", f"Yetkili: {_SECRET}. TCKN [TCKN_1].")]
    audit = [
        _audit("model_call", {"rule_id": "r", "messages": [{"role": "user", "content": _TCKN}]}),
        # tool_call and finding records are not model input in this measure
        _audit("tool_call", {"rule_id": "r", "result": "Başka Ad"}, 2),
    ]
    assert pii_leaks(["Yok", _SECRET, _TCKN, "Başka Ad"], clauses, audit) == (1, 2)


def test_pii_leaks_finds_non_ascii_value_only_in_model_call_json() -> None:
    # the value is in no clause; only the model_call payload carries it. With ASCII escaping
    # ş and ı would become \uXXXX and the leak would go unseen.
    clauses = [_clause("c1", "1", "Yetkili: [AD_1].")]
    audit = [
        _audit("model_call", {"rule_id": "r",
                              "messages": [{"role": "user", "content": "Yetkili: Ayşe Yılmaz"}]}),
    ]  # fmt: skip
    assert pii_leaks(["Ayşe Yılmaz"], clauses, audit) == (0,)


def test_build_contract_run_reports_positions_never_values() -> None:
    label = _label(
        id="contract-05", tags=("pii",), pii_values=(_TCKN, "Saklı Ad"),
        pii_best_effort=("Yok Ad", _SECRET),
    )  # fmt: skip
    clauses = [_clause("c1", "1", f"{_SECRET} ile [AD_1] arasında.")]
    audit = [
        _audit("model_call", {"rule_id": "r", "usage_metadata": {"input_tokens": 100,
                                                                   "output_tokens": 7}}),
        _audit("model_call", {"rule_id": "r", "usage_metadata": None}, 2),
        _audit("model_call", {"rule_id": "r", "usage_metadata": {"input_tokens": 5,
                                                                   "output_tokens": 1}}, 3),
    ]  # fmt: skip
    run = build_contract_run(label, clauses, audit, injection_flagged=False, seconds=2.5)
    assert run.pii_leaked == ()
    assert run.best_effort_leaked == (1,)
    assert (run.input_tokens, run.output_tokens) == (105, 8)
    c = contract_metrics([run], provider="groq", model=PROVIDER_DEFAULTS["groq"].model)
    assert c.best_effort == Ratio(1, 2)
    assert c.pii_leaked == 0
    returned = json.dumps(
        [contract_run_json(run), contract_metrics_json(c), repr(run), repr(c)],
        ensure_ascii=False,
    )
    for value in (*label.pii_values, *label.pii_best_effort):
        assert value not in returned
    assert "pii_best_effort[1]" in returned


def test_build_contract_run_prepare_only_has_no_tokens() -> None:
    run = build_contract_run(_label(), [], [], injection_flagged=True, seconds=None)
    assert (run.seconds, run.input_tokens, run.output_tokens) == (None, None, None)
    c = contract_metrics([run], provider="groq", model=PROVIDER_DEFAULTS["groq"].model)
    assert c.input_tokens is None
    assert c.cost_usd is None
    assert c.seconds_total is None


# --- Labels and file name -------------------------------------------------------------------------


def test_parse_labels_rejects_clause_no_on_not_found() -> None:
    record = {
        "id": "c", "tenant_id": "acme", "contract_file": "x.md", "tags": [],
        "pii_values": [], "pii_best_effort": [],
        "expected": [{"rule_id": "r", "status": "not_found", "clause_no": "3"}],
    }  # fmt: skip
    with pytest.raises(ValueError):
        parse_labels([record])


def test_result_filename_has_utc_stamp_provider_and_safe_model() -> None:
    at = datetime(2026, 11, 12, 8, 5, 9, tzinfo=UTC)
    assert (
        result_filename(at, "groq", "openai/gpt-oss-120b", partial=False, prepare_only=False)
        == "20261112-080509_groq_openai-gpt-oss-120b.json"
    )
    assert result_filename(at, "ollama", "qwen3.5:9b", partial=True, prepare_only=False).endswith(
        "_ollama_qwen3.5-9b_partial.json"
    )
    assert result_filename(at, "ollama", "m", partial=True,
                           prepare_only=True).endswith("_prepare-only.json")  # fmt: skip
    with pytest.raises(ValueError):
        result_filename(at.astimezone(timezone(timedelta(hours=3))), "groq", "m",
                        partial=False, prepare_only=False)  # fmt: skip


# --- Table ----------------------------------------------------------------------------------------


def test_render_table_partial_run_shows_counts_and_not_measured() -> None:
    rows = _mixed_rows()
    m = _metrics(rows)
    c = contract_metrics([_run()], provider="groq", model="başka-model")
    results = evaluate_thresholds(m, c)
    header = TableHeader(
        provider="groq", model="başka-model", started_at="2026-11-12T08:05:09+00:00",
        contracts_run=3, contracts_total=7, mode="full",
    )  # fmt: skip
    text = render_table(header, _metrics([replace(r, needs_review=False) for r in rows]), m, c,
                        results, "evals/results/x.json")  # fmt: skip
    assert "3/7 sözleşme" in text
    assert "Güvensiz hata oranı" in text
    assert "1/8 (%12,5)" in text
    assert NOT_MEASURED in text  # injection metrics
    assert "TUTMADI" in text  # missed non-compliance 2/4
    assert "bilinmiyor" in text  # model overridden
    assert "her not_found kuyruğa gittiği için ayırt edici değildir" in text
    assert text.rstrip().endswith("Sonuç dosyası: evals/results/x.json")
    lines = text.splitlines()
    assert lines.index(next(x for x in lines if x.startswith("Gereksiz yönlendirme"))) == (
        lines.index(next(x for x in lines if x.startswith("Avukata yönlendirme"))) + 1
    )


def test_render_table_prepare_only_everything_but_pii_and_scan_not_measured() -> None:
    m = _metrics([])
    runs = [
        _run(contract_id="contract-05", tags=("pii",), seconds=None, input_tokens=None,
             output_tokens=None, best_effort_leaked=(0, 1, 2, 3, 4), best_effort_total=5,
             pii_total=11),
        _run(contract_id="contract-06", tags=("injection_pattern",), seconds=None,
             input_tokens=None, output_tokens=None, injection_flagged=True),
    ]  # fmt: skip
    c = contract_metrics(runs, provider="groq", model=PROVIDER_DEFAULTS["groq"].model)
    header = TableHeader(
        provider="groq", model=PROVIDER_DEFAULTS["groq"].model, started_at="t",
        contracts_run=7, contracts_total=7, mode="prepare_only",
    )  # fmt: skip
    text = render_table(header, m, m, c, evaluate_thresholds(m, c), "out.json")
    line = {x.split("  ")[0]: x for x in text.splitlines()}
    assert "5 / 5" in line["Garanti edilmeyen PII sızıntısı"]
    assert "tuttu" in line["PII sızıntısı"]
    assert "evet" in line["Enjeksiyon: tarama"]
    assert NOT_MEASURED in line["Tahmini maliyet"]
    assert NOT_MEASURED in line["Etiket doğruluğu"]


def test_render_table_resumed_run_has_resume_line_only_when_resumed() -> None:
    m = _metrics(_mixed_rows())
    c = contract_metrics([_run()], provider="groq", model="m")
    base = TableHeader(
        provider="groq", model="m", started_at="t", contracts_run=7, contracts_total=7,
        mode="full",
    )  # fmt: skip
    plain = render_table(base, m, m, c, evaluate_thresholds(m, c), "out.json")
    assert "Devam:" not in plain
    resumed = replace(base, resumed_contracts=2, resumed_dates=("2026-11-11", "2026-11-12"))
    lines = render_table(resumed, m, m, c, evaluate_thresholds(m, c), "out.json").splitlines()
    assert lines[3] == "Devam: 2 sözleşme önbellekten (tarihler: 2026-11-11, 2026-11-12)"
    assert lines[4:] == plain.splitlines()[3:]


# --- Exact JSON round trip (the eval cache) -------------------------------------------------------


def test_rule_row_json_round_trip_is_exact() -> None:
    rows = [
        *_mixed_rows(),
        _row(tags=(), review_reasons=(), quote_valid=None, search_hit=None, clause_no=None,
             expected_clause_no=None, code_check="passed", interrupted_by="rate_limited"),
        _row(tags=("injection_pattern", "pii"), review_reasons=("quote_not_found", "x")),
    ]  # fmt: skip
    for row in rows:
        back = rule_row_from_json(json.loads(json.dumps(rule_row_to_json(row))))
        assert back == row
        assert isinstance(back.tags, tuple)
        assert isinstance(back.review_reasons, tuple)


def test_contract_run_json_round_trip_is_exact() -> None:
    runs = [
        _run(),
        _run(seconds=12.345678901234567, pii_leaked=(0, 3), best_effort_leaked=(1,),
             tags=("pii", "injection_pattern"), injection_flagged=True),
        _run(seconds=None, input_tokens=None, output_tokens=None, tags=()),
    ]  # fmt: skip
    for run in runs:
        back = contract_run_from_json(json.loads(json.dumps(contract_run_to_json(run))))
        assert back == run
        assert isinstance(back.pii_leaked, tuple)
        assert isinstance(back.best_effort_leaked, tuple)


def test_round_trip_json_covers_every_field() -> None:
    assert set(rule_row_to_json(_row())) == {f.name for f in fields(RuleRow)}
    assert set(contract_run_to_json(_run())) == {f.name for f in fields(ContractRun)}


@pytest.mark.parametrize(
    "change",
    [
        {"tags": "pii"},  # not a list
        {"search_calls": True},  # bool is not an int
        {"needs_review": 1},
        {"status": None},  # not optional
        {"extra": 1},
    ],
)
def test_rule_row_from_json_malformed_raises(change: dict[str, Any]) -> None:
    raw = rule_row_to_json(_row()) | change
    with pytest.raises(ValueError):
        rule_row_from_json(raw)


def test_contract_run_from_json_malformed_raises() -> None:
    raw = contract_run_to_json(_run())
    for bad in ({"pii_leaked": ["0"]}, {"seconds": "1"}, {"pii_total": None}):
        with pytest.raises(ValueError):
            contract_run_from_json(raw | bad)
    with pytest.raises(ValueError):
        contract_run_from_json({k: v for k, v in raw.items() if k != "tags"})


def test_run_dates_are_unique_sorted_days() -> None:
    stamps = ["2026-11-12T23:59:59+00:00", "2026-11-11T00:00:00+00:00", "2026-11-12T01:00:00+00:00"]
    assert run_dates(stamps) == ["2026-11-11", "2026-11-12"]
    assert run_dates([]) == []
