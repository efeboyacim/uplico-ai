"""Tenant-scoped SQLite store: reviews, append-only audit log, PII map and the tenant's rule set.

All database access goes through `Store(data_dir, tenant_id)`. One connection and one lock are
shared per `(data_dir, tenant_id)` inside the process; every read and write runs under that lock and
every write method call is its own transaction. No column or table name ever comes from an argument.
"""

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, get_args

import yaml
from pydantic import TypeAdapter, ValidationError

from src.models import (
    AuditEvent,
    AuditRecord,
    Clause,
    Finding,
    InjectionFlag,
    ReviewRecord,
    Rule,
)

DecidedStatus = Literal["approved", "changes_requested"]

_DB_NAME = "app.db"
_RULES_NAME = "rules.yaml"
_AUDIT_EVENTS: frozenset[str] = frozenset(get_args(AuditEvent))
_DECIDED_STATUSES: frozenset[str] = frozenset(get_args(DecidedStatus))

_CLAUSES = TypeAdapter(list[Clause])
_FLAGS = TypeAdapter(list[InjectionFlag])
_FINDINGS = TypeAdapter(list[Finding])

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    original_text TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    clauses TEXT NOT NULL,
    injection_flags TEXT NOT NULL,
    findings TEXT NOT NULL,
    decision TEXT NULL,
    decided_at TEXT NULL,
    decided_by TEXT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    review_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    event TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_log_review_id ON audit_log (review_id, id);
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log uygulama üzerinden değiştirilemez');
END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log uygulama üzerinden değiştirilemez');
END;
-- INSERT OR REPLACE / REPLACE INTO on an existing id deletes the old row without firing the
-- DELETE trigger (recursive_triggers is off), so an insert onto an existing id is rejected too.
CREATE TRIGGER IF NOT EXISTS audit_log_no_replace BEFORE INSERT ON audit_log
WHEN NEW.id IS NOT NULL AND EXISTS (SELECT 1 FROM audit_log WHERE id = NEW.id)
BEGIN
    SELECT RAISE(ABORT, 'audit_log uygulama üzerinden değiştirilemez');
