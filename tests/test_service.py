import pytest
from fastapi.testclient import TestClient

import warrant.service as service_module
from warrant.service import app


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Each test gets its own fresh graph and audit file instead of
    sharing the module-level singletons across tests."""
    monkeypatch.setattr(service_module, "_graph", None)
    monkeypatch.setattr(service_module, "_audit", None)
    monkeypatch.setattr(service_module, "AUDIT_PATH", tmp_path / "audit.jsonl")
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_health_reports_graph_size(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["graph_entities"] > 0


def test_authorize_allows_a_valid_grant(client):
    response = client.post(
        "/authorize",
        json={"actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:search_docs"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["allowed"] is True
    assert "team:nova-agents" in body["path"]


def test_authorize_denies_an_ungranted_action(client):
    response = client.post(
        "/authorize",
        json={"actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:delete_repository"},
    )
    assert response.status_code == 200
    assert response.json()["allowed"] is False


def test_authorize_logs_every_decision_to_the_audit_trail(client):
    client.post("/authorize", json={"actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:search_docs"})
    client.post("/authorize", json={"actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:delete_repository"})

    count = client.get("/audit/count").json()["decisions_logged"]
    assert count == 2


def test_explain_does_not_log_to_the_audit_trail(client):
    client.get(
        "/explain",
        params={"actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:search_docs"},
    )
    count = client.get("/audit/count").json()["decisions_logged"]
    assert count == 0


def test_authorize_rejects_malformed_request(client):
    response = client.post("/authorize", json={"actor": "x"})
    assert response.status_code == 422
