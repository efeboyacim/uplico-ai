import json
import sqlite3
from pathlib import Path
from typing import Any, TypedDict

import groq
import httpx
import pytest
from conftest import (
    CLAUSES,
    NOTICE_RULE,
    ScriptedChatModel,
    ai,
    broken,
    call,
    done,
    make_context,
)
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from src.agent import (
    RUN_LIMIT,
    RuleRunInterrupted,
    build_rule_agent,
    interrupted_finding,
    run_rule_agent,
)
from src.models import Finding, Proposal
from src.tools import CLOSE_TAG, OPEN_TAG, ReviewContext
from src.verify import verify

PROMPT = (Path(__file__).resolve().parent.parent / "src" / "prompts" / "rule_agent.md").read_text(
    encoding="utf-8"
)
QUOTE = "30 gün önceden"


def _submit(status: str = "compliant", **kwargs: Any) -> Any:
    args: dict[str, Any] = {"status": status, "reasoning": "Gerekçe."}
    if status != "not_found":
        args |= {"clause_id": "c2", "quote": QUOTE, "value": 30, "unit": "gün"}
    return call("submit_finding", **(args | kwargs))


def _search(query: str = "fesih bildirim") -> Any:
    return call("search_contract", query=query)


def _run(context: ReviewContext, model: ScriptedChatModel) -> Finding:
    return run_rule_agent(build_rule_agent(model), context)


def _rows(context: ReviewContext, event: str) -> list[dict[str, Any]]:
    rows = context.store.list_audit(context.review_id)
    return [r.payload for r in rows if r.event == event]


def _verified(finding: Finding, context: ReviewContext) -> Finding:
    clauses = [context.index.get(cid) for cid in CLAUSES]
    [result] = verify([finding], [NOTICE_RULE], [c for c in clauses if c is not None], False)
    return result


def _expected_finding(**kwargs: Any) -> Finding:
    return Finding(
        rule_id=NOTICE_RULE.id,
        status="compliant",
        clause_id="c2",
        quote=QUOTE,
        value=30,
        unit="gün",
        reasoning="Gerekçe.",
        **kwargs,
    )


# --- Onay yok -------------------------------------------------------------------------------------


def test_compiled_agent_tool_node_exactly_five_tools() -> None:
    agent = build_rule_agent(ScriptedChatModel.single())
    assert sorted(agent.nodes["tools"].bound.tools_by_name) == sorted(
        ["search_contract", "get_clause", "submit_finding", "propose_change", "escalate_to_lawyer"]
    )


# --- Scripted run, audit, prompt ------------------------------------------------------------------


def test_scripted_run_finding_with_proposal_and_escalation(data_dir: Path) -> None:
    context = make_context(data_dir)
    model = ScriptedChatModel.single(
        ai(_search()),
        ai(call("get_clause", clause_id="c2")),
        ai(_submit("non_compliant", quote="30 gün önceden yazılı", value=None, unit=None)),
        ai(call("propose_change", clause_id="c2", proposed_text="60 gün önceden", rationale="g")),
        ai(call("escalate_to_lawyer", reason="Emin değilim.")),
        done(),
    )
    finding = _run(context, model)
    assert finding == Finding(
        rule_id=NOTICE_RULE.id,
        status="non_compliant",
        clause_id="c2",
        quote="30 gün önceden yazılı",
        reasoning="Gerekçe.",
        proposal=Proposal(clause_id="c2", proposed_text="60 gün önceden", rationale="g"),
        escalation_reason="Emin değilim.",
    )
    assert finding.interrupted_by is None
    assert finding.no_finding_reason is None
    assert model.calls_left() == 0


