"""
warrant -- a real-time authorization service for AI agents. Answers
"can this agent do this action to this resource" as graph reachability
(the same relationship-based model behind Google's Zanzibar and the
authorization systems it inspired), and hash-chains every decision --
allowed or denied -- into ledger's tamper-evident audit trail.

Built on two other pieces of this portfolio rather than from scratch:
warren's KnowledgeGraph for relationship storage and traversal, and
ledger for the audit trail. The new part is the policy semantics
(inheritance vs. terminal grant relations) and a service layer that
makes those decisions safe under real concurrent load -- something
neither warren nor ledger needed to solve alone, and ledger's own
LedgerStore does not solve by itself (see audit.py).
"""

from warrant.audit import ConcurrentAuditLog
from warrant.policy import AuthDecision, authorize

__all__ = ["AuthDecision", "authorize", "ConcurrentAuditLog"]

__version__ = "0.1.0"
