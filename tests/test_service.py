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
    monkeypatch.setattr(service_module, "_sessions", None)
    monkeypatch.setattr(service_module, "_signing_key", None)
    monkeypatch.setattr(service_module, "_verify_key", None)
    monkeypatch.setattr(service_module, "AUDIT_DB_PATH", tmp_path / "audit.db")
    monkeypatch.setattr(service_module, "KEY_PATH", tmp_path / "signing_key.pem")
    monkeypatch.setenv("WARRANT_KEY_PASSPHRASE", "test-only-passphrase")
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


def test_token_issued_for_an_allowed_grant_and_verifies(client):
    issue = client.post(
        "/token",
        json={"actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:search_docs"},
    )
    assert issue.status_code == 200
    body = issue.json()
    assert body["issued"] is True

    verify = client.post("/token/verify", json={"token": body["token"]})
    assert verify.json() == {"valid": True, "reason": "valid"}


def test_token_not_issued_for_a_denied_grant(client):
    response = client.post(
        "/token",
        json={"actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:delete_repository"},
    )
    body = response.json()
    assert body["issued"] is False
    assert body["token"] is None


def test_tampered_token_fails_verification(client):
    issue = client.post(
        "/token",
        json={"actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:search_docs"},
    )
    token = issue.json()["token"]
    token["resource"] = "tool:delete_repository"

    verify = client.post("/token/verify", json={"token": token})
    assert verify.json()["valid"] is False


def test_session_check_revalidates_first_then_caches(client):
    first = client.post(
        "/session/check",
        json={"session_id": "s1", "actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:search_docs"},
    )
    assert first.json()["revalidated"] is True

    second = client.post(
        "/session/check",
        json={"session_id": "s1", "actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:search_docs"},
    )
    assert second.json()["revalidated"] is False
    assert second.json()["allowed"] is True


def test_simulate_reports_which_checks_would_flip(client):
    response = client.post(
        "/simulate",
        json={
            "source": "agent:rag-fastapi-assistant",
            "target": "team:nova-agents",
            "relation_type": "MEMBER_OF",
            "checks": [["agent:rag-fastapi-assistant", "call", "tool:search_docs"]],
        },
    )
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["before"] is True
    assert result["after"] is False
    assert result["changed"] is True
