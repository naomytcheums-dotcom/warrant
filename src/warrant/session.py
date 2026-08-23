"""
Continuous re-evaluation for long-running agent sessions. Checking
authorization once at the start of a session and trusting it for the
rest of that session's lifetime is exactly the assumption that breaks
once access can be revoked mid-session -- a group membership pulled, a
tool grant removed. This tracks, per session, when a grant was last
actually re-verified against the graph, and forces a fresh check once
that goes stale, instead of caching "allowed" forever.
"""

import threading


class SessionAuthorizer:
    def __init__(self, revalidate_after_seconds=60):
        self.revalidate_after_seconds = revalidate_after_seconds
        self._lock = threading.Lock()
        self._last_checked = {}

    def check(self, graph, session_id, actor, action, resource, now, authorize_fn):
        """Returns `(decision, revalidated)` -- `revalidated` is True
        when this call actually re-ran `authorize_fn` against the
        graph, False when it served a still-fresh cached decision.
        Exposed explicitly so a caller (or a test) can tell the two
        apart, rather than the cache silently doing its job."""
        key = (session_id, actor, action, resource)
        with self._lock:
            cached = self._last_checked.get(key)
            if cached is not None:
                checked_at, decision = cached
                if now - checked_at < self.revalidate_after_seconds:
                    return decision, False

            decision = authorize_fn(graph, actor, action, resource)
            self._last_checked[key] = (now, decision)
            return decision, True
