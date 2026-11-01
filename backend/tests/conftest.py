import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.observability import METRICS


@pytest.fixture(autouse=True)
def reset_metrics():
    METRICS.reset()
    yield


@pytest.fixture
def client(tmp_path):
    # Never call a real provider from the default test fixture.
    app = create_app(Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}", auto_seed=True, llm_api_key="",
        analysis_worker_enabled=False, analysis_max_attempts=1,
    ))
    with TestClient(app) as http:
        yield http


@pytest.fixture
def analyst():
    return {"Authorization": "Bearer demo-analyst"}


@pytest.fixture
def approver():
    return {"Authorization": "Bearer demo-approver"}


@pytest.fixture
def recommendation(client, analyst):
    created = client.post("/api/analyses", headers=analyst, json={})
    assert created.status_code == 202
    detail = client.get(f"/api/analyses/{created.json()['id']}", headers=analyst).json()
    assert detail["status"] == "waiting_approval", detail
    return detail["recommendation_id"]
