import pytest

from warren import KnowledgeGraph
from warrant.simulate import simulate_relation_removal


@pytest.fixture
def graph():
    g = KnowledgeGraph()
    g.add_entity("agent:nova", type="agent")
    g.add_entity("agent:other", type="agent")
    g.add_entity("team:nova-agents", type="group")
    g.add_entity("tool:search_docs", type="tool")
    g.add_entity("tool:escalate", type="tool")
    g.add_relation("agent:nova", "team:nova-agents", "MEMBER_OF")
    g.add_relation("team:nova-agents", "tool:search_docs", "CAN_CALL")
    g.add_relation("team:nova-agents", "tool:escalate", "CAN_CALL")
    g.add_relation("agent:other", "tool:search_docs", "CAN_CALL")
    return g


def test_removing_a_membership_flips_the_dependent_checks(graph):
    checks = [
        ("agent:nova", "call", "tool:search_docs"),
        ("agent:nova", "call", "tool:escalate"),
    ]
    results = simulate_relation_removal(graph, "agent:nova", "team:nova-agents", "MEMBER_OF", checks)

    assert all(r["before"] is True for r in results)
    assert all(r["after"] is False for r in results)
    assert all(r["changed"] is True for r in results)


def test_removing_a_membership_does_not_affect_unrelated_grants(graph):
    checks = [("agent:other", "call", "tool:search_docs")]
    results = simulate_relation_removal(graph, "agent:nova", "team:nova-agents", "MEMBER_OF", checks)

    assert results[0]["before"] is True
    assert results[0]["after"] is True
    assert results[0]["changed"] is False


def test_simulation_does_not_mutate_the_original_graph(graph):
    simulate_relation_removal(graph, "agent:nova", "team:nova-agents", "MEMBER_OF", [])

    # the real graph must still have the relation -- simulation is read-only
    relations = graph.relations_from("agent:nova")
    assert any(r["type"] == "MEMBER_OF" for r in relations)
