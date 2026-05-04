<#
  Tests.ps1  Manual test reference for xyz-platform CLI
  
  Two sections:
    [FLOW]      End-to-end workflow test  run top to bottom to validate the full session lifecycle
    [REFERENCE] Per-command variations  pick individual lines to test specific flags
#>


# ------------------------------------------------------------------------------
# [FLOW] End-to-end session lifecycle
# [FLOW] Optional: set env vars to test the resolution order
#   Explicit flag > XYZ_* env var > .platform/cli.yaml > built-in default
# ------------------------------------------------------------------------------
# $env:XYZ_WORK_PATH  = (Resolve-Path $app).Path   # auto-resolve work path
# $env:XYZ_OUTPUT     = "json"                      # default output format
# $env:XYZ_VERBOSE    = "true"                      # enable verbose log replay
# $env:XYZ_QUIET      = "true"                      # suppress all output

$app = ".app"

New-Item -Path $app -ItemType Directory -Force

.\scripts\Run.ps1 init --name "test-solution" --work-path $app

.\scripts\Run.ps1 config set --work-path $app output console

.\scripts\Run.ps1 repo add traefik https://github.com/huybrechtsxyz/xyz-traefik.git --work-path $app

.\scripts\Run.ps1 repo sync --name traefik --force --work-path $app

.\scripts\Run.ps1 profile add production --work-path $app

.\scripts\Run.ps1 profile add development --work-path $app

.\scripts\Run.ps1 ref envfile add base "@traefik/environments/base.env" --profile production --work-path $app

.\scripts\Run.ps1 profile activate development --work-path $app

.\scripts\Run.ps1 profile list --work-path $app

.\scripts\Run.ps1 profile show production --work-path $app

Remove-Item -Path $app -Recurse -Force -ErrorAction SilentlyContinue


# ==============================================================================
# [REFERENCE] Basic commands
# ==============================================================================

.\scripts\Run.ps1 -h
.\scripts\Run.ps1

.\scripts\Run.ps1 version -h
.\scripts\Run.ps1 version
.\scripts\Run.ps1 version --output console
.\scripts\Run.ps1 version --output json
.\scripts\Run.ps1 version --output text

# ==============================================================================
# [REFERENCE] init — initialize a new solution workspace
# ==============================================================================

.\scripts\Run.ps1 init -h
.\scripts\Run.ps1 init --name "test-solution" --work-path $app
.\scripts\Run.ps1 init --name "test-solution" --work-path $app --output json
.\scripts\Run.ps1 init --name "test-solution" --work-path $app --output text

# ==============================================================================
# [REFERENCE] clean — remove workspace artifacts (logs, temp files)
# ==============================================================================

.\scripts\Run.ps1 clean -h
.\scripts\Run.ps1 clean --work-path $app --dry-run
.\scripts\Run.ps1 clean --work-path $app
.\scripts\Run.ps1 clean --work-path $app --output json

# ==============================================================================
# [REFERENCE] config — persist workspace defaults into .platform/cli.yaml
# ==============================================================================

.\scripts\Run.ps1 config -h
.\scripts\Run.ps1 config

# set — define a default value for the current workspace
.\scripts\Run.ps1 config set -h
.\scripts\Run.ps1 config set --work-path $app output json
.\scripts\Run.ps1 config set --work-path $app output console
.\scripts\Run.ps1 config set --work-path $app output text
.\scripts\Run.ps1 config set --work-path $app verbose true
.\scripts\Run.ps1 config set --work-path $app verbose false
.\scripts\Run.ps1 config set --work-path $app quiet true
.\scripts\Run.ps1 config set --work-path $app quiet false

# list — show all current defaults
.\scripts\Run.ps1 config list -h
.\scripts\Run.ps1 config list --work-path $app
.\scripts\Run.ps1 config list --work-path $app --output json
.\scripts\Run.ps1 config list --work-path $app --output text

# unset — remove a specific default
.\scripts\Run.ps1 config unset -h
.\scripts\Run.ps1 config unset --work-path $app output
.\scripts\Run.ps1 config unset --work-path $app verbose
.\scripts\Run.ps1 config unset --work-path $app quiet

.\scripts\Run.ps1 config env -h

# ==============================================================================
# [REFERENCE] repo — manage repositories registered in the solution
# ==============================================================================

.\scripts\Run.ps1 repo -h
.\scripts\Run.ps1 repo

# repo add — register a repository (no cloning; clone deferred to xyz repo sync)
.\scripts\Run.ps1 repo add -h
.\scripts\Run.ps1 repo add my-repo https://github.com/huybrechtsxyz/xyz-traefik.git --work-path $app
.\scripts\Run.ps1 repo add my-repo-branch https://github.com/huybrechtsxyz/xyz-traefik.git --branch main --work-path $app
.\scripts\Run.ps1 repo add my-repo-path https://github.com/huybrechtsxyz/xyz-traefik.git --path repos/custom --work-path $app
.\scripts\Run.ps1 repo add my-repo-all https://github.com/huybrechtsxyz/xyz-traefik.git --branch develop --path repos/custom --work-path $app
.\scripts\Run.ps1 repo add json-repo https://github.com/org/json-repo.git --work-path $app --output json
.\scripts\Run.ps1 repo add text-repo https://github.com/org/text-repo.git --work-path $app --output text

