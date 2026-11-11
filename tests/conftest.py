import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.prepare.index import clear_indexes
from src.store import close_all

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
def _clear_index_registry() -> Iterator[None]:
    yield
    clear_indexes()
