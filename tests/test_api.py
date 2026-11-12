import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import groq
import pytest
from conftest import ScriptedChatModel, ScriptItem, ai, call, done
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from test_graph import WAIT_S, _config_errors, _fail_audit_event, _groq_error
from test_prepare import ALL_RAW, FIXTURE, SUSPICIOUS_SENTENCE_START

import src.api as api_module
import src.prepare as prepare_module
from src.api import create_app
from src.config import ConfigError, Settings, load_settings
from src.graph import CHECKPOINTS_NAME, close_graphs, get_review_graph, review_config
from src.models import Clause, Finding, InjectionFlag, ReviewRecord
from src.prepare.index import MAX_RESULTS, ClauseIndex, SearchHit, get_index
from src.prepare.pii import PLACEHOLDER
from src.store import RulesError, Store
from src.text import strip_invisible

KEYS: dict[str, dict[str, str]] = {
    "k-acme-lawyer": {"tenant_id": "acme", "role": "lawyer", "name": "Av. Acme"},
    "k-acme-viewer": {"tenant_id": "acme", "role": "viewer", "name": "Gözlemci Acme"},
    "k-globex-lawyer": {"tenant_id": "globex", "role": "lawyer", "name": "Av. Globex"},
    "k-globex-viewer": {"tenant_id": "globex", "role": "viewer", "name": "Gözlemci Globex"},
}
ACME_LAWYER = {"X-API-Key": "k-acme-lawyer"}
ACME_VIEWER = {"X-API-Key": "k-acme-viewer"}
GLOBEX_LAWYER = {"X-API-Key": "k-globex-lawyer"}
GLOBEX_VIEWER = {"X-API-Key": "k-globex-viewer"}

REVIEW_KEYS = {
    "review_id",
    "status",
    "created_at",
    "clauses",
    "injection_flags",
    "findings",
    "contract_decision",
    "decided_at",
    "decided_by",
}
CLAUSE_KEYS = {"clause_id", "clause_no", "text"}
FLAG_KEYS = {"clause_id", "kind", "phrase"}
FINDING_KEYS = {
    "rule_id",
    "status",
    "clause_id",
    "quote",
    "value",
    "unit",
    "reasoning",
    "proposal",
    "escalation_reason",
    "no_finding_reason",
    "interrupted_by",
    "needs_review",
    "review_reasons",
    "final_status",
    "action",
    "proposal_action",
    "edited_text",
}
DECISION_REVIEW_KEYS = ("contract_decision", "decided_at", "decided_by")
DECISION_FINDING_KEYS = ("final_status", "action", "proposal_action", "edited_text")
PROPOSAL_KEYS = {"clause_id", "proposed_text", "rationale", "code_check"}
LAWYER_REVIEW_KEYS = REVIEW_KEYS | {"original_text"}
LAWYER_CLAUSE_KEYS = CLAUSE_KEYS | {"start", "end"}
LAWYER_FLAG_KEYS = FLAG_KEYS | {"start", "end", "detail"}

MSG_INTERNAL = "İnceleme tamamlanamadı: beklenmeyen bir hata oluştu."


# --- Helpers --------------------------------------------------------------------------------------


def _settings(data_dir: Path, keys: Mapping[str, Mapping[str, str]] = KEYS) -> Settings:
    env = {
        "LLM_PROVIDER": "groq",
        "GROQ_API_KEY": "test-key",
        "TENANT_API_KEYS": json.dumps(keys),
        "DATA_DIR": str(data_dir),
    }
    return load_settings(env=env)


@pytest.fixture
def settings(data_dir: Path) -> Settings:
    return _settings(data_dir)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _sample() -> str:
    # Bytes, not read_text: no newline translation, so the test sees the file exactly.
    return FIXTURE.read_bytes().decode("utf-8")


def _not_found() -> list[ScriptItem]:
    return [
        ai(call("search_contract", query="madde")),
        ai(call("submit_finding", status="not_found", reasoning="Yok.")),
        done(),
    ]


def _termination_compliant() -> list[ScriptItem]:
    # c11 is "Madde 10 - Fesih" of the sample contract. The texts carry placeholders, so the
    # lawyer response shows whether every text field of a finding is unmasked.
    return [
        ai(call("search_contract", query="fesih bildirim")),
        ai(call("get_clause", clause_id="c11")),
        ai(call("escalate_to_lawyer", reason="[KISI_2] ile görüşülmeli.")),
        ai(
            call(
                "submit_finding",
                status="compliant",
                clause_id="c11",
                quote="45 gün önceden yazılı bildirimde bulunmak",
                value=45,
                unit="gün",
                reasoning="[KISI_1] ile [KISI_2] arasında 45 gün.",
            )
        ),
        done(),
    ]


def _liability_with_proposal() -> list[ScriptItem]:
    return [
        ai(
            call(
                "submit_finding",
                status="non_compliant",
                clause_id="c12",
                quote="Alıcıya yapılacak bildirimler [KISI_1]",
                reasoning="[KISI_1] bildirimi alır.",
            )
        ),
        ai(
            call(
                "propose_change",
                clause_id="c12",
                proposed_text="Bildirimler [KISI_2] adına yapılır.",
                rationale="[KISI_1] onayı gerekir.",
            )
        ),
        done(),
    ]


def _install_model(
    settings: Settings,
    tenant_id: str,
    overrides: Mapping[str, list[ScriptItem]] | None = None,
    runs: int = 1,
) -> ScriptedChatModel:
    """Compile the tenant's graph with a scripted model before any request reaches it."""
    overrides = overrides or {}
    rules = Store(settings.data_dir, tenant_id).load_rules()
    scripts = {r.text: overrides.get(r.id, _not_found()) * runs for r in rules}
    model = ScriptedChatModel.keyed(scripts)
    get_review_graph(settings, tenant_id, model=model)
    return model


def _review_ids(store: Store) -> list[str]:
    return [row[0] for row in store._conn.execute("SELECT review_id FROM reviews")]


def _audit_count(store: Store) -> int:
    return int(store._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])


def _only_review(store: Store) -> ReviewRecord:
    [review_id] = _review_ids(store)
    record = store.get_review(review_id)
    assert record is not None
    return record


def _events(store: Store, review_id: str) -> list[str]:
    return [r.event for r in store.list_audit(review_id)]


def _assert_no_model_error(store: Store, review_id: str, allowed: Sequence[str] = ()) -> None:
    """No rule ended in an unexpected model error: an exhausted script raises inside the model
    call and would otherwise surface only as an `llm_error` finding."""
    rows = [r.payload for r in store.list_audit(review_id) if r.event == "model_call"]
    errors = [r["error"] for r in rows if "error" in r]
    assert all(e["type"] in allowed for e in errors), errors  # type: ignore[index]


def _post_ok(client: TestClient, headers: Mapping[str, str], **extra: Any) -> dict[str, Any]:
    response = client.post("/reviews", json={"text": _sample(), **extra}, headers=dict(headers))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _assert_failed(store: Store, review_id: str, reason: str) -> None:
    record = store.get_review(review_id)
    assert record is not None
    assert record.status == "failed"
    assert record.findings == []
    rows = store.list_audit(review_id)
    assert rows[-1].event == "review_failed"
    assert rows[-1].actor == "system"
    assert rows[-1].payload == {"reason": reason}
    assert [r.event for r in rows].count("review_failed") == 1
    assert "awaiting_lawyer" not in [r.event for r in rows]
    assert get_index(store.tenant_id, review_id) is None


# --- Startup and app shape ------------------------------------------------------------------------