# duplicate name should fail
.\scripts\Run.ps1 repo add my-repo https://github.com/org/my-repo.git --work-path $app

# repo list — show repositories registered in the solution
.\scripts\Run.ps1 repo list -h
.\scripts\Run.ps1 repo list --work-path $app --output console
.\scripts\Run.ps1 repo list --work-path $app --output json
.\scripts\Run.ps1 repo list --work-path $app --output text
.\scripts\Run.ps1 repo list --name my-repo --work-path $app
.\scripts\Run.ps1 repo list --name my-repo --work-path $app --output json
.\scripts\Run.ps1 repo list --name no-such-repo --work-path $app

# repo remove — unregister a repository from the solution
.\scripts\Run.ps1 repo remove -h
.\scripts\Run.ps1 repo remove my-repo --work-path $app
.\scripts\Run.ps1 repo remove my-repo --work-path $app --output json
.\scripts\Run.ps1 repo remove my-repo --work-path $app --output text
.\scripts\Run.ps1 repo remove my-repo --work-path $app --purge
.\scripts\Run.ps1 repo remove my-repo --work-path $app --purge --output json
.\scripts\Run.ps1 repo remove no-such-repo --work-path $app

# repo sync — clone or update registered repositories
.\scripts\Run.ps1 repo sync -h
.\scripts\Run.ps1 repo sync --work-path $app
.\scripts\Run.ps1 repo sync --work-path $app --output json
.\scripts\Run.ps1 repo sync --work-path $app --output text
.\scripts\Run.ps1 repo sync --name my-repo --work-path $app
.\scripts\Run.ps1 repo sync --name my-repo --work-path $app --output json
.\scripts\Run.ps1 repo sync --work-path $app --force
.\scripts\Run.ps1 repo sync --name my-repo --work-path $app --force
.\scripts\Run.ps1 repo sync --name no-such-repo --work-path $app

# ==============================================================================
# [REFERENCE] profile — manage profiles in the solution
# ==============================================================================

.\scripts\Run.ps1 profile -h
.\scripts\Run.ps1 profile

# profile add — create a new profile (first profile auto-activates)
.\scripts\Run.ps1 profile add -h
.\scripts\Run.ps1 profile add production --work-path $app
.\scripts\Run.ps1 profile add development --work-path $app
.\scripts\Run.ps1 profile add staging --work-path $app
.\scripts\Run.ps1 profile add production --work-path $app --output json
.\scripts\Run.ps1 profile add production --work-path $app --output text
.\scripts\Run.ps1 profile add production --work-path $app          # duplicate — should fail

# profile list — show all profiles with active marker
.\scripts\Run.ps1 profile list -h
.\scripts\Run.ps1 profile list --work-path $app --output console
.\scripts\Run.ps1 profile list --work-path $app --output json
.\scripts\Run.ps1 profile list --work-path $app --output text
.\scripts\Run.ps1 profile list --name production --work-path $app
.\scripts\Run.ps1 profile list --name no-such-profile --work-path $app

# profile show — display all ref paths for a profile grouped by type
.\scripts\Run.ps1 profile show -h
.\scripts\Run.ps1 profile show production --work-path $app
.\scripts\Run.ps1 profile show production --work-path $app --output json
.\scripts\Run.ps1 profile show production --work-path $app --output text
.\scripts\Run.ps1 profile show no-such-profile --work-path $app    # should fail

# profile activate — switch active profile
.\scripts\Run.ps1 profile activate -h
.\scripts\Run.ps1 profile activate development --work-path $app
.\scripts\Run.ps1 profile activate development --work-path $app --output json
.\scripts\Run.ps1 profile activate production --work-path $app --output json
.\scripts\Run.ps1 profile activate no-such-profile --work-path $app

# profile remove — delete a profile (refuses if active)
.\scripts\Run.ps1 profile remove -h
.\scripts\Run.ps1 profile remove development --work-path $app
.\scripts\Run.ps1 profile remove production --work-path $app --output json
.\scripts\Run.ps1 profile remove production --work-path $app       # active — should fail
.\scripts\Run.ps1 profile remove no-such-profile --work-path $app  # should fail

# ==============================================================================
# [REFERENCE] ref — manage named file references within profiles
# ==============================================================================

.\scripts\Run.ps1 ref -h
.\scripts\Run.ps1 ref

