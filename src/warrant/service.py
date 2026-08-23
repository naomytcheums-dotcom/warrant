"""
HTTP service exposing the authorization engine.

- POST /authorize -- a real access-control decision; every call,
  allowed or denied, is logged to the audit trail.
- GET /explain -- the same check without logging it, for inspecting a
  policy before relying on it in an actual access path.
- POST /token -- issue a short-lived, signed capability token for an
  already-allowed grant, so a caller can avoid re-running a full graph
  traversal on every subsequent call.
- POST /token/verify -- verify a token: signature and expiry, no graph
  traversal, no audit write.
- POST /session/check -- session-aware authorization that only
  re-verifies against the graph after a revalidation window, instead
  of trusting a decision made once for the rest of a long-running
  session.
- POST /simulate -- "what if I removed this relationship" impact
  analysis over a batch of checks, without mutating the real graph.
"""

import time
import uuid
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from warren import KnowledgeGraph

from warrant.keystore import load_or_create_keypair
from warrant.policy import authorize
from warrant.session import SessionAuthorizer
from warrant.simulate import simulate_relation_removal
from warrant.sql_store import SQLAuditLog
from warrant.tokens import issue_token, verify_token

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GRAPH_PATH = REPO_ROOT / "data" / "policy_graph.json"
AUDIT_DB_PATH = REPO_ROOT / "audit.db"
KEY_PATH = REPO_ROOT / "signing_key.pem"

app = FastAPI(
    title="warrant",
    description="Authorization-as-a-graph for AI agents -- every decision hash-chained and auditable.",
)

_graph = None
_audit = None
_sessions = None
_signing_key = None
_verify_key = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = KnowledgeGraph.load(GRAPH_PATH) if GRAPH_PATH.exists() else KnowledgeGraph()
    return _graph


def get_audit():
    global _audit
    if _audit is None:
        _audit = SQLAuditLog(AUDIT_DB_PATH)
    return _audit


def get_sessions():
    global _sessions
    if _sessions is None:
        _sessions = SessionAuthorizer(revalidate_after_seconds=60)
    return _sessions


def get_keys():
    """Loaded once per process from an encrypted key file (see
    keystore.py) rather than generated fresh -- a restart reuses the
    same key, so tokens issued before one stay verifiable after it.
    Requires WARRANT_KEY_PASSPHRASE to be set in the environment."""
    global _signing_key, _verify_key
    if _signing_key is None:
        _signing_key, _verify_key = load_or_create_keypair(KEY_PATH)
    return _signing_key, _verify_key


class AuthorizeRequest(BaseModel):
    actor: str
    action: str
    resource: str


class AuthorizeResponse(BaseModel):
    allowed: bool
    reason: str
    path: list


class TokenResponse(BaseModel):
    issued: bool
    token: dict | None = None
    reason: str | None = None


class TokenVerifyRequest(BaseModel):
    token: dict


class TokenVerifyResponse(BaseModel):
    valid: bool
    reason: str


class SessionCheckRequest(BaseModel):
    session_id: str
    actor: str
    action: str
    resource: str


class SessionCheckResponse(BaseModel):
    allowed: bool
    reason: str
    revalidated: bool


class SimulateRequest(BaseModel):
    source: str
    target: str
    relation_type: str
    checks: list[list[str]]  # [[actor, action, resource], ...]


def _record_decision(actor, action, resource, decision):
    get_audit().record(
        decision_id=str(uuid.uuid4()),
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        actor=actor,
        action=action,
        resource=resource,
        decision=decision,
    )


@app.post("/authorize", response_model=AuthorizeResponse)
def post_authorize(req: AuthorizeRequest):
    decision = authorize(get_graph(), req.actor, req.action, req.resource)
    _record_decision(req.actor, req.action, req.resource, decision)
    return AuthorizeResponse(allowed=decision.allowed, reason=decision.reason, path=list(decision.path))


@app.get("/explain", response_model=AuthorizeResponse)
def get_explain(actor: str, action: str, resource: str):
    decision = authorize(get_graph(), actor, action, resource)
    return AuthorizeResponse(allowed=decision.allowed, reason=decision.reason, path=list(decision.path))


@app.post("/token", response_model=TokenResponse)
def post_token(req: AuthorizeRequest, ttl_seconds: int = 300):
    """Only issues a token for a grant that's actually allowed right
    now -- checked (and audited) the same way /authorize does, not
    skipped just because a token was requested instead."""
    decision = authorize(get_graph(), req.actor, req.action, req.resource)
    _record_decision(req.actor, req.action, req.resource, decision)
    if not decision.allowed:
        return TokenResponse(issued=False, reason=decision.reason)

    private_key, _ = get_keys()
    token = issue_token(private_key, req.actor, req.action, req.resource, decision.path, ttl_seconds=ttl_seconds)
    return TokenResponse(issued=True, token=token)


@app.post("/token/verify", response_model=TokenVerifyResponse)
def post_token_verify(req: TokenVerifyRequest):
    _, public_key = get_keys()
    valid, reason = verify_token(public_key, req.token)
    return TokenVerifyResponse(valid=valid, reason=reason)


@app.post("/session/check", response_model=SessionCheckResponse)
def post_session_check(req: SessionCheckRequest):
    decision, revalidated = get_sessions().check(
        get_graph(), req.session_id, req.actor, req.action, req.resource, now=time.time(), authorize_fn=authorize
    )
    if revalidated:
        _record_decision(req.actor, req.action, req.resource, decision)
    return SessionCheckResponse(allowed=decision.allowed, reason=decision.reason, revalidated=revalidated)


@app.post("/simulate")
def post_simulate(req: SimulateRequest):
    checks = [tuple(c) for c in req.checks]
    results = simulate_relation_removal(get_graph(), req.source, req.target, req.relation_type, checks)
    return {"results": results}


@app.get("/health")
def health():
    graph = get_graph()
    return {"status": "ok", "graph_entities": len(graph)}


@app.get("/audit/count")
def audit_count():
    return {"decisions_logged": len(get_audit().read_all())}
