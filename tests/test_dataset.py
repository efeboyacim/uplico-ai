import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from src.prepare.clauses import split_clauses
from src.prepare.injection import scan_injection
from src.prepare.pii import mask
from src.store import Store
from src.text import strip_invisible

DATASET = Path(__file__).resolve().parent.parent / "evals" / "dataset"

_STATUSES = {"compliant", "non_compliant", "not_found"}
_TENANTS = {
    "contract-01": "acme",
    "contract-02": "acme",
    "contract-03": "globex",
    "contract-04": "globex",
    "contract-05": "acme",
    "contract-06": "globex",
    "contract-07": "acme",
}


def _load() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = json.loads(
        (DATASET / "labels.json").read_bytes().decode("utf-8")
    )
    return records


RECORDS = _load()
IDS = [record["id"] for record in RECORDS]
BY_ID = {record["id"]: record for record in RECORDS}


def _text(record: dict[str, Any]) -> str:
    # Bytes, not read_text: no newline translation, so the test sees the file exactly.
    return (DATASET / record["contract_file"]).read_bytes().decode("utf-8")


@cache
def _masked_text(contract_id: str) -> str:
    """Masked clauses of a contract, produced the way `prepare_contract` does it."""
    text = _text(BY_ID[contract_id])
    _, mapping = mask(strip_invisible(text), {})
    parts: list[str] = []
    for raw in split_clauses(text):
        masked, mapping = mask(strip_invisible(raw.text), mapping)
        parts.append(masked)
    return "\n".join(parts)


@cache
def _masked_clauses(contract_id: str) -> dict[str | None, str]:
    """Masked text of each numbered clause, by clause number, masked as `_masked_text` does."""
    text = _text(BY_ID[contract_id])
    _, mapping = mask(strip_invisible(text), {})
    out: dict[str | None, str] = {}
    for raw in split_clauses(text):
        masked, mapping = mask(strip_invisible(raw.text), mapping)
        out[raw.clause_no] = masked
    return out


# The placeholders that make `verify` queue a compliant finding (masked_value_in_clause).
_NUMBER_PLACEHOLDER = re.compile(r"\[(?:TCKN|TELEFON)_[0-9]+\]")


def _rules(data_dir: Path, tenant_id: str) -> dict[str, str]:
    return {rule.id: rule.severity for rule in Store(data_dir, tenant_id).load_rules()}


def test_dataset_labels_seven_unique_contracts_cover_contracts_folder() -> None:
    assert len(RECORDS) == 7
    assert len(set(IDS)) == 7
    expected_ids = [f"contract-0{n}" for n in range(1, 8)]
    assert expected_ids == IDS
    for record in RECORDS:
        assert (DATASET / record["contract_file"]).is_file(), record["id"]
    labeled = {Path(record["contract_file"]).as_posix() for record in RECORDS}
    on_disk = {
        path.relative_to(DATASET).as_posix() for path in (DATASET / "contracts").glob("*.md")
    }
    assert on_disk == labeled


def test_dataset_tenant_distribution_matches_plan() -> None:
    assert {record["id"]: record["tenant_id"] for record in RECORDS} == _TENANTS


@pytest.mark.parametrize("contract_id", IDS)
def test_dataset_contract_text_has_no_carriage_return(contract_id: str) -> None:
    assert "\r" not in _text(BY_ID[contract_id])


@pytest.mark.parametrize("contract_id", IDS)
def test_dataset_expected_rule_ids_match_tenant_rules(contract_id: str, data_dir: Path) -> None:
    record = BY_ID[contract_id]
    expected = record["expected"]
    rule_ids = [entry["rule_id"] for entry in expected]
    assert len(rule_ids) == len(set(rule_ids))
    assert set(rule_ids) == set(_rules(data_dir, record["tenant_id"]))
    for entry in expected:
        assert entry["status"] in _STATUSES, (contract_id, entry)
        if entry["status"] == "not_found":
            assert entry["clause_no"] is None, (contract_id, entry)
        else:
            assert isinstance(entry["clause_no"], str), (contract_id, entry)


