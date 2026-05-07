<#
  Tests.ps1  Manual test reference for xyz-platform CLI
  
  Two sections:
    [FLOW]      End-to-end workflow test  run top to bottom to validate the full session lifecycle
    [REFERENCE] Per-command variations  pick individual lines to test specific flags
#>

$app = ".app"

New-Item -Path $app -ItemType Directory -Force

Remove-Item -Path $app -Recurse -Force -ErrorAction SilentlyContinue

# ==============================================================================
# [REFERENCE] Basic commands
# ==============================================================================

function Test-BasicCommands {
    .\scripts\Run.ps1 -h
    .\scripts\Run.ps1
}

# ==============================================================================
# [REFERENCE] Version commands
# ==============================================================================

function Test-VersionCommands {
    .\scripts\Run.ps1 version -h
    .\scripts\Run.ps1 version
    .\scripts\Run.ps1 version --output console
    .\scripts\Run.ps1 version --output json
    .\scripts\Run.ps1 version --output text
}

# ==============================================================================
# [REFERENCE] Init — initialize a new solution workspace
# ==============================================================================

function Test-InitCommand {
    New-Item -Path "$app-console", "$app-json", "$app-text", "$app-verbose", "$app-quiet" -ItemType Directory -Force

    .\scripts\Run.ps1 init -h
    .\scripts\Run.ps1 init
    .\scripts\Run.ps1 init --name "test-solution" --work-path $app
    .\scripts\Run.ps1 init --name "test-solution" --work-path "$app-console" --output console
    .\scripts\Run.ps1 init --name "test-solution" --work-path "$app-json" --output json
    .\scripts\Run.ps1 init --name "test-solution" --work-path "$app-text" --output text
    .\scripts\Run.ps1 init --name "test-solution" --work-path "$app-verbose" --verbose
    .\scripts\Run.ps1 init --name "test-solution" --work-path "$app-quiet" --quiet

    Remove-Item -Path "$app-console", "$app-json", "$app-text", "$app-verbose", "$app-quiet" -Recurse -Force -ErrorAction SilentlyContinue
}

# ==============================================================================
# [REFERENCE] Config — persist workspace defaults into .platform/cli.yaml
# Allowed keys: output, verbose, quiet, work_path
# ==============================================================================

function Test-ConfigCommmand {
    .\scripts\Run.ps1 config -h
    .\scripts\Run.ps1 config

    # set — define a default value for the current workspace
    .\scripts\Run.ps1 config set -h

    # output: cycle through all valid values
    .\scripts\Run.ps1 config set --work-path $app output json
    .\scripts\Run.ps1 config set --work-path $app output console
    .\scripts\Run.ps1 config set --work-path $app output text
    .\scripts\Run.ps1 config unset --work-path $app output

    # verbose: set/unset cleanly (verbose and quiet are mutually exclusive in cli.yaml)
    .\scripts\Run.ps1 config set --work-path $app verbose true
    .\scripts\Run.ps1 config set --work-path $app verbose false
    .\scripts\Run.ps1 config unset --work-path $app verbose

    # quiet: only set after verbose is unset to avoid mutual exclusivity error
    .\scripts\Run.ps1 config set --work-path $app quiet true
    .\scripts\Run.ps1 config set --work-path $app quiet false
    .\scripts\Run.ps1 config unset --work-path $app quiet

    # work_path
    .\scripts\Run.ps1 config set --work-path $app work_path $app
    .\scripts\Run.ps1 config unset --work-path $app work_path

    # set output variants — confirm --output flag echoes in requested format
    .\scripts\Run.ps1 config set --work-path $app output json --output json
    .\scripts\Run.ps1 config set --work-path $app output text --output text
    .\scripts\Run.ps1 config unset --work-path $app output

    # set invalid cases — should fail (no cleanup needed, nothing is written)
    .\scripts\Run.ps1 config set --work-path $app output invalid       # invalid output value
    .\scripts\Run.ps1 config set --work-path $app unknown_key value    # invalid key

    # list — show all current defaults
    .\scripts\Run.ps1 config list -h
    .\scripts\Run.ps1 config list --work-path $app
    .\scripts\Run.ps1 config list --work-path $app --output console
    .\scripts\Run.ps1 config list --work-path $app --output json
    .\scripts\Run.ps1 config list --work-path $app --output text

    # unset — remove a specific default
    .\scripts\Run.ps1 config unset -h
    .\scripts\Run.ps1 config unset --work-path $app output
    .\scripts\Run.ps1 config unset --work-path $app verbose
    .\scripts\Run.ps1 config unset --work-path $app quiet
    .\scripts\Run.ps1 config unset --work-path $app work_path

    # unset output variants
    .\scripts\Run.ps1 config unset --work-path $app output --output json
    .\scripts\Run.ps1 config unset --work-path $app output --output text

    # unset invalid key — should fail
    .\scripts\Run.ps1 config unset --work-path $app unknown_key
}

