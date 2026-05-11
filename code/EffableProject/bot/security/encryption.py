from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:v1:"
KEY_ENV = "DATA_ENCRYPTION_FERNET_KEY"


def _require_key() -> bytes:
    key = (os.getenv(KEY_ENV) or "").strip()
    if not key:
        raise RuntimeError(
            f"Missing encryption key env var {KEY_ENV}. "
            "Set it to a Fernet key (e.g. Fernet.generate_key())."
        )
    return key.encode("utf-8")


@lru_cache(maxsize=1)
def get_fernet() -> Fernet:
    return Fernet(_require_key())


def encrypt_text(plaintext: Optional[str]) -> Optional[str]:
    if plaintext is None:
        return None
    if plaintext == "":
        # Keep empty strings stable; also helps avoid storing PREFIX for “no content”.
        return ""
    token = get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return f"{PREFIX}{token}"


def decrypt_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if value == "":
        return ""
    if not value.startswith(PREFIX):
        raise ValueError("Expected encrypted value with enc:v1: prefix")
    token = value[len(PREFIX) :]
    try:
        return get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("Failed to decrypt value (invalid token or wrong key)") from e

