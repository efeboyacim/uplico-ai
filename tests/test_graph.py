import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import groq
import httpx
import ollama
import pytest
from conftest import CLAUSES, ScriptedChatModel, ScriptItem, ai, call, done, make_clauses
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from pydantic import PrivateAttr
from test_prepare import ALL_RAW, FIXTURE

from src.config import Settings, load_settings
from src.decision import build_outcome
from src.graph import (
    CHECKPOINTS_NAME,
    ReviewNotAwaitingError,
    ReviewState,
    _ordered,
    close_graphs,
    get_review_graph,
    initial_state,
    resume_command,
    review_config,
)
from src.models import (
    ContractDecision,
    Finding,
    FindingDecision,
    InjectionFlag,
    LawyerDecision,
    Rule,
)
from src.prepare import PreparedContract, prepare_contract
from src.prepare.index import MAX_RESULTS, ClauseIndex, SearchHit, put_index
from src.store import Store, UnknownTenantError

WAIT_S = 10  # deadlock guard for the gates below; never a timing assertion
SRC = Path(__file__).resolve().parent.parent / "src"


# --- Helpers --------------------------------------------------------------------------------------


def _settings(data_dir: Path, provider: str = "groq", max_concurrency: int = 4) -> Settings:
    keys = {"k-acme": {"tenant_id": "acme", "role": "lawyer", "name": "Av. Test"}}
    env = {
        "LLM_PROVIDER": provider,
        "TENANT_API_KEYS": json.dumps(keys),
        "DATA_DIR": str(data_dir),
        "MAX_CONCURRENCY": str(max_concurrency),
    }
    if provider == "groq":
        env["GROQ_API_KEY"] = "test-key"
    return load_settings(env=env)


class _Review:
    """A started review: its row, index and graph input, without running the graph."""

    def __init__(
        self,
        settings: Settings,
        tenant_id: str = "acme",
        rule_count: int | None = None,
        index: ClauseIndex | None = None,
        flags: Sequence[InjectionFlag] = (),
        register_index: bool = True,
    ) -> None:
        self.settings = settings
        self.store = Store(settings.data_dir, tenant_id)
        # The first `rule_count` rules of the tenant's rule set (all of them by default).
        self.rules = self.store.load_rules()[:rule_count]
        self.review_id = self.store.create_review(
            "asıl metin", settings.llm_provider, settings.llm_model
        )
        clauses = make_clauses(CLAUSES)
        if register_index:
            put_index(tenant_id, self.review_id, index or ClauseIndex(clauses))
        prepared = PreparedContract(self.review_id, tuple(clauses), tuple(flags))
        self.state: ReviewState = initial_state(prepared, self.rules)
        self.config = review_config(settings, self.review_id)

    def rows(self, event: str) -> list[dict[str, Any]]:
        return [r.payload for r in self.store.list_audit(self.review_id) if r.event == event]

    def actors(self, event: str) -> set[str]:
        return {r.actor for r in self.store.list_audit(self.review_id) if r.event == event}


def _invoke(review: _Review, model: ScriptedChatModel) -> dict[str, Any]:
    graph = get_review_graph(review.settings, review.store.tenant_id, model=model)
    return graph.invoke(review.state, review.config)


def _not_found() -> list[ScriptItem]:
    return [
        ai(call("search_contract", query="madde")),
        ai(call("submit_finding", status="not_found", reasoning="Yok.")),
        done(),
    ]


def _compliant() -> list[ScriptItem]:
    return [
        ai(call("search_contract", query="fesih bildirim")),
        ai(call("get_clause", clause_id="c2")),
        ai(
            call(
                "submit_finding",
                status="compliant",
                clause_id="c2",
                quote="30 gün önceden",
                value=30,
                unit="gün",
                reasoning="Gerekçe.",
            )
        ),
        done(),
    ]


def _scripts(
    rules: Sequence[Rule], overrides: Mapping[str, list[ScriptItem]] | None = None
) -> dict[str, list[ScriptItem]]:
    """A not_found script for every rule, except the ones overridden by rule id."""
    overrides = overrides or {}
    return {rule.text: overrides.get(rule.id, _not_found()) for rule in rules}


def _groq_error(cls: type[groq.APIStatusError], code: int) -> groq.APIStatusError:
    request = httpx.Request("POST", "https://api.groq.com")
    body = {"error": {"message": "gizli istek metni"}}
    response = httpx.Response(code, request=request, json=body)
    return cls(f"Error code: {code}", response=response, body=body)


