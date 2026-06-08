"""Utility for generating cryptographically secure secret values."""

from __future__ import annotations

import secrets
import string
import time
import uuid

_ALPHANUMERIC = string.ascii_letters + string.digits


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
        fmt:    One of ``urlsafe``, ``hex``, ``alphanumeric``, ``uuid4``, ``uuid7``.
        length: For ``urlsafe`` / ``hex``: number of random *bytes* to source
                (the resulting string will be longer due to encoding).
                For ``alphanumeric``: the exact number of output characters.
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
    if fmt == "uuid4":
        return str(uuid.uuid4())
    if fmt == "uuid7":
        return str(_uuid7())
    raise ValueError(f"Unknown secret format: {fmt!r}")
