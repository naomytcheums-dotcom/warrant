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

## Tests

```bash
pytest tests/ -v
```

16 tests: policy traversal (allow via group chain, deny with no path, direct grants, unknown
actors/resources, action-specificity, max_hops enforcement), the concurrency corruption reproduction and its
fix, and FastAPI integration tests (via `TestClient`, no real network) covering both endpoints and the
audit-vs-explain logging distinction. All offline.

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

## Status

Early (`v0.1.0`, alpha). Policy engine, audit bridge, and HTTP service are complete and tested; load-tested
against a live running instance with a documented, honest before/after (not just claimed). Not yet published to
PyPI. Feedback and issues welcome.

## License

MIT
