import itertools
import shutil
import threading
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.messages.tool import ToolCall, invalid_tool_call
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr, SkipValidation

from src.graph import close_graphs
from src.models import Check, Clause, MaskedText, Rule
from src.prepare.index import ClauseIndex, clear_indexes
from src.store import Store, close_all
from src.tools import ReviewContext

REPO_TENANTS = Path(__file__).resolve().parent.parent / "data" / "tenants"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    for tenant in ("acme", "globex"):
        target = tmp_path / "tenants" / tenant
        target.mkdir(parents=True)
        shutil.copyfile(REPO_TENANTS / tenant / "rules.yaml", target / "rules.yaml")
    return tmp_path


@pytest.fixture(autouse=True)
def _no_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    # A developer shell with tracing on must not send test runs to LangSmith.
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")


@pytest.fixture(autouse=True)
def _close_store_connections() -> Iterator[None]:
    yield
    close_all()


@pytest.fixture(autouse=True)
def _close_graph_connections() -> Iterator[None]:
    # The cached graphs hold their checkpoints.db connections open; Windows would keep the
    # tmp_path files locked.
    yield
    close_graphs()


@pytest.fixture(autouse=True)
def _clear_index_registry() -> Iterator[None]:
    yield
    clear_indexes()


# --- Scripted chat model --------------------------------------------------------------------------
#
# A script is a list of items served one per model call: an AIMessage is returned, an exception is
# raised. Scripts are keyed by the rule text of the run's first HumanMessage ("Kural: <text>"), so
# several rule agents can share one model from parallel threads (step 10). `ANY_RULE` is the
# shorthand key for one-rule tests: it serves every run that has no script of its own.

ANY_RULE = "*"
RULE_PREFIX = "Kural: "
ScriptItem = AIMessage | Exception

_call_ids = itertools.count(1)


class ScriptExhaustedError(AssertionError):
    pass


class ScriptedChatModel(GenericFakeChatModel):
    messages: Iterator[AIMessage | str] = Field(default_factory=lambda: iter(()))
    # SkipValidation: pydantic would try to coerce an exception item into an AIMessage.
    scripts: SkipValidation[dict[str, list[ScriptItem]]] = Field(default_factory=dict)
    disable_streaming: bool = True

    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    _positions: dict[str, int] = PrivateAttr(default_factory=dict)
    _received: list[tuple[str, list[BaseMessage]]] = PrivateAttr(default_factory=list)

    @classmethod
    def keyed(cls, scripts: Mapping[str, Sequence[ScriptItem]]) -> "ScriptedChatModel":
        return cls(scripts={key: list(items) for key, items in scripts.items()})

    @classmethod
    def single(cls, *items: ScriptItem) -> "ScriptedChatModel":
        return cls(scripts={ANY_RULE: list(items)})

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        return self

    def received(self, key: str | None = None) -> list[list[BaseMessage]]:
        """Every message list the model received, in call order (optionally for one rule)."""
        with self._lock:
            return [list(msgs) for k, msgs in self._received if key is None or k == key]

    def calls_left(self, key: str = ANY_RULE) -> int:
        with self._lock:
            return len(self.scripts[key]) - self._positions.get(key, 0)

    @staticmethod
    def _rule_text(messages: Sequence[BaseMessage]) -> str:
        first = next((m for m in messages if isinstance(m, HumanMessage)), None)
        text = first.text if first is not None else ""
        return text.removeprefix(RULE_PREFIX)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        rule_text = self._rule_text(messages)
        with self._lock:
            key = rule_text if rule_text in self.scripts else ANY_RULE
            self._received.append((key, list(messages)))
            script = self.scripts.get(key)
            position = self._positions.get(key, 0)
            if script is None or position >= len(script):
                raise ScriptExhaustedError(f"Script {key!r} has no item for call {position + 1}.")
            self._positions[key] = position + 1
            item = script[position]
        if isinstance(item, Exception):
            raise item
        return ChatResult(generations=[ChatGeneration(message=item.model_copy(deep=True))])


def call(name: str, **args: Any) -> ToolCall:
    return ToolCall(name=name, args=args, id=f"call_{next(_call_ids)}", type="tool_call")


def usage(input_tokens: int = 100, output_tokens: int = 20) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def ai(*calls: ToolCall, content: str = "") -> AIMessage:
    """A scripted model turn: the given tool calls (none ends the run) and usage metadata."""
    return AIMessage(content=content, tool_calls=list(calls), usage_metadata=usage())


def done(content: str = "Bitti.") -> AIMessage:
    return ai(content=content)


def broken(name: str = "submit_finding") -> AIMessage:
    """A turn whose only tool call could not be parsed (`invalid_tool_calls`)."""
    bad = invalid_tool_call(
        name=name, args='{"status": "compliant",', id=f"call_{next(_call_ids)}", error="bad json"
    )
    return AIMessage(content="", invalid_tool_calls=[bad], usage_metadata=usage())


# --- Rule agent context ---------------------------------------------------------------------------

NOTICE_RULE = Rule(
    id="termination_notice",
    text="Fesih bildirim süresi en az 30 gün olmalıdır.",
    check=Check(op=">=", value=30, unit="gün"),
)
# (clause_no, masked text) by clause_id
CLAUSES: dict[str, tuple[str | None, str]] = {
    "c1": (None, "Bu sözleşme [KISI_1] ile Tedarikçi arasında imzalanmıştır."),
    "c2": ("5", "Taraflar sözleşmeyi 30 gün önceden yazılı bildirimle feshedebilir."),
    "c3": ("6", "Ödeme faturanın tesliminden itibaren 45 gün içinde yapılır."),
}


def make_clauses(texts: Mapping[str, tuple[str | None, str]]) -> list[Clause]:
    return [
        Clause(clause_id=cid, clause_no=no, start=0, end=len(text), text=MaskedText(text))
        for cid, (no, text) in texts.items()
    ]


def make_context(
    data_dir: Path,
    clauses: Mapping[str, tuple[str | None, str]] = CLAUSES,
    rule: Rule = NOTICE_RULE,
    tenant_id: str = "acme",
) -> ReviewContext:
    store = Store(data_dir, tenant_id)
    review_id = store.create_review("asıl metin", "groq", "test-model")
    return ReviewContext(store, review_id, rule, ClauseIndex(make_clauses(clauses)))
