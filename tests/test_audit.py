import threading

from ledger.replay import ChainIntegrityError, verify_chain
from ledger.store import LedgerStore
from warrant.audit import ConcurrentAuditLog
from warrant.policy import AuthDecision


def _hammer_unsynchronized_store(path, n_threads=20, per_thread=5):
    store = LedgerStore(path)

    def worker(i):
        for j in range(per_thread):
            store.append(
                id=f"t{i}-{j}",
                timestamp=f"2026-08-23T10:00:{i:02d}Z",
                model_version="mv",
                prompt_version="pv",
                data_snapshot="ds",
                input="x",
                output="y",
            )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return store


def test_unsynchronized_ledger_store_can_corrupt_the_chain_under_concurrency(tmp_path):
    """Documents the real bug this module exists to fix: LedgerStore has
    no locking of its own, so concurrent callers can corrupt the chain
    -- two threads both read the same 'last hash' before either writes.
    This is a race, not a deterministic failure, so it's retried a few
    times rather than asserted on a single attempt; the point is that
    the race is real and reproducible, not that it fires every time."""
    corrupted = False
    for attempt in range(5):
        path = tmp_path / f"race-{attempt}.jsonl"
        store = _hammer_unsynchronized_store(path)
        try:
            verify_chain(store.read_all())
        except ChainIntegrityError:
            corrupted = True
            break
    assert corrupted, "expected at least one run to corrupt the chain under unsynchronized concurrent writes"


def test_concurrent_audit_log_stays_valid_under_the_same_load(tmp_path):
    """Same shape of concurrent load as the test above, through
    ConcurrentAuditLog instead of the raw store -- the lock is meant to
    make exactly this scenario safe."""
    log = ConcurrentAuditLog(tmp_path / "audit.jsonl")
    decision = AuthDecision(True, "test grant", ("a", "b"))

    def worker(i):
        for j in range(5):
            log.record(f"t{i}-{j}", f"2026-08-23T10:00:{i:02d}Z", f"actor-{i}", "call", "tool-x", decision)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    records = log.read_all()
    assert len(records) == 100
    assert verify_chain(records) is True


def test_record_stores_allow_and_deny_distinctly(tmp_path):
    log = ConcurrentAuditLog(tmp_path / "audit.jsonl")
    log.record("d1", "t0", "actor-a", "call", "tool-x", AuthDecision(True, "granted", ("a", "x")))
    log.record("d2", "t1", "actor-b", "call", "tool-y", AuthDecision(False, "no grant path", ()))

    records = log.read_all()
    assert records[0].output.startswith("ALLOWED")
    assert records[1].output.startswith("DENIED")
