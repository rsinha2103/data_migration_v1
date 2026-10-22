import shutil
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "acme_supplier"


@pytest.fixture
def example_project() -> Path:
    return EXAMPLE


@pytest.fixture
def project_copy(tmp_path: Path) -> Path:
    """A writable copy of the example project for negative tests."""
    dest = tmp_path / "project"
    shutil.copytree(EXAMPLE, dest)
    return dest
