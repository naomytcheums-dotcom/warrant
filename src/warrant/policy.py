"""
Authorization as graph reachability -- the same model Google's Zanzibar
paper (and systems built on it) uses: "can X do Y to Z" is answered by
asking whether a path of granted relationships connects X to Z, not by
consulting a flat access-control list per resource. Built on warren's
KnowledgeGraph rather than a second graph implementation.

INHERITANCE_RELATIONS are relation types that propagate access
transitively (group membership, role inheritance) -- an actor
connected to a group via any chain of these relations inherits
whatever that group is granted. The final hop to the resource must be
a relation type matching the requested action itself (e.g. "CAN_CALL"
for an action of "call").
"""

from dataclasses import dataclass

INHERITANCE_RELATIONS = {"MEMBER_OF", "HAS_ROLE"}


@dataclass(frozen=True)
class AuthDecision:
    allowed: bool
    reason: str
    path: tuple


def _action_relation(action):
    return f"CAN_{action.upper()}"


def authorize(graph, actor, action, resource, max_hops=4):
    """Breadth-first search forward from `actor` through
    INHERITANCE_RELATIONS edges, checking at every node reached for a
    direct action-relation edge to `resource`. Returns an AuthDecision
    carrying the actual path of entity ids that justifies it -- an
    audit entry that just says "denied" with no reasoning is barely
    more useful than no log entry at all."""
    if not graph.has_entity(actor):
        return AuthDecision(False, f"unknown actor: {actor!r}", ())
    if not graph.has_entity(resource):
        return AuthDecision(False, f"unknown resource: {resource!r}", ())

    action_relation = _action_relation(action)

    for relation in graph.relations_from(actor):
        if relation["type"] == action_relation and relation["target"] == resource:
            return AuthDecision(True, f"direct grant: {actor} -[{action_relation}]-> {resource}", (actor, resource))

    visited = {actor}
    frontier = [(actor, (actor,))]
    hops = 0
    while frontier and hops < max_hops:
        next_frontier = []
        for node, path in frontier:
            for relation in graph.relations_from(node):
                if relation["type"] == action_relation and relation["target"] == resource:
                    full_path = path + (resource,)
                    return AuthDecision(
                        True,
                        f"granted via {' -> '.join(full_path)} ({action_relation} at the last hop)",
                        full_path,
                    )
                if relation["type"] in INHERITANCE_RELATIONS and relation["target"] not in visited:
                    visited.add(relation["target"])
                    next_frontier.append((relation["target"], path + (relation["target"],)))
        frontier = next_frontier
        hops += 1

    return AuthDecision(False, f"no grant path found from {actor} to {resource} for action {action!r}", ())