def test_audit_model_call_rows_prompt_messages_usage(data_dir: Path) -> None:
    context = make_context(data_dir)
    model = ScriptedChatModel.single(ai(_search()), ai(_submit()), done())
    _run(context, model)
    rows = _rows(context, "model_call")
    assert len(rows) == 3
    for row, received in zip(rows, model.received(), strict=True):
        assert row["rule_id"] == NOTICE_RULE.id
        assert row["system_prompt"] == PROMPT
        # Every message the model got except the system message, in order.
        sent = [m for m in received if not isinstance(m, SystemMessage)]
        assert [m["content"] for m in row["messages"]] == [m.content for m in sent]
        assert row["usage_metadata"] == {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
        }
        assert "error" not in row
    first, second, third = rows
    assert first["messages"] == [{"role": "user", "content": f"Kural: {NOTICE_RULE.text}"}]
    assert [c["name"] for c in first["response"]["tool_calls"]] == ["search_contract"]
    tool_msg = second["messages"][-1]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == first["response"]["tool_calls"][0]["id"]
    assert second["messages"][1]["tool_calls"] == first["response"]["tool_calls"]
    assert third["response"] == {"content": "Bitti.", "tool_calls": [], "invalid_tool_calls": []}


def test_audit_tool_call_rows_status_result_clause_ids(data_dir: Path) -> None:
    long_text = "Fesih bildirimi " + "uzun metin " * 60
    clauses = dict(CLAUSES) | {"c4": ("7", long_text)}
    context = make_context(data_dir, clauses=clauses)
    model = ScriptedChatModel.single(
        ai(_search("fesih bildirim")),
        ai(call("get_clause", clause_id="c4")),
        ai(call("get_clause", clause_id="c99")),
        done(),
    )
    _run(context, model)
    search, read, missing = _rows(context, "tool_call")
    assert search["rule_id"] == NOTICE_RULE.id
    assert search["name"] == "search_contract"
    assert search["args"] == {"query": "fesih bildirim"}
    assert search["status"] == "success"
    assert search["clause_ids"] == [h.clause_id for h in context.index.search("fesih bildirim")]
    assert set(search["clause_ids"]) == {"c2", "c4"}
    assert read["status"] == "success"
    assert read["result"] == f"{OPEN_TAG}\n{long_text}"[:300]
    assert "clause_ids" not in read
    assert missing["status"] == "error"
    assert missing["args"] == {"clause_id": "c99"}


def test_system_prompt_no_contract_text_contract_only_in_tagged_tool_results(
    data_dir: Path,
) -> None:
    context = make_context(data_dir)
    model = ScriptedChatModel.single(
        ai(_search("bildirim ödeme Tedarikçi")),
        ai(call("get_clause", clause_id="c3")),
        ai(_submit()),
        done(),
    )
    _run(context, model)
    texts = [text for _, text in CLAUSES.values()]
    for received in model.received():
        system, human = received[0], received[1]
        assert isinstance(system, SystemMessage)
        assert system.content == PROMPT
        assert isinstance(human, HumanMessage)
        for message in received:
            content = str(message.content)
            for text in texts:
                if text in content:
                    assert isinstance(message, ToolMessage)
                    assert f"{OPEN_TAG}\n{text}\n{CLOSE_TAG}" in content
    last = model.received()[-1]
    seen = "".join(str(m.content) for m in last if isinstance(m, ToolMessage))
    assert all(text in seen for text in texts)
    for row in _rows(context, "model_call"):
        assert not any(text in row["system_prompt"] for text in texts)


# --- 8-call limit ---------------------------------------------------------------------------------


def test_run_ending_by_itself_on_call_8_no_submit(data_dir: Path) -> None:
    context = make_context(data_dir)
    model = ScriptedChatModel.single(*[ai(_search()) for _ in range(RUN_LIMIT - 1)], done())
    finding = _run(context, model)
    assert finding == Finding(
        rule_id=NOTICE_RULE.id, status="not_found", no_finding_reason="no_submit"
    )
    assert len(_rows(context, "model_call")) == RUN_LIMIT
    assert model.calls_left() == 0