END;
CREATE TABLE IF NOT EXISTS pii_map (
    review_id TEXT NOT NULL,
    placeholder TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (review_id, placeholder)
);
"""


class UnknownTenantError(Exception):
    pass


class RulesError(Exception):
    pass


@dataclass(frozen=True)
class _Shared:
    conn: sqlite3.Connection
    lock: threading.Lock


_registry: dict[tuple[Path, str], _Shared] = {}
_registry_lock = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _known_tenants(tenants_dir: Path) -> frozenset[str]:
    try:
        children = list(tenants_dir.iterdir())
    except OSError:
        return frozenset()
    return frozenset(
        child.name for child in children if child.is_dir() and (child / _RULES_NAME).is_file()
    )


def _open(db_path: Path) -> sqlite3.Connection:
    # isolation_level=None: no implicit transactions; writes open their own BEGIN IMMEDIATE.
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
    except BaseException:
        conn.close()
        raise
    return conn


def _shared_for(data_dir: Path, tenant_id: str, db_path: Path) -> _Shared:
    key = (data_dir, tenant_id)
    with _registry_lock:
        shared = _registry.get(key)
        if shared is None:
            shared = _Shared(conn=_open(db_path), lock=threading.Lock())
            _registry[key] = shared
        return shared


def close_all() -> None:
    """Close every shared connection and clear the registry.

    For tests: on Windows an open connection keeps the files under tmp_path locked.
    """
    with _registry_lock:
        for shared in _registry.values():
            with shared.lock:
                shared.conn.close()
        _registry.clear()


class Store:
    def __init__(self, data_dir: Path, tenant_id: str) -> None:
        resolved = Path(data_dir).resolve()
        tenants_dir = resolved / "tenants"
        known = _known_tenants(tenants_dir)
        if tenant_id not in known:
            raise UnknownTenantError("Bilinmeyen tenant.")
        # Only reached when tenant_id is exactly one of the existing tenant directory names.
        self.tenant_id = tenant_id
        self._tenant_dir = tenants_dir / tenant_id
        shared = _shared_for(resolved, tenant_id, self._tenant_dir / _DB_NAME)
        self._conn = shared.conn
        self._lock = shared.lock

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                # COMMIT stays inside the try: a failed COMMIT (locked, I/O, full disk) must roll
                # back too, or the shared connection is left inside an open transaction.
                self._conn.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    # Rules

    def load_rules(self) -> list[Rule]:
        path = self._tenant_dir / _RULES_NAME
        prefix = f"{self.tenant_id} tenant'ının {_RULES_NAME} dosyası geçersiz"
        try:
            raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            raise RulesError(f"{prefix}: dosya okunamadı.") from None
        except yaml.YAMLError:
            raise RulesError(f"{prefix}: YAML ayrıştırılamadı.") from None
        if not isinstance(raw, list) or not raw:
            raise RulesError(f"{prefix}: belge boş olmayan bir kural listesi olmalı.")
        rules: list[Rule] = []
        seen: set[str] = set()
        for position, entry in enumerate(raw, start=1):
            try:
                rule = Rule.model_validate(entry)
            except ValidationError as exc:
                error = exc.errors()[0]
                field = ".".join(str(part) for part in error["loc"]) or "kural"
                message = " ".join(str(error["msg"]).split())
                raise RulesError(f"{prefix}: {position}. kural, {field}: {message}") from None
            if rule.id in seen:
                raise RulesError(f"{prefix}: '{rule.id}' kimliği birden çok kez kullanılmış.")
            seen.add(rule.id)
            rules.append(rule)
        return rules

    # Reviews

    def create_review(self, original_text: str, provider: str, model: str) -> str:
        review_id = str(uuid.uuid4())
        with self._write() as conn:
            conn.execute(
                "INSERT INTO reviews (review_id, status, created_at, original_text, provider, "
                "model, clauses, injection_flags, findings) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (review_id, "running", _now(), original_text, provider, model, "[]", "[]", "[]"),
            )
        return review_id

    def get_review(self, review_id: str) -> ReviewRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT review_id, status, created_at, original_text, provider, model, clauses, "
                "injection_flags, findings, decision, decided_at, decided_by "
                "FROM reviews WHERE review_id = ?",
                (review_id,),
            ).fetchone()
        if row is None:
            return None
        return ReviewRecord(
            review_id=row["review_id"],
            status=row["status"],
            created_at=row["created_at"],
            original_text=row["original_text"],
            provider=row["provider"],
            model=row["model"],
            clauses=_CLAUSES.validate_json(row["clauses"]),
            injection_flags=_FLAGS.validate_json(row["injection_flags"]),
            findings=_FINDINGS.validate_json(row["findings"]),
            decision=None if row["decision"] is None else json.loads(row["decision"]),
            decided_at=row["decided_at"],
            decided_by=row["decided_by"],
        )

    def save_prepared(
        self,
        review_id: str,
        clauses: Sequence[Clause],
        injection_flags: Sequence[InjectionFlag],
    ) -> bool:
        clauses_json = _dumps([c.model_dump(mode="json") for c in clauses])
        flags_json = _dumps([f.model_dump(mode="json") for f in injection_flags])
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE reviews SET clauses = ?, injection_flags = ? "
                "WHERE review_id = ? AND status = ?",
                (clauses_json, flags_json, review_id, "running"),
            )
            return cursor.rowcount == 1

    def save_findings(self, review_id: str, findings: Sequence[Finding]) -> bool:
        findings_json = _dumps([f.model_dump(mode="json") for f in findings])
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE reviews SET findings = ?, status = ? WHERE review_id = ? AND status = ?",
                (findings_json, "awaiting_lawyer", review_id, "running"),
            )
            return cursor.rowcount == 1

    def complete_review(
        self,
        review_id: str,
        status: DecidedStatus,
        findings: Sequence[Finding],
        decision: Mapping[str, object],
        decided_by: str,
    ) -> bool:
        if status not in _DECIDED_STATUSES:
            raise ValueError("complete_review yalnızca approved ya da changes_requested alır.")
        findings_json = _dumps([f.model_dump(mode="json") for f in findings])
        decision_json = _dumps(dict(decision))
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE reviews SET status = ?, findings = ?, decision = ?, decided_at = ?, "
                "decided_by = ? WHERE review_id = ? AND status = ?",
                (
                    status,
                    findings_json,
                    decision_json,
                    _now(),
                    decided_by,
                    review_id,
                    "awaiting_lawyer",
                ),
            )
            return cursor.rowcount == 1

    def mark_failed(self, review_id: str) -> bool:
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE reviews SET status = ? WHERE review_id = ? AND status = ?",
                ("failed", review_id, "running"),
            )
            return cursor.rowcount == 1

    # Audit log (INSERT only; triggers reject UPDATE and DELETE)

    def append_audit(
        self, review_id: str, actor: str, event: AuditEvent, payload: Mapping[str, object]
    ) -> None:
        if event not in _AUDIT_EVENTS:
            raise ValueError("Bilinmeyen audit olayı.")
        payload_json = _dumps(dict(payload))
        with self._write() as conn:
            conn.execute(
                "INSERT INTO audit_log (ts, review_id, actor, event, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now(), review_id, actor, event, payload_json),
            )

    def append_audit_many(
        self,
        review_id: str,
        actor: str,
        event: AuditEvent,
        payloads: Sequence[Mapping[str, object]],
    ) -> None:
        """Append one row per payload in a single transaction: all of them or none.

        One commit instead of one per row, so the time does not grow with the commit count.
        """
        if event not in _AUDIT_EVENTS:
            raise ValueError("Bilinmeyen audit olayı.")
        payloads_json = [_dumps(dict(payload)) for payload in payloads]
        if not payloads_json:
            return
        with self._write() as conn:
            for payload_json in payloads_json:
                conn.execute(
                    "INSERT INTO audit_log (ts, review_id, actor, event, payload) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (_now(), review_id, actor, event, payload_json),
                )

    def list_audit(self, review_id: str) -> list[AuditRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, review_id, actor, event, payload FROM audit_log "
                "WHERE review_id = ? ORDER BY id",
                (review_id,),
            ).fetchall()
        return [
            AuditRecord(
                id=row["id"],
                ts=row["ts"],
                review_id=row["review_id"],
                actor=row["actor"],
                event=row["event"],
                payload=json.loads(row["payload"]),
            )
            for row in rows
        ]

    # PII map (never logged, never put into exception messages)

    def get_pii_map(self, review_id: str) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT placeholder, value FROM pii_map WHERE review_id = ?",
                (review_id,),
            ).fetchall()
        return {row["placeholder"]: row["value"] for row in rows}

    def add_pii_entries(self, review_id: str, entries: Mapping[str, str]) -> None:
        with self._write() as conn:
            for placeholder, value in entries.items():
                row = conn.execute(
                    "SELECT value FROM pii_map WHERE review_id = ? AND placeholder = ?",
                    (review_id, placeholder),
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO pii_map (review_id, placeholder, value) VALUES (?, ?, ?)",
                        (review_id, placeholder, value),
                    )
                elif row["value"] != value:
                    raise ValueError(
                        "PII eşlemesinde çakışma: aynı yer tutucu farklı bir değerle yazılamaz."
                    )
