from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

# RFC 9106/OWASP-aligned Argon2id baseline; parameters are explicit and can be raised later.
_password_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=19_456,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        return _password_hasher.verify(encoded_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