def test_eight_tool_calling_turns_step_limit(data_dir: Path) -> None:
    context = make_context(data_dir)
    model = ScriptedChatModel.single(*[ai(_search()) for _ in range(RUN_LIMIT)])
    finding = _run(context, model)
    assert finding == Finding(
        rule_id=NOTICE_RULE.id, status="not_found", no_finding_reason="step_limit"
    )
    assert len(_rows(context, "model_call")) == RUN_LIMIT
    assert len(model.received()) == RUN_LIMIT


def test_finding_then_ending_by_itself_on_call_8_not_marked(data_dir: Path) -> None:
    context = make_context(data_dir)
    turns = [ai(_submit()), *[ai(_search()) for _ in range(RUN_LIMIT - 2)], done()]
    model = ScriptedChatModel.single(*turns)
    finding = _run(context, model)
    assert finding == _expected_finding()
    assert len(_rows(context, "model_call")) == RUN_LIMIT
    assert _verified(finding, context).review_reasons == []


def test_finding_then_step_limit_kept_marked_and_queued(data_dir: Path) -> None:
    context = make_context(data_dir)
    turns = [
        ai(_submit()),
        ai(call("propose_change", clause_id="c2", proposed_text="60 gün önceden", rationale="g")),
        ai(call("escalate_to_lawyer", reason="Belirsiz.")),
        *[ai(_search()) for _ in range(RUN_LIMIT - 3)],
    ]
    model = ScriptedChatModel.single(*turns)
    finding = _run(context, model)
    assert finding == _expected_finding(
        proposal=Proposal(clause_id="c2", proposed_text="60 gün önceden", rationale="g"),
        escalation_reason="Belirsiz.",
        interrupted_by="step_limit",
    )
    assert len(_rows(context, "model_call")) == RUN_LIMIT
    verified = _verified(finding, context)
    assert verified.needs_review is True
    assert "run_interrupted" in verified.review_reasons


# --- Errors during the run ------------------------------------------------------------------------


def _bad_request() -> groq.BadRequestError:
    request = httpx.Request("POST", "https://api.groq.com")
    body = {"error": {"message": "gizli istek metni", "type": "invalid_request_error"}}
    response = httpx.Response(400, request=request, json=body)
    return groq.BadRequestError("Error code: 400 - gizli istek metni", response=response, body=body)


def test_model_error_after_finding_finding_kept_and_marked(data_dir: Path) -> None:
    context = make_context(data_dir)
    error = _bad_request()
    model = ScriptedChatModel.single(
        ai(_submit()),
        ai(call("propose_change", clause_id="c2", proposed_text="60 gün önceden", rationale="g")),
        error,
    )
    with pytest.raises(RuleRunInterrupted) as info:
        _run(context, model)
    assert info.value.__cause__ is error
    proposal = Proposal(clause_id="c2", proposed_text="60 gün önceden", rationale="g")
    assert info.value.finding == _expected_finding(proposal=proposal)
    marked = interrupted_finding(NOTICE_RULE.id, info.value.finding, "llm_error")
    assert marked == _expected_finding(proposal=proposal, interrupted_by="llm_error")
    verified = _verified(marked.model_copy(update={"proposal": None}), context)
    assert verified.review_reasons == ["run_interrupted"]
    assert verified.needs_review is True


def test_model_error_before_finding_no_finding(data_dir: Path) -> None:
    context = make_context(data_dir)
    error = RuntimeError("bağlantı koptu")
    model = ScriptedChatModel.single(
        ai(call("escalate_to_lawyer", reason="Belirsiz.")),
        error,
    )
    with pytest.raises(RuleRunInterrupted) as info:
        _run(context, model)
    assert info.value.__cause__ is error
    assert info.value.finding is None
    assert interrupted_finding(NOTICE_RULE.id, None, "rate_limited") == Finding(
        rule_id=NOTICE_RULE.id, status="not_found", no_finding_reason="rate_limited"
    )


