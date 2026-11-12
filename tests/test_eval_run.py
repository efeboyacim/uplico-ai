import json
import socket
import tempfile
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

import groq
import httpx
import pytest
import requests
from conftest import ScriptedChatModel, ScriptItem, ai, call, done

import evals.run as runner
from evals.run import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_RATE_LIMITED,
    EXIT_THRESHOLD_FAILED,
    main,
    run_eval,
    run_or_report,
)
from src.config import Settings, load_settings
from src.prepare.clauses import split_clauses
from src.prepare.pii import mask
from src.store import Store
from src.text import strip_invisible

REPO = Path(__file__).resolve().parent.parent
DATASET = REPO / "evals" / "dataset"
LABELS: list[dict[str, Any]] = json.loads((DATASET / "labels.json").read_bytes().decode("utf-8"))
BY_ID = {label["id"]: label for label in LABELS}
STARTED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
SECRET = "GIZLI-HATA-METNI"
TENANT_KEYS = {"k-acme": {"tenant_id": "acme", "role": "lawyer", "name": "Av. Test"}}

Behaviour = Callable[[str, str], list[ScriptItem]]  # (clause_id, quote) -> script


# --- Helpers --------------------------------------------------------------------------------------


def _settings(data_dir: Path) -> Settings:
    return load_settings(
        env={
            "LLM_PROVIDER": "groq",
            "GROQ_API_KEY": "test-key",
            "TENANT_API_KEYS": json.dumps(TENANT_KEYS),
            "DATA_DIR": str(data_dir),
        }
    )


@cache
def _masked_clauses(contract_id: str) -> dict[str, tuple[str, str]]:
    """clause_no -> (clause_id, masked text), produced the way `prepare_contract` does it."""
    text = (DATASET / BY_ID[contract_id]["contract_file"]).read_bytes().decode("utf-8")
    _, mapping = mask(strip_invisible(text), {})
    result: dict[str, tuple[str, str]] = {}
    for raw in split_clauses(text):
        masked, mapping = mask(strip_invisible(raw.text), mapping)
        if raw.clause_no is not None:
            result[raw.clause_no] = (raw.clause_id, str(masked))
    return result


def _not_found() -> list[ScriptItem]:
    return [
        ai(call("search_contract", query="madde")),
        ai(call("submit_finding", status="not_found", reasoning="Yok.")),
        done(),
    ]


def _answer(status: str) -> Behaviour:
    def script(clause_id: str, quote: str) -> list[ScriptItem]:
        return [
            ai(call("search_contract", query="madde")),
            ai(
                call(
                    "submit_finding",
                    status=status,
                    clause_id=clause_id,
                    quote=quote,
                    reasoning="Gerekçe.",
                )
            ),
            done(),
        ]

    return script


def _wrong_noncompliant_with_draft(clause_id: str, quote: str) -> list[ScriptItem]:
    """A compliant clause called non_compliant, with a draft: a rule without a `check` gives
    verification nothing to catch (`unverifiable` draft, valid quote)."""
    return [
        ai(call("search_contract", query="madde")),
        ai(
            call(
                "propose_change",
                clause_id=clause_id,
                proposed_text="Yeni metin.",
                rationale="Gerekçe.",
            )
        ),
        ai(
            call(
                "submit_finding",
                status="non_compliant",
                clause_id=clause_id,
                quote=quote,
                reasoning="Gerekçe.",
            )
        ),
        done(),
    ]


def _model(
    data_dir: Path,
    count: int,
    overrides: Mapping[tuple[str, str], Behaviour | list[ScriptItem]] | None = None,
) -> ScriptedChatModel:
    """Scripts for the first `count` contracts, in label order: every rule answers its label
    (whole masked clause as the quote), except the (contract_id, rule_id) overrides."""
    overrides = overrides or {}
    scripts: dict[str, list[ScriptItem]] = {}
    for label in LABELS[:count]:
        rule_text = {r.id: r.text for r in Store(data_dir, label["tenant_id"]).load_rules()}
        for expected in label["expected"]:
            override = overrides.get((label["id"], expected["rule_id"]))
            if isinstance(override, list):
                items = override
            else:
                behaviour = override or (
                    None if expected["status"] == "not_found" else _answer(expected["status"])
                )
                if behaviour is None:
                    items = _not_found()
                else:
                    clause_id, text = _masked_clauses(label["id"])[expected["clause_no"]]
                    items = behaviour(clause_id, text)
            scripts.setdefault(rule_text[expected["rule_id"]], []).extend(items)
    return ScriptedChatModel.keyed(scripts)


