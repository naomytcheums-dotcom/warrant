# warrant

[![Tests](https://github.com/naomytcheums-dotcom/warrant/actions/workflows/tests.yml/badge.svg)](https://github.com/naomytcheums-dotcom/warrant/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A real-time authorization service for AI agents. Answers "can this
agent do this action to this resource" as graph reachability — the
same relationship-based model behind Google's Zanzibar paper — and
hash-chains every decision, allowed or denied, into a tamper-evident
audit trail.

## Why graph reachability, not an access-control list

A flat ACL answers "what can X do" by looking X up in a table. It
doesn't compose: group membership, role inheritance, and per-resource
grants all need separate mechanisms bolted together. Modeling
authorization as a graph — actors, groups, resources as nodes;
membership and grants as typed, directed edges — lets one traversal
answer all of it: "is there a path of granted relationships from this
actor to this resource." It's also explainable for free: the path *is*
the reason.

## Built on two other pieces of this portfolio, not from scratch

- [warren](https://github.com/naomytcheums-dotcom/warren) supplies the graph: entities, typed relations, storage.
  `warrant/policy.py` adds the part warren doesn't have — inheritance-aware traversal specific to
  authorization semantics (which relation types propagate access, which must be the terminal hop).
- [ledger](https://github.com/naomytcheums-dotcom/ledger) supplies the audit trail: every decision, allowed or
  denied, is hash-chained and tamper-evident. ledger's schema is LLM-shaped (model/prompt/data version);
  `warrant/audit.py` repurposes it for policy decisions instead — the same idea (content-addressed versioning
  of whatever produced a decision) applies to a policy engine just as well as to an LLM call.

## Try it

```bash
pip install -e ../warren -e ../ledger   # local editable installs, not yet on PyPI
pip install -e ".[dev]"
uvicorn warrant.service:app --port 8600
```

```bash
curl -X POST localhost:8600/authorize -H "Content-Type: application/json" \
  -d '{"actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:search_docs"}'
# {"allowed":true,"reason":"granted via agent:rag-fastapi-assistant -> team:nova-agents -> tool:search_docs
#  (CAN_CALL at the last hop)","path":["agent:rag-fastapi-assistant","team:nova-agents","tool:search_docs"]}

curl -X POST localhost:8600/authorize -H "Content-Type: application/json" \
  -d '{"actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:delete_repository"}'
# {"allowed":false,"reason":"no grant path found from agent:rag-fastapi-assistant to tool:delete_repository
#  for action 'call'","path":[]}
```

The seeded policy graph (`data/policy_graph.json`) uses real entities from this portfolio: `rag-fastapi-assistant`
is a member of `nova-agents`, which can call `search_docs` and `escalate_to_human` — but not `delete_repository`,
which only `admin-agents` can call. Both the allow and the deny above are genuine graph traversal results, not
hardcoded.

## Beyond a single check: tokens, sessions, and impact analysis

### Signed capability tokens — skip re-traversing the graph on every call

```bash
curl -X POST "localhost:8600/token?ttl_seconds=300" -H "Content-Type: application/json" \
  -d '{"actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:search_docs"}'
# {"issued": true, "token": {..., "signature": "..."}}  -- only issued if the grant is actually allowed right now

curl -X POST localhost:8600/token/verify -H "Content-Type: application/json" -d '{"token": {...}}'
# {"valid": true, "reason": "valid"}  -- signature + expiry check, no graph traversal, no audit write
```

A token is a signed, time-bound claim, verifiable without hitting the graph again. It's a deliberate
freshness/latency trade-off, not a free win — see Known limitations.

### Session-aware authorization — don't trust a decision forever

```bash
curl -X POST localhost:8600/session/check -H "Content-Type: application/json" \
  -d '{"session_id": "sess-1", "actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:search_docs"}'
# {"allowed": true, "reason": "...", "revalidated": true}   -- first check: hits the graph
# a second check within 60s: {"revalidated": false}         -- served from cache
# a check after 60s: {"revalidated": true}                  -- re-checked against the graph, catching any revocation since
```

Checking authorization once at the start of a long-running agent session and trusting it for the rest of that
session is exactly the assumption that breaks once access can be revoked mid-session. `tests/test_session.py`
proves both halves of this: a revoked grant is still (staleness-)reported as allowed *within* the revalidation
window, and correctly caught once that window expires — the trade-off is tested, not just asserted.

### "What if I removed this relationship" — impact analysis before you actually remove it

```bash
curl -X POST localhost:8600/simulate -H "Content-Type: application/json" -d '{
  "source": "agent:rag-fastapi-assistant", "target": "team:nova-agents", "relation_type": "MEMBER_OF",
  "checks": [["agent:rag-fastapi-assistant", "call", "tool:search_docs"]]
}'
# {"results": [{"actor": "...", "before": true, "after": false, "changed": true}]}
```

Never mutates the real graph — it filters a fresh copy built from a snapshot and re-runs the given checks
against it, so an admin can confirm exactly what a policy change would break before making it for real.

## Two real bugs, found by actually running this under load, not by reading the code

**A real correctness bug**: `LedgerStore.append()` (in the sibling `ledger` project) has no locking of its own
— it reads the current file to compute the next record's `previous_hash`, then appends. A single caller never
notices. An HTTP service handling concurrent requests can corrupt the hash chain, because two requests can both
read the same "last hash" before either writes. `tests/test_audit.py` reproduces this directly: hammering an
unsynchronized `LedgerStore` with 20 threads corrupts the chain; the same load through `ConcurrentAuditLog`
(a `threading.Lock` around the read-then-append section) stays valid. Not a hypothetical — the corruption is
asserted, not assumed.

**A performance investigation that went through two wrong turns before the real answer**: the first load test
against a live, deployed instance measured a 5.9s p50 for `/authorize` — badly slow for what should be a graph
traversal and a file append. First hypothesis: `ConcurrentAuditLog` re-deriving `previous_hash` by re-reading
the whole ledger file on every write (an O(n) cost that grows with the log). Fixed it — cached the last hash in
memory instead of re-reading the file. Re-ran the same load test: **worse**, 7.5s p50. Wrong diagnosis.

Isolated further: hit the trivial `/health` endpoint (no auth logic, no audit write) under the same concurrent
load. Same multi-second latency. That ruled out the audit log, the lock, and the policy engine entirely — the
bottleneck wasn't in warrant's code at all. It was in the load-testing tool: `loadtest/run.py` was opening a
fresh TCP connection for every single request instead of reusing a connection pool, and on this environment
that connection-setup overhead dominated the measurement. A single `record()` call in isolation, benchmarked
directly, takes 0.3ms — the server was never the problem.

Fixed the load generator to use one `httpx.Client` with connection pooling instead of one-off requests. Re-ran
the identical test:

| | before (broken load test) | after (pooled connections) |
|---|---|---|
| p50 | 5,864.7ms | **66.6ms** |
| p95 | 8,598.3ms | 2,145.8ms |
| throughput | 8.3 req/s | **179.3 req/s** |

An ~88x improvement in p50, ~21x in throughput — from fixing the measurement, not the service. The remaining
p95/p99 tail (~2.1-2.2s under 50 concurrent requests) is real and not yet explained; see Known limitations.

## Deploying it

Two paths, both driven from the repo, neither needing this author's own accounts:

- **Buildpack-based hosts** (Render, Railway, etc.) — `render.yaml` is included for Render specifically; most
  others just need `requirements.txt` (also included, since `warren`/`ledger` aren't on PyPI yet and need
  installing from source).
- **Docker-based hosts** — a `Dockerfile` is included, for any host that deploys from a container instead of a
  buildpack (e.g. [Koyeb](https://www.koyeb.com), whose free instance has no time-based quota — the same choice
  already used for [voxbridge](https://github.com/naomytcheums-dotcom/voxbridge) in this portfolio, for the same
  reason: **New App → GitHub → select this repo → Koyeb detects the Dockerfile → deploy**). The Docker build
  itself is validated in CI (see below) rather than locally, since this environment doesn't have Docker
  installed — deliberately not claiming a build works without having actually run it somewhere.

## Tests

```bash
pytest tests/ -v
```

32 tests: policy traversal (allow via group chain, deny with no path, direct grants, unknown
actors/resources, action-specificity, max_hops enforcement), the concurrency corruption reproduction and its
fix, capability tokens (issue/verify round trip, tampering, expiry, wrong key), session revalidation (including
the mid-session revocation trade-off, proven both stale-within-window and caught-after-expiry), simulation
(flips the right checks, leaves unrelated grants alone, never mutates the real graph), and FastAPI integration
tests (via `TestClient`, no real network) covering every endpoint. All offline. CI additionally builds the
Docker image on GitHub's own runners and smoke-tests `/health` inside the running container — the actual build
this author can't run locally, verified where it matters instead of only claimed.

## Known limitations

- **The remaining p95/p99 latency tail under load (~2.1-2.2s) is unexplained.** Fixing the load generator
  closed most of the gap but not all of it. Candidates not yet isolated: FastAPI/Starlette's default thread
  pool size for synchronous path operations, or residual lock contention in `ConcurrentAuditLog` under genuine
  peak concurrency. Documented as open, not silently dropped.
- **`max_hops` is a fixed default (4), not tuned against a real-scale graph.** The seeded demo graph has 7
  entities; behavior on a graph with thousands of groups and nested roles is untested.
- **No support for negative grants (explicit deny) or revocation propagation.** A relationship, once added,
  grants access until the edge is removed; there's no "deny overrides allow" semantics some authorization
  systems need.
- **Single-process, single-file audit log.** `ConcurrentAuditLog`'s lock makes concurrent writes *correct*
  within one process; it doesn't make them fast at very high throughput, and it doesn't help at all across
  multiple server processes/workers writing to the same file, which would need a different storage layer
  entirely (a real database, not a JSONL file).
- **Token signing keys are ephemeral, generated fresh on process start.** Every token issued before a restart
  becomes unverifiable after one — fine for a demo, not for production, which needs persistent, rotated key
  storage instead of an in-memory keypair.
- **Session revalidation is intentionally stale within its window (default 60s).** A grant revoked mid-session
  is still reported as allowed until the next revalidation — tested and documented as a deliberate trade-off
  (see above), not an oversight, but a real one: don't set the window longer than an acceptable exposure time
  for whatever's being protected.
- **The Docker build is validated on GitHub's runners, not on this author's own machine**, which doesn't have
  Docker installed. CI building and smoke-testing the actual container is the verification; nothing here claims
  a local Docker run that never happened.

## Status

Early (`v0.1.0`, alpha). Policy engine, audit bridge, capability tokens, session revalidation, simulation, and
the HTTP service are complete and tested (32 tests); load-tested against a live running instance with a
documented, honest before/after (not just claimed); the Docker image builds and passes a health check in CI.
Not yet published to PyPI. Feedback and issues welcome.

## License

MIT