# ref envfile — .env file references
.\scripts\Run.ps1 ref envfile -h
.\scripts\Run.ps1 ref envfile add base "@infra/environments/base.env" --profile production --work-path $app
.\scripts\Run.ps1 ref envfile add prd "@infra/environments/prd.env" --profile production --work-path $app
.\scripts\Run.ps1 ref envfile add base ".env.local" --work-path $app   # active profile (no --profile)
.\scripts\Run.ps1 ref envfile add base "@infra/base.env" --profile production --work-path $app --output json
.\scripts\Run.ps1 ref envfile add base "@infra/base.env" --profile production --work-path $app  # duplicate — should fail
.\scripts\Run.ps1 ref envfile list --profile production --work-path $app
.\scripts\Run.ps1 ref envfile list --work-path $app                    # active profile
.\scripts\Run.ps1 ref envfile list --profile production --work-path $app --output json
.\scripts\Run.ps1 ref envfile list --profile production --work-path $app --output text
.\scripts\Run.ps1 ref envfile show base --profile production --work-path $app   # display file content
.\scripts\Run.ps1 ref envfile remove base --profile production --work-path $app
.\scripts\Run.ps1 ref envfile remove no-such --profile production --work-path $app  # should fail

# ref configfile — configuration file references (YAML, TOML, etc.)
.\scripts\Run.ps1 ref configfile -h
.\scripts\Run.ps1 ref configfile add main "config/app.yaml" --profile production --work-path $app
.\scripts\Run.ps1 ref configfile add main "config/app.yaml" --profile production --work-path $app --output json
.\scripts\Run.ps1 ref configfile list --profile production --work-path $app
.\scripts\Run.ps1 ref configfile show main --profile production --work-path $app
.\scripts\Run.ps1 ref configfile remove main --profile production --work-path $app

# ref datafile — data/seed file references
.\scripts\Run.ps1 ref datafile -h
.\scripts\Run.ps1 ref datafile add seed "data/seed.sql" --profile production --work-path $app
.\scripts\Run.ps1 ref datafile list --profile production --work-path $app
.\scripts\Run.ps1 ref datafile show seed --profile production --work-path $app
.\scripts\Run.ps1 ref datafile remove seed --profile production --work-path $app

# ref secretfile — secret/vault file references
.\scripts\Run.ps1 ref secretfile -h
.\scripts\Run.ps1 ref secretfile add vault "@infra/secrets/vault.yaml" --profile production --work-path $app
.\scripts\Run.ps1 ref secretfile list --profile production --work-path $app
.\scripts\Run.ps1 ref secretfile show vault --profile production --work-path $app
.\scripts\Run.ps1 ref secretfile remove vault --profile production --work-path $app

# ==============================================================================
# [REFERENCE] log — view execution logs and manage logging configuration
# ==============================================================================

.\scripts\Run.ps1 log -h
.\scripts\Run.ps1 log

# log show — display execution log entries
.\scripts\Run.ps1 log show -h
.\scripts\Run.ps1 log show --work-path $app
.\scripts\Run.ps1 log show --work-path $app --lines 20
.\scripts\Run.ps1 log show --work-path $app --level WARNING
.\scripts\Run.ps1 log show --work-path $app --level DEBUG
.\scripts\Run.ps1 log show --work-path $app --minutes 10
.\scripts\Run.ps1 log show --work-path $app --last
.\scripts\Run.ps1 log show --work-path $app --output json
.\scripts\Run.ps1 log show --work-path $app --output text

# log list — show full logging.yaml content
.\scripts\Run.ps1 log list -h
.\scripts\Run.ps1 log list --work-path $app
.\scripts\Run.ps1 log list --work-path $app --output json
.\scripts\Run.ps1 log list --work-path $app --output text

# log get — retrieve a value by dot-notation key
.\scripts\Run.ps1 log get -h
.\scripts\Run.ps1 log get level --work-path $app
.\scripts\Run.ps1 log get handlers.console.level --work-path $app
.\scripts\Run.ps1 log get loggers.xyz_platform.level --work-path $app

# log set — write a value; 'level' shorthand sets handler + logger level at once
.\scripts\Run.ps1 log set -h
.\scripts\Run.ps1 log set level DEBUG --work-path $app
.\scripts\Run.ps1 log set level INFO --work-path $app
.\scripts\Run.ps1 log set level WARNING --work-path $app
.\scripts\Run.ps1 log set handlers.console.level ERROR --work-path $app

# log unset — remove a key from logging.yaml
.\scripts\Run.ps1 log unset -h
.\scripts\Run.ps1 log unset level --work-path $app
.\scripts\Run.ps1 log unset handlers.console.level --work-path $app

# log reset — restore to package default
.\scripts\Run.ps1 log reset -h
.\scripts\Run.ps1 log reset --work-path $app

# ==============================================================================
# End of reference commands
# ==============================================================================

Remove-Item -Path $app -Recurse -Force -ErrorAction SilentlyContinue