@pytest.mark.parametrize("contract_id", IDS)
def test_dataset_expected_clause_no_exists_in_split_clauses(contract_id: str) -> None:
    record = BY_ID[contract_id]
    clause_nos = {clause.clause_no for clause in split_clauses(_text(record))}
    for entry in record["expected"]:
        if entry["clause_no"] is not None:
            assert entry["clause_no"] in clause_nos, (contract_id, entry)


@pytest.mark.parametrize("contract_id", IDS)
def test_dataset_pii_values_present_in_raw_and_absent_after_masking(contract_id: str) -> None:
    record = BY_ID[contract_id]
    raw = _text(record)
    masked = _masked_text(contract_id)
    assert record["pii_values"]
    assert masked != raw
    for value in record["pii_values"]:
        assert value in raw, (contract_id, value)
        assert value not in masked, (contract_id, value)


@pytest.mark.parametrize("contract_id", IDS)
def test_dataset_pii_best_effort_present_in_raw_and_disjoint_from_pii_values(
    contract_id: str,
) -> None:
    record = BY_ID[contract_id]
    raw = _text(record)
    for value in record["pii_best_effort"]:
        assert value in raw, (contract_id, value)
    assert not set(record["pii_best_effort"]) & set(record["pii_values"])


def test_dataset_contract_05_has_best_effort_pii() -> None:
    assert BY_ID["contract-05"]["pii_best_effort"]


# Contract 5's `pii_best_effort`: two names (0: in the body, next to her e-mail; 1: an unlabelled
# signature line), masked since step 19c, and the bare mobile number, the spaced TCKN and the
# freely grouped IBAN (2-4), masked since step 17b. They stay best effort: no guarantee is claimed.
_CONTRACT_05_MASKED_BEST_EFFORT = {0, 1, 2, 3, 4}


def test_dataset_contract_05_best_effort_values_all_masked() -> None:
    best_effort = BY_ID["contract-05"]["pii_best_effort"]
    masked = _masked_text("contract-05")
    assert len(best_effort) == 5
    hidden = {i for i, value in enumerate(best_effort) if value not in masked}
    assert hidden == _CONTRACT_05_MASKED_BEST_EFFORT


@pytest.mark.parametrize("contract_id", IDS)
def test_dataset_compliant_expected_clause_has_no_tckn_or_phone_placeholder(
    contract_id: str,
) -> None:
    # TCKN and phone numbers stand only outside the rule clauses (parties, signatures); otherwise
    # masked_value_in_clause would queue a correct compliant finding in the eval.
    clauses = _masked_clauses(contract_id)
    for entry in BY_ID[contract_id]["expected"]:
        if entry["status"] != "compliant":
            continue
        masked = clauses[entry["clause_no"]]
        assert not _NUMBER_PLACEHOLDER.search(masked), (contract_id, entry["rule_id"], masked)


def test_dataset_tckn_or_phone_placeholders_exist_outside_compliant_clauses() -> None:
    # The test above is not vacuous: the dataset's TCKN / phone values do become placeholders.
    assert any(
        _NUMBER_PLACEHOLDER.search(masked)
        for contract_id in IDS
        for masked in _masked_clauses(contract_id).values()
    )


@pytest.mark.parametrize("contract_id", IDS)
def test_dataset_injection_flags_only_in_contract_06(contract_id: str) -> None:
    text = _text(BY_ID[contract_id])
    flags = scan_injection(text, split_clauses(text))
    kinds = {flag.kind for flag in flags}
    if contract_id == "contract-06":
        assert "pattern" in kinds
        assert "hidden_characters" in kinds
    else:
        assert flags == []


def test_dataset_contract_03_not_found_covers_critical_and_normal_rules(data_dir: Path) -> None:
    record = BY_ID["contract-03"]
    severities = _rules(data_dir, record["tenant_id"])
    missing = {e["rule_id"] for e in record["expected"] if e["status"] == "not_found"}
    assert any(severities[rule_id] == "critical" for rule_id in missing)
    assert any(severities[rule_id] != "critical" for rule_id in missing)