# ==============================================================================
# [REFERENCE] clean — remove workspace artifacts (logs, temp files)
# ==============================================================================

function Test-CleanCommand {
    .\scripts\Run.ps1 clean -h

    # dry-run — report what would be deleted without removing anything
    .\scripts\Run.ps1 clean --work-path $app --dry-run
    .\scripts\Run.ps1 clean --work-path $app --dry-run --output json
    .\scripts\Run.ps1 clean --work-path $app --dry-run --output text

    # clean — actually delete log files
    .\scripts\Run.ps1 clean --work-path $app
    .\scripts\Run.ps1 clean --work-path $app --output json
    .\scripts\Run.ps1 clean --work-path $app --output text

    # output modifiers
    .\scripts\Run.ps1 clean --work-path $app --verbose
    .\scripts\Run.ps1 clean --work-path $app --quiet

    # no initialized solution — should fail (INIT_REQUIRED = True)
    .\scripts\Run.ps1 clean --work-path "$app-missing"
}


# ==============================================================================
# [REFERENCE] repo — manage repositories registered in the solution
# ==============================================================================

function Test-RepoCommands {
    .\scripts\Run.ps1 repo -h
    .\scripts\Run.ps1 repo

    # repo add — git repositories
    # Each name is unique so the full remove/sync sequence below stays consistent
    .\scripts\Run.ps1 repo add -h
    .\scripts\Run.ps1 repo add repo-default  https://github.com/huybrechtsxyz/xyz-traefik.git --work-path $app
    .\scripts\Run.ps1 repo add repo-branch   https://github.com/huybrechtsxyz/xyz-traefik.git --branch main --work-path $app
    .\scripts\Run.ps1 repo add repo-path     https://github.com/huybrechtsxyz/xyz-traefik.git --path repos/custom --work-path $app
    .\scripts\Run.ps1 repo add repo-all      https://github.com/huybrechtsxyz/xyz-traefik.git --branch develop --path repos/all --work-path $app
    .\scripts\Run.ps1 repo add repo-json     https://github.com/huybrechtsxyz/xyz-traefik.git --work-path $app --output json
    .\scripts\Run.ps1 repo add repo-text     https://github.com/huybrechtsxyz/xyz-traefik.git --work-path $app --output text
    .\scripts\Run.ps1 repo add repo-clone    https://github.com/huybrechtsxyz/xyz-traefik.git --work-path $app --clone
    .\scripts\Run.ps1 repo add repo-purge    https://github.com/huybrechtsxyz/xyz-traefik.git --work-path $app
    .\scripts\Run.ps1 repo add repo-purge-json https://github.com/huybrechtsxyz/xyz-traefik.git --work-path $app

    # repo add — local path (type=local, no branch/clone needed)
    .\scripts\Run.ps1 repo add repo-local    $PWD --work-path $app                 # valid local directory
    .\scripts\Run.ps1 repo add repo-missing  C:/no/such/path --work-path $app      # non-existent path — should fail
    .\scripts\Run.ps1 repo add repo-file     $MyInvocation.MyCommand.Path --work-path $app  # file not directory — should fail

    # duplicate name — should fail
    .\scripts\Run.ps1 repo add repo-default  https://github.com/org/other.git --work-path $app

    # repo list — all output formats and filter by name
    .\scripts\Run.ps1 repo list -h
    .\scripts\Run.ps1 repo list --work-path $app
    .\scripts\Run.ps1 repo list --work-path $app --output console
    .\scripts\Run.ps1 repo list --work-path $app --output json
    .\scripts\Run.ps1 repo list --work-path $app --output text
    .\scripts\Run.ps1 repo list --name repo-default --work-path $app
    .\scripts\Run.ps1 repo list --name repo-default --work-path $app --output json
    .\scripts\Run.ps1 repo list --name no-such-repo --work-path $app    # not found — should fail

    # repo remove — each variant uses a distinct registered repo to avoid double-remove
    .\scripts\Run.ps1 repo remove -h
    .\scripts\Run.ps1 repo remove repo-default   --work-path $app
    .\scripts\Run.ps1 repo remove repo-json      --work-path $app --output json
    .\scripts\Run.ps1 repo remove repo-text      --work-path $app --output text
    .\scripts\Run.ps1 repo remove repo-purge     --work-path $app --purge
    .\scripts\Run.ps1 repo remove repo-purge-json --work-path $app --purge --output json
    .\scripts\Run.ps1 repo remove no-such-repo   --work-path $app      # should fail

    # repo sync — operates on repos still registered after the removes above:
    #   repo-branch, repo-path, repo-all, repo-clone, repo-local
    .\scripts\Run.ps1 repo sync -h
    .\scripts\Run.ps1 repo sync --work-path $app
    .\scripts\Run.ps1 repo sync --work-path $app --output json
    .\scripts\Run.ps1 repo sync --work-path $app --output text
    .\scripts\Run.ps1 repo sync --name repo-branch --work-path $app
    .\scripts\Run.ps1 repo sync --name repo-branch --work-path $app --output json
    .\scripts\Run.ps1 repo sync --name repo-local  --work-path $app    # local type — no git ops
    .\scripts\Run.ps1 repo sync --work-path $app --force
    .\scripts\Run.ps1 repo sync --name repo-branch --work-path $app --force
    .\scripts\Run.ps1 repo sync --name no-such-repo --work-path $app   # should fail
}



