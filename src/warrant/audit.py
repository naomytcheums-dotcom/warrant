"""
Thread-safe wrapper around ledger's LedgerStore for concurrent callers.
LedgerStore itself doesn't claim to be safe under concurrent writers --
it reads the current file to compute the next record's previous_hash,
then appends, with no locking of its own. A single-process library used
by one caller at a time never notices; an HTTP service handling
concurrent requests can corrupt the chain (two requests both reading
the same "last hash" before either writes) without an explicit lock
serializing the read-then-append critical section. See
tests/test_audit.py for a reproduction of the actual corruption.

Load-tested and found a second, distinct problem: `LedgerStore.append()`
re-reads and re-hashes the *entire* file on every single call to derive
`previous_hash`, so throughput degrades as the ledger grows -- O(n) work
per write, O(n^2) total for n sequential writes. Fine for a library used
occasionally; catastrophic for a service under real request volume (500
requests at 50 concurrent measured a 5.8s p50 before this fix). Fixed
here by caching the last hash in memory and updating it after each
write instead of re-deriving it from disk -- safe specifically because
the lock already guarantees this class is the only writer touching the
file, and it owns the cache alongside that guarantee.
"""

import json
import threading

from ledger import GENESIS_HASH, DecisionRecord, LedgerStore, data_snapshot, model_version, prompt_version


class ConcurrentAuditLog:
    def __init__(self, ledger_path):
        self._store = LedgerStore(ledger_path)
        self._lock = threading.Lock()
        self._cached_last_hash = None

    def _last_hash(self):
        """Populated lazily from disk once (so a log reopened over an
        existing file picks up where it left off), then served from
        memory afterward -- the whole point of the cache."""
        if self._cached_last_hash is None:
            records = self._store.read_all()
            self._cached_last_hash = records[-1].record_hash() if records else GENESIS_HASH
        return self._cached_last_hash

    def record(self, decision_id, timestamp, actor, action, resource, decision):
        """ledger's schema is LLM-shaped (model/prompt/data version) --
        repurposed here for a policy decision: `model_version` becomes
        the policy engine's own version, `prompt_version` the action
        being checked, `data_snapshot` the resource. The underlying
        idea (content-addressed versioning of whatever produced a
        decision) applies just as well to a policy engine as to an
        LLM call."""
        with self._lock:
            record = DecisionRecord(
                id=decision_id,
                timestamp=timestamp,
                model_version=model_version("warrant-policy-engine-v1"),
                prompt_version=prompt_version(f"authorize:{action}"),
                data_snapshot=data_snapshot({"resource": resource}),
                input=f"{actor} -> {action} -> {resource}",
                output=f"{'ALLOWED' if decision.allowed else 'DENIED'}: {decision.reason}",
                previous_hash=self._last_hash(),
                actor=actor,
            )
            with open(self._store.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
            self._cached_last_hash = record.record_hash()
            return record

    def read_all(self):
        with self._lock:
            return self._store.read_all()
