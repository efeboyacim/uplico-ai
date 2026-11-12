"""Eval runner: `python -m evals.run [--limit N] [--prepare-only] [--results-dir DIR]`.

For every contract of `evals/dataset/labels.json` (in file order, sequentially) the runner
prepares the contract and runs the review graph **once** up to its interrupt in a temporary
`DATA_DIR`; the agent's raw findings are verified twice by code (`verify(enabled=False)` for
"before", `verify(enabled=True)` for "after"). The rule rows of both modes go through LangSmith
`evaluate()` (local without a key, `upload_results=False`) and the counts it returns are summed by
`evals/metrics.py` into the table on stdout and the result file under `evals/results/`.

Exit codes (docs/evals.md §6): 0 run complete and the hard thresholds hold (or were not
measured); 1 configuration error or the run crashed; 2 at least one rule was rate limited (no
table, no result file); 3 run complete but a hard threshold failed (table and file written).

Only `main` reads the environment (`load_settings_or_exit`) and the clock; everything below takes
them as parameters, so tests pass a scripted model, a fixed time and a temporary results folder.
"""

import argparse
import contextlib
import dataclasses
import io
import json
import shutil
import sys
import tempfile
import time
import uuid
import warnings
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langsmith import Client, evaluate
from langsmith.schemas import Example

from evals.metrics import (
    ContractRun,
    Label,
    Mode,
    RuleRow,
    TableHeader,
    build_contract_run,
    build_rule_rows,
    contract_metrics,
    contract_metrics_json,
    contract_run_json,
    count_evaluator,
    counts_from_scores,
    dataset_name,
    evaluate_thresholds,
    example_inputs,
    example_outputs,
    mode_metrics,
    mode_metrics_json,
    order_by_rules,
    parse_labels,
    rate_limited_rule_ids,
    render_table,
    result_filename,
    rows_to_outputs,
    rule_json,
    sum_counts,
    thresholds_json,
    thresholds_passed,
)
from src.config import Settings, load_settings_or_exit
from src.graph import close_graphs, get_review_graph, initial_state, review_config
from src.llm import classify_provider_error
from src.models import Finding
from src.prepare import prepare_contract
from src.prepare.index import clear_indexes, drop_index
from src.store import Store, close_all
from src.verify import verify

EVALS_DIR = Path(__file__).resolve().parent
DATASET_DIR = EVALS_DIR / "dataset"
LABELS_PATH = DATASET_DIR / "labels.json"
DEFAULT_RESULTS_DIR = EVALS_DIR / "results"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_RATE_LIMITED = 2
EXIT_THRESHOLD_FAILED = 3

_RULES_NAME = "rules.yaml"
_MODES: tuple[Mode, ...] = ("before", "after")
# Local examples need a dataset id; nothing is uploaded with it.
_LOCAL_DATASET_ID = uuid.UUID(int=0)
_EXAMPLE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "contract-review-eval")


class EvalError(RuntimeError):
    """A failure of the runner itself; its message never carries contract text or PII."""


class ContractRunError(Exception):
    """A contract's run crashed. Carries the contract id and the error class name only.

    The original error's message is never kept here: it may carry contract text.
    """

    def __init__(self, contract_id: str, error: Exception) -> None:
        self.contract_id = contract_id
        self.error_name = type(error).__name__
        self.provider_config = classify_provider_error(error) == "config"
        self.detail = str(error) if isinstance(error, EvalError) else None
        super().__init__(contract_id, self.error_name)


@dataclasses.dataclass(frozen=True)
class _ContractResult:
    run: ContractRun
    rows: Mapping[Mode, list[RuleRow]]  # empty lists in prepare-only mode


# --- Labels and data dir --------------------------------------------------------------------------


def load_labels(path: Path = LABELS_PATH) -> tuple[bytes, tuple[Label, ...]]:
    """The raw bytes of `labels.json` (for the dataset name) and its validated records."""
    raw = path.read_bytes()
    return raw, parse_labels(json.loads(raw.decode("utf-8")))


def _contract_text(label: Label) -> str:
    # Bytes, not read_text: no newline translation, the contract is read exactly as stored.
    return (DATASET_DIR / label.contract_file).read_bytes().decode("utf-8")


def _copy_rules(source_dir: Path, target_dir: Path, tenant_ids: Sequence[str]) -> None:
    """Copy each tenant's `rules.yaml` into the temporary data dir; nothing else is copied."""
    for tenant_id in sorted(set(tenant_ids)):
        source = source_dir / "tenants" / tenant_id / _RULES_NAME
        if not source.is_file():
            raise EvalError(f"{tenant_id} tenant'ının {_RULES_NAME} dosyası bulunamadı.")
        target = target_dir / "tenants" / tenant_id
        target.mkdir(parents=True)
        shutil.copyfile(source, target / _RULES_NAME)


