"""The rule agent: `create_agent` with the five tools, audit middleware and a model-call limit.

Design (`docs/agent_design.md` §1, §6, §7):

- One agent per rule; the system prompt is `src/prompts/rule_agent.md`, the rule text is the
  user message, contract text reaches the model only through the tools.
- The agent never writes to a checkpoint (`checkpointer=False`); otherwise it would inherit the
  parent graph's saver and store every rule's message history under its own namespace.
- Two middlewares write the audit log: one `model_call` row per model call (a failed call too,
  with the error class and status code but never the message text) and one `tool_call` row per
  tool call. An audit write error (`sqlite3.Error`) is never caught.
- `run_rule_agent` streams the run so the last state survives an error, and turns the final
  state into exactly one finding (the table in §6 "Koşu kendiliğinden bitmediyse"). Provider
  errors are classified by the caller, not here.
"""

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    InputAgentState,
    ModelCallLimitMiddleware,
    ModelRequest,
    ModelResponse,
    OutputAgentState,
)
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from src.models import Finding, InterruptReason, Proposal
from src.tools import TOOLS, ReviewContext, RuleAgentState

RUN_LIMIT = 8
RESULT_CHARS = 300
ACTOR = "agent"

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "rule_agent.md"
_ROLES = {"human": "user", "ai": "assistant", "tool": "tool", "system": "system"}

RuleAgent = CompiledStateGraph[
    AgentState[Any], ReviewContext, InputAgentState, OutputAgentState[Any]
]


class RuleRunInterrupted(Exception):
    """The run raised before it ended by itself; the original error is `__cause__`.

    `finding` is the last submitted finding with the proposal and escalation from the same run
    merged in, not yet marked as interrupted (None when nothing was submitted).
    """

    def __init__(self, finding: Finding | None) -> None:
        super().__init__("Kural agent'ının koşusu hatayla yarıda kesildi.")
        self.finding = finding


# --- Audit middleware -----------------------------------------------------------------------------


def _message_payload(message: BaseMessage) -> dict[str, object]:
    payload: dict[str, object] = {
        "role": _ROLES.get(message.type, message.type),
        "content": message.content,
    }
    if isinstance(message, AIMessage) and message.tool_calls:
        payload["tool_calls"] = [dict(call) for call in message.tool_calls]
    if isinstance(message, ToolMessage):
        payload["tool_call_id"] = message.tool_call_id
    return payload


def _response_payload(response: ModelResponse[Any]) -> tuple[dict[str, object], object]:
    ai = next((m for m in response.result if isinstance(m, AIMessage)), None)
    if ai is None:
        return {"content": None, "tool_calls": [], "invalid_tool_calls": []}, None
    body: dict[str, object] = {
        "content": ai.content,
        "tool_calls": [dict(call) for call in ai.tool_calls],
        "invalid_tool_calls": [dict(call) for call in ai.invalid_tool_calls],
    }
    usage = dict(ai.usage_metadata) if ai.usage_metadata is not None else None
    return body, usage


def _error_payload(exc: Exception) -> dict[str, object]:
    """The error class and, when the exception carries one, the provider's status code.

    The message text is not written: it may echo the request.
    """
    payload: dict[str, object] = {"type": type(exc).__name__}
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        payload["code"] = code
    return payload


