# How To: Check That a Deployed App Actually Works

A deploy succeeds when its tool does. Terraform's `apply` succeeds as soon as the cloud accepted the change, so a
web app that never starts, because a configuration setting is missing, still ends in `success`. This guide adds a
check that runs after the step, waits for the app to answer, and fails the run when it does not.

The reference is [Verifying a deploy](../config/deployment.md#verifying-a-deploy).

## 1. Expose what the check needs

The check needs the app's address. Make the Terraform root module output it:

```hcl
output "default_hostname" {
  value = azurerm_linux_web_app.web.default_hostname
}
```

`deploy run` collects a step's outputs right after its apply, so the check can read it as
`${output:apply_web.default_hostname}`.

## 2. Write the check

A check is any command. Exit 0 when the app works, anything else when it does not; the first line you print becomes
the check's message in the run's output and the audit record.

`checks/http-ok.ps1` (PowerShell 7):

```powershell
param([string]$HostName)
try {
    $response = Invoke-WebRequest -Uri "https://$HostName/health" -TimeoutSec 20 -SkipHttpErrorCheck
}
catch {
    Write-Output "no answer from $HostName: $($_.Exception.Message)"
    exit 1
}
Write-Output "HTTP $($response.StatusCode) from $HostName"
if ($response.StatusCode -eq 200) { exit 0 } else { exit 1 }
```

Or `checks/http_ok.py`:

```python
import sys
import urllib.error
import urllib.request

host = sys.argv[1]
try:
    with urllib.request.urlopen(f"https://{host}/health", timeout=20) as response:
        print(f"HTTP {response.status} from {host}")
        sys.exit(0 if response.status == 200 else 1)
except urllib.error.HTTPError as error:
    print(f"HTTP {error.code} from {host}")
except OSError as error:
    print(f"no answer from {host}: {error}")
sys.exit(1)
```

Check the app, not the platform: a web app's state says the site is running even while the application inside it
fails at startup. A request to the app is what fails when its configuration is missing.

For a plain request, a script is not needed: strata makes it itself with an `http` check (step 3 shows both). A
`tcp` check (`host`, `port`) only tests that a port accepts connections, for a database or a message broker.

## 3. Add it to the deployment's stage

```yaml
kind: deployment
spec:
  stages:
    - step: apply_web
      verify:
        initial_delay: 30
        checks:
          - name: web-answers
            run: [pwsh, -File, checks/http-ok.ps1, "${output:apply_web.default_hostname}"]
```

`run` is an argument list run from the solution root, never a shell string. For the Python version:
`run: [python, checks/http_ok.py, "${output:apply_web.default_hostname}"]`.

The same check, built in:

```yaml
          - name: web-answers
            type: http
            url: https://${output:apply_web.default_hostname}/health
            expect_status: 200                # the default; a list accepts several
```

When the app answers `application/health+json`, its `status` decides: `pass`, `warn` (degraded: a warning) or
`fail`.

Behind a private CA or a self-signed certificate, the request fails with "the certificate ... is not trusted".
Give the check the CA to trust, as a PEM file in the solution, rather than turning the check off with `insecure`:

```yaml
            ca_file: certs/internal-ca.pem    # relative to the solution root
```

A health endpoint that takes a query (GraphQL, for example) gets `method: POST` and a `body`, with its
`Content-Type` in `headers`.

The check is retried: here 30 seconds after the apply, then every 10 seconds, up to 30 attempts, so the app has about
five minutes to come up. Tune it with `period`, `timeout` and `failure_threshold`.

Run `strata validate` before the first deploy. Besides the tokens, it reports a check script or `ca_file` that is not
in the solution (`verify_script_missing`, `verify_ca_file_unusable`). Without it, the check could not answer, and
the run would stop after the step was applied.

## 4. Deploy

The output, abridged, when the app never comes up:

```console
$ strata deploy run app --force
  deployed step 'apply_web' via TerraformIntegration
  verify step 'apply_web': waiting 30s before the first check
  verify step 'apply_web': check 'web-answers' attempt 1 fail: HTTP 503 from web-prd.azurewebsites.net
  ...
  verify step 'apply_web': check 'web-answers' fail after 30 attempt(s): HTTP 500 from web-prd.azurewebsites.net
error: Step 'apply_web': verify check 'web-answers' failed after 30 attempt(s): HTTP 500 from
       web-prd.azurewebsites.net. The step was applied, but the deployment does not work as checked.
       [verify_check_failed]
```

The run exits 3 and its audit record is `partial`: the change was applied, and the record says the app did not work.
Later steps do not run. Nothing is rolled back.

## 5. Introduce a check without blocking

While a check is new, let it report without failing the run:

```yaml
      verify:
        enforcement: warn
```

A failure is then a warning. Set it back to `deny` (the default) once the check is trusted. A check that cannot
answer at all (its command missing, every attempt timing out) follows `on_unknown`: `block` (default), `warn` or
`skip`.

To deploy without the checks, for a pipeline that checks in a later stage, pass `--no-verify`.

## 6. Check the whole deployment

A stage's check runs right after its step. A check that needs everything in place, a request through the front door
that reaches the API and the database, goes in `spec.verify` and runs once after every step:

```yaml
kind: deployment
spec:
  verify:
    checks:
      - name: checkout-flow
        run: [python, checks/checkout_flow.py, "${output:apply_web.default_hostname}"]
        env:
          API_KEY: ${secret:SMOKE_API_KEY}
```

It may read the outputs of any step. It does not run when `--stage` or `--scope` deploys only some steps.

## 7. Check again later

`strata deploy verify` runs the same checks against what is deployed now, and changes nothing:

```console
$ strata deploy verify app
  verify step 'apply_web': check 'web-answers' pass after 1 attempt(s): HTTP 200 from web-prd.azurewebsites.net
  verify the deployment: check 'checkout-flow' pass after 1 attempt(s): order 4711 placed
  verified: yes
```

It skips the initial delays, reads `${output:}` from the workspace's `output_store`, and exits 3 when a check fails.
A pipeline that deploys with `--no-verify` runs it in a later stage; a scheduled pipeline can run it to check the
deployment keeps working.

## 8. Check that a destroy removed it

A destroy succeeds when its tool does, too. A check with `when: [destroy]` runs after the step is destroyed, and
`expect: down` passes once the app no longer answers:

```yaml
          - name: web-gone
            when: [destroy]
            type: http
            url: https://${output:apply_web.default_hostname}/
            expect: down
```

A script check can do the same (`STRATA_ACTION` is `destroy`): exit 0 when what it looks for is gone. The check's
`${output:}` comes from the output store, read before the first step is destroyed, so the workspace needs an
`output_store`. A check that should run after both, such as one that tests a shared DNS name, says
`when: [apply, destroy]`. `deploy verify` runs only the `apply` checks.

## 9. Run a Helm chart's own tests

Helm already waits for a release to be ready before `deploy run` goes on. A chart that ships tests (pods with the
`helm.sh/hook: test` annotation) can also say whether the release *works*. A `helm` check runs them after the step:

```yaml
    - step: apply_charts
      verify:
        checks:
          - name: chart-tests
            type: helm
            release: web                      # as the step installed it
            namespace: apps
            timeout: 300                      # helm test --timeout
```

It runs once, against the deployment's `kube_context`: a failed test fails the step, and the message is Helm's
error. `tests: [web-test-connection]` runs only those tests. `strata deploy verify` runs them again.

## 10. Wait for a GitOps controller

A GitOps step succeeds once its file is pushed; the Argo CD or Flux controller applies it later. strata does not
call the controller, so wait for it with a check that reads the controller's status. To wait until Flux reports the
Kustomization ready, `kubectl` alone does, on the deployment's cluster:

```yaml
          - name: flux-ready
            run: [kubectl, --context, "${deployment:kube_context}", -n, flux-system, wait, kustomization/apps,
                  --for=condition=Ready, --timeout=20s]
```

Ready is not yet *this* deploy: the controller may still be on the previous commit. A script can compare the
revision with the commit the step pushed. It reads the deployment's context from `STRATA_KUBE_CONTEXT`, which every
script check gets. `checks/flux-applied.ps1`:

```powershell
param([string]$Commit)
$status = kubectl --context $env:STRATA_KUBE_CONTEXT -n flux-system get kustomization apps -o json |
    ConvertFrom-Json
$ready = $status.status.conditions | Where-Object type -eq 'Ready'
if ($ready.status -ne 'True') { Write-Output "not ready: $($ready.message)"; exit 1 }
if ($status.status.lastAppliedRevision -notlike "*$Commit") {
    Write-Output "ready, still at $($status.status.lastAppliedRevision)"; exit 1
}
Write-Output "applied $Commit"
```

```yaml
    - step: publish_gitops
      verify:
        checks:
          - name: flux-applied
            run: [pwsh, -File, checks/flux-applied.ps1, "${output:publish_gitops.commit_sha}"]
```

It passes only once the controller applied *this* commit, not an earlier one. For Argo CD, read the Application
instead (`-n argocd get application apps`): `.status.sync.revision` is the commit, and `.status.health.status` must
be `Healthy`. The check is retried every `period` seconds, so a controller that syncs every few minutes has the
default five minutes to catch up; raise `failure_threshold` for a slower one.
