import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

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
def _close_store_connections() -> Iterator[None]:
    yield
    close_all()
