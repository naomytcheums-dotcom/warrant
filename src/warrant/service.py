"""
HTTP service exposing the authorization engine. POST /authorize is a
real access-control decision -- every call, allowed or denied, is
logged to the audit trail. GET /explain runs the same check without
logging it, for inspecting a policy before relying on it in an actual
access path.
"""

import time
import uuid
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from warren import KnowledgeGraph

from warrant.audit import ConcurrentAuditLog
from warrant.policy import authorize

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GRAPH_PATH = REPO_ROOT / "data" / "policy_graph.json"
AUDIT_PATH = REPO_ROOT / "audit.jsonl"

app = FastAPI(
    title="warrant",
    description="Authorization-as-a-graph for AI agents -- every decision hash-chained and auditable.",
)

_graph = None
_audit = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = KnowledgeGraph.load(GRAPH_PATH) if GRAPH_PATH.exists() else KnowledgeGraph()
    return _graph


def get_audit():
    global _audit
    if _audit is None:
        _audit = ConcurrentAuditLog(AUDIT_PATH)
    return _audit


class AuthorizeRequest(BaseModel):
    actor: str
    action: str
    resource: str


class AuthorizeResponse(BaseModel):
    allowed: bool
    reason: str
    path: list


@app.post("/authorize", response_model=AuthorizeResponse)
def post_authorize(req: AuthorizeRequest):
    decision = authorize(get_graph(), req.actor, req.action, req.resource)
    get_audit().record(
        decision_id=str(uuid.uuid4()),
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        actor=req.actor,
        action=req.action,
        resource=req.resource,
        decision=decision,
    )
    return AuthorizeResponse(allowed=decision.allowed, reason=decision.reason, path=list(decision.path))


@app.get("/explain", response_model=AuthorizeResponse)
def get_explain(actor: str, action: str, resource: str):
    decision = authorize(get_graph(), actor, action, resource)
    return AuthorizeResponse(allowed=decision.allowed, reason=decision.reason, path=list(decision.path))


@app.get("/health")
def health():
    graph = get_graph()
    return {"status": "ok", "graph_entities": len(graph)}


@app.get("/audit/count")
def audit_count():
    return {"decisions_logged": len(get_audit().read_all())}
