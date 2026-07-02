# Publishing

The Strata VS Code extension is published to the [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=huybrechtsxyz.xyz-strata) as part of the automated release pipeline.

## Automated publishing (CI)

Publishing is triggered automatically by `ci-release.yml` when a `vX.Y.Z` tag is pushed:

1. `ci-build.yml` compiles the TypeScript, packages a `.vsix`, and uploads it as the `vsix` artifact.
2. `ci-release.yml` downloads the artifact, attaches it to the GitHub Release, then publishes to the Marketplace using `@vscode/vsce`.

The only secret required is `VSCE_PAT` — a Personal Access Token scoped to **Marketplace > Manage**.

### Adding the VSCE_PAT secret

1. Go to [https://marketplace.visualstudio.com/manage](https://marketplace.visualstudio.com/manage) and sign in with the publisher account (`huybrechtsxyz`).
2. Click your account icon → **Personal access tokens** → **New Token**.
3. Set:
   - **Name:** `VSCE_PAT`
   - **Organization:** All accessible organizations
   - **Scopes:** Marketplace → **Manage**
   - **Expiry:** 1 year (maximum)
4. Copy the token.
5. In the GitHub repository go to **Settings → Secrets and variables → Actions → New repository secret**.
6. Name: `VSCE_PAT`, value: the token you copied.

## Manual publishing

To publish manually from a local machine:

```bash
cd src/vscode
npm ci
npx tsc -p tsconfig.json --noEmit false
npx @vscode/vsce package --no-dependencies --skip-license --out ../../dist/
npx @vscode/vsce publish --packagePath ../../dist/xyz-strata-*.vsix
```

`vsce publish` will prompt for your PAT if `VSCE_PAT` is not set in the environment.

Alternatively, set the environment variable first:

```powershell
$env:VSCE_PAT = "<your-token>"
npx @vscode/vsce publish --packagePath ../../dist/xyz-strata-*.vsix
```

## Bumping the extension version

The extension version in [src/vscode/package.json](../../src/vscode/package.json) is **independent** of the Python CLI version in `VERSION.txt`. Bump it manually before releasing when the extension has user-visible changes:

```powershell
# In src/vscode/
npm version minor   # or patch / major
```

This updates `version` in `package.json` and creates a local git commit. Include the commit in your release branch before merging.

## Verifying a release

After the pipeline completes, confirm the new version is live:

```bash
npx @vscode/vsce show huybrechtsxyz.xyz-strata
```

Or visit [https://marketplace.visualstudio.com/items?itemName=huybrechtsxyz.xyz-strata](https://marketplace.visualstudio.com/items?itemName=huybrechtsxyz.xyz-strata).