def test_interrupted_finding_returns_copy() -> None:
    partial = _expected_finding()
    marked = interrupted_finding(NOTICE_RULE.id, partial, "step_limit")
    assert marked is not partial
    assert partial.interrupted_by is None
    assert marked == _expected_finding(interrupted_by="step_limit")


def test_model_error_written_to_model_call_row(data_dir: Path) -> None:
    context = make_context(data_dir)
    model = ScriptedChatModel.single(ai(_search()), _bad_request())
    with pytest.raises(RuleRunInterrupted):
        _run(context, model)
    ok, failed = _rows(context, "model_call")
    assert "error" not in ok
    assert failed["response"] is None
    assert failed["usage_metadata"] is None
    assert failed["error"] == {"type": "BadRequestError", "code": 400}
    assert failed["system_prompt"] == PROMPT
    assert len(failed["messages"]) == 3
    assert "gizli istek metni" not in json.dumps(failed, ensure_ascii=False)


def test_error_without_status_code_recorded_by_class_name(data_dir: Path) -> None:
    context = make_context(data_dir)
    model = ScriptedChatModel.single(TimeoutError("zaman aşımı"))
    with pytest.raises(RuleRunInterrupted):
        _run(context, model)
    [failed] = _rows(context, "model_call")
    assert failed["error"] == {"type": "TimeoutError"}


# --- no_finding never carries a proposal or an escalation -----------------------------------------


def test_no_submit_no_proposal_or_escalation_attached(data_dir: Path) -> None:
    context = make_context(data_dir)
    model = ScriptedChatModel.single(
        ai(call("propose_change", clause_id="c2", proposed_text="60 gün önceden", rationale="g")),
        ai(call("escalate_to_lawyer", reason="Belirsiz.")),
        done(),
    )
    finding = _run(context, model)
    assert finding == Finding(
        rule_id=NOTICE_RULE.id, status="not_found", no_finding_reason="no_submit"
    )
    assert finding.proposal is None
    assert finding.escalation_reason is None
    # They stay in the audit log.
    assert [r["name"] for r in _rows(context, "tool_call")] == [
        "propose_change",
        "escalate_to_lawyer",
    ]


def test_step_limit_without_finding_no_proposal_or_escalation_attached(data_dir: Path) -> None:
    context = make_context(data_dir)
    turns = [
        ai(call("propose_change", clause_id="c2", proposed_text="60 gün önceden", rationale="g")),
        ai(call("escalate_to_lawyer", reason="Belirsiz.")),
        *[ai(_search()) for _ in range(RUN_LIMIT - 2)],
    ]
    finding = _run(context, ScriptedChatModel.single(*turns))
    assert finding == Finding(
        rule_id=NOTICE_RULE.id, status="not_found", no_finding_reason="step_limit"
    )


# --- Broken tool call -----------------------------------------------------------------------------


def test_only_broken_tool_call_no_submit_visible_in_audit(data_dir: Path) -> None:
    context = make_context(data_dir)
    model = ScriptedChatModel.single(broken())
    finding = _run(context, model)
    assert finding == Finding(
        rule_id=NOTICE_RULE.id, status="not_found", no_finding_reason="no_submit"
    )
    [row] = _rows(context, "model_call")
    assert row["response"]["tool_calls"] == []
    [invalid] = row["response"]["invalid_tool_calls"]
    assert invalid["name"] == "submit_finding"
    assert _rows(context, "tool_call") == []


# --- Audit write failures propagate ---------------------------------------------------------------


def _fail_audit_event(context: ReviewContext, event: str) -> None:
    # TEMP: the trigger lives on the store's shared connection, not in the database file.
    context.store._conn.execute(
        f"CREATE TEMP TRIGGER fail_{event} BEFORE INSERT ON main.audit_log "
        f"WHEN NEW.event = '{event}' BEGIN SELECT RAISE(ABORT, '{event} yazilamadi'); END"
    )