def _fail_audit_event(store: Store, event: str) -> None:
    # TEMP: the trigger lives on the store's shared connection, not in the database file.
    store._conn.execute(
        f"CREATE TEMP TRIGGER fail_{event} BEFORE INSERT ON main.audit_log "
        f"WHEN NEW.event = '{event}' BEGIN SELECT RAISE(ABORT, '{event} yazilamadi'); END"
    )


def _assert_no_failed_rule(review: _Review) -> None:
    """No rule ended in a model error: a hook's own failure (a gate that timed out) is raised in
    the model call, so it would otherwise surface only as an `llm_error` finding."""
    errors = [r for r in review.rows("model_call") if "error" in r]
    assert all(r["error"]["type"] == "AuthenticationError" for r in errors), errors


class _HookedModel(ScriptedChatModel):
    """The scripted model with a hook called before each call: (rule text, call position)."""

    _hook: Callable[[str, int], None] | None = PrivateAttr(default=None)

    def with_hook(self, hook: Callable[[str, int], None]) -> "_HookedModel":
        self._hook = hook
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        key = self._rule_text(messages)
        if self._hook is not None:
            self._hook(key, len(self.received(key)))
        return super()._generate(messages, stop, run_manager, **kwargs)


# --- Runs to the interrupt ------------------------------------------------------------------------


def test_graph_runs_to_interrupt_with_findings_verified_and_audit(data_dir: Path) -> None:
    review = _Review(_settings(data_dir))
    rules = review.rules
    model = ScriptedChatModel.keyed(_scripts(rules, {"termination_notice": _compliant()}))
    out = _invoke(review, model)

    assert out["__interrupt__"]
    graph = get_review_graph(review.settings, "acme")
    snapshot = graph.get_state(review.config)
    assert snapshot.next == ("human_review",)
    assert snapshot.values["findings"] == out["findings"]
    assert snapshot.values["verified"] == out["verified"]
    # Plain dicts in the state (and so in the checkpoint), never pydantic objects.
    assert all(type(f) is dict for f in out["findings"] + out["verified"])

    verified = [Finding.model_validate(f) for f in out["verified"]]
    assert [f.rule_id for f in verified] == [r.id for r in rules]
    notice = verified[0]
    assert notice.status == "compliant"
    assert notice.needs_review is False
    for finding in verified[1:]:
        assert finding.status == "not_found"
        assert "unverified_not_found" in finding.review_reasons

    finding_rows = review.rows("finding")
    assert review.actors("finding") == {"agent"}
    assert sorted(finding_rows, key=lambda p: p["rule_id"]) == sorted(
        out["findings"], key=lambda p: p["rule_id"]
    )
    verification_rows = review.rows("verification")
    assert review.actors("verification") == {"system"}
    assert verification_rows == [
        {
            "rule_id": f.rule_id,
            "needs_review": f.needs_review,
            "review_reasons": f.review_reasons,
            "code_check": None,
        }
        for f in verified
    ]
    events = [r.event for r in review.store.list_audit(review.review_id)]
    # Every finding is recorded before verification; nothing after it (the API writes next).
    assert events[-len(rules) :] == ["verification"] * len(rules)
    assert events.count("finding") == len(rules)


def test_graph_verification_row_carries_proposal_code_check(data_dir: Path) -> None:
    review = _Review(_settings(data_dir), rule_count=1)
    [notice] = review.rules
    assert notice.id == "termination_notice"
    script: list[ScriptItem] = [
        ai(
            call(
                "submit_finding",
                status="non_compliant",
                clause_id="c2",
                quote="30 gün önceden",
                reasoning="g",
            )
        ),
        ai(call("propose_change", clause_id="c2", proposed_text="15 gün önceden", rationale="r")),
        done(),
    ]
    _invoke(review, ScriptedChatModel.keyed(_scripts([notice], {notice.id: script})))
    # Only the given rule ran: the graph input decides which rules are reviewed.
    assert review.rows("verification") == [
        {
            "rule_id": "termination_notice",
            "needs_review": True,
            "review_reasons": ["numeric_mismatch", "proposal_noncompliant"],
            "code_check": "failed",
        }
    ]


def test_graph_injection_suspected_from_prepared_flags(data_dir: Path) -> None:
    flag = InjectionFlag(clause_id="c1", kind="pattern", phrase="x", start=0, end=1)
    review = _Review(_settings(data_dir), flags=[flag])
    assert review.state["injection_suspected"] is True
    out = _invoke(review, ScriptedChatModel.keyed(_scripts(review.rules)))
    assert all("injection_suspected" in f["review_reasons"] for f in out["verified"])


