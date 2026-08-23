import pytest

from warren import KnowledgeGraph
from warrant.policy import authorize


@pytest.fixture
def graph():
    g = KnowledgeGraph()
    g.add_entity("agent:nova", type="agent")
    g.add_entity("agent:code-fix", type="agent")
    g.add_entity("team:nova-agents", type="group")
    g.add_entity("team:admin-agents", type="group")
    g.add_entity("tool:search_docs", type="tool")
    g.add_entity("tool:delete_repository", type="tool")
    g.add_relation("agent:nova", "team:nova-agents", "MEMBER_OF")
    g.add_relation("agent:code-fix", "team:admin-agents", "MEMBER_OF")
    g.add_relation("team:nova-agents", "tool:search_docs", "CAN_CALL")
    g.add_relation("team:admin-agents", "tool:delete_repository", "CAN_CALL")
    return g


def test_allows_through_a_group_membership_chain(graph):
    decision = authorize(graph, "agent:nova", "call", "tool:search_docs")
    assert decision.allowed is True
    assert decision.path == ("agent:nova", "team:nova-agents", "tool:search_docs")


def test_denies_when_no_grant_path_exists(graph):
    decision = authorize(graph, "agent:nova", "call", "tool:delete_repository")
    assert decision.allowed is False
    assert "no grant path" in decision.reason


def test_direct_grant_without_any_group(graph):
    graph.add_entity("agent:standalone", type="agent")
    graph.add_relation("agent:standalone", "tool:search_docs", "CAN_CALL")
    decision = authorize(graph, "agent:standalone", "call", "tool:search_docs")
    assert decision.allowed is True
    assert decision.path == ("agent:standalone", "tool:search_docs")


def test_unknown_actor_is_denied_not_an_error(graph):
    decision = authorize(graph, "agent:ghost", "call", "tool:search_docs")
    assert decision.allowed is False
    assert "unknown actor" in decision.reason


def test_unknown_resource_is_denied_not_an_error(graph):
    decision = authorize(graph, "agent:nova", "call", "tool:ghost")
    assert decision.allowed is False
    assert "unknown resource" in decision.reason


def test_different_action_on_the_same_resource_is_denied(graph):
    """Being allowed to CALL a tool doesn't imply being allowed to
    DELETE it -- the action name is part of the grant, not just the
    resource."""
    decision = authorize(graph, "agent:nova", "delete", "tool:search_docs")
    assert decision.allowed is False


def test_max_hops_bounds_the_search(graph):
    """A membership chain longer than max_hops is correctly denied --
    proves the bound is enforced, not just present as an unused
    parameter."""
    graph.add_entity("team:sub-team", type="group")
    graph.add_relation("agent:nova", "team:sub-team", "MEMBER_OF")
    graph.add_relation("team:sub-team", "team:nova-agents", "MEMBER_OF")
    # nova -> sub-team -> nova-agents -> search_docs is 3 hops; cap it at 1
    decision = authorize(graph, "agent:nova", "call", "tool:search_docs", max_hops=1)
    assert decision.allowed is False
