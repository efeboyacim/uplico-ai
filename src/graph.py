"""The review graph: one rule agent per rule (`Send`), code verification, one lawyer interrupt.

Design (`docs/agent_design.md` §1, §4, §6, §7):

- `START → review_rule per rule (Send) → verify → human_review (interrupt) → END`. Preparation runs
  before the graph; only masked clauses enter it. `finalize` comes in a later step.
- The state holds plain dicts (`model_dump(mode="json")`), never pydantic objects: the checkpoint
  serializer reads registered types only. Nodes validate what they read.
- The clause index is never in the state or in a `Send` payload; `review_rule` takes it from the
  process registry with `get_index(tenant_id, review_id)`.
- Errors: only a model call error reaches `review_rule` as `RuleRunInterrupted`; it is classified
  by its cause. A `config` class provider error is re-raised (the review fails); a rate limit or
  any other provider error becomes the rule's finding. Every other error (code, audit write)
  propagates and fails the whole review.
- The graph is compiled once per `(data_dir, tenant_id)` with the tenant's own `checkpoints.db`.
- Everything is synchronous: `invoke` only; parallelism is `Send` + `max_concurrency`.
"""

import operator
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, NotRequired, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send, interrupt
from pydantic import TypeAdapter

from src.agent import (
    RuleAgent,
    RuleRunInterrupted,
    build_rule_agent,
    interrupted_finding,
    run_rule_agent,
)
from src.config import Settings
from src.llm import build_chat_model, classify_provider_error
from src.models import Clause, Finding, InterruptReason, Rule
from src.prepare import PreparedContract
from src.prepare.index import get_index
from src.store import Store
from src.tools import ReviewContext
from src.verify import verify

CHECKPOINTS_NAME = "checkpoints.db"
AGENT_ACTOR = "agent"
SYSTEM_ACTOR = "system"

_RULES = TypeAdapter(list[Rule])
_CLAUSES = TypeAdapter(list[Clause])
_FINDINGS = TypeAdapter(list[Finding])


class ReviewState(TypedDict):
    review_id: str
    rules: list[dict[str, Any]]
    clauses: list[dict[str, Any]]
    injection_suspected: bool
    # Raw findings of the rule agents, one per rule, collected from the branches in any order.
    findings: Annotated[list[dict[str, Any]], operator.add]
    # The verified findings, in the rule order of the rule set.
    verified: NotRequired[list[dict[str, Any]]]


class RuleTask(TypedDict):
    """The `Send` payload of one `review_rule` branch."""

    review_id: str
    rule: dict[str, Any]


ReviewGraph = CompiledStateGraph[ReviewState, None, ReviewState, ReviewState]


# --- Invoke helpers -------------------------------------------------------------------------------


def initial_state(prepared: PreparedContract, rules: Sequence[Rule]) -> ReviewState:
    """The graph input for a prepared contract and the tenant's rules."""
    if not rules:
        raise ValueError("Kural seti boş olamaz.")
    return ReviewState(
        review_id=prepared.review_id,
        rules=[rule.model_dump(mode="json") for rule in rules],
        clauses=[clause.model_dump(mode="json") for clause in prepared.clauses],
        injection_suspected=bool(prepared.injection_flags),
        findings=[],
    )


def review_config(settings: Settings, review_id: str) -> RunnableConfig:
    """The invoke config of a review: its thread and the branch concurrency limit."""
    return {
        "configurable": {"thread_id": review_id},
        "max_concurrency": settings.max_concurrency,
    }


# --- Nodes ----------------------------------------------------------------------------------------


def _fan_out(state: ReviewState) -> list[Send]:
    return [
        Send("review_rule", RuleTask(review_id=state["review_id"], rule=rule))
        for rule in state["rules"]
    ]


def _interrupt_reason(cause: BaseException) -> InterruptReason:
    """The finding's reason for a provider error; a `config` class error is re-raised."""
    kind = classify_provider_error(cause)
    if kind == "config":
        raise cause
    return "rate_limited" if kind == "rate_limit" else "llm_error"


def _ordered(findings: Sequence[Finding], rules: Sequence[Rule]) -> list[Finding]:
    """The findings in rule order; exactly one finding per rule, or an error."""
    by_rule: dict[str, list[Finding]] = {rule.id: [] for rule in rules}
    for finding in findings:
        if finding.rule_id not in by_rule:
            raise RuntimeError(f"Kural setinde olmayan kural için bulgu: {finding.rule_id!r}.")
        by_rule[finding.rule_id].append(finding)
    for rule_id, found in by_rule.items():
        if len(found) != 1:
            raise RuntimeError(f"{rule_id!r} kuralı için {len(found)} bulgu var; tam bir olmalı.")
    return [by_rule[rule.id][0] for rule in rules]


