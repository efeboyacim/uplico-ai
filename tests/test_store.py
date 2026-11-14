import sqlite3
import threading
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.models import (
    AuditEvent,
    Check,
    Clause,
    ContractDecision,
    Finding,
    FindingOutcome,
    InjectionFlag,
    MaskedText,
    Proposal,
    ReviewOutcome,
    Rule,
)
from src.store import RulesError, Store, UnknownTenantError, close_all

SAMPLE_TEXT = (
    "Madde 1 \u2013 Taraflar\r\nİşbu sözleşme Ayşe Yılmaz ile ŞİRKET arasında\u200b yapılmıştır.\n"
    "Madde 2 \u2013 Fesih\n  Taraflar otuz (30) gün önceden bildirerek feshedebilir.\u200d\ufeff\n"
    "Çğıöşü ÇĞİÖŞÜ \u201ctırnak\u201d \u00a0 son.\n"
)


def _snapshot(root: Path) -> set[Path]:
    return set(root.rglob("*"))


def _clauses() -> list[Clause]:
    return [
        Clause(
            clause_id="c1",
            clause_no="1",
            text=MaskedText("Madde 1 [KISI_1] ile"),
            start=0,
            end=80,
        ),
        Clause(clause_id="c2", clause_no=None, text=MaskedText("imza"), start=80, end=90),
    ]


def _flags() -> list[InjectionFlag]:
    return [
        InjectionFlag(
            clause_id="c1",
            kind="hidden_characters",
            phrase="arasında yapılmıştır",
            start=40,
            end=62,
            detail="zero_width_space",
        )
    ]


def _findings() -> list[Finding]:
    return [
        Finding(
            rule_id="termination_notice",
            status="non_compliant",
            clause_id="c2",
            quote="otuz (30) gün",
            value=30,
            unit="gün",
            reasoning="Süre kısa.",
            proposal=Proposal(
                clause_id="c2",
                proposed_text="altmış (60) gün",
                rationale="Kurala uyum.",
                code_check="passed",
            ),
            needs_review=True,
            review_reasons=["unit_conversion_uncertain"],
        ),
        Finding(
            rule_id="governing_law",
            status="not_found",
            needs_review=True,
            review_reasons=["unverified_not_found"],
        ),
    ]


def _outcome(contract_decision: ContractDecision = "request_changes") -> ReviewOutcome:
    return ReviewOutcome(
        contract_decision=contract_decision,
        findings=[
            FindingOutcome(
                rule_id="termination_notice",
                severity="critical",
                final_status="non_compliant",
                action="accept",
                proposal_action="edit",
                edited_text=MaskedText("[KISI_1] altmış (60) gün; Türkçe açıklama"),
            ),
            FindingOutcome(rule_id="governing_law", severity="normal", final_status="not_found"),
        ],
    )


def _events(status: str = "changes_requested") -> list[tuple[str, AuditEvent, dict[str, object]]]:
    return [
        ("lawyer:Ayşe", "finding_decision", {"rule_id": "termination_notice", "action": "accept"}),
        ("lawyer:Ayşe", "contract_decision", {"status": status}),
        ("system", "report_created", {"final_status_counts": {"non_compliant": 1}}),
        ("system", "review_closed", {"status": status}),
    ]


def _awaiting(store: Store) -> str:
    review_id = store.create_review("metin", "groq", "m")
    assert store.save_findings(review_id, _findings()) is True
    return review_id


def _write_rules(data_dir: Path, content: str) -> None:
    (data_dir / "tenants" / "acme" / "rules.yaml").write_text(content, encoding="utf-8")


# Audit log


def test_audit_log_raw_update_and_delete_raise_and_row_unchanged(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    store.append_audit("r1", "system", "review_started", {"provider": "groq"})
    before = store.list_audit("r1")

    raw = sqlite3.connect(data_dir / "tenants" / "acme" / "app.db")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="uygulama üzerinden"):
            raw.execute("UPDATE audit_log SET actor = 'lawyer:x' WHERE review_id = 'r1'")
        raw.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="uygulama üzerinden"):
            raw.execute("DELETE FROM audit_log WHERE review_id = 'r1'")
        raw.rollback()
    finally:
        raw.close()

    assert store.list_audit("r1") == before
    assert len(before) == 1


