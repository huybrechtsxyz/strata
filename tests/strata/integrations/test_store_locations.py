#!/usr/bin/env python3
"""`StoreIntegration.location()` (docs/work/deployment-documentation.md, section 2): connection settings only,
never a credential or token, and never a store call."""

import json

from strata.integrations.azure_keyvault_resolver import AzureKeyVaultResolver
from strata.integrations.infisical_resolver import InfisicalResolver


def test_key_vault_location_is_the_vault_url_and_the_name(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://kv.vault.azure.net/")
    assert AzureKeyVaultResolver().location("sql-pwd") == {"vault": "https://kv.vault.azure.net/", "name": "sql-pwd"}


def test_key_vault_location_without_a_url_is_unknown(monkeypatch):
    monkeypatch.delenv("AZURE_KEYVAULT_URL", raising=False)
    assert AzureKeyVaultResolver().location("sql-pwd") == {"vault": None, "name": "sql-pwd"}


def test_infisical_location_never_carries_credentials(monkeypatch):
    monkeypatch.setenv("INFISICAL_ADDR", "https://app.infisical.com")
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj-1")
    monkeypatch.setenv("INFISICAL_ENVIRONMENT", "prod")
    monkeypatch.setenv("INFISICAL_TOKEN", "tok-SECRET-1")
    monkeypatch.setenv("INFISICAL_CLIENT_ID", "cid-SECRET-2")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "cs-SECRET-3")

    location = InfisicalResolver().location("SQL_PWD")

    assert location == {
        "address": "https://app.infisical.com",
        "project_id": "proj-1",
        "environment": "prod",
        "path": "/",
        "name": "SQL_PWD",
    }
    assert "SECRET" not in json.dumps(location)
