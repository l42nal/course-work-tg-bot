import os

import pytest
from cryptography.fernet import Fernet

from bot.db.types import EncryptedText
from bot.security.encryption import PREFIX, decrypt_text, encrypt_text


def _set_test_key(monkeypatch) -> str:
    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("DATA_ENCRYPTION_FERNET_KEY", key)
    return key


def test_encrypt_decrypt_roundtrip(monkeypatch) -> None:
    _set_test_key(monkeypatch)
    src = "привет plans + future messages"
    enc = encrypt_text(src)
    assert isinstance(enc, str)
    assert enc.startswith(PREFIX)
    assert decrypt_text(enc) == src


def test_encrypt_decrypt_none_and_empty(monkeypatch) -> None:
    _set_test_key(monkeypatch)
    assert encrypt_text(None) is None
    assert decrypt_text(None) is None

    assert encrypt_text("") == ""
    assert decrypt_text("") == ""


def test_decrypt_requires_prefix(monkeypatch) -> None:
    _set_test_key(monkeypatch)
    with pytest.raises(ValueError):
        decrypt_text("not-encrypted")


def test_encrypted_text_type_bind_and_result(monkeypatch) -> None:
    _set_test_key(monkeypatch)
    t = EncryptedText()

    bound = t.process_bind_param("hello", None)
    assert isinstance(bound, str)
    assert bound.startswith(PREFIX)

    # If already encrypted, should not double-encrypt
    bound2 = t.process_bind_param(bound, None)
    assert bound2 == bound

    # Reading from DB yields plaintext
    out = t.process_result_value(bound, None)
    assert out == "hello"


def test_encrypted_text_type_keeps_empty(monkeypatch) -> None:
    _set_test_key(monkeypatch)
    t = EncryptedText()
    assert t.process_bind_param("", None) == ""
    assert t.process_result_value("", None) == ""