def test_review_config_thread_and_max_concurrency(data_dir: Path) -> None:
    settings = _settings(data_dir, max_concurrency=3)
    assert review_config(settings, "r-1") == {
        "configurable": {"thread_id": "r-1"},
        "max_concurrency": 3,
    }


def test_initial_state_rejects_empty_rule_set() -> None:
    with pytest.raises(ValueError):
        initial_state(PreparedContract("r", (), ()), [])


# --- Findings in rule order -----------------------------------------------------------------------


def test_graph_findings_in_rule_order_whatever_the_completion_order(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = _Review(_settings(data_dir, max_concurrency=5))
    first, *others = review.rules
    others_recorded = threading.Event()
    recorded: list[str] = []
    lock = threading.Lock()
    original = Store.append_audit

    def recording(self: Store, review_id: str, actor: str, event: Any, payload: Any) -> None:
        original(self, review_id, actor, event, payload)
        if event == "finding":
            with lock:
                recorded.append(payload["rule_id"])
                if len(recorded) == len(others):
                    others_recorded.set()

    monkeypatch.setattr(Store, "append_audit", recording)

    def hold_first(key: str, position: int) -> None:
        # The first rule's branch waits until every other branch recorded its finding.
        if key == first.text and position == 0:
            assert others_recorded.wait(WAIT_S)

    model = _HookedModel(scripts=_scripts(review.rules)).with_hook(hold_first)
    out = _invoke(review, model)
    _assert_no_failed_rule(review)
    assert recorded[-1] == first.id
    assert [r["rule_id"] for r in review.rows("finding")][-1] == first.id
    assert [f["rule_id"] for f in out["verified"]] == [r.id for r in review.rules]
    assert [r["rule_id"] for r in review.rows("verification")] == [r.id for r in review.rules]


# --- Exactly one finding per rule -----------------------------------------------------------------


def _stray(rule_id: str) -> dict[str, Any]:
    return Finding(rule_id=rule_id, status="not_found", reasoning="Yok.").model_dump(mode="json")


@pytest.mark.parametrize(
    ("rule_id", "message"),
    [("termination_notice", "2 bulgu"), ("ghost_rule", "olmayan kural")],
    ids=["second-finding-for-a-rule", "finding-for-unknown-rule"],
)
def test_graph_verify_raises_unless_one_finding_per_rule(
    data_dir: Path, rule_id: str, message: str
) -> None:
    review = _Review(_settings(data_dir))
    # A finding already in the input: after the branches the rule set no longer matches one to one.
    review.state["findings"] = [_stray(rule_id)]
    with pytest.raises(RuntimeError, match=message):
        _invoke(review, ScriptedChatModel.keyed(_scripts(review.rules)))
    assert len(review.rows("finding")) == len(review.rules)
    assert review.rows("verification") == []
    snapshot = get_review_graph(review.settings, "acme").get_state(review.config)
    assert snapshot.next != ("human_review",)


def test_ordered_raises_for_rule_without_finding(data_dir: Path) -> None:
    rules = Store(data_dir, "acme").load_rules()
    findings = [Finding.model_validate(_stray(rule.id)) for rule in rules]
    assert [f.rule_id for f in _ordered(list(reversed(findings)), rules)] == [r.id for r in rules]
    with pytest.raises(RuntimeError, match="0 bulgu"):
        _ordered(findings[1:], rules)


# --- Checkpoint is read back without the serializer's warning -------------------------------------


def test_graph_checkpoint_read_back_logs_no_unregistered_type(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    review = _Review(_settings(data_dir))
    model = ScriptedChatModel.keyed(_scripts(review.rules, {"termination_notice": _compliant()}))
    with caplog.at_level(logging.DEBUG):
        out = _invoke(review, model)
        # A fresh connection: the state is deserialized from the file, not served from memory.
        close_graphs()
        graph = get_review_graph(review.settings, "acme", model=model)
        snapshot = graph.get_state(review.config)
    assert snapshot.next == ("human_review",)
    assert snapshot.values["verified"] == out["verified"]
    assert all(type(f) is dict for f in snapshot.values["findings"] + snapshot.values["verified"])
    serializer = [r for r in caplog.records if r.name.startswith("langgraph.checkpoint")]
    assert serializer == [], [r.getMessage() for r in serializer]


# --- Resume and finalize --------------------------------------------------------------------------

CLOSING_EVENTS = ("report_created", "review_closed")


def _to_interrupt(review: _Review) -> Any:
    """Run the review to its interrupt and store the findings, as the API does."""
    model = ScriptedChatModel.keyed(_scripts(review.rules))
    graph = get_review_graph(review.settings, review.store.tenant_id, model=model)
    out = graph.invoke(review.state, review.config)
    assert out["__interrupt__"]
    findings = [Finding.model_validate(f) for f in out["verified"]]
    assert review.store.save_findings(review.review_id, findings)
    return graph


def _accept_all(review: _Review, contract: ContractDecision = "approve") -> LawyerDecision:
    entries = [FindingDecision(rule_id=rule.id, action="accept") for rule in review.rules]
    return LawyerDecision(findings=entries, contract_decision=contract)


def _events_of(review: _Review) -> list[str]:
    return [r.event for r in review.store.list_audit(review.review_id)]


def test_graph_resume_runs_finalize_and_closes_review(data_dir: Path) -> None:
    review = _Review(_settings(data_dir))
    graph = _to_interrupt(review)
    before = _events_of(review)

    graph.invoke(resume_command(_accept_all(review), "Av. Test"), review.config)

    snapshot = graph.get_state(review.config)
    assert snapshot.next == ()
    assert snapshot.values["decision"]["decided_by"] == "Av. Test"
    record = review.store.get_review(review.review_id)
    assert record is not None and record.status == "approved"
    assert record.decided_by == "Av. Test"
    assert record.decision is not None
    assert [o.rule_id for o in record.decision.findings] == [r.id for r in review.rules]
    after = _events_of(review)[len(before) :]
    assert after == ["finding_decision"] * len(review.rules) + [
        "contract_decision",
        *CLOSING_EVENTS,
    ]
    assert review.actors("finding_decision") == {"lawyer:Av. Test"}
    assert review.actors("review_closed") == {"system"}


def test_graph_resume_checkpoint_read_back_logs_no_unregistered_type(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    review = _Review(_settings(data_dir))
    graph = _to_interrupt(review)
    with caplog.at_level(logging.DEBUG):
        graph.invoke(resume_command(_accept_all(review), "Av. Test"), review.config)
        # A fresh connection: the state is deserialized from the file, not served from memory.
        close_graphs()
        snapshot = get_review_graph(review.settings, "acme").get_state(review.config)
    assert snapshot.next == ()
    assert type(snapshot.values["decision"]) is dict
    assert type(snapshot.values["decision"]["decision"]) is dict
    serializer = [r for r in caplog.records if r.name.startswith("langgraph.checkpoint")]
    assert serializer == [], [r.getMessage() for r in serializer]


def test_graph_finalize_review_no_longer_awaiting_raises_writes_nothing(data_dir: Path) -> None:
    review = _Review(_settings(data_dir))
    graph = _to_interrupt(review)
    # Closed in between by someone else (another process): complete_review answers False.
    verified = [
        Finding.model_validate(f) for f in graph.get_state(review.config).values["verified"]
    ]
    outcome = build_outcome(_accept_all(review), verified, review.rules)
    assert review.store.complete_review(review.review_id, "approved", outcome, "Başka", [])
    rows = review.store.list_audit(review.review_id)

    with pytest.raises(ReviewNotAwaitingError):
        graph.invoke(resume_command(_accept_all(review), "Av. Test"), review.config)
    assert review.store.list_audit(review.review_id) == rows
    record = review.store.get_review(review.review_id)
    assert record is not None and record.decided_by == "Başka"
    assert graph.get_state(review.config).next == ("finalize",)


def test_graph_finalize_failure_then_resume_with_new_decision_records_latest(
    data_dir: Path,
) -> None:
    review = _Review(_settings(data_dir))
    graph = _to_interrupt(review)
    rows_before = review.store.list_audit(review.review_id)
    _fail_audit_event(review.store, "review_closed")

    with pytest.raises(sqlite3.IntegrityError):
        graph.invoke(resume_command(_accept_all(review, "approve"), "Av. Eski"), review.config)
    assert graph.get_state(review.config).next == ("finalize",)
    record = review.store.get_review(review.review_id)
    assert record is not None and record.status == "awaiting_lawyer"
    assert record.decision is None
    assert review.store.list_audit(review.review_id) == rows_before
    assert not review.store._conn.in_transaction

    review.store._conn.execute("DROP TRIGGER temp.fail_review_closed")
    graph.invoke(resume_command(_accept_all(review, "request_changes"), "Av. Yeni"), review.config)
    assert graph.get_state(review.config).next == ()
    record = review.store.get_review(review.review_id)
    assert record is not None and record.status == "changes_requested"
    assert record.decided_by == "Av. Yeni"
    assert record.decision is not None and record.decision.contract_decision == "request_changes"
    # The decision rows are written exactly once, for the new decision.
    events = _events_of(review)
    assert events.count("finding_decision") == len(review.rules)
    assert events.count("proposal_decision") == 0
    for event in ("contract_decision", *CLOSING_EVENTS):
        assert events.count(event) == 1, event
    assert review.actors("contract_decision") == {"lawyer:Av. Yeni"}
    assert review.rows("contract_decision") == [
        {"contract_decision": "request_changes", "status": "changes_requested"}
    ]
    assert events[-1] == "review_closed"


# --- Provider errors ------------------------------------------------------------------------------


def test_graph_rate_limit_rule_becomes_rate_limited_others_unaffected(data_dir: Path) -> None:
    review = _Review(_settings(data_dir))
    rate_limit = _groq_error(groq.RateLimitError, 429)
    server_error = _groq_error(groq.InternalServerError, 500)
    after_finding: list[ScriptItem] = [
        ai(call("submit_finding", status="not_found", reasoning="Yok.")),
        server_error,
    ]
    model = ScriptedChatModel.keyed(
        _scripts(
            review.rules,
            {
                "termination_notice": _compliant(),
                "late_penalty_cap": [rate_limit],
                "governing_law": after_finding,
            },
        )
    )
    out = _invoke(review, model)
    verified = {f["rule_id"]: Finding.model_validate(f) for f in out["verified"]}

    limited = verified["late_penalty_cap"]
    assert limited.status == "not_found"
    assert limited.no_finding_reason == "rate_limited"
    assert limited.needs_review is True
    assert limited.review_reasons == ["no_finding"]

    interrupted = verified["governing_law"]
    assert interrupted.interrupted_by == "llm_error"
    assert interrupted.no_finding_reason is None
    assert "run_interrupted" in interrupted.review_reasons

    notice = verified["termination_notice"]
    assert notice.status == "compliant"
    assert notice.needs_review is False
    for rule_id in ("insurance_minimum", "liability_cap"):
        assert verified[rule_id].no_finding_reason is None
        assert verified[rule_id].interrupted_by is None
    assert len(review.rows("finding")) == len(review.rules)


@pytest.mark.parametrize(
    "error",
    [
        groq.APIConnectionError(request=httpx.Request("POST", "https://api.groq.com")),
        ConnectionError("bağlantı reddedildi"),
        httpx.ConnectError("bağlantı reddedildi"),
    ],
    ids=["groq-connection", "ollama-connection", "httpx-connect"],
)
def test_graph_connection_refused_not_raised_llm_error(data_dir: Path, error: Exception) -> None:
    review = _Review(_settings(data_dir))
    model = ScriptedChatModel.keyed(_scripts(review.rules, {"liability_cap": [error]}))
    out = _invoke(review, model)
    verified = {f["rule_id"]: f for f in out["verified"]}
    assert verified["liability_cap"]["no_finding_reason"] == "llm_error"
    assert verified["liability_cap"]["needs_review"] is True


def _config_errors() -> list[Exception]:
    return [
        _groq_error(groq.AuthenticationError, 401),
        _groq_error(groq.PermissionDeniedError, 403),
        _groq_error(groq.NotFoundError, 404),
        ollama.ResponseError("model not found", 404),
    ]


@pytest.mark.parametrize("error", _config_errors(), ids=["groq401", "groq403", "groq404", "ol404"])
def test_graph_config_class_error_raised_out_of_graph(data_dir: Path, error: Exception) -> None:
    review = _Review(_settings(data_dir, max_concurrency=1))
    model = ScriptedChatModel.keyed(_scripts(review.rules, {"late_penalty_cap": [error]}))
    with pytest.raises(type(error)) as info:
        _invoke(review, model)
    assert info.value is error
    # The failing rule never got a finding; no other rule's finding was turned into one either.
    findings = review.rows("finding")
    assert "late_penalty_cap" not in {f["rule_id"] for f in findings}
    assert all(f["no_finding_reason"] is None for f in findings)
    assert review.rows("verification") == []
    [failed] = [r for r in review.rows("model_call") if "error" in r]
    assert failed["rule_id"] == "late_penalty_cap"


def test_graph_config_error_every_started_branch_ends_before_invoke_raises(
    data_dir: Path,
) -> None:
    # Two branches at a time: A fails while B is still running. The thread A freed may still
    # pick up the next queued branch before the failure is seen; whatever started has ended
    # (its finding row is written) before invoke raises, and nothing is written afterwards.
    review = _Review(_settings(data_dir, max_concurrency=2), rule_count=4)
    a, b, c, d = review.rules
    error = _groq_error(groq.AuthenticationError, 401)
    a_failing = threading.Event()

    def gate(key: str, position: int) -> None:
        if key == a.text:
            a_failing.set()
        elif key == b.text and position == 0:
            assert a_failing.wait(WAIT_S)

    scripts = _scripts([a, b, c, d], {a.id: [error]})
    model = _HookedModel(scripts=scripts).with_hook(gate)
    with pytest.raises(groq.AuthenticationError):
        _invoke(review, model)

    rows = review.store.list_audit(review.review_id)
    started = {rule.id for rule in (a, b, c, d) if model.received(rule.text)}
    assert {a.id, b.id} <= started
    # Every started branch except the failed one wrote its last row (finding) before invoke
    # raised; the failed one wrote only its failed model call; the others wrote nothing.
    findings = sorted(r.payload["rule_id"] for r in rows if r.event == "finding")
    assert findings == sorted(started - {a.id})
    _assert_no_failed_rule(review)
    assert {r.payload["rule_id"] for r in rows} == started
    assert [r.event for r in rows if r.payload["rule_id"] == a.id] == ["model_call"]
    assert review.rows("verification") == []
    # Nothing is appended after invoke raised: every started branch has already ended.
    assert review.store.list_audit(review.review_id) == rows


# --- Code errors ----------------------------------------------------------------------------------


class _ExplodingIndex(ClauseIndex):
    def search(self, query: str, limit: int = MAX_RESULTS) -> list[SearchHit]:
        if query == "patlat":
            raise RuntimeError("beklenmeyen kod hatası")
        return super().search(query, limit)


def test_graph_unexpected_code_error_in_one_rule_propagates(data_dir: Path) -> None:
    review = _Review(_settings(data_dir), index=_ExplodingIndex(make_clauses(CLAUSES)))
    exploding: list[ScriptItem] = [ai(call("search_contract", query="patlat")), done()]
    model = ScriptedChatModel.keyed(_scripts(review.rules, {"liability_cap": exploding}))
    with pytest.raises(RuntimeError, match="beklenmeyen kod hatası"):
        _invoke(review, model)
    findings = review.rows("finding")
    assert "liability_cap" not in {f["rule_id"] for f in findings}
    assert all(f["no_finding_reason"] is None for f in findings)
    assert review.rows("verification") == []


def test_graph_missing_index_raises(data_dir: Path) -> None:
    review = _Review(_settings(data_dir), register_index=False)
    with pytest.raises(LookupError):
        _invoke(review, ScriptedChatModel.keyed(_scripts(review.rules)))
    assert review.rows("finding") == []


@pytest.mark.parametrize("event", ["model_call", "tool_call", "finding", "verification"])
def test_graph_audit_write_failure_raised_not_turned_into_no_finding(
    data_dir: Path, event: str
) -> None:
    review = _Review(_settings(data_dir))
    _fail_audit_event(review.store, event)
    model = ScriptedChatModel.keyed(_scripts(review.rules))
    with pytest.raises(sqlite3.IntegrityError, match=f"{event} yazilamadi"):
        _invoke(review, model)
    findings = review.rows("finding")
    assert all(f["no_finding_reason"] is None for f in findings)
    assert all(f["interrupted_by"] is None for f in findings)
    assert review.rows("verification") == []
    snapshot = get_review_graph(review.settings, "acme").get_state(review.config)
    assert snapshot.next != ("human_review",)
    assert not review.store._conn.in_transaction


# --- Concurrency ----------------------------------------------------------------------------------


@pytest.mark.parametrize("limit", [1, 2, 4])
def test_graph_simultaneous_branches_never_exceed_max_concurrency(
    data_dir: Path, limit: int
) -> None:
    review = _Review(_settings(data_dir, max_concurrency=limit), rule_count=4)
    rules = review.rules
    lock = threading.Lock()
    active = 0
    peak = 0
    # Every branch waits at its first call until `limit` branches are inside: the limit is
    # reached on every round, so the peak is measured at the limit, not below it by chance.
    barrier = threading.Barrier(limit)
    last_position = len(_not_found()) - 1

    def count(key: str, position: int) -> None:
        nonlocal active, peak
        if position == 0:
            with lock:
                active += 1
                peak = max(peak, active)
            barrier.wait(WAIT_S)
        elif position == last_position:
            with lock:
                active -= 1

    _invoke(review, _HookedModel(scripts=_scripts(rules)).with_hook(count))
    _assert_no_failed_rule(review)
    assert peak == limit
    assert active == 0
    assert len(review.rows("finding")) == len(rules)


def test_graph_parallel_audit_counts_match_scripts(data_dir: Path) -> None:
    review = _Review(_settings(data_dir, max_concurrency=4))
    two_calls_one_turn: list[ScriptItem] = [
        ai(call("search_contract", query="ödeme"), call("get_clause", clause_id="c3")),
        ai(call("submit_finding", status="not_found", reasoning="Yok.")),
        done(),
    ]
    only_submit: list[ScriptItem] = [
        ai(call("submit_finding", status="not_found", reasoning="Yok.")),
        done(),
    ]
    scripts = {
        "termination_notice": _compliant(),
        "late_penalty_cap": _not_found(),
        "insurance_minimum": two_calls_one_turn,
        "liability_cap": only_submit,
        "governing_law": [done()],
    }
    model = ScriptedChatModel.keyed(_scripts(review.rules, scripts))
    out = _invoke(review, model)

    def tool_calls(items: list[ScriptItem]) -> int:
        return sum(len(i.tool_calls) for i in items if isinstance(i, AIMessage))

    model_calls = review.rows("model_call")
    tool_rows = review.rows("tool_call")
    for rule_id, script in scripts.items():
        assert sum(r["rule_id"] == rule_id for r in model_calls) == len(script), rule_id
        assert sum(r["rule_id"] == rule_id for r in tool_rows) == tool_calls(script), rule_id
    assert len(model_calls) == sum(len(s) for s in scripts.values())
    assert len(tool_rows) == sum(tool_calls(s) for s in scripts.values())
    assert sorted(r["rule_id"] for r in review.rows("finding")) == sorted(scripts)
    verified = {f["rule_id"]: f for f in out["verified"]}
    assert verified["governing_law"]["no_finding_reason"] == "no_submit"


# --- No unmasked text to the model or the checkpoint ----------------------------------------------


def _sample() -> str:
    return FIXTURE.read_bytes().decode("utf-8")


def _prepared_review(settings: Settings) -> tuple[Store, PreparedContract, list[Rule]]:
    store = Store(settings.data_dir, "acme")
    prepared = prepare_contract(
        store, _sample(), provider=settings.llm_provider, model=settings.llm_model
    )
    return store, prepared, store.load_rules()


def _read_everything(prepared: PreparedContract) -> list[ScriptItem]:
    """A script that reads every clause, so every masked clause text reaches the model."""
    reads = [call("get_clause", clause_id=c.clause_id) for c in prepared.clauses]
    return [
        ai(call("search_contract", query="taraflar fesih ödeme")),
        ai(*reads),
        ai(call("submit_finding", status="not_found", reasoning="Yok.")),
        done(),
    ]


def _sent_text(messages: Iterator[BaseMessage] | list[BaseMessage]) -> str:
    parts: list[str] = []
    for message in messages:
        parts.append(str(message.content))
        if isinstance(message, AIMessage):
            parts.append(json.dumps(message.tool_calls, ensure_ascii=False))
    return "\n".join(parts)


@pytest.mark.parametrize("provider", ["groq", "ollama"])
def test_graph_no_unmasked_text_reaches_model(data_dir: Path, provider: str) -> None:
    settings = _settings(data_dir, provider=provider)
    store, prepared, rules = _prepared_review(settings)
    model = ScriptedChatModel.keyed({r.text: _read_everything(prepared) for r in rules})
    graph = get_review_graph(settings, "acme", model=model)
    graph.invoke(initial_state(prepared, rules), review_config(settings, prepared.review_id))

    sent = "\n".join(_sent_text(messages) for messages in model.received())
    assert "[KISI_1]" in sent  # the masked contract did reach the model
    for value in ALL_RAW:
        assert value not in sent, f"{value!r} reached the model"
    audit = json.dumps(
        [r.payload for r in store.list_audit(prepared.review_id)], ensure_ascii=False
    )
    for value in ALL_RAW:
        assert value not in audit, f"{value!r} reached the audit log"


@pytest.mark.parametrize(
    "extra",
    [
        {"LLM_PROVIDER": "mistralai", "LLM_MODEL": "mistral-large-latest"},
        {
            "LLM_PROVIDER": "openai",
            "LLM_MODEL": "m",
            "LLM_API_KEY": "k",
            "LLM_BASE_URL": "http://x",
        },
    ],
    ids=["unlisted", "openai_compatible"],
)
def test_graph_no_unmasked_text_reaches_model_other_providers(
    data_dir: Path, extra: dict[str, str]
) -> None:
    # Masking does not depend on the provider: the same check as above, other settings.
    keys = {"k-acme": {"tenant_id": "acme", "role": "lawyer", "name": "Av. Test"}}
    env = {"TENANT_API_KEYS": json.dumps(keys), "DATA_DIR": str(data_dir)} | extra
    settings = load_settings(env=env)
    assert settings.llm_provider == extra["LLM_PROVIDER"]
    store, prepared, rules = _prepared_review(settings)
    model = ScriptedChatModel.keyed({r.text: _read_everything(prepared) for r in rules})
    graph = get_review_graph(settings, "acme", model=model)
    graph.invoke(initial_state(prepared, rules), review_config(settings, prepared.review_id))

    sent = "\n".join(_sent_text(messages) for messages in model.received())
    assert "[KISI_1]" in sent
    for value in ALL_RAW:
        assert value not in sent, f"{value!r} reached the model"
    audit = json.dumps(
        [r.payload for r in store.list_audit(prepared.review_id)], ensure_ascii=False
    )
    for value in ALL_RAW:
        assert value not in audit, f"{value!r} reached the audit log"


def test_graph_checkpoint_holds_no_raw_pii(data_dir: Path) -> None:
    settings = _settings(data_dir)
    store, prepared, rules = _prepared_review(settings)
    model = ScriptedChatModel.keyed({r.text: _read_everything(prepared) for r in rules})
    graph = get_review_graph(settings, "acme", model=model)
    out = graph.invoke(initial_state(prepared, rules), review_config(settings, prepared.review_id))
    assert out["__interrupt__"]
    # Closing the last connection folds the WAL into the database file.
    close_graphs()
    path = store.tenant_dir / CHECKPOINTS_NAME
    stored = b"".join(
        p.read_bytes() for p in (path, path.with_name(f"{CHECKPOINTS_NAME}-wal")) if p.exists()
    )
    assert b"[KISI_1]" in stored  # the masked clauses are in the checkpoint
    assert prepared.review_id.encode() in stored
    for value in ALL_RAW:
        assert value.encode("utf-8") not in stored, f"{value!r} is in checkpoints.db"


# --- Per-tenant graphs ----------------------------------------------------------------------------


def _thread_ids(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute("SELECT DISTINCT thread_id FROM checkpoints")}
    finally:
        conn.close()


def test_graph_two_tenants_separate_graphs_and_checkpoint_files(data_dir: Path) -> None:
    settings = _settings(data_dir)
    acme = _Review(settings, "acme")
    globex = _Review(settings, "globex")
    acme_model = ScriptedChatModel.keyed(_scripts(acme.rules))
    globex_model = ScriptedChatModel.keyed(_scripts(globex.rules))
    acme_graph = get_review_graph(settings, "acme", model=acme_model)
    globex_graph = get_review_graph(settings, "globex", model=globex_model)
    assert acme_graph is not globex_graph
    # Compiled once per tenant: later calls get the cached graph.
    assert get_review_graph(settings, "acme") is acme_graph
    assert get_review_graph(settings, "acme", model=acme_model) is acme_graph
    with pytest.raises(ValueError):
        get_review_graph(settings, "acme", model=globex_model)

    acme_graph.invoke(acme.state, acme.config)
    globex_graph.invoke(globex.state, globex.config)
    assert globex_graph.get_state(acme.config).values == {}
    close_graphs()
    acme_path = acme.store.tenant_dir / CHECKPOINTS_NAME
    globex_path = globex.store.tenant_dir / CHECKPOINTS_NAME
    assert acme_path != globex_path
    assert _thread_ids(acme_path) == {acme.review_id}
    assert _thread_ids(globex_path) == {globex.review_id}
    assert len(acme.rows("finding")) == len(acme.rules)
    assert len(globex.rows("finding")) == len(globex.rules)


@pytest.mark.parametrize("tenant_id", ["unknown", "../acme", "acme/..", ""])
def test_graph_unknown_tenant_rejected_before_any_file(data_dir: Path, tenant_id: str) -> None:
    with pytest.raises(UnknownTenantError):
        get_review_graph(_settings(data_dir), tenant_id, model=ScriptedChatModel.single())
    assert list(data_dir.rglob(f"{CHECKPOINTS_NAME}*")) == []


# --- Synchronous only -----------------------------------------------------------------------------


def test_src_has_no_async_code() -> None:
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in ("async def", "ainvoke", "astream", "awrap_", "await "):
            assert token not in text, f"{token!r} in {path}"
