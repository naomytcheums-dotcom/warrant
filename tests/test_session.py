import pytest

from warren import KnowledgeGraph
from warrant.policy import authorize
from warrant.session import SessionAuthorizer


@pytest.fixture
def graph():
    g = KnowledgeGraph()
    g.add_entity("agent:nova", type="agent")
    g.add_entity("team:nova-agents", type="group")
    g.add_entity("tool:search_docs", type="tool")
    g.add_relation("agent:nova", "team:nova-agents", "MEMBER_OF")
    g.add_relation("team:nova-agents", "tool:search_docs", "CAN_CALL")
    return g


def test_first_check_always_revalidates(graph):
    sessions = SessionAuthorizer(revalidate_after_seconds=60)
    decision, revalidated = sessions.check(graph, "sess-1", "agent:nova", "call", "tool:search_docs", now=1000, authorize_fn=authorize)
    assert decision.allowed is True
    assert revalidated is True


def test_second_check_within_window_is_served_from_cache(graph):
    sessions = SessionAuthorizer(revalidate_after_seconds=60)
    sessions.check(graph, "sess-1", "agent:nova", "call", "tool:search_docs", now=1000, authorize_fn=authorize)

    decision, revalidated = sessions.check(graph, "sess-1", "agent:nova", "call", "tool:search_docs", now=1030, authorize_fn=authorize)

    assert decision.allowed is True
    assert revalidated is False


def test_check_after_window_expires_revalidates_again(graph):
    sessions = SessionAuthorizer(revalidate_after_seconds=60)
    sessions.check(graph, "sess-1", "agent:nova", "call", "tool:search_docs", now=1000, authorize_fn=authorize)

    decision, revalidated = sessions.check(graph, "sess-1", "agent:nova", "call", "tool:search_docs", now=1061, authorize_fn=authorize)

    assert decision.allowed is True
    assert revalidated is True


def test_revocation_mid_session_is_not_caught_until_the_cache_expires(graph):
    """Documents the real, deliberate trade-off: within the cache
    window, a revoked grant is still reported as allowed. This is
    tested explicitly, not just claimed in a docstring."""
    sessions = SessionAuthorizer(revalidate_after_seconds=60)
    sessions.check(graph, "sess-1", "agent:nova", "call", "tool:search_docs", now=1000, authorize_fn=authorize)

    graph.add_entity("team:empty", type="group")  # simulate revocation: remove nova from the granting team
    # warren has no relation-removal API -- rebuild the graph without the grant instead, same as simulate.py does
    data = graph.to_dict()
    data["relations"] = [r for r in data["relations"] if r["type"] != "MEMBER_OF"]
    from warren import KnowledgeGraph

    revoked_graph = KnowledgeGraph.from_dict(data)

    still_within_window, revalidated = sessions.check(
        revoked_graph, "sess-1", "agent:nova", "call", "tool:search_docs", now=1030, authorize_fn=authorize
    )
    assert still_within_window.allowed is True  # stale -- the whole point of the trade-off
    assert revalidated is False

    after_window, revalidated_again = sessions.check(
        revoked_graph, "sess-1", "agent:nova", "call", "tool:search_docs", now=1061, authorize_fn=authorize
    )
    assert after_window.allowed is False  # caught once the cache actually expires
    assert revalidated_again is True
