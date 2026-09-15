"""Machine-to-machine bearer-token verification against several trusted OIDC issuers
(ADR-0067 Step 10).

This is the M2M counterpart of `oidc_relying_party.OidcRelyingParty.verify_id_token()`
for human login, and deliberately shares its verification primitives (via
`fetch_discovery_document()`/`fetch_jwks_document()`) rather than duplicating them —
see ADR-0067's "Step 10 design" section: every M2M credential this ADR supports —
a GitHub Actions OIDC token, an Azure DevOps workload-identity-federation token, a
Client Credentials access token, or an Azure/GCP Managed/Workload Identity token —
reduces to the same thing at this boundary: a signed JWT from a trusted issuer,
checked against that issuer's JWKS for an expected audience. One verifier, not one
per caller type.

Unlike `OidcRelyingParty` (configured against exactly one human-login IdP per `serve
run` invocation), this module supports *several* trusted issuers at once — a real
deployment plausibly needs to trust GitHub Actions' issuer *and* Azure DevOps' issuer
*and* a generic Client-Credentials-only IdP simultaneously.

Deliberately out of scope (see ADR-0067's "Step 10 design" section):
- No `nonce` check — there is no interactive flow to bind one to, unlike human login.
- No RBAC — Step 9. A verified token proves *which* trusted caller this is, not what
  it may do; role/subject mapping is read from `TrustedIssuer.subject_claim` for a
  future authorization layer to consume, not enforced here.
- No AWS keyless (Workload-Identity-equivalent) support — explicitly on hold, a scope
  decision. An AWS caller uses the classic Client Credentials fallback instead, which
  *is* covered here (its access token is just another JWT from a trusted issuer).
- `/v1/events` (ADR-0065 ingest tokens) never goes through this module — those are
  workspace-scoped, DB-hash-verified, non-JWT credentials, structurally separate by
  design (see `server/routes/security.py`'s `verify_ingest_token`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from joserfc.errors import JoseError
from joserfc.jwt import JWTClaimsRegistry
from joserfc.jwt import decode as _joserfc_jwt_decode

from strata.server.auth.oidc_relying_party import fetch_discovery_document, fetch_jwks_document


@dataclass(frozen=True)
class TrustedIssuer:
    """One externally-trusted OIDC issuer allowed to present M2M access tokens.

    `name` is a human-readable label (e.g. "github-actions", "azure-devops") used only
    in error messages and as the claims' `_trusted_issuer_name` marker — never part of
    the trust decision itself, which is entirely `issuer` + `audience`.

    `subject_claim` names which verified claim identifies the calling principal for a
    future RBAC layer (Step 9) to map onto a role — e.g. `sub` for most IdPs,
    `repository` for a GitHub Actions token scoped to a specific repo. Not enforced
    here; carried through so Step 9 does not have to re-derive it per issuer type.
    """

    name: str
    issuer: str
    audience: str
    subject_claim: str = "sub"


def _peek_unverified_issuer(token: str) -> str:
    """Return the `iss` claim from *token* without verifying its signature.

    This is the one legitimate use of an unverified peek: selecting *which* trusted
    issuer's JWKS to check the token's signature against. It is never used to accept
    any claim as true — `_verify_against()` re-derives and verifies `iss` (along with
    every other claim) from the signature-checked token before returning anything.
    Deliberately not `strata.utils.jwt_utils.decode_payload_unverified` — that module's
    own docstring warns it must never be used to accept a token from a remote caller,
    which is exactly what this function's *caller* (a network request) is; keeping this
    peek local and narrowly scoped (one claim, for issuer selection only) avoids
    repurposing a utility whose safety contract explicitly excludes this use.
    """
    import base64
    import json

    try:
        payload_segment = token.split(".")[1]
        padding = "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_segment + padding))
    except Exception as exc:
        raise ValueError(f"malformed token: {exc}") from exc

    issuer = payload.get("iss")
    if not issuer:
        raise ValueError("token has no 'iss' claim")
    return str(issuer)


class M2mVerifier:
    """Verifies a caller-presented bearer token against one of several configured
    trusted issuers (ADR-0067 Step 10).
    """

    def __init__(self, trusted_issuers: List[TrustedIssuer]) -> None:
        self._trusted_issuers = trusted_issuers
        self._discovery_cache: Dict[str, Dict[str, Any]] = {}
        self._jwks_cache: Dict[str, Any] = {}

    def _discovery_for(self, issuer: str) -> Dict[str, Any]:
        if issuer not in self._discovery_cache:
            self._discovery_cache[issuer] = fetch_discovery_document(issuer)
        return self._discovery_cache[issuer]

    def _jwks_for(self, issuer: str, discovery: Dict[str, Any]) -> Any:
        if issuer not in self._jwks_cache:
            jwks_uri = discovery.get("jwks_uri")
            if not jwks_uri:
                raise ValueError(f"trusted issuer '{issuer}' discovery document does not advertise a jwks_uri")
            self._jwks_cache[issuer] = fetch_jwks_document(jwks_uri)
        return self._jwks_cache[issuer]

    def _verify_against(self, token: str, trusted: TrustedIssuer) -> Dict[str, Any]:
        discovery = self._discovery_for(trusted.issuer)
        key_set = self._jwks_for(trusted.issuer, discovery)
        try:
            decoded = _joserfc_jwt_decode(token, key_set)
            claims_registry = JWTClaimsRegistry(
                leeway=30,  # same clock-skew tolerance as human id_token verification
                iss={"essential": True, "value": discovery.get("issuer", trusted.issuer)},
                aud={"essential": True, "value": trusted.audience},
                exp={"essential": True},
            )
            claims_registry.validate(decoded.claims)
        except JoseError as exc:
            raise ValueError(f"m2m token verification failed for trusted issuer '{trusted.name}': {exc}") from exc

        claims = dict(decoded.claims)
        claims["_trusted_issuer_name"] = trusted.name
        return claims

    def verify(self, token: str) -> Dict[str, Any]:
        """Verify *token* against whichever configured trusted issuer issued it.

        Raises `ValueError` if the token is malformed, its `iss` claim matches no
        configured trusted issuer, or signature/claim verification then fails against
        every trusted issuer entry sharing that `iss` (more than one entry can share an
        issuer with a different expected `audience` — e.g. two apps registered with the
        same IdP).
        """
        issuer_claim = _peek_unverified_issuer(token)
        candidates = [ti for ti in self._trusted_issuers if ti.issuer.rstrip("/") == issuer_claim.rstrip("/")]
        if not candidates:
            raise ValueError(f"token issuer '{issuer_claim}' is not a configured trusted issuer")

        last_error: Optional[ValueError] = None
        for trusted in candidates:
            try:
                return self._verify_against(token, trusted)
            except ValueError as exc:
                last_error = exc
        assert last_error is not None  # candidates is non-empty, so the loop always sets this
        raise last_error