def _close_everything(reviews: Sequence[tuple[str, str]]) -> None:
    """Release every handle on the temporary data dir before it is deleted (decision 13)."""
    for tenant_id, review_id in reviews:
        drop_index(tenant_id, review_id)
    close_graphs()
    close_all()
    clear_indexes()


# --- One contract ---------------------------------------------------------------------------------


def _run_contract(
    settings: Settings,
    label: Label,
    *,
    prepare_only: bool,
    model: BaseChatModel | None,
    reviews: list[tuple[str, str]],
) -> tuple[_ContractResult | None, list[str]]:
    """Prepare (and unless prepare-only, review) one contract.

    Returns the contract's result and the ids of its rate-limited rules; the result is None when
    a rule was rate limited (the run is then invalid and its rows are not built).
    """
    store = Store(settings.data_dir, label.tenant_id)
    rules = store.load_rules()
    prepared = prepare_contract(
        store, _contract_text(label), provider=settings.llm_provider, model=settings.llm_model
    )
    review_id = prepared.review_id
    reviews.append((label.tenant_id, review_id))
    injection_flagged = bool(prepared.injection_flags)

    if prepare_only:
        run = build_contract_run(
            label,
            prepared.clauses,
            store.list_audit(review_id),
            injection_flagged=injection_flagged,
            seconds=None,
        )
        return _ContractResult(run=run, rows={"before": [], "after": []}), []

    graph = get_review_graph(settings, label.tenant_id, model=model)
    started = time.perf_counter()
    out = graph.invoke(initial_state(prepared, rules), review_config(settings, review_id))
    seconds = time.perf_counter() - started
    if "__interrupt__" not in out or "verified" not in out:
        raise EvalError("Graph avukat aşamasına ulaşmadan döndü.")

    raw = order_by_rules([Finding.model_validate(f) for f in out["findings"]], rules)
    limited = rate_limited_rule_ids(raw)
    if limited:
        return None, limited

    before = verify(raw, rules, prepared.clauses, injection_flagged, enabled=False)
    after = verify(raw, rules, prepared.clauses, injection_flagged, enabled=True)
    if [f.model_dump(mode="json") for f in after] != out["verified"]:
        raise EvalError("Kodla yeniden hesaplanan doğrulama graph'takiyle aynı değil.")

    audit = store.list_audit(review_id)
    before_rows, after_rows = build_rule_rows(
        label, rules, prepared.clauses, raw, before, after, audit
    )
    run = build_contract_run(
        label, prepared.clauses, audit, injection_flagged=injection_flagged, seconds=seconds
    )
    return _ContractResult(run=run, rows={"before": before_rows, "after": after_rows}), []


# --- LangSmith evaluate() -------------------------------------------------------------------------


def _local_examples(labels: Sequence[Label]) -> list[Example]:
    return [
        Example(
            id=uuid.uuid5(_EXAMPLE_NAMESPACE, label.id),
            dataset_id=_LOCAL_DATASET_ID,
            inputs=example_inputs(label),
            outputs=example_outputs(label),
        )
        for label in labels
    ]


def _uploaded_dataset(client: Client, name: str, labels: Sequence[Label]) -> str:
    """Create the dataset with every label's example unless it already exists; its name."""
    if not client.has_dataset(dataset_name=name):
        dataset = client.create_dataset(name)
        client.create_examples(
            dataset_id=dataset.id,
            examples=[
                {"inputs": example_inputs(label), "outputs": example_outputs(label)}
                for label in labels
            ],
        )
    return name


def _row_evaluator(outputs: dict[str, Any]) -> dict[str, Any]:
    """`count_evaluator` in the `{"results": [...]}` form `evaluate()` is typed for."""
    return {"results": count_evaluator(outputs)}


