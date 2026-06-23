"""Cryptographic secret generation utilities.

This module is the single implementation used by both the CLI command
(``strata secret generate``) and the ValueController seed-on-missing flow.
Controllers must not import from commands/, so the function lives here.
"""

from __future__ import annotations

import base64
import secrets
import string
import time
import uuid

_ALPHANUMERIC = string.ascii_letters + string.digits
# Symbols commonly accepted by password policies; excludes ambiguous/shell-special chars
_SYMBOLS = "!@#$%^&*()-_=+"
_PASSWORD_CHARS = _ALPHANUMERIC + _SYMBOLS
_NUMERIC = string.digits


def _uuid7() -> uuid.UUID:
    """Generate a UUID version 7 (time-ordered) per RFC 9562.

    Layout (128 bits):
      bits 127-80 : 48-bit Unix timestamp in milliseconds
      bits 79-76  : version = 0x7
      bits 75-64  : 12 random bits (rand_a)
      bits 63-62  : variant = 0b10
      bits 61-0   : 62 random bits (rand_b)
    """
    ts_ms = int(time.time() * 1000)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    int_val = (ts_ms & 0xFFFFFFFFFFFF) << 80 | 0x7 << 76 | rand_a << 64 | 0b10 << 62 | rand_b
    return uuid.UUID(int=int_val)


def generate_secret(fmt: str, length: int) -> str:
    """Return a cryptographically secure secret in the requested format.

    Args:
        fmt:    One of ``urlsafe``, ``hex``, ``alphanumeric``, ``password``,
                ``numeric``, ``base64``, ``uuid4``, ``uuid7``.
        length: For ``urlsafe`` / ``hex`` / ``base64``: number of random *bytes*
                (the resulting string will be longer after encoding).
                For ``alphanumeric`` / ``password`` / ``numeric``: exact character count.
                Ignored for ``uuid4`` / ``uuid7``.

    Returns:
        The generated secret as a plain string.
    """
    if fmt == "urlsafe":
        return secrets.token_urlsafe(length)
    if fmt == "hex":
        return secrets.token_hex(length)
    if fmt == "alphanumeric":
        return "".join(secrets.choice(_ALPHANUMERIC) for _ in range(length))
    if fmt == "password":
        # Guarantee at least one character from each required class.
        if length < 4:
            raise ValueError("password format requires length >= 4.")
        mandatory = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits),
            secrets.choice(_SYMBOLS),
        ]
        rest = [secrets.choice(_PASSWORD_CHARS) for _ in range(length - 4)]
        pool = mandatory + rest
        secrets.SystemRandom().shuffle(pool)
        return "".join(pool)
    if fmt == "numeric":
        return "".join(secrets.choice(_NUMERIC) for _ in range(length))
    if fmt == "base64":
        return base64.b64encode(secrets.token_bytes(length)).decode()
    if fmt == "uuid4":
        return str(uuid.uuid4())
    if fmt == "uuid7":
        return str(_uuid7())
    raise ValueError(f"Unknown secret format: {fmt!r}")