def test_model_call_audit_write_failure_propagates(data_dir: Path) -> None:
    context = make_context(data_dir)
    _fail_audit_event(context, "model_call")
    model = ScriptedChatModel.single(ai(_submit()), done())
    with pytest.raises(sqlite3.IntegrityError, match="model_call yazilamadi"):
        _run(context, model)
    assert context.store.list_audit(context.review_id) == []
    assert not context.store._conn.in_transaction


def test_model_call_audit_write_failure_after_model_error_propagates(data_dir: Path) -> None:
    context = make_context(data_dir)
    _fail_audit_event(context, "model_call")
    model = ScriptedChatModel.single(_bad_request())
    with pytest.raises(sqlite3.IntegrityError, match="model_call yazilamadi"):
        _run(context, model)


def test_tool_call_audit_write_failure_propagates(data_dir: Path) -> None:
    context = make_context(data_dir)
    _fail_audit_event(context, "tool_call")
    model = ScriptedChatModel.single(ai(_submit()), done())
    with pytest.raises(sqlite3.IntegrityError, match="tool_call yazilamadi"):
        _run(context, model)
    assert [r.event for r in context.store.list_audit(context.review_id)] == ["model_call"]
    assert model.calls_left() == 1
    assert not context.store._conn.in_transaction


# --- Isolation between runs, checkpoint -----------------------------------------------------------


def test_same_compiled_agent_two_runs_independent(data_dir: Path) -> None:
    first = make_context(data_dir)
    other_rule = NOTICE_RULE.model_copy(update={"id": "payment_term", "text": "Ödeme 60 gün."})
    second = make_context(data_dir, rule=other_rule)
    long_run = [*[ai(_search()) for _ in range(RUN_LIMIT - 2)], ai(_submit()), done()]
    model = ScriptedChatModel.keyed(
        {
            NOTICE_RULE.text: long_run,
            other_rule.text: [*[ai(_search()) for _ in range(RUN_LIMIT - 1)], done()],
        }
    )
    agent = build_rule_agent(model)
    assert run_rule_agent(agent, first) == _expected_finding()
    # Neither the finding, the messages nor the call count of the first run carry over.
    assert run_rule_agent(agent, second) == Finding(
        rule_id="payment_term", status="not_found", no_finding_reason="no_submit"
    )
    second_calls = model.received(other_rule.text)
    assert len(second_calls) == RUN_LIMIT
    assert [type(m) for m in second_calls[0]] == [SystemMessage, HumanMessage]
    assert len(_rows(first, "model_call")) == RUN_LIMIT
    assert len(_rows(second, "model_call")) == RUN_LIMIT


class _ParentState(TypedDict):
    findings: list[dict[str, Any]]


def test_agent_in_sqlite_saver_parent_graph_no_sub_namespace(data_dir: Path) -> None:
    context = make_context(data_dir)
    model = ScriptedChatModel.single(ai(_search()), ai(_submit()), done())
    agent = build_rule_agent(model)

    def review_rule(state: _ParentState) -> dict[str, Any]:
        return {"findings": [run_rule_agent(agent, context).model_dump()]}

    builder = StateGraph(_ParentState)
    builder.add_node("review_rule", review_rule)
    builder.add_edge(START, "review_rule")
    builder.add_edge("review_rule", END)
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        graph = builder.compile(checkpointer=SqliteSaver(conn))
        out = graph.invoke({"findings": []}, {"configurable": {"thread_id": "t1"}})
        assert out["findings"] == [_expected_finding().model_dump()]
        namespaces = {row[0] for row in conn.execute("SELECT checkpoint_ns FROM checkpoints")}
        namespaces |= {row[0] for row in conn.execute("SELECT checkpoint_ns FROM writes")}
        assert namespaces == {""}
    finally:
        conn.close()
