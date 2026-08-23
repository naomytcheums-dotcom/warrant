"""
"What if I removed this relationship" impact analysis -- reconstructs
the graph without one relation and re-runs a batch of authorize()
checks against it, reporting exactly which access would be lost.
warren's KnowledgeGraph has no removal method, so this never mutates
the live graph: it filters a fresh graph built from a snapshot
(`to_dict()`/`from_dict()`), the same read-only-simulation pattern
`replay.py` in ledger uses for comparing records without altering them.
"""

from warren import KnowledgeGraph

from warrant.policy import authorize


def _graph_without_relation(graph, source, target, relation_type):
    data = graph.to_dict()
    data["relations"] = [
        r
        for r in data["relations"]
        if not (r["source"] == source and r["target"] == target and r["type"] == relation_type)
    ]
    return KnowledgeGraph.from_dict(data)


def simulate_relation_removal(graph, source, target, relation_type, checks):
    """`checks` is a list of `(actor, action, resource)` tuples to
    re-evaluate against the graph with that one relation removed.
    Returns one result dict per check -- before/after decision and
    whether it changed -- for every check given, not just the ones
    that flip, so a caller can confirm nothing they expected to keep
    working actually broke."""
    simulated_graph = _graph_without_relation(graph, source, target, relation_type)

    results = []
    for actor, action, resource in checks:
        before = authorize(graph, actor, action, resource)
        after = authorize(simulated_graph, actor, action, resource)
        results.append(
            {
                "actor": actor,
                "action": action,
                "resource": resource,
                "before": before.allowed,
                "after": after.allowed,
                "changed": before.allowed != after.allowed,
            }
        )
    return results
