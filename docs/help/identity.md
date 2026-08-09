# Identity — CLI Login to a Control Plane or Any OIDC Service

Authenticate the CLI to strata's control plane (or any OIDC/OAuth2-protected
service) without strata ever storing a password.

Strata is a relying party, never a credential store: an `identity`-capable
integration drives an OIDC/OAuth2 login against a configured identity provider
(Azure AD, AWS IAM Identity Center, Google, Auth0, GitHub OAuth, or any
generic OIDC issuer), caches the resulting session locally, and refreshes it
silently. There is no `strata login` command — login triggers lazily the
first time a command needs a session and finds none cached.

See ADR-0067 for the full design (session model, RBAC, and why ingest tokens
from ADR-0065 can never be upgraded into a human session).

---

## Configured Under `spec.integrations`

An identity provider is declared like any other integration — no separate
`spec.identity` block — with the new `identity` capability:

```yaml
spec:
  integrations:
    - name: strata-control-plane
      type: azure_ad          # or: google, aws_identity_center, auth0, github_oauth, generic_oidc
      capabilities: [identity]
      authentication:
        method: oauth2
        oauth2:
          client_id: OIDC_CLIENT_ID       # env var name holding the app registration's client id
          client_secret: UNUSED           # required by the model; device-code is a public-client flow
          tenant_id: OIDC_TENANT_ID       # azure_ad only — env var name holding the tenant id
```

A workspace can declare more than one `identity`-capable integration — one
pointed at strata's own control plane, another at an unrelated OIDC-protected
service the CLI needs to call. Each is resolved, cached, refreshed, and
health-checked independently.

---

## Providers

| Type                  | Provider                  | Notes                                                                                                                                                                                  |
| --------------------- | ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `azure_ad`            | Azure AD / Entra ID       | Reuses an already-authenticated `azure_cli` session before falling back to its own device-code flow — no separate login if you already ran `az login`.                                 |
| `google`              | Google / Google Workspace | Reuses an already-authenticated `gcloud_cli` session (via `gcloud auth print-identity-token`) before falling back to its own OIDC flow.                                                |
| `aws_identity_center` | AWS IAM Identity Center   | Standalone — no reuse of `aws_cli` (access-key/STS credentials are not OIDC tokens). Uses IAM Identity Center's `sso-oidc` device-authorization flow with dynamic client registration. |
| `auth0`               | Auth0 (managed IdP)       | Turnkey option for teams with no existing enterprise IdP.                                                                                                                              |
| `github_oauth`        | GitHub OAuth App          | Zero-infrastructure default for the smallest teams; classic OAuth2 device flow, no OIDC discovery.                                                                                     |
| `generic_oidc`        | Any standard OIDC issuer  | Okta, Keycloak, PingFederate, or anything exposing standard OIDC discovery.                                                                                                            |

`endpoints.address` overrides provider defaults where relevant (e.g. a
non-default Azure AD issuer, or the `generic_oidc` issuer URL itself).

---

## Checking and Triggering Login — `sln doctor`

No dedicated login command. `strata sln doctor --deep` already loops over
every configured integration and calls `check_auth()` — an `identity`-capable
integration is picked up automatically, the same way `azure_cli`/`aws_cli`/
`gcloud_cli` already are.

```
strata sln doctor --deep            # reports whether the cached session is still valid
strata sln doctor --deep --login    # also drives the login inline if it isn't
```

For `azure_cli`/`aws_cli`/`gcloud_cli`, `--login` changes nothing — those
still point at their external tool (`az login`, etc.), since strata only
checks their authentication rather than performing it.

---

## Actor Attribution

When a control-plane session is active, the authenticated identity (`sub`/
`email`/`preferred_username`) becomes the `actor` on audit records — it
outranks the CLI-local resolution chain (cloud CLI → CI actor env var → OS
login) from ADR-0066, because strata itself performed the authentication
rather than merely reading an ambient credential.

---

## Discovery

- `strata help --topic identity` — this page
- `strata help --topic audit` — deploy-log and SIEM sinks, `actor` attribution
- `strata sln doctor --deep --login` — check and fix login for every configured integration