class ModelCallAudit(AgentMiddleware[AgentState[Any], ReviewContext]):
    """One `model_call` audit row per model call, written after the call returns or fails."""

    def wrap_model_call(
        self,
        request: ModelRequest[ReviewContext],
        handler: Callable[[ModelRequest[ReviewContext]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        context = request.runtime.context
        payload: dict[str, object] = {
            "rule_id": context.rule.id,
            "system_prompt": request.system_prompt,
            "messages": [_message_payload(m) for m in request.messages],
        }
        try:
            response = handler(request)
        except Exception as exc:
            # The masked text has already left: the call is recorded, then the error goes on.
            payload |= {"response": None, "usage_metadata": None, "error": _error_payload(exc)}
            context.store.append_audit(context.review_id, ACTOR, "model_call", payload)
            raise
        body, usage = _response_payload(response)
        payload |= {"response": body, "usage_metadata": usage}
        context.store.append_audit(context.review_id, ACTOR, "model_call", payload)
        return response


def _tool_message(result: ToolMessage | Command[Any], tool_call_id: str | None) -> ToolMessage:
    """The ToolMessage of a tool result; a record tool returns it inside its Command."""
    if isinstance(result, ToolMessage):
        return result
    update = result.update if isinstance(result.update, Mapping) else {}
    messages: Sequence[object] = update.get("messages", [])
    for message in messages:
        if isinstance(message, ToolMessage) and message.tool_call_id == tool_call_id:
            return message
    raise ValueError("Tool sonucu, çağrının ToolMessage'ını içermiyor.")


def _content_text(content: str | list[str | dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    return "".join(part if isinstance(part, str) else str(part.get("text", "")) for part in content)


class ToolCallAudit(AgentMiddleware[AgentState[Any], ReviewContext]):
    """One `tool_call` audit row per tool call, schema errors and unknown tool names included.

    Tool calls of one model message run concurrently, so their rows are in completion order.
    """

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        result = handler(request)
        call = request.tool_call
        message = _tool_message(result, call["id"])
        context = request.runtime.context
        if not isinstance(context, ReviewContext):
            raise TypeError("Kural agent'ı ReviewContext ile çalıştırılmalı.")
        payload: dict[str, object] = {
            "rule_id": context.rule.id,
            "name": call["name"],
            "args": call["args"],
            "status": message.status,
            "result": _content_text(message.content)[:RESULT_CHARS],
        }
        if call["name"] == "search_contract":
            artifact = message.artifact if isinstance(message.artifact, list) else []
            payload["clause_ids"] = [str(clause_id) for clause_id in artifact]
        context.store.append_audit(context.review_id, ACTOR, "tool_call", payload)
        return result


# --- Building and running -------------------------------------------------------------------------


def build_rule_agent(model: BaseChatModel) -> RuleAgent:
    middleware: list[AgentMiddleware[Any, ReviewContext]] = [
        ModelCallAudit(),
        ToolCallAudit(),
        ModelCallLimitMiddleware(run_limit=RUN_LIMIT, exit_behavior="end"),
    ]
    return create_agent(
        model,
        list(TOOLS),
        system_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        middleware=middleware,
        state_schema=RuleAgentState,
        context_schema=ReviewContext,
        checkpointer=False,
    )


def interrupted_finding(rule_id: str, partial: Finding | None, reason: InterruptReason) -> Finding:
    """The finding of a run that did not end by itself.

    A submitted finding is kept and marked (`interrupted_by`); without one the rule could not be
    reviewed (`no_finding_reason`).
    """
    if partial is not None:
        return partial.model_copy(deep=True, update={"interrupted_by": reason})
    return Finding(rule_id=rule_id, status="not_found", no_finding_reason=reason)


def _submitted(state: Mapping[str, Any]) -> Finding | None:
    """The last submitted finding with the run's proposal and escalation merged in, as a copy."""
    finding = state.get("finding")
    if not isinstance(finding, Finding):
        return None
    proposal = state.get("proposal")
    escalation = state.get("escalation")
    return finding.model_copy(
        deep=True,
        update={
            "proposal": proposal.model_copy(deep=True) if isinstance(proposal, Proposal) else None,
            "escalation_reason": escalation if isinstance(escalation, str) else None,
        },
    )


def run_rule_agent(agent: RuleAgent, context: ReviewContext) -> Finding:
    """Run the rule agent once and return exactly one finding (agent_design.md §6).

    Ended by itself: the submitted finding, or `no_finding` (`no_submit`). Stopped by the step
    limit (more AI messages than `RUN_LIMIT`, the limit's own last message included): the
    submitted finding marked `step_limit`, or `no_finding` (`step_limit`). A `no_finding`
    finding never carries the run's proposal or escalation. An error during the run is raised
    as `RuleRunInterrupted` holding the submitted finding, except `sqlite3.Error` from an audit
    write, which propagates as it is.
    """
    rule_id = context.rule.id
    state: Mapping[str, Any] = {}
    message = HumanMessage(f"Kural: {context.rule.text}")
    try:
        for value in agent.stream({"messages": [message]}, context=context, stream_mode="values"):
            state = value
    except sqlite3.Error:
        raise
    except Exception as exc:
        raise RuleRunInterrupted(_submitted(state)) from exc
    submitted = _submitted(state)
    ai_messages = sum(isinstance(m, AIMessage) for m in state.get("messages", []))
    if ai_messages > RUN_LIMIT:
        return interrupted_finding(rule_id, submitted, "step_limit")
    if submitted is None:
        return Finding(rule_id=rule_id, status="not_found", no_finding_reason="no_submit")
    return submitted
