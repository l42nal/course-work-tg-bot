from __future__ import annotations

from typing import Optional

from sqlalchemy.types import TEXT, TypeDecorator

from ..security.encryption import PREFIX, decrypt_text, encrypt_text


class EncryptedText(TypeDecorator):
    """
    SQLAlchemy type that stores ciphertext in DB, returns plaintext in Python.

    Backed by TEXT column type (no schema change required).
    """

    impl = TEXT
    cache_ok = True

    def process_bind_param(self, value: Optional[str], dialect) -> Optional[str]:
        if value is None:
            return None
        if value == "":
            return ""
        # Be defensive: if someone passes already-encrypted value, don't double-encrypt.
        if isinstance(value, str) and value.startswith(PREFIX):
            return value
        return encrypt_text(str(value))

    def process_result_value(self, value: Optional[str], dialect) -> Optional[str]:
        if value is None:
            return None
        if value == "":
            return ""
        return decrypt_text(str(value))

