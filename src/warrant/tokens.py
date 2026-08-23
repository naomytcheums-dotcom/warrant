"""
Short-lived, signed capacity tokens -- once an agent's access is
checked via authorize(), it can hold a token instead of re-running the
graph traversal on every subsequent call for the same grant. A token
is a signed, time-bound claim: "this actor was allowed this action on
this resource, as of this time, until this expiry." Verifying a token
is a signature check and an expiry check -- no graph traversal, no
audit-log write -- while issuing one still goes through the full
authorize() path once.

This is a deliberate latency/freshness trade-off: a token holder keeps
acting on a permission that might have been revoked in the graph since
the token was issued, until the token expires. Keep TTLs short for
anything sensitive -- see the README's Known limitations.
"""

import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from ledger.record import content_hash


@dataclass(frozen=True)
class CapabilityToken:
    actor: str
    action: str
    resource: str
    issued_at: float
    expires_at: float
    path: tuple

    def payload(self):
        return {
            "actor": self.actor,
            "action": self.action,
            "resource": self.resource,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "path": list(self.path),
        }

    def payload_hash(self):
        return content_hash(self.payload())

    def is_expired(self, now=None):
        now = time.time() if now is None else now
        return now >= self.expires_at


def issue_token(private_key, actor, action, resource, path, ttl_seconds=300, now=None):
    now = time.time() if now is None else now
    token = CapabilityToken(actor, action, resource, now, now + ttl_seconds, tuple(path))
    signature = private_key.sign(token.payload_hash().encode("utf-8")).hex()
    return {**token.payload(), "signature": signature}


def verify_token(public_key, token_dict, now=None):
    """Returns `(valid, reason)`. Checks signature integrity AND expiry
    as two distinct, explicit failures -- a validly signed but expired
    token is invalid for a different reason than a fresh but forged
    one, and a caller may care which."""
    fields = {k: v for k, v in token_dict.items() if k != "signature"}
    token = CapabilityToken(
        actor=fields["actor"],
        action=fields["action"],
        resource=fields["resource"],
        issued_at=fields["issued_at"],
        expires_at=fields["expires_at"],
        path=tuple(fields["path"]),
    )

    try:
        public_key.verify(bytes.fromhex(token_dict["signature"]), token.payload_hash().encode("utf-8"))
    except InvalidSignature:
        return False, "invalid signature"

    if token.is_expired(now):
        return False, "expired"

    return True, "valid"
