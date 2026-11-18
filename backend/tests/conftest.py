import os
import tempfile
from pathlib import Path

import pytest

# Point the app at a throwaway database before any prosight module reads the settings.
_tmp = Path(tempfile.mkdtemp()) / "test.db"
os.environ["PROSIGHT_DATABASE_URL"] = f"sqlite:///{_tmp}"
for key in list(os.environ):
    if key.startswith(("PROSIGHT_SAP", "PROSIGHT_SALESFORCE", "PROSIGHT_NETSUITE")):
        del os.environ[key]

from fastapi.testclient import TestClient  # noqa: E402

from prosight.main import app  # noqa: E402
from prosight.seed import reset_and_seed  # noqa: E402


@pytest.fixture
def client():
    reset_and_seed()
    with TestClient(app) as c:
        yield c