def test_create_app_key_for_unknown_tenant_raises_config_error(data_dir: Path) -> None:
    keys = {**KEYS, "k-initech-secret": {"tenant_id": "initech", "role": "lawyer", "name": "X"}}
    with pytest.raises(ConfigError) as info:
        create_app(_settings(data_dir, keys))
    assert "initech" in str(info.value)
    assert "k-initech-secret" not in str(info.value)


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_auto_docs_endpoints_disabled_not_found(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404
    assert client.get(path, headers=ACME_LAWYER).status_code == 404


def test_api_module_has_no_module_level_app() -> None:
    assert not any(isinstance(v, FastAPI) for v in vars(api_module).values())


# --- Keys and roles (walk the route list) ---------------------------------------------------------


def _routes(app: FastAPI) -> list[tuple[str, str]]:
    return sorted(
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    )


def _url(path: str) -> str:
    return path.replace("{review_id}", str(uuid.uuid4()))


def test_routes_walk_covers_the_known_endpoints(app: FastAPI) -> None:
    routes = _routes(app)
    assert ("POST", "/reviews") in routes
    assert ("GET", "/reviews/{review_id}") in routes
    assert ("GET", "/reviews/{review_id}/audit") in routes
    assert ("POST", "/reviews/{review_id}/decision") in routes


def test_all_write_endpoints_viewer_key_forbidden(
    app: FastAPI, client: TestClient, data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    writes = [(m, p) for m, p in _routes(app) if m != "GET"]
    assert writes
    with caplog.at_level(logging.WARNING):
        for method, path in writes:
            for headers in (ACME_VIEWER, GLOBEX_VIEWER):
                body = {"text": _sample(), "role": "lawyer"}
                response = client.request(method, _url(path), json=body, headers=headers)
                assert response.status_code == 403, (method, path)
    for tenant in ("acme", "globex"):
        store = Store(data_dir, tenant)
        assert _review_ids(store) == []
        assert _audit_count(store) == 0
    assert "k-acme-viewer" not in caplog.text
    assert "k-globex-viewer" not in caplog.text
    assert any(r.levelno == logging.WARNING and "403" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-API-Key": ""}, {"X-API-Key": "unknown-key-123"}, {"X-API-Key": "K-ACME-LAWYER"}],
    ids=["missing", "empty", "unknown", "wrong-case"],
)
def test_all_endpoints_missing_or_unknown_key_unauthorized(
    app: FastAPI,
    client: TestClient,
    data_dir: Path,
    headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    # GET / (the data-free UI page) is the only endpoint allowed without a key.
    routes = [(m, p) for m, p in _routes(app) if (m, p) != ("GET", "/")]
    assert routes
    with caplog.at_level(logging.WARNING):
        for method, path in routes:
            body = {"text": _sample()} if method != "GET" else None
            response = client.request(method, _url(path), json=body, headers=headers)
            assert response.status_code == 401, (method, path)
    for tenant in ("acme", "globex"):
        assert _review_ids(Store(data_dir, tenant)) == []
    assert "unknown-key-123" not in caplog.text
    assert "K-ACME-LAWYER" not in caplog.text


def test_post_review_malformed_json_without_key_unprocessable(
    client: TestClient, data_dir: Path
) -> None:
    # The one known exception to the check order: FastAPI parses the body before dependencies.
    response = client.post(
        "/reviews", content=b'{"text": "bozuk', headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert _review_ids(Store(data_dir, "acme")) == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"json": {"metin": "x"}},
        {"json": {"text": ""}},
        {"content": b"text=x", "headers": {"Content-Type": "application/x-www-form-urlencoded"}},
    ],
    ids=["no-body", "wrong-schema", "empty-text", "not-json"],
)
def test_post_review_bad_body_without_key_unauthorized(
    client: TestClient, kwargs: dict[str, Any]
) -> None:
    assert client.post("/reviews", **kwargs).status_code == 401


@pytest.mark.parametrize("text", ["", "   \n\t"], ids=["empty", "blank"])
def test_post_review_empty_text_unprocessable_no_review(
    client: TestClient, data_dir: Path, text: str
) -> None:
    response = client.post("/reviews", json={"text": text}, headers=ACME_LAWYER)
    assert response.status_code == 422
    assert _review_ids(Store(data_dir, "acme")) == []


# --- Contract size cap ----------------------------------------------------------------------------


def test_max_contract_chars_is_two_million() -> None:
    assert api_module.MAX_CONTRACT_CHARS == 2_000_000


def test_post_review_oversized_text_payload_too_large_nothing_written(
    client: TestClient, settings: Settings
) -> None:
    model = _install_model(settings, "acme")
    text = "a" * (api_module.MAX_CONTRACT_CHARS + 1)
    response = client.post("/reviews", json={"text": text}, headers=ACME_LAWYER)
    assert response.status_code == 413
    assert len(response.content) < 1000
    assert "aaaa" not in response.text
    store = Store(settings.data_dir, "acme")
    assert _review_ids(store) == []
    assert _audit_count(store) == 0
    assert model.received() == []


def test_post_review_oversized_text_without_key_unauthorized(
    client: TestClient, data_dir: Path
) -> None:
    text = "a" * (api_module.MAX_CONTRACT_CHARS + 1)
    assert client.post("/reviews", json={"text": text}).status_code == 401
    assert _review_ids(Store(data_dir, "acme")) == []


@pytest.mark.parametrize(
    "text",
    [" " * (api_module.MAX_CONTRACT_CHARS + 1), ["a" * (api_module.MAX_CONTRACT_CHARS + 1)]],
    ids=["blank", "wrong-type"],
)
def test_post_review_oversized_invalid_body_unprocessable_input_not_echoed(
    client: TestClient, data_dir: Path, text: object
) -> None:
    # These never reach the 413 check; the 422 body must not carry the input back either.
    response = client.post("/reviews", json={"text": text}, headers=ACME_LAWYER)
    assert response.status_code == 422
    assert len(response.content) < 1000
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "text"]
    assert all(set(error) == {"type", "loc", "msg"} for error in detail)
    assert _review_ids(Store(data_dir, "acme")) == []


