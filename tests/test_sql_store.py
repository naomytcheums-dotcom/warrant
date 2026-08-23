import threading

from ledger.replay import verify_chain
from warrant.policy import AuthDecision
from warrant.sql_store import SQLAuditLog


def test_record_and_read_round_trip(tmp_path):
    log = SQLAuditLog(tmp_path / "audit.db")
    decision = AuthDecision(True, "granted", ("a", "b"))

    log.record("d1", "t0", "actor-a", "call", "tool-x", decision)

    records = log.read_all()
    assert len(records) == 1
    assert records[0].output.startswith("ALLOWED")


def test_chain_stays_valid_across_sequential_writes(tmp_path):
    log = SQLAuditLog(tmp_path / "audit.db")
    decision = AuthDecision(True, "granted", ("a", "b"))
    for i in range(5):
        log.record(f"d{i}", f"t{i}", "actor-a", "call", "tool-x", decision)

    assert verify_chain(log.read_all()) is True


def test_concurrent_writes_from_many_threads_stay_valid(tmp_path):
    """The same class of race ConcurrentAuditLog's threading.Lock
    fixes within one process -- proven here for SQLAuditLog too, which
    additionally has to survive multiple *connections* hammering the
    same file, not just multiple threads sharing one lock object."""
    log = SQLAuditLog(tmp_path / "audit.db")
    decision = AuthDecision(True, "granted", ("a", "b"))

    def worker(i):
        for j in range(10):
            log.record(f"t{i}-{j}", f"2026-08-23T10:00:{i:02d}Z", f"actor-{i}", "call", "tool-x", decision)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    records = log.read_all()
    assert len(records) == 200
    assert verify_chain(records) is True


def test_persists_across_separate_instances_pointed_at_the_same_file(tmp_path):
    """Simulates a process restart: a fresh SQLAuditLog instance
    opened against an existing database file must pick up where the
    last one left off -- previous_hash chaining correctly, not
    starting back at GENESIS_HASH."""
    db_path = tmp_path / "audit.db"
    decision = AuthDecision(True, "granted", ("a", "b"))

    first_instance = SQLAuditLog(db_path)
    first_instance.record("d0", "t0", "actor-a", "call", "tool-x", decision)

    second_instance = SQLAuditLog(db_path)
    second_instance.record("d1", "t1", "actor-a", "call", "tool-x", decision)

    records = second_instance.read_all()
    assert len(records) == 2
    assert verify_chain(records) is True
