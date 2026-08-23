from ledger.pki import generate_keypair

from warrant.tokens import issue_token, verify_token


def test_issue_and_verify_round_trip():
    private_key, public_key = generate_keypair()
    token = issue_token(private_key, "agent:nova", "call", "tool:search_docs", ("agent:nova", "tool:search_docs"), now=1000)

    valid, reason = verify_token(public_key, token, now=1010)

    assert valid is True
    assert reason == "valid"


def test_tampered_token_fails_signature_check():
    private_key, public_key = generate_keypair()
    token = issue_token(private_key, "agent:nova", "call", "tool:search_docs", ("agent:nova", "tool:search_docs"), now=1000)

    token["resource"] = "tool:delete_repository"  # forged after signing

    valid, reason = verify_token(public_key, token, now=1010)

    assert valid is False
    assert reason == "invalid signature"


def test_expired_token_fails_even_with_a_valid_signature():
    private_key, public_key = generate_keypair()
    token = issue_token(
        private_key, "agent:nova", "call", "tool:search_docs", ("agent:nova", "tool:search_docs"),
        ttl_seconds=60, now=1000,
    )

    valid, reason = verify_token(public_key, token, now=1000 + 61)

    assert valid is False
    assert reason == "expired"


def test_wrong_public_key_fails_signature_check():
    private_key, _ = generate_keypair()
    _, other_public_key = generate_keypair()
    token = issue_token(private_key, "agent:nova", "call", "tool:search_docs", ("agent:nova", "tool:search_docs"), now=1000)

    valid, reason = verify_token(other_public_key, token, now=1005)

    assert valid is False
    assert reason == "invalid signature"
