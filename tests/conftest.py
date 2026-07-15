"""Test fixtures and helpers."""
import os
import sys
from pathlib import Path

import pytest

# Ensure src/ is on the path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(scope="session")
def artifacts_path():
    return ROOT / "src" / "xchain" / "contracts" / "artifacts"


@pytest.fixture(scope="session")
def compiled(artifacts_path):
    import json
    return json.load(open(artifacts_path / "compiled.json"))
