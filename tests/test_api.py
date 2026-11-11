import json
import logging
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import groq
import pytest
from conftest import ScriptedChatModel, ScriptItem, ai, call, done
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from test_graph import _config_errors, _fail_audit_event, _groq_error
from test_prepare import ALL_RAW, FIXTURE, SUSPICIOUS_SENTENCE_START

import src.api as api_module
import src.prepare as prepare_module
from src.api import create_app
from src.config import ConfigError, Settings, load_settings
from src.graph import get_review_graph
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

REVIEW_KEYS = {"review_id", "status", "created_at", "clauses", "injection_flags", "findings"}
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
}
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
    assert all(set(c) == CLAUSE_KEYS for c in viewer["clauses"])
    assert all(set(f) == FLAG_KEYS for f in viewer["injection_flags"])
    assert all(set(f) == FINDING_KEYS for f in viewer["findings"])
    proposals = [f["proposal"] for f in viewer["findings"] if f["proposal"] is not None]
    assert proposals and all(set(p) == PROPOSAL_KEYS for p in proposals)

    lawyer = client.get(f"/reviews/{review_id}", headers=ACME_LAWYER).json()
    assert set(lawyer) == LAWYER_REVIEW_KEYS
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
