"""Eval cache: completed contracts on disk, so a rate-limited run can go on with `--resume`.

docs/evals.md §6. One file per provider and model, `<results_dir>/.cache/<provider>_<model>.json`
(another dataset than the default one gets its own file, see `cache_path`; UTF-8, LF,
`ensure_ascii=False`), rewritten atomically after every completed contract. A record
holds the contract's `ContractRun`, the rule rows of both modes, the contract's content digest and
its completion time; no quote, draft, reasoning, raw finding, audit record or raw PII.

A record is reused only while its digests match today's: the run digest (the code that produced
it, see `run_fingerprint`) and the contract digest (`contract_fingerprint`). Digests are sha256
over file bytes: never the commit, the date, the working directory or a modification time.

The repository root, the results folder and the clock are parameters; this module reads no
environment and keeps no state.
"""

import contextlib
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from evals.metrics import (
    ContractRun,
    Mode,
    RuleRow,
    contract_run_from_json,
    contract_run_to_json,
    rule_row_from_json,
    rule_row_to_json,
    safe_model_name,
)
from src.config import DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE

# 2: rule rows carry `proposal_calls` and `proposal_errors`; a version 1 file is a corrupt cache.
CACHE_VERSION = 2
CACHE_DIR_NAME = ".cache"
# Files besides `src/` whose content decides the rows (repo-relative, posix).
FINGERPRINT_FILES: tuple[str, ...] = (
    "evals/cache.py",
    "evals/metrics.py",
    "evals/run.py",
    "uv.lock",
)
_MODES: tuple[Mode, ...] = ("before", "after")
_TOP_KEYS = {"version", "provider", "model", "run_fingerprint", "contracts"}
_ENTRY_KEYS = {"fingerprint", "completed_at", "run", "rows"}


class CacheError(RuntimeError):
    """The cache could not be used. Messages carry paths and reasons, never contract text."""


class FingerprintError(CacheError):
    """A file the run digest needs is missing."""


class CorruptCacheError(CacheError):
    """The cache file exists but is not a cache this code wrote."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


@dataclass(frozen=True)
class CachedContract:
    fingerprint: str
    completed_at: str  # UTC ISO, seconds
    run: ContractRun
    rows: Mapping[Mode, list[RuleRow]]


@dataclass(frozen=True)
class CacheFile:
    provider: str
    model: str
    run_fingerprint: str
    contracts: Mapping[str, CachedContract]  # by contract id, in completion order


# --- Digests --------------------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def tracked_files(repo_root: Path) -> list[str]:
    """Repo-relative posix paths the run digest covers, sorted.

    Every file under `src/` except `__pycache__` folders and `src/static/`, plus
    `FINGERPRINT_FILES`; a missing one of those (or a missing `src/`) is a `FingerprintError`.
    """
    src = repo_root / "src"
    if not src.is_dir():
        raise FingerprintError(f"Önbellek özeti için src/ klasörü bulunamadı: {src}")
    paths: list[str] = []
    for path in src.rglob("*"):
        parts = path.relative_to(repo_root).parts
        if "__pycache__" in parts or parts[:2] == ("src", "static") or not path.is_file():
            continue
        paths.append("/".join(parts))
    for name in FINGERPRINT_FILES:
        if not (repo_root / name).is_file():
            raise FingerprintError(f"Önbellek özeti için gereken dosya yok: {name}")
        paths.append(name)
    return sorted(paths)


def run_fingerprint(
    repo_root: Path,
    *,
    provider: str,
    model: str,
    num_ctx: int,
    temperature: float | None = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    base_url: str | None = None,
) -> str:
    """sha256 of the tracked files' (path, content digest) pairs and the model settings.

    The settings are the effective values (`Settings`), so an unset variable and its default
    give the same digest; `temperature=None` (LLM_TEMPERATURE=none) differs from 0.
    """
    files = [[rel, _sha256((repo_root / rel).read_bytes())] for rel in tracked_files(repo_root)]
    return _sha256(
        _canonical(
            {
                "files": files,
                "provider": provider,
                "model": model,
                "num_ctx": num_ctx,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "base_url": base_url,
            }
        )
    )


def contract_fingerprint(contract: bytes, label_record: Mapping[str, Any], rules: bytes) -> str:
    """sha256 of the contract's bytes, its labels.json record (canonical JSON) and rules.yaml."""
    return _sha256(
        _canonical({"contract": _sha256(contract), "label": label_record, "rules": _sha256(rules)})
    )


def label_records(labels_bytes: bytes) -> dict[str, Mapping[str, Any]]:
    """labels.json records by id, as parsed (already validated by `parse_labels`)."""
    records: list[Mapping[str, Any]] = json.loads(labels_bytes.decode("utf-8"))
    return {str(record["id"]): record for record in records}


# --- The file -------------------------------------------------------------------------------------