class _Nodes:
    """The nodes that need the tenant's store or the rule agent."""

    def __init__(self, store: Store, agent: RuleAgent) -> None:
        self._store = store
        self._agent = agent

    def review_rule(self, state: RuleTask) -> dict[str, Any]:
        """Run one rule's agent and record its finding; a code error propagates."""
        rule = Rule.model_validate(state["rule"])
        review_id = state["review_id"]
        index = get_index(self._store.tenant_id, review_id)
        if index is None:
            raise LookupError("İncelemenin madde indeksi bulunamadı.")
        context = ReviewContext(self._store, review_id, rule, index)
        try:
            finding = run_rule_agent(self._agent, context)
        except RuleRunInterrupted as exc:
            cause = exc.__cause__
            if cause is None:
                raise
            finding = interrupted_finding(rule.id, exc.finding, _interrupt_reason(cause))
        payload = finding.model_dump(mode="json")
        self._store.append_audit(review_id, AGENT_ACTOR, "finding", payload)
        return {"findings": [payload]}

    def verify(self, state: ReviewState) -> dict[str, Any]:
        """Verify the findings in rule order and record one `verification` row per finding."""
        rules = _RULES.validate_python(state["rules"])
        findings = _ordered(_FINDINGS.validate_python(state["findings"]), rules)
        clauses = _CLAUSES.validate_python(state["clauses"])
        verified = verify(findings, rules, clauses, state["injection_suspected"])
        payloads = [
            {
                "rule_id": finding.rule_id,
                "needs_review": finding.needs_review,
                "review_reasons": list(finding.review_reasons),
                "code_check": finding.proposal.code_check if finding.proposal else None,
            }
            for finding in verified
        ]
        self._store.append_audit_many(state["review_id"], SYSTEM_ACTOR, "verification", payloads)
        return {"verified": [finding.model_dump(mode="json") for finding in verified]}


def _human_review(state: ReviewState) -> dict[str, Any]:
    # No side effect before interrupt(): on resume this node runs again from its start.
    interrupt({"review_id": state["review_id"]})
    return {}


def _compile(store: Store, model: BaseChatModel, saver: SqliteSaver) -> ReviewGraph:
    nodes = _Nodes(store, build_rule_agent(model))
    builder = StateGraph(ReviewState)
    builder.add_node("review_rule", nodes.review_rule)
    builder.add_node("verify", nodes.verify)
    builder.add_node("human_review", _human_review)
    builder.add_conditional_edges(START, _fan_out, ["review_rule"])
    builder.add_edge("review_rule", "verify")
    builder.add_edge("verify", "human_review")
    builder.add_edge("human_review", END)
    return builder.compile(checkpointer=saver)


# --- Per-tenant cache -----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Entry:
    graph: ReviewGraph
    model: BaseChatModel
    conn: sqlite3.Connection


_cache: dict[tuple[Path, str], _Entry] = {}
_cache_lock = threading.Lock()


def get_review_graph(
    settings: Settings, tenant_id: str, *, model: BaseChatModel | None = None
) -> ReviewGraph:
    """The tenant's compiled review graph, compiled on first use and cached.

    The cache key is `(resolved data_dir, tenant_id)`. Without `model` the chat model is built
    from `settings` on first use. A `model` (tests) is used when the graph is compiled; asking
    for a graph already compiled with another model raises `ValueError`. An unknown tenant raises
    `UnknownTenantError` before any path is built.
    """
    store = Store(settings.data_dir, tenant_id)
    key = (Path(settings.data_dir).resolve(), tenant_id)
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None:
            if model is not None and model is not entry.model:
                raise ValueError("Bu tenant'ın graph'ı başka bir modelle derlenmiş.")
            return entry.graph
        chat_model = model if model is not None else build_chat_model(settings)
        conn = sqlite3.connect(store.tenant_dir / CHECKPOINTS_NAME, check_same_thread=False)
        try:
            graph = _compile(store, chat_model, SqliteSaver(conn))
        except BaseException:
            conn.close()
            raise
        _cache[key] = _Entry(graph=graph, model=chat_model, conn=conn)
        return graph


def close_graphs() -> None:
    """Close every checkpoint connection and clear the cache.

    For tests: on Windows an open connection keeps the files under tmp_path locked.
    """
    with _cache_lock:
        for entry in _cache.values():
            entry.conn.close()
        _cache.clear()