#   audit     Observe and audit platform activity: execution history,...
#   build     Build platform and Terraform artifacts.
#   context   Manage team-shared template variables (stored in solution.json).
#   deploy    Deploy platform using provisioners.
#   help      Show help topics and workflow guidance.
#   new       Create a new platform configuration file from a template.
#   profile   Manage profiles in the current solution.
#   ref       Manage file references (envfile, configfile, datafile,...
#   repo      Manage repositories in the current solution.
#   status    Show workspace health: solution, profile, repositories, and...
#   tools     Manage and inspect external tool integrations.
#   validate  Validate a platform YAML file against its kind-specific schema.
#   values    Inspect and manage deployment values (variables, secrets,...

# ------------------------------------------------------------------------------
# [FLOW] End-to-end session lifecycle
# [FLOW] Optional: set env vars to test the resolution order
#   Explicit flag > XYZ_* env var > .platform/cli.yaml > built-in default
# ------------------------------------------------------------------------------
# $env:XYZ_WORK_PATH  = (Resolve-Path $app).Path   # auto-resolve work path
# $env:XYZ_OUTPUT     = "json"                      # default output format
# $env:XYZ_VERBOSE    = "true"                      # enable verbose log replay
# $env:XYZ_QUIET      = "true"                      # suppress all output




.\scripts\Run.ps1 init --name "test-solution" --work-path $app

















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