def _groq_error(cls: type[groq.APIStatusError], code: int) -> groq.APIStatusError:
    request = httpx.Request("POST", "https://api.groq.com")
    body = {"error": {"message": SECRET}}
    response = httpx.Response(code, request=request, json=body)
    return cls(f"{SECRET} {code}", response=response, body=body)


def _run(data_dir: Path, results_dir: Path, model: ScriptedChatModel | None, **kwargs: Any) -> int:
    options: dict[str, Any] = {"limit": None, "prepare_only": False} | kwargs
    return run_eval(
        _settings(data_dir),
        results_dir=results_dir,
        started_at=STARTED_AT,
        model=model,
        **options,
    )


def _result(results_dir: Path) -> tuple[Path, dict[str, Any]]:
    files = list(results_dir.iterdir())
    assert len(files) == 1, files
    data: dict[str, Any] = json.loads(files[0].read_bytes().decode("utf-8"))
    return files[0], data


def _line(stdout: str, label: str) -> str:
    """The line of `label` in the table's "Metrikler (sonra)" section."""
    lines = stdout.splitlines()
    section = lines[lines.index("Metrikler (sonra)") + 1 :]
    return next(line for line in section if line.startswith(label))


@pytest.fixture(autouse=True)
def network(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Every HTTP request or socket connection attempt is recorded and refused."""
    attempts: list[str] = []

    def refuse_request(self: requests.Session, method: str, url: str, *a: Any, **k: Any) -> Any:
        attempts.append(f"{method} {url}")
        raise AssertionError("HTTP isteği yapılmamalı.")

    def refuse_connect(self: socket.socket, address: Any) -> None:
        attempts.append(f"connect {address!r}")
        raise AssertionError("Ağ bağlantısı yapılmamalı.")

    monkeypatch.setattr(requests.Session, "request", refuse_request)
    monkeypatch.setattr(socket.socket, "connect", refuse_connect)
    for name in ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY", "LANGSMITH_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    yield attempts


@pytest.fixture
def results_dir(tmp_path: Path) -> Path:
    return tmp_path / "results"


# --- Tests ----------------------------------------------------------------------------------------


def test_run_limit_3_runs_only_first_three_contracts(
    data_dir: Path, results_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    model = _model(data_dir, 3)
    code = _run(data_dir, results_dir, model, limit=3)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    # 3 contracts x 5 rules x 3 scripted turns; a fourth contract would have asked for more.
    assert len(model.received()) == 45
    path, data = _result(results_dir)
    assert path.name == "20260102-030405_groq_openai-gpt-oss-120b_partial.json"
    assert data["contracts_run"] == 3
    assert data["contracts_total"] == 7
    assert data["partial"] is True
    assert data["mode"] == "full"
    assert [c["contract_id"] for c in data["contracts"]] == [x["id"] for x in LABELS[:3]]
    assert {r["contract_id"] for r in data["rules"]} == {x["id"] for x in LABELS[:3]}
    assert "3/7 sözleşme" in out
    # Injection metrics need contracts 6 and 7: not measured, and they do not fail the run.
    assert "ölçülmedi" in _line(out, "Enjeksiyon: tarama")
    assert "ölçülmedi" in _line(out, "Enjeksiyon: model kandı mı")
    assert "ölçülmedi" in _line(out, "Enjeksiyon: sonuç")
    assert data["thresholds"]["injection_scan"]["passed"] is None
    assert data["thresholds"]["injection_outcome"]["passed"] is None
    assert data["metrics"]["after"]["injection_outcome"] is None
    # Every label answered correctly: the measured hard thresholds hold.
    assert data["metrics"]["after"]["unsafe_error"]["num"] == 0
    assert data["thresholds"]["unsafe_error"]["passed"] is True
    assert "TUTMADI" not in out
    assert all(c["seconds"] is not None for c in data["contracts"])  # recorded, never asserted


def test_run_rate_limited_rule_exits_2_without_table_or_file(
    data_dir: Path, results_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rate_limit = _groq_error(groq.RateLimitError, 429)
    model = _model(data_dir, 1, {("contract-01", "termination_notice"): [rate_limit]})
    code = _run(data_dir, results_dir, model, limit=1)
    captured = capsys.readouterr()

    assert code == EXIT_RATE_LIMITED
    assert captured.out == ""
    assert not results_dir.exists() or not list(results_dir.iterdir())
    assert "1 kural limite takıldı" in captured.err
    assert "contract-01" in captured.err
    assert SECRET not in captured.err


def test_run_broken_hard_threshold_exits_3_with_table_and_file(
    data_dir: Path, results_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    model = _model(data_dir, 1, {("contract-01", "liability_cap"): _wrong_noncompliant_with_draft})
    code = _run(data_dir, results_dir, model, limit=1)
    out = capsys.readouterr().out

    assert code == EXIT_THRESHOLD_FAILED
    _, data = _result(results_dir)
    assert data["thresholds"]["unsafe_error"]["passed"] is False
    assert data["metrics"]["after"]["unsafe_error"] == {"num": 1, "den": 5, "value": 0.2}
    assert data["metrics"]["after"]["proposal_unverifiable"]["num"] == 1
    assert "TUTMADI" in _line(out, "Güvensiz hata oranı  ")
    assert "Sonuç dosyası:" in out
    rule = next(r for r in data["rules"] if r["rule_id"] == "liability_cap")
    assert rule["needs_review"] == {"before": False, "after": False}
    assert rule["code_check"] == "unverifiable"
    assert rule["quote_valid"] is True


def test_run_prepare_only_calls_no_model_and_measures_pii(
    data_dir: Path,
    results_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluate_calls: list[object] = []
    monkeypatch.setattr(runner, "evaluate", lambda *a, **k: evaluate_calls.append(a))
    model = ScriptedChatModel.keyed({})
    code = _run(data_dir, results_dir, model, limit=5, prepare_only=True)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert model.received() == []
    assert evaluate_calls == []
    path, data = _result(results_dir)
    assert path.name.endswith("_prepare-only.json")
    assert data["mode"] == "prepare_only"
    assert data["langsmith"] == "skipped"
    assert data["rules"] == []
    assert all(value is None for value in data["metrics"]["after"].values())
    contracts = data["metrics"]["contracts"]
    assert contracts["pii_leaked"] == 0
    assert contracts["best_effort"] == {"num": 5, "den": 5, "value": 1.0}
    assert contracts["input_tokens"] is None
    assert "5 / 5" in _line(out, "Garanti edilmeyen PII sızıntısı")
    assert "ölçülmedi" in _line(out, "Güvensiz hata oranı  ")
    assert "ölçülmedi" in _line(out, "Enjeksiyon: tarama")  # contract 6 not run


def test_run_prepare_only_all_contracts_measures_injection_scan(
    data_dir: Path, results_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run(data_dir, results_dir, None, prepare_only=True)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    _, data = _result(results_dir)
    assert data["partial"] is False
    assert data["thresholds"]["injection_scan"]["passed"] is True
    assert [c["contract_id"] for c in data["contracts"] if c["injection_flagged"]] == [
        "contract-06"
    ]
    assert "7/7 sözleşme" in out
    assert "tuttu" in _line(out, "Enjeksiyon: tarama")


def test_run_without_langsmith_key_evaluates_locally_without_http(
    data_dir: Path,
    results_dir: Path,
    network: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    real_evaluate = runner.evaluate
    calls: list[dict[str, Any]] = []

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(runner, "evaluate", spy)

    # Mark contract-02's scores: if the metrics show the mark, they are the evaluator's scores
    # as evaluate() returned them, not a recount of the rows.
    real_counter = runner.count_evaluator

    def marked(outputs: dict[str, Any]) -> list[dict[str, Any]]:
        scores = real_counter(outputs)
        if any(r["contract_id"] == "contract-02" for r in outputs["rows"]):
            return [s | {"score": 0} if s["key"] == "status_correct" else s for s in scores]
        return scores

    monkeypatch.setattr(runner, "count_evaluator", marked)
    code = _run(data_dir, results_dir, _model(data_dir, 2), limit=2)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert [c["experiment_prefix"] for c in calls] == ["before", "after"]
    assert all(c["upload_results"] is False for c in calls)
    assert network == []
    _, data = _result(results_dir)
    assert data["langsmith"] == "local"
    # The table's counts are the evaluator's scores: contract-02's correct statuses were zeroed
    # by the marked evaluator, so only contract-01's count reaches the metrics.
    correct_all = sum(r["status"] == r["expected_status"] for r in data["rules"])
    correct_01 = sum(
        r["status"] == r["expected_status"]
        for r in data["rules"]
        if r["contract_id"] == "contract-01"
    )
    assert correct_01 < correct_all  # the mark is visible
    assert data["metrics"]["after"]["label_accuracy"] == {
        "num": correct_01,
        "den": 10,
        "value": correct_01 / 10,
    }
    assert data["metrics"]["before"]["label_accuracy"]["num"] == correct_01
    assert f"{correct_01}/10 " in _line(out, "Etiket doğruluğu")
    routed_before = sum(r["needs_review"]["before"] for r in data["rules"])
    routed_after = sum(r["needs_review"]["after"] for r in data["rules"])
    assert data["metrics"]["before"]["routed_to_lawyer"]["num"] == routed_before
    assert data["metrics"]["after"]["routed_to_lawyer"]["num"] == routed_after
    assert routed_after > routed_before  # not_found rules reach the queue only when verified


def test_run_evaluator_error_raises_runtime_error(
    data_dir: Path, results_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_evaluator(outputs: dict[str, Any]) -> list[dict[str, Any]]:
        raise ValueError("evaluator bozuk")

    monkeypatch.setattr(runner, "count_evaluator", broken_evaluator)
    with pytest.raises(RuntimeError, match="evaluator"):
        _run(data_dir, results_dir, _model(data_dir, 1), limit=1)
    assert not results_dir.exists()


def test_run_target_error_raises_runtime_error(
    data_dir: Path, results_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_target(rows: Sequence[Any]) -> dict[str, Any]:
        raise ValueError("hedef bozuk")

    monkeypatch.setattr(runner, "rows_to_outputs", broken_target)
    with pytest.raises(RuntimeError, match="hedef"):
        _run(data_dir, results_dir, _model(data_dir, 1), limit=1)
    assert not results_dir.exists()


def test_run_provider_auth_error_exits_1_with_class_name_only(
    data_dir: Path, results_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    error = _groq_error(groq.AuthenticationError, 401)
    model = _model(data_dir, 1, {("contract-01", "termination_notice"): [error]})
    code = run_or_report(
        _settings(data_dir),
        limit=1,
        prepare_only=False,
        results_dir=results_dir,
        started_at=STARTED_AT,
        model=model,
    )
    captured = capsys.readouterr()

    assert code == EXIT_ERROR
    assert captured.out == ""
    assert "AuthenticationError" in captured.err
    assert "contract-01" in captured.err
    assert SECRET not in captured.err
    assert not results_dir.exists()


def test_run_contract_05_outputs_carry_no_raw_pii(
    data_dir: Path, results_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(data_dir, results_dir, _model(data_dir, 5), limit=5)
    out = capsys.readouterr().out
    path, data = _result(results_dir)
    written = path.read_bytes().decode("utf-8")

    contract = next(c for c in data["contracts"] if c["contract_id"] == "contract-05")
    assert contract["best_effort_leaked_count"] == 5
    assert contract["best_effort_leaked"][0] == "pii_best_effort[0]"
    for label in LABELS[:5]:
        for value in label["pii_values"] + label["pii_best_effort"]:
            assert value not in out, (label["id"], "stdout")
            assert value not in written, (label["id"], "result file")


def test_run_removes_temp_dir_and_leaves_data_dirs_untouched(
    data_dir: Path,
    results_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def snapshot(root: Path) -> set[tuple[str, int]]:
        return {(p.relative_to(root).as_posix(), p.stat().st_size) for p in root.rglob("*")}

    model = _model(data_dir, 1)  # reads the rules through a Store: before the snapshot
    repo_data = snapshot(REPO / "data")
    source = snapshot(data_dir / "tenants")
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))

    _run(data_dir, results_dir, model, limit=1)
    capsys.readouterr()

    assert list(temp_root.iterdir()) == []
    assert snapshot(REPO / "data") == repo_data
    assert snapshot(data_dir / "tenants") == source  # rules are copied out; nothing is written back


def test_main_prepare_only_writes_result_and_exits_0(
    data_dir: Path,
    results_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("TENANT_API_KEYS", json.dumps(TENANT_KEYS))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.delenv("LLM_MODEL", raising=False)
    code = main(["--prepare-only", "--limit", "1", "--results-dir", str(results_dir)])
    out = capsys.readouterr().out

    assert code == EXIT_OK
    path, data = _result(results_dir)
    assert path.name.startswith(data["started_at"][:10].replace("-", ""))
    assert data["provider"] == "ollama"
    assert "1/7 sözleşme" in out


@pytest.mark.parametrize("limit", ["0", "8", "abc"])
def test_main_limit_out_of_range_exits_1(limit: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--limit", limit])
    assert info.value.code == EXIT_ERROR
    assert "--limit" in capsys.readouterr().err


def test_main_missing_tenant_keys_exits_1(
    results_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("TENANT_API_KEYS", raising=False)
    with pytest.raises(SystemExit) as info:
        main(["--prepare-only", "--results-dir", str(results_dir)])
    assert info.value.code == EXIT_ERROR
    assert "TENANT_API_KEYS" in capsys.readouterr().err
    assert not results_dir.exists()


def test_eval_run_test_source_has_no_invisible_characters() -> None:
    for path in (Path(__file__), REPO / "evals" / "run.py"):
        text = path.read_bytes().decode("utf-8")
        assert not [ch for ch in text if unicodedata.category(ch) == "Cf"], path