def _evaluate_mode(
    mode: Mode,
    rows: Mapping[str, list[RuleRow]],
    labels: Sequence[Label],
    *,
    data: str | list[Example],
    client: Client,
    upload: bool,
    metadata: dict[str, Any],
) -> list[dict[str, int]]:
    """Run `evaluate()` for one mode; the per-contract counts, in label order.

    `evaluate()` swallows target and evaluator exceptions; every row is checked here and any
    error stops the run (no metric is printed from partial counts).
    """

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        return rows_to_outputs(rows[inputs["contract_id"]])

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*upload_results.*beta", category=UserWarning)
        # The library's own use of `ast.Str` (Python 3.12); nothing the runner can act on.
        warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"langsmith\.")
        # The library prints its "Starting evaluation" line with print(); keep stdout the table's.
        with contextlib.redirect_stdout(sys.stderr):
            results = evaluate(
                target,
                data=data,
                evaluators=[_row_evaluator],
                experiment_prefix=mode,
                metadata=metadata,
                max_concurrency=0,
                client=client,
                upload_results=upload,
            )

    counts: dict[str, dict[str, int]] = {}
    for row in results:
        contract_id = str((row["example"].inputs or {}).get("contract_id"))
        if row["run"].error:
            raise EvalError(f"evaluate() ({mode}): hedef {contract_id} için hata verdi.")
        scores = row["evaluation_results"]["results"]
        if any(r.extra and r.extra.get("error") for r in scores):
            raise EvalError(f"evaluate() ({mode}): evaluator {contract_id} için hata verdi.")
        if contract_id in counts:
            raise EvalError(f"evaluate() ({mode}): {contract_id} iki kez değerlendirildi.")
        counts[contract_id] = counts_from_scores((r.key, r.score) for r in scores)
    wanted = [label.id for label in labels]
    if set(counts) != set(wanted):
        raise EvalError(f"evaluate() ({mode}): değerlendirilen sözleşmeler koşulanlarla aynı değil")
    return [counts[contract_id] for contract_id in wanted]


def _evaluate_both(
    settings: Settings,
    results: Mapping[str, _ContractResult],
    labels: Sequence[Label],
    labels_bytes: bytes,
    *,
    partial: bool,
) -> tuple[dict[Mode, list[dict[str, int]]], str]:
    """Counts of both modes from `evaluate()` and the LangSmith status (uploaded / local)."""
    upload = settings.langsmith_api_key is not None and not partial
    data: str | list[Example]
    if upload:
        client = Client(api_key=settings.langsmith_api_key)
        data = _uploaded_dataset(client, dataset_name(labels_bytes), labels)
    else:
        # Without auto batch tracing the client sends nothing, not even its `GET /info`.
        client = Client(auto_batch_tracing=False)
        data = _local_examples(labels)
    metadata = {"provider": settings.llm_provider, "model": settings.llm_model}
    counts: dict[Mode, list[dict[str, int]]] = {}
    for mode in _MODES:
        rows = {contract_id: result.rows[mode] for contract_id, result in results.items()}
        counts[mode] = _evaluate_mode(
            mode, rows, labels, data=data, client=client, upload=upload, metadata=metadata
        )
    return counts, "uploaded" if upload else "local"


# --- The run --------------------------------------------------------------------------------------


