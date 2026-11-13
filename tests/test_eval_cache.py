import hashlib
import json
import os
import unicodedata
from pathlib import Path

import pytest

from evals.cache import (
    CACHE_VERSION,
    FINGERPRINT_FILES,
    CachedContract,
    CacheFile,
    CorruptCacheError,
    FingerprintError,
    cache_path,
    contract_fingerprint,
    delete_cache,
    load_cache,
    run_fingerprint,
    tracked_files,
    usable_entries,
    write_cache,
)
from evals.metrics import ContractRun, RuleRow

REPO = Path(__file__).resolve().parent.parent
LABEL = {"id": "contract-01", "tenant_id": "acme", "tags": ["clean"], "expected": []}

# --- Helpers --------------------------------------------------------------------------------------


def _tree(root: Path) -> Path:
    """A minimal repository root: the files the run digest covers, plus ones it must not."""
    files = {
        "src/__init__.py": "",
        "src/agent.py": "AGENT = 1\n",
        "src/prepare/pii.py": "MASK = True\n",
        "src/static/index.html": "<p>arayüz</p>\n",
        "src/__pycache__/agent.cpython-312.pyc": "bytecode",
        "evals/cache.py": "# cache\n",
        "evals/metrics.py": "# metrics\n",
        "evals/run.py": "# run\n",
        "uv.lock": "version = 1\n",
        "docs/evals.md": "# doküman\n",
        "tests/test_x.py": "# test\n",
        "README.md": "# readme\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return root


def _digest(root: Path, **overrides: object) -> str:
    options: dict[str, object] = {"provider": "groq", "model": "m", "num_ctx": 8192} | overrides
    return run_fingerprint(root, **options)  # type: ignore[arg-type]


def _row(contract_id: str = "contract-01") -> RuleRow:
    return RuleRow(
        contract_id=contract_id, tags=("clean",), rule_id="r1", severity="normal",
        rule_has_check=True, expected_status="compliant", expected_clause_no="1",
        status="compliant", clause_no="1", has_quote=True, quote_valid=True,
        needs_review=False, review_reasons=(), has_proposal=False, code_check=None,
        no_finding_reason=None, interrupted_by=None, search_hit=True, search_calls=1,
        proposal_calls=2, proposal_errors=1,
    )  # fmt: skip


def _entry(contract_id: str = "contract-01", fingerprint: str = "f1") -> CachedContract:
    run = ContractRun(
        contract_id=contract_id, tags=("clean",), seconds=1.5, input_tokens=10,
        output_tokens=2, injection_flagged=False, pii_leaked=(1,), pii_total=2,
        best_effort_leaked=(), best_effort_total=0,
    )  # fmt: skip
    return CachedContract(
        fingerprint=fingerprint,
        completed_at="2026-11-12T08:00:00+00:00",
        run=run,
        rows={"before": [_row(contract_id)], "after": [_row(contract_id)]},
    )


def _cache(**entries: CachedContract) -> CacheFile:
    return CacheFile("groq", "m", "run-digest", entries or {"contract-01": _entry()})


# --- Run digest -----------------------------------------------------------------------------------


def test_tracked_files_cover_src_and_runner_files_but_not_static_or_pycache(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path)
    assert tracked_files(root) == sorted(
        ["src/__init__.py", "src/agent.py", "src/prepare/pii.py", *FINGERPRINT_FILES]
    )


def test_tracked_files_of_this_repository_exist_and_skip_static() -> None:
    files = tracked_files(REPO)
    assert set(FINGERPRINT_FILES) <= set(files)
    assert "src/config.py" in files
    assert not [f for f in files if f.startswith("src/static/") or "__pycache__" in f]


def test_run_fingerprint_same_content_other_root_and_mtimes_is_same(tmp_path: Path) -> None:
    first = _tree(tmp_path / "a")
    second = _tree(tmp_path / "elsewhere" / "b")
    for path in second.rglob("*"):
        if path.is_file():
            os.utime(path, (1_000_000_000, 1_000_000_000))
    assert _digest(first) == _digest(second)
    assert len(_digest(first)) == 64


@pytest.mark.parametrize(
    "rel", ["src/static/index.html", "src/__pycache__/agent.cpython-312.pyc", "docs/evals.md",
            "tests/test_x.py", "README.md"],
)  # fmt: skip
def test_run_fingerprint_untracked_file_change_keeps_digest(tmp_path: Path, rel: str) -> None:
    root = _tree(tmp_path)
    before = _digest(root)
    (root / rel).write_bytes(b"changed")
    (root / "docs" / "new.md").write_bytes(b"new")
    assert _digest(root) == before


@pytest.mark.parametrize("rel", ["src/agent.py", "src/prepare/pii.py", *FINGERPRINT_FILES])
def test_run_fingerprint_tracked_file_byte_changes_digest(tmp_path: Path, rel: str) -> None:
    root = _tree(tmp_path)
    before = _digest(root)
    path = root / rel
    path.write_bytes(path.read_bytes() + b" ")
    assert _digest(root) != before


def test_run_fingerprint_new_src_file_or_crlf_changes_digest(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    before = _digest(root)
    (root / "src" / "new.py").write_bytes(b"")
    added = _digest(root)
    assert added != before
    (root / "src" / "agent.py").write_bytes(b"AGENT = 1\r\n")  # bytes, no newline translation
    assert _digest(root) != added


def test_run_fingerprint_provider_model_num_ctx_change_digest(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    base = _digest(root)
    assert _digest(root, provider="ollama") != base
    assert _digest(root, model="other") != base
    assert _digest(root, num_ctx=4096) != base


@pytest.mark.parametrize(
    "override",
    [
        {"temperature": 0.5},
        {"temperature": None},
        {"max_tokens": 4096},
        {"base_url": "http://compat.local/v1"},
    ],
    ids=["temperature", "temperature_none", "max_tokens", "base_url"],
)
def test_run_fingerprint_model_settings_change_digest_and_drop_records(
    tmp_path: Path, override: dict[str, object]
) -> None:
    root = _tree(tmp_path)
    defaults: dict[str, object] = {"temperature": 0.0, "max_tokens": 2048, "base_url": None}
    base = _digest(root, **defaults)
    assert _digest(root) == base  # the defaults are the effective values of empty variables
    changed = _digest(root, **(defaults | override))
    assert changed != base
    cache = CacheFile("groq", "m", base, {"contract-01": _entry()})
    digests = {"contract-01": "f1"}
    kept = usable_entries(
        cache, provider="groq", model="m", run_digest=base, contract_digests=digests
    )
    assert list(kept) == ["contract-01"]
    dropped = usable_entries(
        cache, provider="groq", model="m", run_digest=changed, contract_digests=digests
    )
    assert dropped == {}


@pytest.mark.parametrize("rel", ["uv.lock", "evals/run.py"])
def test_run_fingerprint_missing_required_file_raises(tmp_path: Path, rel: str) -> None:
    root = _tree(tmp_path)
    (root / rel).unlink()
    with pytest.raises(FingerprintError, match=rel):
        _digest(root)


# --- Contract digest ------------------------------------------------------------------------------


def test_contract_fingerprint_changes_with_text_label_or_rules_only() -> None:
    base = contract_fingerprint(b"metin", LABEL, b"rules")
    assert contract_fingerprint(b"metin", dict(reversed(LABEL.items())), b"rules") == base
    assert contract_fingerprint(b"metin ", LABEL, b"rules") != base
    assert contract_fingerprint(b"metin", LABEL | {"tags": ["pii"]}, b"rules") != base
    assert contract_fingerprint(b"metin", LABEL, b"rules\n") != base


# --- The file -------------------------------------------------------------------------------------


def test_cache_path_per_provider_and_safe_model(tmp_path: Path) -> None:
    assert cache_path(tmp_path, "groq", "openai/gpt-oss-120b") == (
        tmp_path / ".cache" / "groq_openai-gpt-oss-120b.json"
    )
    assert cache_path(tmp_path, "ollama", "x") != cache_path(tmp_path, "groq", "x")


def test_cache_path_per_dataset_keeps_default_name(tmp_path: Path) -> None:
    default = cache_path(tmp_path, "groq", "openai/gpt-oss-120b")
    assert cache_path(tmp_path, "groq", "openai/gpt-oss-120b", dataset=None) == default
    holdout = cache_path(tmp_path, "groq", "openai/gpt-oss-120b", dataset="evals/holdout")
    digest = hashlib.sha256(b"evals/holdout").hexdigest()[:8]
    assert holdout == tmp_path / ".cache" / f"groq_openai-gpt-oss-120b_dataset-{digest}.json"
    assert cache_path(tmp_path, "groq", "openai/gpt-oss-120b", dataset="evals/x") != holdout


def test_write_then_load_round_trip_is_exact_and_leaves_no_temp_file(tmp_path: Path) -> None:
    path = cache_path(tmp_path, "groq", "m")
    cache = _cache(**{"contract-01": _entry(), "contract-02": _entry("contract-02", "f2")})
    write_cache(path, cache)
    write_cache(path, cache)  # replaces in place

    assert load_cache(path) == cache
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    data = path.read_bytes()
    assert b"\r\n" not in data
    raw = json.loads(data.decode("utf-8"))
    assert raw["version"] == CACHE_VERSION
    assert list(raw["contracts"]) == ["contract-01", "contract-02"]


def test_write_cache_failure_keeps_old_file_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = cache_path(tmp_path, "groq", "m")
    write_cache(path, _cache())
    old = path.read_bytes()

    def broken_replace(src: str, dst: Path) -> None:
        raise OSError("disk dolu")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        write_cache(path, _cache(**{"contract-02": _entry("contract-02")}))
    assert path.read_bytes() == old
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_load_cache_missing_file_is_none(tmp_path: Path) -> None:
    assert load_cache(tmp_path / ".cache" / "none.json") is None


@pytest.mark.parametrize(
    "content",
    [
        b"{bozuk",
        b"\xff\xfe",
        json.dumps({"version": 99}).encode(),
        json.dumps([1, 2]).encode(),
    ],
)
def test_load_cache_corrupt_raises_with_path(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "c.json"
    path.write_bytes(content)
    with pytest.raises(CorruptCacheError) as info:
        load_cache(path)
    assert info.value.path == path


def test_load_cache_version_1_without_proposal_counts_is_corrupt(tmp_path: Path) -> None:
    """A cache written before rows carried `proposal_calls` / `proposal_errors` is not half-read."""
    assert CACHE_VERSION == 2
    raw = _valid_raw(tmp_path)
    raw["version"] = 1
    contracts = raw["contracts"]
    assert isinstance(contracts, dict)
    for rows in contracts["contract-01"]["rows"].values():
        for row in rows:
            del row["proposal_calls"], row["proposal_errors"]
    path = tmp_path / "v1.json"
    path.write_bytes(json.dumps(raw).encode("utf-8"))
    with pytest.raises(CorruptCacheError) as info:
        load_cache(path)
    assert info.value.path == path
    assert "1" in str(info.value)


@pytest.mark.parametrize("field", ["proposal_calls", "proposal_errors"])
def test_load_cache_row_without_proposal_count_is_corrupt(tmp_path: Path, field: str) -> None:
    raw = _valid_raw(tmp_path)
    contracts = raw["contracts"]
    assert isinstance(contracts, dict)
    del contracts["contract-01"]["rows"]["after"][0][field]
    path = tmp_path / "broken.json"
    path.write_bytes(json.dumps(raw).encode("utf-8"))
    with pytest.raises(CorruptCacheError):
        load_cache(path)


def _valid_raw(tmp_path: Path) -> dict[str, object]:
    path = tmp_path / "c.json"
    write_cache(path, _cache())
    raw: dict[str, object] = json.loads(path.read_bytes().decode("utf-8"))
    return raw


@pytest.mark.parametrize(
    "breakage",
    [
        "missing_rows",
        "bad_row",
        "other_contract_row",
        "naive_completed_at",
        "extra_top_key",
    ],
)
def test_load_cache_broken_schema_raises(tmp_path: Path, breakage: str) -> None:
    raw = _valid_raw(tmp_path)
    contracts = raw["contracts"]
    assert isinstance(contracts, dict)
    entry = contracts["contract-01"]
    if breakage == "missing_rows":
        del entry["rows"]
    elif breakage == "bad_row":
        entry["rows"]["after"][0]["search_calls"] = "1"
    elif breakage == "other_contract_row":
        entry["rows"]["before"][0]["contract_id"] = "contract-02"
    elif breakage == "naive_completed_at":
        entry["completed_at"] = "2026-11-12T08:00:00"
    else:
        raw["extra"] = 1
    path = tmp_path / "broken.json"
    path.write_bytes(json.dumps(raw).encode("utf-8"))
    with pytest.raises(CorruptCacheError):
        load_cache(path)


def test_delete_cache_removes_folder_only_when_empty(tmp_path: Path) -> None:
    groq = cache_path(tmp_path, "groq", "m")
    ollama = cache_path(tmp_path, "ollama", "m")
    write_cache(groq, _cache())
    write_cache(ollama, _cache())
    delete_cache(groq)
    assert not groq.exists() and ollama.exists()
    delete_cache(ollama)
    assert not ollama.parent.exists()
    delete_cache(ollama)  # nothing there: no error


def test_usable_entries_need_every_digest_to_match() -> None:
    cache = _cache(**{"contract-01": _entry(), "contract-02": _entry("contract-02", "f2")})
    digests = {"contract-01": "f1", "contract-02": "changed"}
    args = {"provider": "groq", "model": "m", "run_digest": "run-digest"}
    assert list(usable_entries(cache, contract_digests=digests, **args)) == ["contract-01"]
    for key, value in (("provider", "ollama"), ("model", "x"), ("run_digest", "other")):
        assert usable_entries(cache, contract_digests=digests, **(args | {key: value})) == {}


def test_eval_cache_sources_have_no_invisible_characters() -> None:
    for path in (Path(__file__), REPO / "evals" / "cache.py", REPO / "evals" / "metrics.py"):
        text = path.read_bytes().decode("utf-8")
        assert not [ch for ch in text if unicodedata.category(ch) == "Cf"], path
