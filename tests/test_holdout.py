"""Consistency of the held-out contracts and their labels (docs/plan.md "Adım 18b").

The two contracts and the table of expected findings were written by a session that never saw
this project; they are kept byte for byte. These tests check the labels against the table and
the text, never against what the masker or the agent does with them: that is what the single
held-out run measures.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from src.prepare.clauses import split_clauses
from src.store import Store

ROOT = Path(__file__).resolve().parent.parent
HOLDOUT = ROOT / "evals" / "holdout"

# sha256 of the files as they were handed over; a changed byte fails here.
_SHA256 = {
    "contracts/sozlesme_1_acme.md": (
        "ae41d08c00c870dd02df3dca03b9c69efd7b95a3b707e64cb13dee7114824d71"
    ),
    "contracts/sozlesme_2_globex.md": (
        "2ffb551b275f851adbe2d7d66ebe169d904af926ed3c035cbdff2091c1119ea6"
    ),
    "expected.md": "809c654ee54a2b10235fcced169d4679953cab9e639546bae9e73be28d6dcebd",
}
_STATUS = {"uygun": "compliant", "uygun değil": "non_compliant", "bulunamadı": "not_found"}
_ROW = re.compile(
    r"^\| `(?P<rule>\w+)` \| (?P<status>[^|]+) \| (?P<clause>[^|]+) \| (?P<quote>[^|]+) \|"
)


def _records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = json.loads(
        (HOLDOUT / "labels.json").read_bytes().decode("utf-8")
    )
    return records


RECORDS = _records()
IDS = [record["id"] for record in RECORDS]
BY_ID = {record["id"]: record for record in RECORDS}


def _text(record: dict[str, Any]) -> str:
    # Bytes, not read_text: no newline translation, so the test sees the file exactly.
    return (HOLDOUT / record["contract_file"]).read_bytes().decode("utf-8")


def _table() -> list[list[tuple[str, str, str | None, str | None]]]:
    """Rows of expected.md, one list per contract: (rule, status, clause number, quote)."""
    tables: list[list[tuple[str, str, str | None, str | None]]] = []
    for line in (HOLDOUT / "expected.md").read_bytes().decode("utf-8").splitlines():
        if line.startswith("### "):
            tables.append([])
            continue
        match = _ROW.match(line)
        if match is None:
            continue
        clause = match["clause"].strip()
        quote = match["quote"].strip()
        tables[-1].append(
            (
                match["rule"],
                _STATUS[match["status"].strip()],
                None if clause == "—" else clause,
                None if quote == "—" else quote.removeprefix('"').removesuffix('"'),
            )
        )
    return tables


@pytest.mark.parametrize("name", sorted(_SHA256))
def test_holdout_file_bytes_unchanged(name: str) -> None:
    data = (HOLDOUT / name).read_bytes()
    assert hashlib.sha256(data).hexdigest() == _SHA256[name]


def test_holdout_two_contracts_one_per_tenant_and_apart_from_the_dataset() -> None:
    assert [(r["id"], r["tenant_id"]) for r in RECORDS] == [
        ("holdout-01", "acme"),
        ("holdout-02", "globex"),
    ]
    files = {path.name for path in (HOLDOUT / "contracts").iterdir()}
    assert files == {Path(record["contract_file"]).name for record in RECORDS}
    dataset: list[dict[str, Any]] = json.loads(
        (ROOT / "evals" / "dataset" / "labels.json").read_bytes().decode("utf-8")
    )
    assert not {record["id"] for record in dataset} & set(IDS)


def test_holdout_labels_equal_the_expected_table_row_by_row() -> None:
    tables = _table()
    assert len(tables) == len(RECORDS)
    for record, rows in zip(RECORDS, tables, strict=True):
        from_table = [(rule, status, clause) for rule, status, clause, _ in rows]
        from_labels = [(e["rule_id"], e["status"], e["clause_no"]) for e in record["expected"]]
        assert from_labels == from_table, record["id"]


@pytest.mark.parametrize("contract_id", IDS)
def test_holdout_expected_rule_ids_match_tenant_rules(contract_id: str, data_dir: Path) -> None:
    record = BY_ID[contract_id]
    rule_ids = [entry["rule_id"] for entry in record["expected"]]
    tenant_rules = {rule.id for rule in Store(data_dir, record["tenant_id"]).load_rules()}
    assert len(rule_ids) == len(set(rule_ids))
    assert set(rule_ids) == tenant_rules
    for entry in record["expected"]:
        assert (entry["clause_no"] is None) == (entry["status"] == "not_found"), entry


@pytest.mark.parametrize("index", range(len(RECORDS)))
def test_holdout_table_quote_is_verbatim_in_the_expected_clause(index: int) -> None:
    record = RECORDS[index]
    clauses = {clause.clause_no: clause.text for clause in split_clauses(_text(record))}
    for rule, _, clause_no, quote in _table()[index]:
        if clause_no is None:
            assert quote is None, rule
            continue
        assert clause_no in clauses, (record["id"], rule)
        assert quote is not None and quote in clauses[clause_no], (record["id"], rule)


@pytest.mark.parametrize("contract_id", IDS)
def test_holdout_pii_lists_present_in_raw_text_and_disjoint(contract_id: str) -> None:
    record = BY_ID[contract_id]
    raw = _text(record)
    assert record["pii_values"] and record["pii_best_effort"]
    assert not set(record["pii_values"]) & set(record["pii_best_effort"])
    for value in (*record["pii_values"], *record["pii_best_effort"]):
        assert value in raw, (contract_id, value)
