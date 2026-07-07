"""Secret metadata returned by store integrations that support rotation age checks."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class SecretMetadata:
    """Metadata about a secret in a store — used for rotation age calculations.

    Attributes:
        created_at:  When the secret was first created (UTC).
        updated_at:  When the secret was last modified (UTC).
        expires_on:  Store-native expiry time, if set (UTC).
        version:     Store-specific version identifier (e.g. Key Vault version GUID).
    """

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    expires_on: Optional[datetime] = None
    version: Optional[str] = None
