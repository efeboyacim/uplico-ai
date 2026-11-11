"""The rule agent's five tools, its state and its per-run context.

Design (`docs/agent_design.md` §2):

- Context (store, review, rule, index) comes from `ToolRuntime.context`; no tool takes a tenant,
  review or rule argument, and no tool touches the database (the audit middleware does that).
- The docstring of each tool is the whole description the model sees, so it is exactly the
  Turkish text of the §2 tables and nothing else.
- Contract text reaches the model only here, between `<sozlesme_maddesi>` tags, verbatim: it is
  not escaped and a tag inside it is not removed. Each snippet and clause is followed by the
  tool's own closing tag.
- Expected errors (unknown clause, missing evidence) are returned, never raised, as
  `ToolMessage(status="error")`; they write no state, so an earlier valid record stays.
- The record tools write state with `Command(update=...)`. Their state fields keep the last
  write, so two calls in one model message do not crash the run.
"""

from dataclasses import dataclass
from typing import Annotated, Any, NotRequired

from langchain.agents import AgentState
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command
from pydantic import ValidationError

from src.models import Clause, Finding, FindingStatus, Proposal, Rule
from src.prepare.index import ClauseIndex
from src.store import Store

OPEN_TAG = "<sozlesme_maddesi>"
CLOSE_TAG = "</sozlesme_maddesi>"
NO_CLAUSE_NO = "-"


@dataclass(frozen=True)
class ReviewContext:
    """Per-run context: everything the tools and the audit middleware need from code."""

    store: Store
    review_id: str
    rule: Rule
    index: ClauseIndex


def _last[T](_old: T, new: T) -> T:
    """Reducer: the last write wins (several writes in one step keep call order)."""
    return new


class RuleAgentState(AgentState):
    finding: NotRequired[Annotated[Finding | None, _last]]
    proposal: NotRequired[Annotated[Proposal | None, _last]]
    escalation: NotRequired[Annotated[str | None, _last]]


def _tagged(text: str) -> str:
    return f"{OPEN_TAG}\n{text}\n{CLOSE_TAG}"


def _error(runtime: ToolRuntime[ReviewContext], text: str) -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=runtime.tool_call_id or "", status="error")


def _unknown_clause(runtime: ToolRuntime[ReviewContext], clause_id: str) -> ToolMessage:
    return _error(runtime, f"{clause_id} diye bir madde yok; `search_contract` kullan.")


def _lookup(runtime: ToolRuntime[ReviewContext], clause_id: str) -> Clause | None:
    return runtime.context.index.get(clause_id)


def _validation_message(exc: ValidationError) -> str:
    """The first error's own text: a validator's ValueError message without pydantic's prefix."""
    error = exc.errors()[0]
    cause = error.get("ctx", {}).get("error")
    message = str(cause) if cause is not None else str(error["msg"])
    return " ".join(message.split())


def _recorded(
    runtime: ToolRuntime[ReviewContext], key: str, value: object, text: str
) -> Command[Any]:
    message = ToolMessage(content=text, tool_call_id=runtime.tool_call_id or "")
    return Command(update={key: value, "messages": [message]})


@tool(response_format="content_and_artifact")
def search_contract(query: str, runtime: ToolRuntime[ReviewContext]) -> tuple[str, list[str]]:
    """Kuralla ilgili maddeleri bulmak için sözleşmede ara. İlk adım olarak kullan."""
    hits = runtime.context.index.search(query)
    if not hits:
        return "Bu aramayla eşleşen madde yok; başka sözcüklerle ara.", []
    blocks = [
        f"clause_id={hit.clause_id} madde_no={hit.clause_no or NO_CLAUSE_NO}\n"
        + _tagged(hit.snippet)
        for hit in hits
    ]
    return "\n\n".join(blocks), [hit.clause_id for hit in hits]


@tool
def get_clause(clause_id: str, runtime: ToolRuntime[ReviewContext]) -> str | ToolMessage:
    """Bir maddenin tam metnini oku. Alıntı yapmadan önce mutlaka kullan."""
    clause = _lookup(runtime, clause_id)
    if clause is None:
        return _unknown_clause(runtime, clause_id)
    return _tagged(clause.text)


@tool
def submit_finding(
    status: FindingStatus,
    reasoning: str,
    runtime: ToolRuntime[ReviewContext],
    clause_id: str | None = None,
    quote: str | None = None,
    value: float | None = None,
    unit: str | None = None,
) -> Command[Any] | ToolMessage:
    """Kural için sonucunu bildir. Görev başına bir kez."""
    try:
        finding = Finding(
            rule_id=runtime.context.rule.id,
            status=status,
            clause_id=clause_id,
            quote=quote,
            value=value,
            unit=unit,
            reasoning=reasoning,
        )
    except ValidationError as exc:
        return _error(runtime, _validation_message(exc))
    if clause_id is not None and _lookup(runtime, clause_id) is None:
        return _unknown_clause(runtime, clause_id)
    return _recorded(runtime, "finding", finding, "Bulgu kaydedildi.")


@tool
def propose_change(
    clause_id: str, proposed_text: str, rationale: str, runtime: ToolRuntime[ReviewContext]
) -> Command[Any] | ToolMessage:
    """Uygun olmayan madde için yeni metin taslağı öner."""
    try:
        proposal = Proposal(clause_id=clause_id, proposed_text=proposed_text, rationale=rationale)
    except ValidationError as exc:
        return _error(runtime, _validation_message(exc))
    if _lookup(runtime, clause_id) is None:
        return _unknown_clause(runtime, clause_id)
    return _recorded(runtime, "proposal", proposal, "Taslak kaydedildi.")


@tool
def escalate_to_lawyer(reason: str, runtime: ToolRuntime[ReviewContext]) -> Command[Any]:
    """Emin değilsen ya da madde belirsizse avukata bırak."""
    return _recorded(runtime, "escalation", reason, "Avukata devredildi.")


TOOLS: tuple[BaseTool, ...] = (
    search_contract,
    get_clause,
    submit_finding,
    propose_change,
    escalate_to_lawyer,
)