def cache_path(results_dir: Path, provider: str, model: str, *, dataset: str | None = None) -> Path:
    """`<provider>_<model>.json`; for a dataset other than the default one (`dataset`, the
    result file's `dataset_dir` text) `<provider>_<model>_dataset-<sha256[:8]>.json`.

    A run rewrites its file with its own records only, so two datasets sharing one file would
    delete each other's records (docs/evals.md §6 "Önbellek veri kümesine göre ayrılır").
    """
    name = f"{provider}_{safe_model_name(model)}"
    if dataset is not None:
        name += f"_dataset-{_sha256(dataset.encode('utf-8'))[:8]}"
    return results_dir / CACHE_DIR_NAME / f"{name}.json"


def cache_to_json(cache: CacheFile) -> dict[str, Any]:
    return {
        "version": CACHE_VERSION,
        "provider": cache.provider,
        "model": cache.model,
        "run_fingerprint": cache.run_fingerprint,
        "contracts": {
            contract_id: {
                "fingerprint": entry.fingerprint,
                "completed_at": entry.completed_at,
                "run": contract_run_to_json(entry.run),
                "rows": {
                    mode: [rule_row_to_json(row) for row in entry.rows[mode]] for mode in _MODES
                },
            }
            for contract_id, entry in cache.contracts.items()
        },
    }


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{what} boş olmayan bir metin olmalı.")
    return value


def _rows(raw: object, contract_id: str) -> dict[Mode, list[RuleRow]]:
    if not isinstance(raw, dict) or set(raw) != set(_MODES):
        raise ValueError(f"{contract_id}: rows yalnızca before ve after içermeli.")
    rows: dict[Mode, list[RuleRow]] = {}
    for mode in _MODES:
        items = raw[mode]
        if not isinstance(items, list):
            raise ValueError(f"{contract_id}: rows.{mode} bir liste olmalı.")
        rows[mode] = [rule_row_from_json(item) for item in items]
        if any(row.contract_id != contract_id for row in rows[mode]):
            raise ValueError(f"{contract_id}: satır başka bir sözleşmeye ait.")
    return rows


def _entry(contract_id: str, raw: object) -> CachedContract:
    if not isinstance(raw, dict) or set(raw) != _ENTRY_KEYS:
        raise ValueError(f"{contract_id}: kayıt alanları beklenenle eşleşmiyor.")
    completed_at = _text(raw["completed_at"], "completed_at")
    stamp = datetime.fromisoformat(completed_at)  # ValueError when it is not ISO
    if stamp.utcoffset() != timedelta(0):
        raise ValueError(f"{contract_id}: completed_at UTC olmalı.")
    run = contract_run_from_json(raw["run"])
    if run.contract_id != contract_id:
        raise ValueError(f"{contract_id}: run başka bir sözleşmeye ait.")
    return CachedContract(
        fingerprint=_text(raw["fingerprint"], "fingerprint"),
        completed_at=completed_at,
        run=run,
        rows=_rows(raw["rows"], contract_id),
    )


def cache_from_json(raw: object) -> CacheFile:
    """Inverse of `cache_to_json`; `ValueError` on anything else."""
    if not isinstance(raw, dict):
        raise ValueError("Önbellek bir nesne değil.")
    if raw.get("version") != CACHE_VERSION:
        raise ValueError(f"tanınmayan önbellek sürümü: {raw.get('version')!r}")
    if set(raw) != _TOP_KEYS:
        raise ValueError("Önbelleğin üst düzey alanları beklenenle eşleşmiyor.")
    contracts = raw["contracts"]
    if not isinstance(contracts, dict):
        raise ValueError("contracts bir nesne olmalı.")
    return CacheFile(
        provider=_text(raw["provider"], "provider"),
        model=_text(raw["model"], "model"),
        run_fingerprint=_text(raw["run_fingerprint"], "run_fingerprint"),
        contracts={str(cid): _entry(str(cid), entry) for cid, entry in contracts.items()},
    )


def load_cache(path: Path) -> CacheFile | None:
    """The cache at `path`, None when there is none; `CorruptCacheError` when it is unusable."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CorruptCacheError(path, "JSON değil") from None
    try:
        return cache_from_json(raw)
    except ValueError as exc:
        raise CorruptCacheError(path, str(exc)) from None


def write_cache(path: Path, cache: CacheFile) -> None:
    """Write atomically: a temporary file in the same folder, then `os.replace`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(cache_to_json(cache), ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        replaced = True
    finally:
        if not replaced:
            Path(temp_name).unlink(missing_ok=True)


def delete_cache(path: Path) -> None:
    """Remove the cache file, and its folder when nothing else is in it."""
    path.unlink(missing_ok=True)
    # Not empty: another provider's / model's cache is still there.
    with contextlib.suppress(OSError):
        path.parent.rmdir()


def usable_entries(
    cache: CacheFile,
    *,
    provider: str,
    model: str,
    run_digest: str,
    contract_digests: Mapping[str, str],
) -> dict[str, CachedContract]:
    """The records whose provider, model, run digest and contract digest all match today's."""
    if (cache.provider, cache.model, cache.run_fingerprint) != (provider, model, run_digest):
        return {}
    return {
        contract_id: entry
        for contract_id, entry in cache.contracts.items()
        if contract_digests.get(contract_id) == entry.fingerprint
    }
