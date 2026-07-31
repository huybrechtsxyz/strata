# Cookbook: Hash a Secret Before Storing It

> **Audience:** Operator who needs to store a *transformed* (e.g. hashed) version of a generated secret, while keeping the raw plaintext retrievable for a separate, human purpose (e.g. logging into an admin UI).

---

## When to use this

Some backends don't accept a raw secret directly — they require the **stored** value to already be a one-way transform of it. The canonical example is [Vaultwarden](https://github.com/dani-garcia/vaultwarden)'s `ADMIN_TOKEN`: Vaultwarden expects an Argon2 hash in its config, but a human still needs the **raw** value separately — to paste it into another vault (e.g. Bitwarden) or type it in during an emergency.

`strata secret put KEY --generate` alone can't do this: it generates a value and writes it straight to the store as-is. There's no built-in transform step between "generate" and "store".

This recipe uses only existing CLI commands (`secret put --generate`, `secret get --unmask`, `secret put --value`) to bootstrap **two separate keys** — one holding the raw value, one holding the transformed value — with no code changes required.

---

## The recipe

Run this once, interactively, from your own terminal.

### PowerShell

```powershell
# 1. Bootstrap the raw secret once (human-run, interactive)
strata secret put VAULTWARDEN_ADMIN_TOKEN_RAW --generate -f deploy.yaml

# 2. Read the raw value back
$raw = strata secret get VAULTWARDEN_ADMIN_TOKEN_RAW --unmask -f deploy.yaml

# 3. Transform it (Vaultwarden's own hashing tool, reads stdin, writes stdout)
$hash = $raw | docker run --rm -i vaultwarden/server /vaultwarden hash --preset owasp

# 4. Store the transformed value under a second, separate key
strata secret put VAULTWARDEN_ADMIN_TOKEN_HASH --value $hash -f deploy.yaml
```

### bash

```bash
# 1. Bootstrap the raw secret once (human-run, interactive)
strata secret put VAULTWARDEN_ADMIN_TOKEN_RAW --generate -f deploy.yaml

# 2. Read the raw value back
raw=$(strata secret get VAULTWARDEN_ADMIN_TOKEN_RAW --unmask -f deploy.yaml)

# 3. Transform it (Vaultwarden's own hashing tool, reads stdin, writes stdout)
hash=$(echo -n "$raw" | docker run --rm -i vaultwarden/server /vaultwarden hash --preset owasp)

# 4. Store the transformed value under a second, separate key
strata secret put VAULTWARDEN_ADMIN_TOKEN_HASH --value "$hash" -f deploy.yaml
```

---

## Required YAML

Both keys must be declared in your environment file, and **both need an integration-backed `store:`** (`bitwarden`, `vault`, `azure-keyvault`, or `infisical`). `put` has no integration to call for `constant`, `environment`, or `github` — it will error with `No integration registered for store type ...`.

```yaml
secrets:
  - key: VAULTWARDEN_ADMIN_TOKEN_RAW
    store: bitwarden
    value: vw-admin-token-raw
    generate:
      type: urlsafe
      length: 48

  - key: VAULTWARDEN_ADMIN_TOKEN_HASH
    store: azure-keyvault
    value: vw-admin-token-hash
```

The raw key keeps its normal `generate:` spec (and can still be rotated normally — see [Secrets, Variables, and Features](secrets-variables-features.md)). The hash key has no `generate:` spec at all — it's only ever written via `secret put --value` in step 4.

---

## Why this is a documented recipe, not a built-in feature

A design review looked at adding a `post_generate:` hook to the generate spec, so this transform could run automatically. It was rejected: running an arbitrary transform automatically during an unattended `build run` / `deploy run` generate-on-missing or auto-rotate would risk permanently losing the one-way-hashed raw value with nobody present to capture it — there'd be no way to recover it afterward. This recipe keeps the raw secret's full existing lifecycle (generate, rotate, get) completely untouched, and only needs to be run once, interactively, by a human. If this pattern becomes common across more than this one use case, a first-class `derive:` secret concept may be added later — until then, this doc is the interim, zero-code-change answer.

---

## Security notes

- **Run steps 1–4 interactively from your own terminal, not from a CI pipeline.** The raw value only exists transiently in your session.
- The raw value is captured only into a local shell variable and piped directly via stdin — it is never written to a temp file, and never passed as a literal CLI argument.
- ⚠️ **Known caveat:** `strata secret put --value <value>` currently records the full command arguments (including the value) in strata's own audit log (`.strata/deploy-log/`). This is a separate, already-tracked issue unrelated to this recipe — until it's fixed, step 4 above will write the transformed hash value into your local audit log.
- Never paste the raw value into chat, tickets, or commit messages. Treat step 2's output as sensitive for the duration of your terminal session only.

---

## Verify before you rely on it

Before using this in a real rotation, smoke-test the transform command once in your own environment and confirm it prints **exactly one line**, with no extra prompt text mixed into stdout:

```powershell
"test-value" | docker run --rm -i vaultwarden/server /vaultwarden hash --preset owasp
```

If the output has extra lines or banner text, `$hash` / `hash` will contain more than the hash itself.

---

## Checklist

- [ ] Raw key declared with `generate:` spec, backed by an integration store (`bitwarden`, `vault`, `azure-keyvault`, or `infisical`)
- [ ] Hash key declared separately, also backed by an integration store, with no `generate:` spec
- [ ] Steps run interactively, never from CI
- [ ] Transform command smoke-tested once (single-line output confirmed)
- [ ] Raw value never pasted outside your terminal session