@pytest.mark.parametrize("verb", ["INSERT OR REPLACE", "REPLACE"])
def test_audit_log_raw_replace_on_existing_id_raises_and_row_unchanged(
    data_dir: Path, verb: str
) -> None:
    store = Store(data_dir, "acme")
    store.append_audit("r1", "system", "review_started", {"provider": "groq"})
    before = store.list_audit("r1")

    raw = sqlite3.connect(data_dir / "tenants" / "acme" / "app.db")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="uygulama üzerinden"):
            raw.execute(
                f"{verb} INTO audit_log (id, ts, review_id, actor, event, payload) "
                "VALUES (?, 't', 'r1', 'evil', 'tool_call', '{}')",
                (before[0].id,),
            )
        raw.rollback()
    finally:
        raw.close()

    assert store.list_audit("r1") == before
    store.append_audit("r1", "system", "review_closed", {})
    assert len(store.list_audit("r1")) == 2


def test_write_failed_commit_rolls_back_and_next_write_succeeds(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    store.append_audit("r1", "system", "review_started", {})
    store._conn.execute("PRAGMA busy_timeout = 100")

    # A reader holding a SHARED lock makes COMMIT fail with "database is locked".
    reader = sqlite3.connect(data_dir / "tenants" / "acme" / "app.db", isolation_level=None)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM audit_log").fetchall()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.append_audit("r1", "system", "tool_call", {})
        assert store._conn.in_transaction is False
        assert len(store.list_audit("r1")) == 1
        reader.execute("COMMIT")
    finally:
        reader.close()

    store.append_audit("r1", "system", "review_closed", {})
    assert [r.event for r in store.list_audit("r1")] == ["review_started", "review_closed"]


def test_audit_append_and_list_returns_records_in_order(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    store.append_audit("r1", "system", "review_started", {"model": "m", "n": 1})
    store.append_audit("r1", "agent", "tool_call", {"name": "get_clause", "ç": "ğüşıöİ"})
    store.append_audit("r2", "system", "review_started", {})
    records = store.list_audit("r1")
    assert [r.event for r in records] == ["review_started", "tool_call"]
    assert records[0].id < records[1].id
    assert records[1].payload == {"name": "get_clause", "ç": "ğüşıöİ"}
    assert records[1].actor == "agent"
    assert records[0].ts.endswith("+00:00")


def test_audit_append_unknown_event_raises(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    with pytest.raises(ValueError):
        store.append_audit("r1", "system", "made_up", {})  # type: ignore[arg-type]
    assert store.list_audit("r1") == []


def test_audit_append_many_writes_one_row_per_payload_in_order(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    store.append_audit("r1", "system", "review_started", {})
    store.append_audit_many(
        "r1", "system", "injection_flagged", [{"n": 1, "ç": "ğüşıöİ"}, {"n": 2}, {"n": 3}]
    )
    records = store.list_audit("r1")
    assert [r.event for r in records] == ["review_started", *["injection_flagged"] * 3]
    assert [r.payload for r in records[1:]] == [{"n": 1, "ç": "ğüşıöİ"}, {"n": 2}, {"n": 3}]
    assert [r.id for r in records] == sorted({r.id for r in records})
    assert {r.actor for r in records} == {"system"}
    assert store.list_audit("r2") == []


def test_audit_append_many_empty_writes_nothing(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    store.append_audit_many("r1", "system", "injection_flagged", [])
    assert store.list_audit("r1") == []
    assert store._conn.in_transaction is False


def test_audit_append_many_unknown_event_raises(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    with pytest.raises(ValueError):
        store.append_audit_many("r1", "system", "made_up", [{}])  # type: ignore[arg-type]
    assert store.list_audit("r1") == []


def test_audit_append_many_failure_on_a_later_row_leaves_no_row(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    store.append_audit("r1", "system", "review_started", {})
    # RAISE(ABORT) undoes only the failing INSERT; the earlier rows of the batch go away only
    # because the transaction is rolled back.
    store._conn.execute(
        "CREATE TEMP TRIGGER fail_third_row BEFORE INSERT ON main.audit_log "
        "WHEN NEW.event = 'injection_flagged' AND (SELECT COUNT(*) FROM main.audit_log "
        "WHERE review_id = NEW.review_id AND event = 'injection_flagged') = 2 "
        "BEGIN SELECT RAISE(ABORT, 'ucuncu satir yazilamadi'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="ucuncu satir yazilamadi"):
        store.append_audit_many("r1", "system", "injection_flagged", [{"n": i} for i in range(5)])
    assert [r.event for r in store.list_audit("r1")] == ["review_started"]
    assert store._conn.in_transaction is False
    # The connection is still usable, and a batch that stays under the limit is written whole.
    store.append_audit_many("r2", "system", "injection_flagged", [{"n": 1}, {"n": 2}])
    assert len(store.list_audit("r2")) == 2


def test_audit_parallel_writes_from_eight_threads_keep_all_rows_in_thread_order(
    data_dir: Path,
) -> None:
    threads_count, per_thread = 8, 200
    barrier = threading.Barrier(threads_count)
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            store = Store(data_dir, "acme")
            barrier.wait()
            for seq in range(per_thread):
                store.append_audit("par", f"agent:{index}", "tool_call", {"t": index, "s": seq})
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    records = Store(data_dir, "acme").list_audit("par")
    assert len(records) == threads_count * per_thread
    for index in range(threads_count):
        seqs = [r.payload["s"] for r in records if r.payload["t"] == index]
        assert seqs == list(range(per_thread))


# Tenant validation and isolation


@pytest.mark.parametrize(
    "tenant_id",
    ["unknown", "../acme", "", "acme/", "ACME", "tenants/acme", "acme/../globex", "empty"],
)
def test_store_unknown_or_unsafe_tenant_raises_and_creates_nothing(
    data_dir: Path, tenant_id: str
) -> None:
    (data_dir / "tenants" / "empty").mkdir()
    before = _snapshot(data_dir)
    with pytest.raises(UnknownTenantError):
        Store(data_dir, tenant_id)
    assert _snapshot(data_dir) == before


def test_store_absolute_path_tenant_raises_and_creates_nothing(data_dir: Path) -> None:
    before = _snapshot(data_dir)
    with pytest.raises(UnknownTenantError):
        Store(data_dir, str((data_dir / "tenants" / "acme").resolve()))
    assert _snapshot(data_dir) == before


def test_store_missing_tenants_dir_raises_and_creates_nothing(tmp_path: Path) -> None:
    with pytest.raises(UnknownTenantError):
        Store(tmp_path, "acme")
    assert list(tmp_path.iterdir()) == []


def test_store_other_tenant_cannot_see_review_audit_or_pii(data_dir: Path) -> None:
    acme = Store(data_dir, "acme")
    globex = Store(data_dir, "globex")
    review_id = acme.create_review(SAMPLE_TEXT, "groq", "m")
    acme.append_audit(review_id, "system", "review_started", {"provider": "groq"})
    acme.add_pii_entries(review_id, {"[KISI_1]": "Ayşe Yılmaz"})

    assert globex.get_review(review_id) is None
    assert globex.list_audit(review_id) == []
    assert globex.get_pii_map(review_id) == {}
    assert globex.save_findings(review_id, _findings()) is False
    assert globex.mark_failed(review_id) is False

    record = acme.get_review(review_id)
    assert record is not None
    assert record.status == "running"
    assert len(acme.list_audit(review_id)) == 1
    assert acme.get_pii_map(review_id) == {"[KISI_1]": "Ayşe Yılmaz"}
    assert (data_dir / "tenants" / "acme" / "app.db").is_file()
    assert (data_dir / "tenants" / "globex" / "app.db").is_file()


# Shared connection


def test_store_same_data_dir_and_tenant_share_connection_and_lock(data_dir: Path) -> None:
    first = Store(data_dir, "acme")
    second = Store(data_dir / "tenants" / "..", "acme")
    assert first._conn is second._conn
    assert first._lock is second._lock


def test_store_different_tenant_or_data_dir_gets_different_connection(
    data_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    other_dir = tmp_path_factory.mktemp("other")
    (other_dir / "tenants" / "acme").mkdir(parents=True)
    (other_dir / "tenants" / "acme" / "rules.yaml").write_bytes(
        (data_dir / "tenants" / "acme" / "rules.yaml").read_bytes()
    )
    acme = Store(data_dir, "acme")
    assert Store(data_dir, "globex")._conn is not acme._conn
    assert Store(other_dir, "acme")._conn is not acme._conn


def test_store_concurrent_first_construction_creates_one_connection(data_dir: Path) -> None:
    count = 8
    barrier = threading.Barrier(count)
    stores: list[Store] = []
    stores_lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        store = Store(data_dir, "acme")
        with stores_lock:
            stores.append(store)

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(stores) == count
    assert len({id(s._conn) for s in stores}) == 1


def test_store_after_close_all_reopens_and_keeps_data(data_dir: Path) -> None:
    review_id = Store(data_dir, "acme").create_review("metin", "groq", "m")
    close_all()
    assert Store(data_dir, "acme").get_review(review_id) is not None


# Rules


def test_load_rules_real_files_load(data_dir: Path) -> None:
    acme = Store(data_dir, "acme").load_rules()
    globex = Store(data_dir, "globex").load_rules()
    assert [r.id for r in acme] == [
        "termination_notice",
        "late_penalty_cap",
        "insurance_minimum",
        "liability_cap",
        "governing_law",
    ]
    assert len(globex) == 5
    assert acme[0] == Rule(
        id="termination_notice",
        text="Fesih bildirim süresi en az 30 gün olmalıdır.",
        severity="critical",
        check=Check(op=">=", value=30, unit="gün"),
    )
    assert acme[2].check == Check(op=">=", value=1_000_000, unit="TL")
    assert globex[0].check is not None and globex[0].check.value == 60


def test_load_rules_missing_severity_defaults_to_normal(data_dir: Path) -> None:
    rules = {r.id: r for r in Store(data_dir, "acme").load_rules()}
    assert rules["late_penalty_cap"].severity == "normal"
    assert rules["governing_law"].severity == "normal"


def test_load_rules_missing_check_is_none(data_dir: Path) -> None:
    rules = {r.id: r for r in Store(data_dir, "acme").load_rules()}
    assert rules["liability_cap"].check is None
    assert rules["governing_law"].check is None


def test_load_rules_hour_unit_loads(data_dir: Path) -> None:
    _write_rules(data_dir, '- id: a\n  text: "x"\n  check: {op: "<=", value: 48, unit: saat}\n')
    (rule,) = Store(data_dir, "acme").load_rules()
    assert rule.check == Check(op="<=", value=48, unit="saat")


@pytest.mark.parametrize(
    ("content", "expected_in_message"),
    [
        ('- id: a\n  text: "x"\n  check: {op: "=>", value: 1, unit: gün}\n', "check.op"),
        ('- id: a\n  text: "x"\n  check: {op: ">=", value: 1, unit: dakika}\n', "check.unit"),
        ('- id: a\n  text: "x"\n  keywords: [fesih]\n', "keywords"),
        ('- id: a\n  text: "x"\n- id: a\n  text: "y"\n', "'a'"),
        ('- id: a\n  text: "x"\n  severity: high\n', "severity"),
        ('- id: ""\n  text: "x"\n', "id"),
        ('- id: a\n  text: "x"\n  check: {op: ">=", unit: gün}\n', "check.value"),
        ("id: a\ntext: x\n", "liste"),
        ("", "liste"),
        ("- [unclosed\n", "YAML"),
        ("- just a string\n", "1. kural"),
    ],
    ids=[
        "unknown_op",
        "unknown_unit",
        "unknown_field_keywords",
        "duplicate_id",
        "unknown_severity",
        "empty_id",
        "missing_check_value",
        "not_a_list",
        "empty_document",
        "invalid_yaml",
        "entry_not_mapping",
    ],
)
def test_load_rules_invalid_file_raises_rules_error(
    data_dir: Path, content: str, expected_in_message: str
) -> None:
    _write_rules(data_dir, content)
    with pytest.raises(RulesError) as info:
        Store(data_dir, "acme").load_rules()
    message = str(info.value)
    assert "acme" in message
    assert expected_in_message in message
    assert "\n" not in message


# Status transitions


def test_review_round_trip_persists_and_reads_back_equal_models(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    review_id = store.create_review(SAMPLE_TEXT, "groq", "openai/gpt-oss-120b")
    assert uuid.UUID(review_id).version == 4

    created = store.get_review(review_id)
    assert created is not None
    assert created.status == "running"
    assert created.clauses == [] and created.injection_flags == [] and created.findings == []
    assert created.decision is None and created.decided_at is None and created.decided_by is None

    assert store.save_prepared(review_id, _clauses(), _flags()) is True
    assert store.save_findings(review_id, _findings()) is True
    awaiting = store.get_review(review_id)
    assert awaiting is not None
    assert awaiting.status == "awaiting_lawyer"
    assert awaiting.findings == _findings()

    events_before = store.list_audit(review_id)
    assert (
        store.complete_review(review_id, "changes_requested", _outcome(), "Ayşe", _events()) is True
    )

    done = store.get_review(review_id)
    assert done is not None
    assert done.status == "changes_requested"
    assert done.clauses == _clauses()
    assert done.injection_flags == _flags()
    # The findings are not rewritten: the model's result stays as stored.
    assert done.findings == _findings()
    assert done.decision == _outcome()
    rows = store.list_audit(review_id)
    assert rows[: len(events_before)] == events_before
    assert [(r.actor, r.event, r.payload) for r in rows[len(events_before) :]] == _events()
    assert done.decided_by == "Ayşe"
    assert done.decided_at is not None and done.decided_at.endswith("+00:00")
    assert done.provider == "groq"
    assert done.model == "openai/gpt-oss-120b"
    assert done.original_text == SAMPLE_TEXT
    assert done.created_at == created.created_at


def test_review_original_text_stored_byte_for_byte(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    review_id = store.create_review(SAMPLE_TEXT, "ollama", "m")
    close_all()
    record = Store(data_dir, "acme").get_review(review_id)
    assert record is not None
    assert record.original_text.encode("utf-8") == SAMPLE_TEXT.encode("utf-8")
    assert "\u200b" in record.original_text and "\r\n" in record.original_text


def test_review_complete_twice_returns_false_and_changes_nothing(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    review_id = _awaiting(store)
    assert store.complete_review(
        review_id, "approved", _outcome("approve"), "Ayşe", _events("approved")
    )
    before = store.get_review(review_id)
    audit_before = store.list_audit(review_id)

    assert (
        store.complete_review(review_id, "changes_requested", _outcome(), "Can", _events()) is False
    )
    assert store.save_findings(review_id, []) is False
    assert store.save_prepared(review_id, [], []) is False
    assert store.mark_failed(review_id) is False
    assert store.get_review(review_id) == before
    # Not awaiting_lawyer: no event row of the second call was written.
    assert store.list_audit(review_id) == audit_before


def test_review_complete_while_running_returns_false_and_writes_no_row(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    review_id = store.create_review("metin", "groq", "m")
    assert store.complete_review(review_id, "approved", _outcome(), "Ayşe", _events()) is False
    record = store.get_review(review_id)
    assert record is not None and record.status == "running" and record.decided_at is None
    assert record.decision is None
    assert store.list_audit(review_id) == []


def test_review_complete_event_insert_failure_rolls_back_status(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    review_id = _awaiting(store)
    audit_before = store.list_audit(review_id)
    # TEMP: the trigger lives on the store's shared connection; the last event row fails.
    store._conn.execute(
        "CREATE TEMP TRIGGER fail_closed BEFORE INSERT ON main.audit_log "
        "WHEN NEW.event = 'review_closed' BEGIN SELECT RAISE(ABORT, 'yazilamadi'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="yazilamadi"):
        store.complete_review(review_id, "changes_requested", _outcome(), "Ayşe", _events())
    record = store.get_review(review_id)
    assert record is not None
    assert record.status == "awaiting_lawyer"
    assert record.decision is None and record.decided_at is None and record.decided_by is None
    # The rows before review_closed were rolled back with the status.
    assert store.list_audit(review_id) == audit_before
    assert not store._conn.in_transaction

    store._conn.execute("DROP TRIGGER temp.fail_closed")
    assert store.complete_review(review_id, "changes_requested", _outcome(), "Ayşe", _events())
    assert [r.event for r in store.list_audit(review_id)][len(audit_before) :] == [
        e for _, e, _ in _events()
    ]


def test_review_complete_unknown_event_raises_and_writes_nothing(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    review_id = _awaiting(store)
    audit_before = store.list_audit(review_id)
    bad = [("system", "not_an_event", {})]
    with pytest.raises(ValueError):
        store.complete_review(review_id, "approved", _outcome(), "Ayşe", bad)  # type: ignore[arg-type]
    record = store.get_review(review_id)
    assert record is not None and record.status == "awaiting_lawyer"
    assert store.list_audit(review_id) == audit_before


def test_review_save_findings_twice_returns_false_and_keeps_first(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    review_id = store.create_review("metin", "groq", "m")
    assert store.save_findings(review_id, _findings()) is True
    assert store.save_findings(review_id, []) is False
    record = store.get_review(review_id)
    assert record is not None and record.findings == _findings()


def test_review_save_findings_appends_awaiting_lawyer_row(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    review_id = store.create_review("metin", "groq", "m")
    findings = [*_findings(), Finding(rule_id="liability_cap", status="not_found")]
    assert store.save_findings(review_id, findings) is True
    [row] = store.list_audit(review_id)
    assert row.event == "awaiting_lawyer"
    assert row.actor == "system"
    assert row.payload == {"finding_count": 3, "needs_review_count": 2}


def test_review_save_findings_not_running_writes_no_audit_row(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    review_id = store.create_review("metin", "groq", "m")
    store.mark_failed(review_id)
    assert store.save_findings(review_id, _findings()) is False
    assert store.save_findings(str(uuid.uuid4()), _findings()) is False
    assert store.list_audit(review_id) == []
    awaiting = store.create_review("metin", "groq", "m")
    assert store.save_findings(awaiting, []) is True
    assert store.save_findings(awaiting, _findings()) is False
    assert [r.event for r in store.list_audit(awaiting)] == ["awaiting_lawyer"]


def test_review_save_findings_audit_failure_rolls_back_status_and_findings(
    data_dir: Path,
) -> None:
    store = Store(data_dir, "acme")
    review_id = store.create_review("metin", "groq", "m")
    # TEMP: the trigger lives on the store's shared connection, not in the database file.
    store._conn.execute(
        "CREATE TEMP TRIGGER fail_awaiting BEFORE INSERT ON main.audit_log "
        "WHEN NEW.event = 'awaiting_lawyer' BEGIN SELECT RAISE(ABORT, 'yazilamadi'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="yazilamadi"):
        store.save_findings(review_id, _findings())
    record = store.get_review(review_id)
    assert record is not None
    assert record.status == "running"
    assert record.findings == []
    assert store.list_audit(review_id) == []
    assert not store._conn.in_transaction


def test_review_complete_with_invalid_status_raises(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    review_id = store.create_review("metin", "groq", "m")
    store.save_findings(review_id, [])
    with pytest.raises(ValueError):
        store.complete_review(review_id, "failed", _outcome(), "Ayşe", [])  # type: ignore[arg-type]
    record = store.get_review(review_id)
    assert record is not None and record.status == "awaiting_lawyer"


def test_review_mark_failed_only_from_running(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    running = store.create_review("metin", "groq", "m")
    assert store.mark_failed(running) is True
    failed = store.get_review(running)
    assert failed is not None and failed.status == "failed"
    assert store.mark_failed(running) is False
    assert store.save_findings(running, []) is False

    awaiting = store.create_review("metin", "groq", "m")
    store.save_findings(awaiting, [])
    assert store.mark_failed(awaiting) is False
    record = store.get_review(awaiting)
    assert record is not None and record.status == "awaiting_lawyer"


def test_review_missing_id_returns_none_and_false(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    missing = str(uuid.uuid4())
    assert store.get_review(missing) is None
    assert store.save_prepared(missing, [], []) is False
    assert store.save_findings(missing, []) is False
    assert store.complete_review(missing, "approved", _outcome(), "Ayşe", _events()) is False
    assert store.list_audit(missing) == []
    assert store.mark_failed(missing) is False


# PII map


def test_pii_map_round_trip_returns_entries(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    entries = {"[KISI_1]": "Ayşe Yılmaz", "[TCKN_1]": "10000000146", "[EPOSTA_1]": "a@b.com"}
    store.add_pii_entries("r1", entries)
    store.add_pii_entries("r2", {"[KISI_1]": "Can Öztürk"})
    assert store.get_pii_map("r1") == entries
    assert store.get_pii_map("r2") == {"[KISI_1]": "Can Öztürk"}
    assert store.get_pii_map("r3") == {}


def test_pii_map_same_value_readded_is_noop(data_dir: Path) -> None:
    store = Store(data_dir, "acme")
    store.add_pii_entries("r1", {"[KISI_1]": "Ayşe Yılmaz"})
    store.add_pii_entries("r1", {"[KISI_1]": "Ayşe Yılmaz", "[KISI_2]": "Can Öztürk"})
    assert store.get_pii_map("r1") == {"[KISI_1]": "Ayşe Yılmaz", "[KISI_2]": "Can Öztürk"}


def test_pii_map_conflicting_value_raises_without_leaking_and_writes_nothing(
    data_dir: Path,
) -> None:
    store = Store(data_dir, "acme")
    store.add_pii_entries("r1", {"[KISI_1]": "Ayşe Yılmaz"})
    with pytest.raises(ValueError) as info:
        store.add_pii_entries("r1", {"[KISI_2]": "Can Öztürk", "[KISI_1]": "Mehmet Demir"})
    message = str(info.value)
    assert "Ayşe" not in message and "Mehmet" not in message and "Can" not in message
    assert store.get_pii_map("r1") == {"[KISI_1]": "Ayşe Yılmaz"}


# Finding validator


@pytest.mark.parametrize("status", ["compliant", "non_compliant"])
@pytest.mark.parametrize(
    ("clause_id", "quote"),
    [(None, "alıntı"), ("c1", None), ("", "alıntı"), ("c1", "  "), (None, None)],
)
def test_finding_with_evidence_status_missing_quote_or_clause_is_rejected(
    status: str, clause_id: str | None, quote: str | None
) -> None:
    with pytest.raises(ValidationError):
        Finding.model_validate(
            {"rule_id": "r", "status": status, "clause_id": clause_id, "quote": quote}
        )


def test_finding_not_found_without_evidence_is_valid() -> None:
    finding = Finding(rule_id="r", status="not_found")
    assert finding.clause_id is None and finding.quote is None
    assert finding.needs_review is False and finding.review_reasons == []


def test_finding_unknown_field_or_reason_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Finding.model_validate({"rule_id": "r", "status": "not_found", "approved": True})
    with pytest.raises(ValidationError):
        Finding.model_validate(
            {"rule_id": "r", "status": "not_found", "review_reasons": ["keyword_missing"]}
        )


def test_clause_end_before_start_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Clause(clause_id="c1", text=MaskedText("x"), start=5, end=4)
    with pytest.raises(ValidationError):
        Clause(clause_id="c1", text=MaskedText("x"), start=-1, end=4)