def test_post_review_text_limit_boundary_exact_accepted_one_over_rejected(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = _sample()
    monkeypatch.setattr("src.api.MAX_CONTRACT_CHARS", len(sample))
    _install_model(settings, "acme")
    ok = client.post("/reviews", json={"text": sample}, headers=ACME_LAWYER)
    assert ok.status_code == 200, ok.text
    store = Store(settings.data_dir, "acme")
    assert len(_review_ids(store)) == 1

    over = client.post("/reviews", json={"text": sample + " "}, headers=ACME_LAWYER)
    assert over.status_code == 413
    assert len(_review_ids(store)) == 1


# --- Starting a review ----------------------------------------------------------------------------


def test_post_review_runs_to_awaiting_lawyer_and_drops_index(
    client: TestClient, settings: Settings
) -> None:
    _install_model(settings, "acme", {"termination_notice": _termination_compliant()})
    body = _post_ok(client, ACME_LAWYER)
    store = Store(settings.data_dir, "acme")
    review_id = body["review_id"]
    _assert_no_model_error(store, review_id)

    assert body["status"] == "awaiting_lawyer"
    rules = store.load_rules()
    assert [f["rule_id"] for f in body["findings"]] == [r.id for r in rules]
    record = store.get_review(review_id)
    assert record is not None and record.status == "awaiting_lawyer"
    assert [f.rule_id for f in record.findings] == [r.id for r in rules]
    notice = record.findings[0]
    assert notice.status == "compliant" and notice.clause_id == "c11"

    rows = store.list_audit(review_id)
    assert rows[-1].event == "awaiting_lawyer"
    assert rows[-1].actor == "system"
    assert rows[-1].payload == {
        "finding_count": len(rules),
        "needs_review_count": sum(f.needs_review for f in record.findings),
    }
    assert "review_failed" not in _events(store, review_id)
    assert get_index("acme", review_id) is None


def test_post_review_response_same_as_get_for_lawyer(
    client: TestClient, settings: Settings
) -> None:
    _install_model(settings, "acme", {"termination_notice": _termination_compliant()})
    posted = _post_ok(client, ACME_LAWYER)
    fetched = client.get(f"/reviews/{posted['review_id']}", headers=ACME_LAWYER)
    assert fetched.status_code == 200
    assert fetched.json() == posted


@pytest.mark.parametrize("error", _config_errors(), ids=["groq401", "groq403", "groq404", "ol404"])
def test_post_review_provider_config_error_failed_502(
    client: TestClient, settings: Settings, error: Exception
) -> None:
    _install_model(settings, "acme", {"late_penalty_cap": [error]})
    response = client.post("/reviews", json={"text": _sample()}, headers=ACME_LAWYER)
    assert response.status_code == 502
    assert "gizli istek metni" not in response.text
    store = Store(settings.data_dir, "acme")
    record = _only_review(store)
    _assert_failed(store, record.review_id, "provider_config")
    # The failing rule never became a finding.
    findings = [r.payload for r in store.list_audit(record.review_id) if r.event == "finding"]
    assert "late_penalty_cap" not in {f["rule_id"] for f in findings}


def test_post_review_rate_limit_not_config_class_still_200(
    client: TestClient, settings: Settings
) -> None:
    _install_model(settings, "acme", {"late_penalty_cap": [_groq_error(groq.RateLimitError, 429)]})
    body = _post_ok(client, ACME_LAWYER)
    store = Store(settings.data_dir, "acme")
    _assert_no_model_error(store, body["review_id"], allowed=("RateLimitError",))
    limited = {f["rule_id"]: f for f in body["findings"]}["late_penalty_cap"]
    assert limited["no_finding_reason"] == "rate_limited"


def test_post_review_tool_body_error_failed_500(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = ClauseIndex.search

    def exploding(self: ClauseIndex, query: str, limit: int = MAX_RESULTS) -> list[SearchHit]:
        if query == "patlat":
            raise RuntimeError("beklenmeyen kod hatası: Ayşe Kaya")
        return original(self, query, limit)

    monkeypatch.setattr(ClauseIndex, "search", exploding)
    _install_model(
        settings, "acme", {"liability_cap": [ai(call("search_contract", query="patlat")), done()]}
    )
    response = client.post("/reviews", json={"text": _sample()}, headers=ACME_LAWYER)
    assert response.status_code == 500
    assert response.json() == {"detail": MSG_INTERNAL}
    store = Store(settings.data_dir, "acme")
    record = _only_review(store)
    _assert_failed(store, record.review_id, "internal_error")
    findings = [r.payload for r in store.list_audit(record.review_id) if r.event == "finding"]
    assert "liability_cap" not in {f["rule_id"] for f in findings}
    assert all(f["no_finding_reason"] is None for f in findings)


def test_post_review_failure_logs_type_not_message(
    client: TestClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    original = ClauseIndex.search

    def exploding(self: ClauseIndex, query: str, limit: int = MAX_RESULTS) -> list[SearchHit]:
        if query == "patlat":
            raise RuntimeError("sözleşme metni: Ayşe Kaya")
        return original(self, query, limit)

    monkeypatch.setattr(ClauseIndex, "search", exploding)
    _install_model(
        settings, "acme", {"liability_cap": [ai(call("search_contract", query="patlat")), done()]}
    )
    with caplog.at_level(logging.DEBUG, logger="src.api"):
        response = client.post("/reviews", json={"text": _sample()}, headers=ACME_LAWYER)
    assert response.status_code == 500
    api_logs = "\n".join(r.getMessage() for r in caplog.records if r.name == "src.api")
    assert "RuntimeError" in api_logs
    assert "Ayşe" not in api_logs
    assert "Ayşe" not in response.text


def test_post_review_preparation_failure_failed_500(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_split(text: str) -> list[Any]:
        raise RuntimeError("bölme hatası")

    monkeypatch.setattr(prepare_module, "split_clauses", failing_split)
    _install_model(settings, "acme")
    response = client.post("/reviews", json={"text": _sample()}, headers=ACME_LAWYER)
    assert response.status_code == 500
    assert response.json() == {"detail": MSG_INTERNAL}
    store = Store(settings.data_dir, "acme")
    record = _only_review(store)
    _assert_failed(store, record.review_id, "prepare_error")
    assert "model_call" not in _events(store, record.review_id)


def test_post_review_rules_failure_before_preparation_leaves_no_row(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_rules(self: Store) -> list[Any]:
        raise RulesError("acme tenant'ının rules.yaml dosyası geçersiz")

    monkeypatch.setattr(Store, "load_rules", failing_rules)
    response = client.post("/reviews", json={"text": _sample()}, headers=ACME_LAWYER)
    assert response.status_code == 500
    store = Store(settings.data_dir, "acme")
    assert _review_ids(store) == []
    assert _audit_count(store) == 0


def test_post_review_awaiting_lawyer_write_atomic_failed_500(
    client: TestClient, settings: Settings
) -> None:
    _install_model(settings, "acme")
    store = Store(settings.data_dir, "acme")
    _fail_audit_event(store, "awaiting_lawyer")
    response = client.post("/reviews", json={"text": _sample()}, headers=ACME_LAWYER)
    assert response.status_code == 500
    record = _only_review(store)
    _assert_no_model_error(store, record.review_id)
    # Not left running, not awaiting_lawyer: failed, without the findings.
    _assert_failed(store, record.review_id, "internal_error")
    assert "verification" in _events(store, record.review_id)
    assert not store._conn.in_transaction


class _StubGraph:
    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self._result = result
        self._error = error

    def invoke(self, state: Any, config: Any) -> Any:
        if self._error is not None:
            raise self._error
        return self._result


def test_post_review_graph_returns_without_interrupt_failed_500(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubGraph(result={"findings": [], "verified": []})
    monkeypatch.setattr(api_module, "get_review_graph", lambda settings, tenant_id: stub)
    response = client.post("/reviews", json={"text": _sample()}, headers=ACME_LAWYER)
    assert response.status_code == 500
    store = Store(settings.data_dir, "acme")
    _assert_failed(store, _only_review(store).review_id, "internal_error")


class _Abort(BaseException):
    pass


def test_post_review_base_exception_marks_failed_then_propagates(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubGraph(error=_Abort())
    monkeypatch.setattr(api_module, "get_review_graph", lambda settings, tenant_id: stub)
    with pytest.raises(_Abort):
        client.post("/reviews", json={"text": _sample()}, headers=ACME_LAWYER)
    store = Store(settings.data_dir, "acme")
    _assert_failed(store, _only_review(store).review_id, "internal_error")


def test_post_review_cleanup_failure_does_not_hide_original_error(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_mark(self: Store, review_id: str) -> bool:
        raise sqlite3.OperationalError("disk dolu")

    monkeypatch.setattr(Store, "mark_failed", failing_mark)
    stub = _StubGraph(error=_groq_error(groq.AuthenticationError, 401))
    monkeypatch.setattr(api_module, "get_review_graph", lambda settings, tenant_id: stub)
    response = client.post("/reviews", json={"text": _sample()}, headers=ACME_LAWYER)
    # The provider config error is still what the caller sees; review_failed is still written.
    assert response.status_code == 502
    store = Store(settings.data_dir, "acme")
    record = _only_review(store)
    rows = store.list_audit(record.review_id)
    assert rows[-1].event == "review_failed"
    assert rows[-1].payload == {"reason": "provider_config"}
    assert get_index("acme", record.review_id) is None


# --- Tenant isolation -----------------------------------------------------------------------------


def test_tenant_isolation_other_tenant_review_and_audit_not_found(
    client: TestClient, settings: Settings
) -> None:
    _install_model(settings, "acme")
    review_id = _post_ok(client, ACME_LAWYER)["review_id"]
    acme = Store(settings.data_dir, "acme")
    audit_before = acme.list_audit(review_id)

    for headers in (GLOBEX_LAWYER, GLOBEX_VIEWER):
        assert client.get(f"/reviews/{review_id}", headers=headers).status_code == 404
        assert client.get(f"/reviews/{review_id}/audit", headers=headers).status_code == 404
    globex = Store(settings.data_dir, "globex")
    assert globex.get_review(review_id) is None
    assert globex.list_audit(review_id) == []
    assert globex.get_pii_map(review_id) == {}
    assert _review_ids(globex) == []
    assert acme.list_audit(review_id) == audit_before


def test_unknown_review_id_not_found(client: TestClient) -> None:
    missing = str(uuid.uuid4())
    for headers in (ACME_LAWYER, ACME_VIEWER):
        assert client.get(f"/reviews/{missing}", headers=headers).status_code == 404
        assert client.get(f"/reviews/{missing}/audit", headers=headers).status_code == 404


def test_tenant_parameter_in_query_and_body_ignored(client: TestClient, settings: Settings) -> None:
    _install_model(settings, "acme", runs=2)
    _install_model(settings, "globex")
    globex_id = _post_ok(client, GLOBEX_LAWYER)["review_id"]
    params = {"tenant": "globex", "tenant_id": "globex", "role": "lawyer"}

    # Another tenant's review: still 404, no globex data.
    for path in (f"/reviews/{globex_id}", f"/reviews/{globex_id}/audit"):
        response = client.get(path, params=params, headers=ACME_VIEWER)
        assert response.status_code == 404
        assert response.json() == {"detail": "İnceleme bulunamadı."}

    # Body and query fields are ignored on POST: the review lands in the key's own tenant.
    response = client.post(
        "/reviews",
        params=params,
        json={"text": _sample(), "tenant": "globex", "tenant_id": "globex", "role": "viewer"},
        headers=ACME_LAWYER,
    )
    assert response.status_code == 200
    acme_id = response.json()["review_id"]
    assert Store(settings.data_dir, "acme").get_review(acme_id) is not None
    assert Store(settings.data_dir, "globex").get_review(acme_id) is None

    # The same parameters on the caller's own review change nothing.
    plain = client.get(f"/reviews/{acme_id}", headers=ACME_VIEWER).json()
    with_params = client.get(f"/reviews/{acme_id}", params=params, headers=ACME_VIEWER)
    assert with_params.status_code == 200
    assert with_params.json() == plain
    assert "original_text" not in with_params.json()
    audit = client.get(f"/reviews/{acme_id}/audit", params=params, headers=ACME_VIEWER)
    assert audit.status_code == 200


# --- Role-based presentation ----------------------------------------------------------------------


def _all_text(body: Any) -> str:
    return json.dumps(body, ensure_ascii=False)


def test_role_based_presentation_review_and_audit(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_model(
        settings,
        "acme",
        {
            "termination_notice": _termination_compliant(),
            "liability_cap": _liability_with_proposal(),
        },
    )
    posted = _post_ok(client, ACME_LAWYER)
    review_id = posted["review_id"]
    store = Store(settings.data_dir, "acme")
    _assert_no_model_error(store, review_id)
    mapping = store.get_pii_map(review_id)
    assert {"[KISI_1]", "[KISI_2]"} <= set(mapping)
    audit_count = _audit_count(store)

    pii_reads: list[str] = []
    original_get_pii_map = Store.get_pii_map

    def recording(self: Store, rid: str) -> dict[str, str]:
        pii_reads.append(rid)
        return original_get_pii_map(self, rid)

    monkeypatch.setattr(Store, "get_pii_map", recording)

    viewer = client.get(f"/reviews/{review_id}", headers=ACME_VIEWER)
    assert viewer.status_code == 200
    assert pii_reads == []  # the PII map is never read for a viewer
    viewer_body = viewer.json()
    viewer_text = _all_text(viewer_body)
    assert "original_text" not in viewer_body
    for value in ALL_RAW:
        assert value not in viewer_text, f"{value!r} reached the viewer"
    assert "[KISI_1]" in viewer_text
    # The suspicious phrase holds a name: masked for the viewer.
    pattern_phrases = [
        f["phrase"] for f in viewer_body["injection_flags"] if f["kind"] == "pattern"
    ]
    assert any("[KISI_" in p for p in pattern_phrases)
    viewer_findings = {f["rule_id"]: f for f in viewer_body["findings"]}
    assert viewer_findings["termination_notice"]["reasoning"] == (
        "[KISI_1] ile [KISI_2] arasında 45 gün."
    )

    lawyer = client.get(f"/reviews/{review_id}", headers=ACME_LAWYER)
    assert lawyer.status_code == 200
    assert pii_reads == [review_id]
    lawyer_body = lawyer.json()
    assert lawyer_body == posted
    assert lawyer_body["original_text"] == _sample()
    lawyer_text = _all_text({k: v for k, v in lawyer_body.items() if k != "original_text"})
    # Every text field of the lawyer response is unmasked: no placeholder is left anywhere.
    assert PLACEHOLDER.search(lawyer_text) is None, PLACEHOLDER.search(lawyer_text)
    assert mapping["[KISI_1]"] in lawyer_text and mapping["[KISI_2]"] in lawyer_text
    findings = {f["rule_id"]: f for f in lawyer_body["findings"]}
    notice = findings["termination_notice"]
    assert (
        notice["reasoning"] == f"{mapping['[KISI_1]']} ile {mapping['[KISI_2]']} arasında 45 gün."
    )
    assert notice["escalation_reason"] == f"{mapping['[KISI_2]']} ile görüşülmeli."
    liability = findings["liability_cap"]
    assert liability["quote"] == f"Alıcıya yapılacak bildirimler {mapping['[KISI_1]']}"
    assert liability["proposal"]["proposed_text"] == (
        f"Bildirimler {mapping['[KISI_2]']} adına yapılır."
    )
    assert liability["proposal"]["rationale"] == f"{mapping['[KISI_1]']} onayı gerekir."
    lawyer_phrases = [f["phrase"] for f in lawyer_body["injection_flags"] if f["kind"] == "pattern"]
    assert any("Ayşe Kaya" in p for p in lawyer_phrases)

    # The audit log is returned as stored, masked, for both roles; reading it writes nothing.
    stored = [r.model_dump(mode="json") for r in store.list_audit(review_id)]
    for headers in (ACME_VIEWER, ACME_LAWYER):
        audit = client.get(f"/reviews/{review_id}/audit", headers=headers)
        assert audit.status_code == 200
        assert audit.json() == stored
        audit_text = _all_text(audit.json())
        for value in ALL_RAW:
            assert value not in audit_text, f"{value!r} in the audit response"
    assert pii_reads == [review_id]
    assert _audit_count(store) == audit_count


def test_response_fixed_field_lists_per_role(client: TestClient, settings: Settings) -> None:
    _install_model(settings, "acme", {"liability_cap": _liability_with_proposal()})
    review_id = _post_ok(client, ACME_LAWYER)["review_id"]

    viewer = client.get(f"/reviews/{review_id}", headers=ACME_VIEWER).json()
    assert set(viewer) == REVIEW_KEYS
    assert viewer["clauses"] and viewer["injection_flags"] and viewer["findings"]
    # Before the decision every decision field is null, for both roles.
    assert all(viewer[k] is None for k in DECISION_REVIEW_KEYS)
    assert all(f[k] is None for f in viewer["findings"] for k in DECISION_FINDING_KEYS)
    assert all(set(c) == CLAUSE_KEYS for c in viewer["clauses"])
    assert all(set(f) == FLAG_KEYS for f in viewer["injection_flags"])
    assert all(set(f) == FINDING_KEYS for f in viewer["findings"])
    proposals = [f["proposal"] for f in viewer["findings"] if f["proposal"] is not None]
    assert proposals and all(set(p) == PROPOSAL_KEYS for p in proposals)

    lawyer = client.get(f"/reviews/{review_id}", headers=ACME_LAWYER).json()
    assert set(lawyer) == LAWYER_REVIEW_KEYS
    assert all(lawyer[k] is None for k in DECISION_REVIEW_KEYS)
    assert all(f[k] is None for f in lawyer["findings"] for k in DECISION_FINDING_KEYS)
    assert all(set(c) == LAWYER_CLAUSE_KEYS for c in lawyer["clauses"])
    assert all(set(f) == LAWYER_FLAG_KEYS for f in lawyer["injection_flags"])
    assert all(set(f) == FINDING_KEYS for f in lawyer["findings"])
    proposals = [f["proposal"] for f in lawyer["findings"] if f["proposal"] is not None]
    assert proposals and all(set(p) == PROPOSAL_KEYS for p in proposals)


class _ClauseWithExtra(Clause):
    secret_note: str = "GIZLI-MADDE"


class _FlagWithExtra(InjectionFlag):
    secret_note: str = "GIZLI-ISARET"


class _FindingWithExtra(Finding):
    secret_note: str = "GIZLI-BULGU"


class _RecordWithExtra(ReviewRecord):
    secret_note: str = "GIZLI-KAYIT"


def test_field_added_to_record_or_finding_not_in_any_response(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_model(settings, "acme")
    review_id = _post_ok(client, ACME_LAWYER)["review_id"]
    original = Store.get_review

    def extended(self: Store, rid: str) -> ReviewRecord | None:
        record = original(self, rid)
        if record is None:
            return None
        data = record.model_dump()
        data["clauses"] = [_ClauseWithExtra(**c) for c in data["clauses"]]
        data["injection_flags"] = [_FlagWithExtra(**f) for f in data["injection_flags"]]
        data["findings"] = [_FindingWithExtra(**f) for f in data["findings"]]
        return _RecordWithExtra(**data)

    monkeypatch.setattr(Store, "get_review", extended)
    armed = Store(settings.data_dir, "acme").get_review(review_id)
    assert armed is not None
    dumped = armed.model_dump_json(serialize_as_any=True)
    for marker in ("GIZLI-KAYIT", "GIZLI-MADDE", "GIZLI-ISARET", "GIZLI-BULGU"):
        assert marker in dumped  # the trap is armed: a dump of the record would leak it
    for headers in (ACME_VIEWER, ACME_LAWYER):
        response = client.get(f"/reviews/{review_id}", headers=headers)
        assert response.status_code == 200
        assert "secret_note" not in response.text
        assert "GIZLI" not in response.text


# --- Suspicious phrase is not deleted -------------------------------------------------------------


def test_suspicious_phrase_kept_lawyer_sees_flag_phrase_and_positions(
    client: TestClient, settings: Settings
) -> None:
    _install_model(settings, "acme")
    body = _post_ok(client, ACME_LAWYER)
    text = _sample()
    assert body["original_text"] == text
    record = Store(settings.data_dir, "acme").get_review(body["review_id"])
    assert record is not None
    assert record.original_text.encode("utf-8") == FIXTURE.read_bytes()

    flags = body["injection_flags"]
    pattern = [f for f in flags if f["kind"] == "pattern"]
    hidden = [f for f in flags if f["kind"] == "hidden_characters"]
    assert pattern and hidden
    assert any(f["phrase"].startswith(SUSPICIOUS_SENTENCE_START) for f in pattern)
    for flag in flags:
        assert 0 <= flag["start"] < flag["end"] <= len(text)
        assert flag["clause_id"]
    # The positions point at the flagged text in original_text, not just somewhere inside it.
    for flag in hidden:
        span = text[flag["start"] : flag["end"]]
        assert span and strip_invisible(span) == ""
        assert flag["detail"] == ", ".join(
            f"U+{ord(ch):04X} x{span.count(ch)}" for ch in dict.fromkeys(span)
        )
    for flag in pattern:
        span = strip_invisible(text[flag["start"] : flag["end"]])
        assert span.strip() and span in flag["phrase"]
    clause_ids = {c["clause_id"] for c in body["clauses"]}
    assert {f["clause_id"] for f in flags} <= clause_ids
    # The flagged clause still holds the visible suspicious sentence for the lawyer.
    flagged = next(c for c in body["clauses"] if c["clause_id"] == pattern[0]["clause_id"])
    assert SUSPICIOUS_SENTENCE_START in flagged["text"]
    assert text[flagged["start"] : flagged["end"]].startswith("## Madde 13")


# --- Lawyer decision ------------------------------------------------------------------------------
#
# The sample contract flags every finding with injection_suspected, so nothing would pass without
# the lawyer. These tests use a short, unflagged contract instead (c1 parties, c2 termination, c3
# governing law) where the scripts below decide which findings are queued:
#   termination_notice: non_compliant (15 days) with a passing draft  -> outside the queue, draft
#   governing_law:      compliant                                     -> outside the queue
#   the other three:    not_found                                     -> queued (unverified)

KNOWN_NAME = "Mehmet Demir"
NEW_TCKN = "10000000146"
CLEAN_TEXT = (
    "# TEDARİK SÖZLEŞMESİ\n\n"
    f"Bu sözleşme Alıcı A.Ş. adına temsilen Genel Müdür {KNOWN_NAME} ile Tedarikçi Ltd. Şti. "
    "arasında imzalanmıştır.\n\n"
    "## Madde 1 - Fesih\n\n"
    "Taraflar sözleşmeyi 15 gün önceden yazılı bildirimle feshedebilir.\n\n"
    "## Madde 2 - Uygulanacak Hukuk\n\n"
    "Bu sözleşmeye Türk hukuku uygulanır ve İstanbul mahkemeleri yetkilidir.\n"
)
DECISION_EVENTS = ("finding_decision", "proposal_decision", "contract_decision")
MSG_DECISION_FAILED = "Karar kaydedilemedi: beklenmeyen bir hata oluştu."


def _termination_draft(proposed_days: int = 30) -> list[ScriptItem]:
    # 30 days passes the rule's check (outside the queue); 20 fails it (proposal_noncompliant).
    return [
        ai(call("search_contract", query="fesih bildirim")),
        ai(
            call(
                "submit_finding",
                status="non_compliant",
                clause_id="c2",
                quote="15 gün önceden",
                value=15,
                unit="gün",
                reasoning="Süre kısa.",
            )
        ),
        ai(
            call(
                "propose_change",
                clause_id="c2",
                proposed_text=f"Taraflar sözleşmeyi {proposed_days} gün önceden yazılı "
                "bildirimle feshedebilir.",
                rationale="Kurala uyum.",
            )
        ),
        done(),
    ]


def _governing_law_compliant() -> list[ScriptItem]:
    return [
        ai(
            call(
                "submit_finding",
                status="compliant",
                clause_id="c3",
                quote="Türk hukuku uygulanır ve İstanbul mahkemeleri yetkilidir",
                reasoning="Uygun.",
            )
        ),
        done(),
    ]


def _clean_scripts(proposed_days: int = 30) -> dict[str, list[ScriptItem]]:
    return {
        "termination_notice": _termination_draft(proposed_days),
        "governing_law": _governing_law_compliant(),
    }


def _post_clean(
    client: TestClient, settings: Settings, headers: Mapping[str, str] = ACME_LAWYER
) -> dict[str, Any]:
    response = client.post("/reviews", json={"text": CLEAN_TEXT}, headers=dict(headers))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    tenant = settings.api_keys[headers["X-API-Key"]].tenant_id
    _assert_no_model_error(Store(settings.data_dir, tenant), body["review_id"])
    return body


def _clean_review(
    client: TestClient, settings: Settings, proposed_days: int = 30, runs: int = 1
) -> dict[str, Any]:
    _install_model(settings, "acme", _clean_scripts(proposed_days), runs=runs)
    return _post_clean(client, settings)


def _queue_decision(review: Mapping[str, Any], contract: str = "approve") -> dict[str, Any]:
    """A decision with exactly what is required: every queued finding accepted, and its draft."""
    entries: list[dict[str, Any]] = []
    for finding in review["findings"]:
        if finding["needs_review"]:
            entry = {"rule_id": finding["rule_id"], "action": "accept"}
            if finding["proposal"] is not None:
                entry["proposal_action"] = "accept"
            entries.append(entry)
    return {"findings": entries, "contract_decision": contract}


def _with_entry(body: Mapping[str, Any], entry: Mapping[str, Any]) -> dict[str, Any]:
    return {**body, "findings": [*body["findings"], dict(entry)]}


def _decide(
    client: TestClient,
    review_id: str,
    body: Any,
    headers: Mapping[str, str] = ACME_LAWYER,
) -> Any:
    return client.post(f"/reviews/{review_id}/decision", json=body, headers=dict(headers))


def _snapshot(settings: Settings, tenant: str, review_id: str) -> tuple[Any, ...]:
    """What a rejected decision must leave unchanged: status, audit rows, PII map, graph."""
    store = Store(settings.data_dir, tenant)
    record = store.get_review(review_id)
    assert record is not None
    graph = get_review_graph(settings, tenant)
    next_nodes = graph.get_state(review_config(settings, review_id)).next
    return (
        record.status,
        record.decision,
        len(store.list_audit(review_id)),
        store.get_pii_map(review_id),
        next_nodes,
    )


def _decision_rows(store: Store, review_id: str) -> list[Any]:
    return [r for r in store.list_audit(review_id) if r.event in DECISION_EVENTS]


def _outcomes(store: Store, review_id: str) -> dict[str, Any]:
    record = store.get_review(review_id)
    assert record is not None and record.decision is not None
    return {o.rule_id: o for o in record.decision.findings}


def test_clean_contract_queues_only_the_not_found_rules(
    client: TestClient, settings: Settings
) -> None:
    # The premise of the decision tests below.
    review = _clean_review(client, settings)
    assert review["injection_flags"] == []
    findings = {f["rule_id"]: f for f in review["findings"]}
    termination = findings["termination_notice"]
    assert termination["status"] == "non_compliant"
    assert termination["needs_review"] is False
    assert termination["proposal"]["code_check"] == "passed"
    assert findings["governing_law"]["status"] == "compliant"
    assert findings["governing_law"]["needs_review"] is False
    queued = {r for r, f in findings.items() if f["needs_review"]}
    assert queued == {"late_penalty_cap", "insurance_minimum", "liability_cap"}


# Roles and identity


def test_decision_viewer_forbidden_then_same_body_lawyer_ok(
    client: TestClient, settings: Settings
) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    body = _queue_decision(review)
    before = _snapshot(settings, "acme", review_id)
    assert before[0] == "awaiting_lawyer" and before[4] == ("human_review",)

    response = _decide(client, review_id, body, ACME_VIEWER)
    assert response.status_code == 403
    assert _snapshot(settings, "acme", review_id) == before
    store = Store(settings.data_dir, "acme")
    assert _decision_rows(store, review_id) == []

    ok = _decide(client, review_id, body)
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "approved"


def test_decision_role_and_name_in_body_ignored(client: TestClient, settings: Settings) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    body = _queue_decision(review)
    before = _snapshot(settings, "acme", review_id)

    forged = {**body, "role": "lawyer"}
    assert _decide(client, review_id, forged, ACME_VIEWER).status_code == 403
    assert _snapshot(settings, "acme", review_id) == before

    impostor = {
        **body,
        "reviewer": "başkası",
        "decided_by": "başkası",
        "role": "viewer",
        "tenant": "globex",
    }
    response = _decide(client, review_id, impostor)
    assert response.status_code == 200, response.text
    assert response.json()["decided_by"] == "Av. Acme"
    store = Store(settings.data_dir, "acme")
    record = store.get_review(review_id)
    assert record is not None and record.decided_by == "Av. Acme"
    lawyer_rows = [r for r in store.list_audit(review_id) if r.actor.startswith("lawyer:")]
    assert lawyer_rows and {r.actor for r in lawyer_rows} == {"lawyer:Av. Acme"}
    assert "başkası" not in _all_text(
        [r.model_dump(mode="json") for r in store.list_audit(review_id)]
    )


# Required fields


def test_decision_queued_finding_without_action_unprocessable(
    client: TestClient, settings: Settings
) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    body = _queue_decision(review)
    body["findings"] = body["findings"][1:]
    before = _snapshot(settings, "acme", review_id)
    response = _decide(client, review_id, body)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert [d["type"] for d in detail] == ["missing_action"]
    assert _snapshot(settings, "acme", review_id) == before


def test_decision_queued_draft_without_proposal_action_unprocessable(
    client: TestClient, settings: Settings
) -> None:
    review = _clean_review(client, settings, proposed_days=20)
    findings = {f["rule_id"]: f for f in review["findings"]}
    assert findings["termination_notice"]["needs_review"] is True  # the failing draft queued it
    review_id = review["review_id"]
    body = _queue_decision(review)
    for entry in body["findings"]:
        entry.pop("proposal_action", None)
    before = _snapshot(settings, "acme", review_id)
    response = _decide(client, review_id, body)
    assert response.status_code == 422
    assert [d["type"] for d in response.json()["detail"]] == ["missing_proposal_action"]
    assert _snapshot(settings, "acme", review_id) == before

    body = _queue_decision(review)
    assert _decide(client, review_id, body).status_code == 200


def test_decision_findings_outside_queue_not_sent_ok_and_stored_untouched(
    client: TestClient, settings: Settings
) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    response = _decide(client, review_id, _queue_decision(review))
    assert response.status_code == 200, response.text
    store = Store(settings.data_dir, "acme")
    outcomes = _outcomes(store, review_id)
    termination = outcomes["termination_notice"]
    # The draft outside the queue got no decision: stored as null, not as accepted.
    assert termination.proposal_action is None
    assert termination.action is None
    assert termination.final_status == "non_compliant"
    assert outcomes["governing_law"].final_status == "compliant"
    assert {
        r.payload["rule_id"] for r in _decision_rows(store, review_id) if "rule_id" in r.payload
    } == {
        "late_penalty_cap",
        "insurance_minimum",
        "liability_cap",
    }
    body = {f["rule_id"]: f for f in response.json()["findings"]}
    assert body["termination_notice"]["proposal_action"] is None


def test_decision_outside_queue_correct_changes_stored_final_status(
    client: TestClient, settings: Settings
) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    body = _with_entry(
        _queue_decision(review),
        {"rule_id": "governing_law", "action": "correct", "status": "non_compliant"},
    )
    response = _decide(client, review_id, body)
    assert response.status_code == 200, response.text
    store = Store(settings.data_dir, "acme")
    governing = _outcomes(store, review_id)["governing_law"]
    assert governing.final_status == "non_compliant"
    assert governing.action == "correct"
    # The model's finding itself is kept as it was.
    record = store.get_review(review_id)
    assert record is not None
    assert {f.rule_id: f.status for f in record.findings}["governing_law"] == "compliant"
    shown = {f["rule_id"]: f for f in response.json()["findings"]}["governing_law"]
    assert shown["status"] == "compliant" and shown["final_status"] == "non_compliant"
    [row] = [
        r for r in _decision_rows(store, review_id) if r.payload.get("rule_id") == "governing_law"
    ]
    assert row.event == "finding_decision"
    assert row.payload == {
        "rule_id": "governing_law",
        "action": "correct",
        "status": "non_compliant",
        "final_status": "non_compliant",
    }


def test_decision_outside_queue_draft_with_proposal_action_only_ok(
    client: TestClient, settings: Settings
) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    body = _with_entry(
        _queue_decision(review), {"rule_id": "termination_notice", "proposal_action": "reject"}
    )
    response = _decide(client, review_id, body)
    assert response.status_code == 200, response.text
    store = Store(settings.data_dir, "acme")
    termination = _outcomes(store, review_id)["termination_notice"]
    assert termination.final_status == "non_compliant"  # the model's
    assert termination.action is None
    assert termination.proposal_action == "reject"
    rows = [
        r for r in store.list_audit(review_id) if r.payload.get("rule_id") == "termination_notice"
    ]
    assert [(r.event, r.payload) for r in rows if r.event in DECISION_EVENTS] == [
        (
            "proposal_decision",
            {"rule_id": "termination_notice", "proposal_action": "reject", "edited_text": None},
        )
    ]


# Invalid values


def _set(rule_id: str, **fields: Any) -> Callable[[dict[str, Any]], None]:
    def change(body: dict[str, Any]) -> None:
        for entry in body["findings"]:
            if entry["rule_id"] == rule_id:
                entry.clear()
                entry.update({"rule_id": rule_id, **fields})
                return
        body["findings"].append({"rule_id": rule_id, **fields})

    return change


def _top(**fields: Any) -> Callable[[dict[str, Any]], None]:
    def change(body: dict[str, Any]) -> None:
        body.update(fields)

    return change


def _duplicate(body: dict[str, Any]) -> None:
    body["findings"].append(dict(body["findings"][0]))


INVALID_BODIES: dict[str, Callable[[dict[str, Any]], None]] = {
    "action-reject": _set("liability_cap", action="reject"),
    "correct-without-status": _set("liability_cap", action="correct"),
    "accept-with-status": _set("liability_cap", action="accept", status="compliant"),
    "neither-action-nor-proposal-action": _set("liability_cap"),
    "edit-empty-text": _set("termination_notice", proposal_action="edit", edited_text=""),
    "edit-blank-text": _set("termination_notice", proposal_action="edit", edited_text="  \n\t"),
    "edit-without-text": _set("termination_notice", proposal_action="edit"),
    "proposal-action-without-draft": _set("governing_law", proposal_action="accept"),
    "unknown-rule-id": _set("ghost_rule", action="accept"),
    "duplicate-rule-id": _duplicate,
    "contract-decision-reject": _top(contract_decision="reject"),
    "contract-decision-missing": lambda body: body.pop("contract_decision"),
    "accept-with-edited-text": _set(
        "termination_notice", proposal_action="accept", edited_text="metin"
    ),
    "reject-with-edited-text": _set(
        "termination_notice", proposal_action="reject", edited_text="metin"
    ),
    "edited-text-over-limit": _set(
        "termination_notice", proposal_action="edit", edited_text="a" * 20_001
    ),
    "edit-only-zero-width": _set(
        "termination_notice", proposal_action="edit", edited_text="\u200b"
    ),
    "edit-zero-width-and-space": _set(
        "termination_notice", proposal_action="edit", edited_text="\u200b \u200b\n\u200d"
    ),
    "unknown-field-in-entry": _set("liability_cap", action="accept", proposal_actoin="accept"),
    "findings-not-a-list": _top(findings={"rule_id": "liability_cap"}),
}


@pytest.mark.parametrize("change", INVALID_BODIES.values(), ids=INVALID_BODIES.keys())
def test_decision_invalid_body_unprocessable_nothing_changes(
    client: TestClient, settings: Settings, change: Callable[[dict[str, Any]], None]
) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    body = _queue_decision(review)
    change(body)
    before = _snapshot(settings, "acme", review_id)
    response = _decide(client, review_id, body)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail and all(set(error) == {"type", "loc", "msg"} for error in detail)
    assert all(error["loc"][0] == "body" for error in detail)
    assert _snapshot(settings, "acme", review_id) == before
    assert _decision_rows(Store(settings.data_dir, "acme"), review_id) == []


def test_decision_edited_text_at_limit_accepted(client: TestClient, settings: Settings) -> None:
    review = _clean_review(client, settings)
    body = _with_entry(
        _queue_decision(review),
        {"rule_id": "termination_notice", "proposal_action": "edit", "edited_text": "a" * 20_000},
    )
    assert _decide(client, review["review_id"], body).status_code == 200


def test_decision_unprocessable_body_echoes_no_input(
    client: TestClient, settings: Settings
) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    secret = "GIZLI-DUZENLEME-METNI"
    # A schema error (edited_text with accept) and a record-check error (unknown rule id).
    schema_error = _with_entry(
        _queue_decision(review),
        {"rule_id": "termination_notice", "proposal_action": "accept", "edited_text": secret},
    )
    record_error = _with_entry(
        _with_entry(
            _queue_decision(review),
            {"rule_id": "termination_notice", "proposal_action": "edit", "edited_text": secret},
        ),
        {"rule_id": "GIZLI-KURAL", "action": "accept"},
    )
    for body in (schema_error, record_error):
        response = _decide(client, review_id, body)
        assert response.status_code == 422
        assert secret not in response.text
        assert "GIZLI" not in response.text
        detail = response.json()["detail"]
        assert all(set(error) == {"type", "loc", "msg"} for error in detail)
    [unknown] = _decide(client, review_id, record_error).json()["detail"]
    assert unknown["type"] == "unknown_rule_id"
    assert unknown["loc"] == ["body", "findings", 4, "rule_id"]


def test_decision_unknown_rule_id_rejected_before_masking(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    masked: list[str] = []
    original_mask = api_module.mask

    def spying(text: str, mapping: Mapping[str, str]) -> Any:
        masked.append(text)
        return original_mask(text, mapping)

    monkeypatch.setattr(api_module, "mask", spying)
    body = _with_entry(
        _with_entry(
            _queue_decision(review),
            {
                "rule_id": "termination_notice",
                "proposal_action": "edit",
                "edited_text": f"Yeni TCKN {NEW_TCKN}.",
            },
        ),
        {"rule_id": "ghost_rule", "action": "accept"},
    )
    before = _snapshot(settings, "acme", review_id)
    assert _decide(client, review_id, body).status_code == 422
    assert masked == []
    assert _snapshot(settings, "acme", review_id) == before
    assert NEW_TCKN not in Store(settings.data_dir, "acme").get_pii_map(review_id).values()


# Final decision


def test_decision_request_changes_changes_requested(client: TestClient, settings: Settings) -> None:
    review = _clean_review(client, settings)
    response = _decide(client, review["review_id"], _queue_decision(review, "request_changes"))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "changes_requested"
    assert response.json()["contract_decision"] == "request_changes"
    record = Store(settings.data_dir, "acme").get_review(review["review_id"])
    assert record is not None and record.status == "changes_requested"


def test_decision_approve_with_non_compliant_finding_approved(
    client: TestClient, settings: Settings
) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    response = _decide(client, review_id, _queue_decision(review, "approve"))
    assert response.status_code == 200, response.text
    store = Store(settings.data_dir, "acme")
    record = store.get_review(review_id)
    assert record is not None and record.status == "approved"
    # The finding that passed without the lawyer is still non_compliant in the final result.
    assert _outcomes(store, review_id)["termination_notice"].final_status == "non_compliant"
    assert [r.payload for r in store.list_audit(review_id) if r.event == "contract_decision"] == [
        {"contract_decision": "approve", "status": "approved"}
    ]


def test_decision_response_same_as_get_and_fixed_fields(
    client: TestClient, settings: Settings
) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    response = _decide(client, review_id, _queue_decision(review))
    assert response.status_code == 200
    decided = response.json()
    assert client.get(f"/reviews/{review_id}", headers=ACME_LAWYER).json() == decided
    assert set(decided) == LAWYER_REVIEW_KEYS
    assert decided["decided_by"] == "Av. Acme" and decided["decided_at"]
    assert all(set(f) == FINDING_KEYS for f in decided["findings"])
    assert all(f["final_status"] is not None for f in decided["findings"])
    viewer = client.get(f"/reviews/{review_id}", headers=ACME_VIEWER).json()
    assert set(viewer) == REVIEW_KEYS
    assert all(set(f) == FINDING_KEYS for f in viewer["findings"])
    assert viewer["contract_decision"] == "approve" and viewer["decided_by"] == "Av. Acme"
    assert "provider" not in decided and "model" not in decided


# Edited text is masked


def test_decision_edited_text_masked_everywhere_but_lawyer_response(
    client: TestClient, settings: Settings
) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    store = Store(settings.data_dir, "acme")
    mapping_before = store.get_pii_map(review_id)
    assert mapping_before == {"[KISI_1]": KNOWN_NAME}
    edited = f"Bildirimler {KNOWN_NAME} adına 30 gün önceden yapılır; TCKN {NEW_TCKN}."
    body = _with_entry(
        _queue_decision(review),
        {"rule_id": "termination_notice", "proposal_action": "edit", "edited_text": edited},
    )
    response = _decide(client, review_id, body)
    assert response.status_code == 200, response.text
    raw_values = (KNOWN_NAME, NEW_TCKN)

    # The known name kept its placeholder; the new TCKN was added to the map.
    mapping = store.get_pii_map(review_id)
    assert mapping["[KISI_1]"] == KNOWN_NAME
    [tckn] = [p for p, v in mapping.items() if v == NEW_TCKN]
    assert set(mapping) == {"[KISI_1]", tckn}
    expected = f"Bildirimler [KISI_1] adına 30 gün önceden yapılır; TCKN {tckn}."

    # The record, the graph state and the audit log hold the masked text only.
    termination = _outcomes(store, review_id)["termination_notice"]
    assert termination.edited_text == expected
    record = store.get_review(review_id)
    assert record is not None and record.decision is not None
    stored = record.decision.model_dump_json()
    values = get_review_graph(settings, "acme").get_state(review_config(settings, review_id)).values
    state_text = json.dumps(values, ensure_ascii=False)
    assert expected in state_text
    audit_text = _all_text([r.model_dump(mode="json") for r in store.list_audit(review_id)])
    [row] = [r for r in store.list_audit(review_id) if r.event == "proposal_decision"]
    assert row.payload["edited_text"] == expected
    for value in raw_values:
        assert value not in stored
        assert value not in state_text
        assert value not in audit_text

    # The lawyer response is unmasked, the viewer response masked.
    shown = {f["rule_id"]: f for f in response.json()["findings"]}["termination_notice"]
    assert shown["edited_text"] == edited
    viewer = client.get(f"/reviews/{review_id}", headers=ACME_VIEWER).json()
    viewer_edit = {f["rule_id"]: f for f in viewer["findings"]}["termination_notice"]
    assert viewer_edit["edited_text"] == expected

    # And so does the checkpoint file, byte for byte.
    close_graphs()  # closing the last connection folds the WAL into the database file
    path = store.tenant_dir / CHECKPOINTS_NAME
    raw = b"".join(
        p.read_bytes() for p in (path, path.with_name(f"{CHECKPOINTS_NAME}-wal")) if p.exists()
    )
    assert tckn.encode() in raw  # the masked decision is in the checkpoint
    for value in raw_values:
        assert value.encode("utf-8") not in raw, f"{value!r} is in checkpoints.db"


# The audit log is complete


def test_decision_full_review_audit_log_complete_ends_with_review_closed(
    client: TestClient, settings: Settings
) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    body = _with_entry(
        _queue_decision(review, "request_changes"),
        {"rule_id": "termination_notice", "action": "accept", "proposal_action": "accept"},
    )
    assert _decide(client, review_id, body).status_code == 200
    store = Store(settings.data_dir, "acme")
    rows = store.list_audit(review_id)
    events = [r.event for r in rows]
    for event in (
        "review_started",
        "clauses_split",
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
    ):
        assert event in events, event
    tail = events[events.index("awaiting_lawyer") + 1 :]
    # finding_decision for the three queued rules and termination, in rule order.
    assert tail == [
        "finding_decision",
        "proposal_decision",
        "finding_decision",
        "finding_decision",
        "finding_decision",
        "contract_decision",
        "report_created",
        "review_closed",
    ]
    assert rows[-1].event == "review_closed" and rows[-1].actor == "system"
    assert rows[-1].payload == {"status": "changes_requested"}
    assert rows[-2].payload == {
        "final_status_counts": {"compliant": 1, "non_compliant": 1, "not_found": 3}
    }


# Rejected requests leave no trace; the check order


def test_decision_second_decision_conflict_and_other_tenant_not_found(
    client: TestClient, settings: Settings
) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    body = _queue_decision(review)
    assert _decide(client, review_id, body).status_code == 200
    closed = _snapshot(settings, "acme", review_id)
    assert closed[0] == "approved" and closed[4] == ()

    second = _decide(client, review_id, _queue_decision(review, "request_changes"))
    assert second.status_code == 409
    assert _snapshot(settings, "acme", review_id) == closed

    for headers in (GLOBEX_LAWYER,):
        assert _decide(client, review_id, body, headers).status_code == 404
    assert _decide(client, review_id, body, GLOBEX_VIEWER).status_code == 403
    assert _snapshot(settings, "acme", review_id) == closed
    globex = Store(settings.data_dir, "globex")
    assert _review_ids(globex) == [] and _audit_count(globex) == 0


def test_decision_other_tenant_awaiting_review_not_found_nothing_changes(
    client: TestClient, settings: Settings
) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    before = _snapshot(settings, "acme", review_id)
    assert _decide(client, review_id, _queue_decision(review), GLOBEX_LAWYER).status_code == 404
    assert _snapshot(settings, "acme", review_id) == before


def test_decision_check_order_role_then_review_then_status_before_body(
    client: TestClient, settings: Settings
) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    invalid = {"findings": [{"rule_id": "", "action": "reject"}], "contract_decision": "reject"}
    assert _decide(client, review_id, invalid, ACME_VIEWER).status_code == 403
    assert _decide(client, review_id, "not even an object", ACME_VIEWER).status_code == 403
    assert _decide(client, str(uuid.uuid4()), invalid).status_code == 404
    assert _decide(client, "not-a-uuid", invalid).status_code == 404
    assert _decide(client, review_id, invalid, GLOBEX_LAWYER).status_code == 404
    assert _decide(client, review_id, _queue_decision(review)).status_code == 200
    assert _decide(client, review_id, invalid).status_code == 409
    assert _decide(client, review_id, invalid, {"X-API-Key": "yanlis"}).status_code == 401


# Failures and concurrency


def test_decision_finalize_failure_stays_awaiting_then_new_decision_recorded(
    client: TestClient, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    store = Store(settings.data_dir, "acme")
    rows_before = store.list_audit(review_id)
    _fail_audit_event(store, "review_closed")
    first = _with_entry(
        _queue_decision(review, "approve"),
        {
            "rule_id": "termination_notice",
            "proposal_action": "edit",
            "edited_text": f"Eski metin, TCKN {NEW_TCKN}.",
        },
    )
    with caplog.at_level(logging.DEBUG, logger="src.api"):
        response = _decide(client, review_id, first)
    assert response.status_code == 500
    assert response.json() == {"detail": MSG_DECISION_FAILED}
    api_logs = "\n".join(r.getMessage() for r in caplog.records if r.name == "src.api")
    assert "IntegrityError" in api_logs
    assert NEW_TCKN not in api_logs and "Eski metin" not in api_logs
    record = store.get_review(review_id)
    assert record is not None and record.status == "awaiting_lawyer" and record.decision is None
    assert store.list_audit(review_id) == rows_before
    assert "review_failed" not in _events(store, review_id)
    assert not store._conn.in_transaction
    # The map entries written before the failure stay (append-only).
    assert NEW_TCKN in store.get_pii_map(review_id).values()

    store._conn.execute("DROP TRIGGER temp.fail_review_closed")
    second = _with_entry(
        _queue_decision(review, "request_changes"),
        {"rule_id": "termination_notice", "proposal_action": "reject"},
    )
    response = _decide(client, review_id, second)
    assert response.status_code == 200, response.text
    record = store.get_review(review_id)
    assert record is not None and record.status == "changes_requested"
    assert _outcomes(store, review_id)["termination_notice"].proposal_action == "reject"
    rows = store.list_audit(review_id)
    new_rows = rows[len(rows_before) :]
    assert [r.event for r in new_rows].count("contract_decision") == 1
    assert [r.event for r in new_rows].count("review_closed") == 1
    assert [r.payload for r in new_rows if r.event == "proposal_decision"] == [
        {"rule_id": "termination_notice", "proposal_action": "reject", "edited_text": None}
    ]
    assert [r.payload for r in new_rows if r.event == "contract_decision"] == [
        {"contract_decision": "request_changes", "status": "changes_requested"}
    ]
    assert len([r for r in new_rows if r.event == "finding_decision"]) == 3


def test_decision_complete_review_false_conflict_no_decision_or_audit_written(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The last guard of a multi-process deployment: the status changed after the re-read. No
    # decision or audit row is written, but the map entries added before the resume stay
    # (append-only) and the thread stays at finalize, as after a 500.
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    before = _snapshot(settings, "acme", review_id)
    assert NEW_TCKN not in before[3].values()
    monkeypatch.setattr(Store, "complete_review", lambda self, *args, **kwargs: False)
    body = _with_entry(
        _queue_decision(review),
        {
            "rule_id": "termination_notice",
            "proposal_action": "edit",
            "edited_text": f"Eski metin, TCKN {NEW_TCKN}.",
        },
    )
    response = _decide(client, review_id, body)
    assert response.status_code == 409
    after = _snapshot(settings, "acme", review_id)
    assert after[:3] == before[:3]
    assert after[0] == "awaiting_lawyer" and after[1] is None
    assert NEW_TCKN in after[3].values()
    assert {k: v for k, v in after[3].items() if k in before[3]} == before[3]
    assert after[4] == ("finalize",)


def test_decision_two_concurrent_decisions_one_ok_one_conflict(
    app: FastAPI, client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Event-ordered, not timed: the first request is held inside `check_decision` until the
    # second one has either been refused the tenant's lock (it is held) or, without a lock,
    # entered `check_decision` too. Only then is the first one released.
    review = _clean_review(client, settings)
    review_id = review["review_id"]
    first_inside = threading.Event()
    second_arrived = threading.Event()
    release = threading.Event()
    counter_lock = threading.Lock()
    calls = 0
    inside = 0
    peak = 0
    real_check = api_module.check_decision
    real_lock = api_module.decision_lock

    def held_check(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls, inside, peak
        with counter_lock:
            calls += 1
            call_no = calls
            inside += 1
            peak = max(peak, inside)
        try:
            if call_no == 1:
                first_inside.set()
                release.wait(WAIT_S)
            else:
                second_arrived.set()
            return real_check(*args, **kwargs)
        finally:
            with counter_lock:
                inside -= 1

    class ObservedLock:
        def __init__(self, lock: Any) -> None:
            self._lock = lock

        def __enter__(self) -> None:
            if not self._lock.acquire(blocking=False):
                second_arrived.set()  # the lock is held: this request now waits for it
                self._lock.acquire()

        def __exit__(self, *exc: object) -> None:
            self._lock.release()

    monkeypatch.setattr(api_module, "check_decision", held_check)
    monkeypatch.setattr(
        api_module,
        "decision_lock",
        lambda data_dir, tenant: ObservedLock(real_lock(data_dir, tenant)),
    )
    statuses: list[int] = []
    lock = threading.Lock()

    def send(contract: str) -> None:
        own = TestClient(app)
        response = _decide(own, review_id, _queue_decision(review, contract))
        with lock:
            statuses.append(response.status_code)

    threads = [threading.Thread(target=send, args=(c,)) for c in ("approve", "request_changes")]
    threads[0].start()
    assert first_inside.wait(WAIT_S)
    threads[1].start()
    assert second_arrived.wait(WAIT_S)
    release.set()
    for thread in threads:
        thread.join(WAIT_S * 3)
        assert not thread.is_alive()
    assert sorted(statuses) == [200, 409]
    assert peak == 1
    store = Store(settings.data_dir, "acme")
    events = _events(store, review_id)
    assert events.count("contract_decision") == 1
    assert events.count("review_closed") == 1
    assert events.count("finding_decision") == 3


def test_decision_lock_per_tenant_other_tenant_decides_while_held(
    client: TestClient, settings: Settings
) -> None:
    _install_model(settings, "globex")
    globex_review = _post_clean(client, settings, GLOBEX_LAWYER)
    acme_lock = api_module.decision_lock(settings.data_dir, "acme")
    assert api_module.decision_lock(settings.data_dir, "acme") is acme_lock
    assert api_module.decision_lock(settings.data_dir, "globex") is not acme_lock

    result: list[int] = []

    def decide_globex() -> None:
        response = _decide(
            client, globex_review["review_id"], _queue_decision(globex_review), GLOBEX_LAWYER
        )
        result.append(response.status_code)

    with acme_lock:
        thread = threading.Thread(target=decide_globex)
        thread.start()
        thread.join(WAIT_S)  # deadlock guard, not a timing assertion
        assert not thread.is_alive(), "globex waited for acme's decision lock"
    assert result == [200]
