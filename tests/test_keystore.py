import pytest

from warrant.keystore import KeyStoreError, load_or_create_keypair


def test_raises_when_passphrase_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("WARRANT_KEY_PASSPHRASE", raising=False)
    with pytest.raises(KeyStoreError, match="WARRANT_KEY_PASSPHRASE"):
        load_or_create_keypair(tmp_path / "key.pem")


def test_creates_a_new_key_when_none_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("WARRANT_KEY_PASSPHRASE", "correct horse battery staple")
    key_path = tmp_path / "key.pem"

    private_key, public_key = load_or_create_keypair(key_path)

    assert key_path.exists()
    assert private_key.public_key().public_bytes_raw() == public_key.public_bytes_raw()


def test_key_survives_a_simulated_restart(tmp_path, monkeypatch):
    """The whole point of this module: load_or_create_keypair called
    again against the same path returns the SAME key, not a new one --
    a token signed before a 'restart' verifies after it."""
    monkeypatch.setenv("WARRANT_KEY_PASSPHRASE", "correct horse battery staple")
    key_path = tmp_path / "key.pem"

    first_private, first_public = load_or_create_keypair(key_path)
    second_private, second_public = load_or_create_keypair(key_path)

    assert first_public.public_bytes_raw() == second_public.public_bytes_raw()

    message = b"same key, different process lifetime"
    signature = first_private.sign(message)
    second_public.verify(signature, message)  # raises InvalidSignature if this were a different key


def test_wrong_passphrase_fails_to_decrypt_an_existing_key(tmp_path, monkeypatch):
    key_path = tmp_path / "key.pem"
    monkeypatch.setenv("WARRANT_KEY_PASSPHRASE", "the-real-passphrase")
    load_or_create_keypair(key_path)  # creates and persists the key

    monkeypatch.setenv("WARRANT_KEY_PASSPHRASE", "a-wrong-guess")
    with pytest.raises(KeyStoreError, match="could not decrypt"):
        load_or_create_keypair(key_path)


def test_key_file_is_not_plaintext_pem(tmp_path, monkeypatch):
    """Confirms the key is actually encrypted on disk, not just
    wrapped in a PEM envelope that anyone with file access could load
    unprotected."""
    monkeypatch.setenv("WARRANT_KEY_PASSPHRASE", "correct horse battery staple")
    key_path = tmp_path / "key.pem"
    load_or_create_keypair(key_path)

    contents = key_path.read_text()
    assert "ENCRYPTED" in contents