def _write_results(path: Path, content: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(content, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _report_rate_limit(limited: Mapping[str, list[str]]) -> None:
    total = sum(len(rule_ids) for rule_ids in limited.values())
    where = ", ".join(f"{contract_id}: {len(ids)}" for contract_id, ids in limited.items())
    print(
        f"Hız limiti: {total} kural limite takıldı ({where}). Koşu durduruldu; metrik "
        "basılmadı, sonuç dosyası yazılmadı.",
        file=sys.stderr,
    )


def run_eval(
    settings: Settings,
    *,
    limit: int | None,
    prepare_only: bool,
    results_dir: Path,
    started_at: datetime,
    model: BaseChatModel | None = None,
) -> int:
    """Run the eval; returns 0, 2 (rate limited) or 3 (a hard threshold failed).

    A crash raises: `ContractRunError` for a contract's run (provider configuration errors and
    unexpected code errors are never turned into findings), `EvalError` for the runner itself.
    `model` is passed to the review graph (tests); without it the graph builds it from settings.
    """
    labels_bytes, labels = load_labels()
    if limit is not None and not 1 <= limit <= len(labels):
        raise ValueError(f"limit 1 ile {len(labels)} arasında olmalı.")
    selected = labels[:limit] if limit is not None else labels
    partial = len(selected) < len(labels)

    results: dict[str, _ContractResult] = {}
    limited: dict[str, list[str]] = {}
    reviews: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="contract-eval-") as tmp:
        eval_settings = dataclasses.replace(settings, data_dir=Path(tmp))
        try:
            _copy_rules(settings.data_dir, eval_settings.data_dir, [x.tenant_id for x in selected])
            for label in selected:  # sequentially; parallelism is only inside the graph
                try:
                    result, rate_limited = _run_contract(
                        eval_settings,
                        label,
                        prepare_only=prepare_only,
                        model=model,
                        reviews=reviews,
                    )
                except Exception as exc:
                    raise ContractRunError(label.id, exc) from exc
                if rate_limited:
                    # The limit will not lift within this run; stop instead of spending more calls.
                    limited[label.id] = rate_limited
                    break
                if result is not None:
                    results[label.id] = result
        finally:
            _close_everything(reviews)

    if limited:
        _report_rate_limit(limited)
        return EXIT_RATE_LIMITED

    if prepare_only:
        counts: dict[Mode, list[dict[str, int]]] = {"before": [], "after": []}
        langsmith_status = "skipped"
    else:
        counts, langsmith_status = _evaluate_both(
            settings, results, selected, labels_bytes, partial=partial
        )

    before = mode_metrics(sum_counts(counts["before"]))
    after = mode_metrics(sum_counts(counts["after"]))
    runs = [results[label.id].run for label in selected]
    contracts = contract_metrics(runs, provider=settings.llm_provider, model=settings.llm_model)
    thresholds = evaluate_thresholds(after, contracts)

    started_iso = started_at.isoformat(timespec="seconds")
    path = results_dir / result_filename(
        started_at,
        settings.llm_provider,
        settings.llm_model,
        partial=partial,
        prepare_only=prepare_only,
    )
    rules = [
        rule_json(b, a)
        for label in selected
        for b, a in zip(
            results[label.id].rows["before"], results[label.id].rows["after"], strict=True
        )
    ]
    _write_results(
        path,
        {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "started_at": started_iso,
            "mode": "prepare_only" if prepare_only else "full",
            "contracts_run": len(selected),
            "contracts_total": len(labels),
            "partial": partial,
            "max_concurrency": settings.max_concurrency,
            "langsmith": langsmith_status,
            "metrics": {
                "before": mode_metrics_json(before),
                "after": mode_metrics_json(after),
                "contracts": contract_metrics_json(contracts),
            },
            "thresholds": thresholds_json(thresholds),
            "contracts": [contract_run_json(run) for run in runs],
            "rules": rules,
        },
    )
    header = TableHeader(
        provider=settings.llm_provider,
        model=settings.llm_model,
        started_at=started_iso,
        contracts_run=len(selected),
        contracts_total=len(labels),
        mode="prepare_only" if prepare_only else "full",
    )
    print(render_table(header, before, after, contracts, thresholds, str(path)), end="")
    return EXIT_OK if thresholds_passed(thresholds) else EXIT_THRESHOLD_FAILED


def run_or_report(
    settings: Settings,
    *,
    limit: int | None,
    prepare_only: bool,
    results_dir: Path,
    started_at: datetime,
    model: BaseChatModel | None = None,
) -> int:
    """`run_eval`, with a crash reported on stderr (class name and contract only) as exit 1."""
    try:
        return run_eval(
            settings,
            limit=limit,
            prepare_only=prepare_only,
            results_dir=results_dir,
            started_at=started_at,
            model=model,
        )
    except ContractRunError as exc:
        kind = "sağlayıcı kimlik/yapılandırma hatası" if exc.provider_config else "koşu hatası"
        detail = f": {exc.detail}" if exc.detail else ""
        print(
            f"Eval durdu ({kind}): sözleşme={exc.contract_id}, hata={exc.error_name}{detail}",
            file=sys.stderr,
        )
    except EvalError as exc:
        print(f"Eval durdu: {exc}", file=sys.stderr)
    except Exception as exc:
        # Only the class name: an error message may carry contract text.
        print(f"Eval durdu: hata={type(exc).__name__}", file=sys.stderr)
    return EXIT_ERROR


# --- Command line ---------------------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    """Argument errors exit with 1 (configuration), not argparse's 2 (2 means rate limited)."""

    def error(self, message: str) -> Any:
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: hata: {message}\n")


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("bir tam sayı olmalı") from None
    if value < 1:
        raise argparse.ArgumentTypeError("en az 1 olmalı")
    return value


def parse_args(argv: Sequence[str] | None, contracts_total: int) -> argparse.Namespace:
    parser = _Parser(
        prog="python -m evals.run",
        description="Eval: önce/sonra tablosu ve sonuç dosyası (docs/evals.md).",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help=f"labels.json sırasındaki ilk N sözleşmeyi koş (1-{contracts_total}).",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Model çağırma; yalnızca hazırlık, PII ve enjeksiyon taraması ölç.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=DEFAULT_RESULTS_DIR,
        help="Sonuç dosyasının yazılacağı klasör (varsayılan: evals/results).",
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit > contracts_total:
        parser.error(f"--limit en fazla {contracts_total} olabilir.")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    _, labels = load_labels()
    args = parse_args(argv, len(labels))
    settings = load_settings_or_exit()
    return run_or_report(
        settings,
        limit=args.limit,
        prepare_only=args.prepare_only,
        results_dir=args.results_dir,
        started_at=datetime.now(UTC),
    )


def _utf8_stdio() -> None:
    # The table is Turkish; a Windows pipe would otherwise use the ANSI code page.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


if __name__ == "__main__":
    _utf8_stdio()
    sys.exit(main())
