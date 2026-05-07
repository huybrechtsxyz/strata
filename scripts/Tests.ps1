<#
  Tests.ps1  Manual test reference for xyz-platform CLI
  
  Two sections:
    [FLOW]      End-to-end workflow test  run top to bottom to validate the full session lifecycle
    [REFERENCE] Per-command variations  pick individual lines to test specific flags
#>

# ==============================================================================
#   Explicit flag > XYZ_* env var > .platform/cli.yaml > built-in default
# ==============================================================================
# $env:XYZ_WORK_PATH  = (Resolve-Path $app).Path   # auto-resolve work path
# $env:XYZ_OUTPUT     = "json"                      # default output format
# $env:XYZ_VERBOSE    = "true"                      # enable verbose log replay
# $env:XYZ_QUIET      = "true"                      # suppress all output

$app = ".app"
$cfgBase = (Resolve-Path "config").Path

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
# [REFERENCE] Help command — general help and workflow guidance
# ==============================================================================

function Test-HelpCommand {
    # default — getting-started overview (no workspace required)
    .\scripts\Run.ps1 help -h
    .\scripts\Run.ps1 help

    # --list — all available topics (built-in + workspace)
    .\scripts\Run.ps1 help --list
    .\scripts\Run.ps1 help --list --work-path $app   # workspace topics included when .platform/help/ exists

    # --topic — platform guides
    .\scripts\Run.ps1 help --topic quickstart
    .\scripts\Run.ps1 help --topic workspace
    .\scripts\Run.ps1 help --topic profiles
    .\scripts\Run.ps1 help --topic refs
    .\scripts\Run.ps1 help --topic config-merge
    .\scripts\Run.ps1 help --topic cross-repo
    .\scripts\Run.ps1 help --topic environments
    .\scripts\Run.ps1 help --topic troubleshooting

    # --topic — integration topics
    .\scripts\Run.ps1 help --topic git
    .\scripts\Run.ps1 help --topic terraform
    .\scripts\Run.ps1 help --topic docker

    # unknown topic — should fail (exit 1) and print topic list
    .\scripts\Run.ps1 help --topic no-such-topic
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

    # repo add — local folders from config/* (type=local, no git required)
    # Each name is unique so the full remove/sync sequence below stays consistent
    .\scripts\Run.ps1 repo add -h
    .\scripts\Run.ps1 repo add repo-default  "$cfgBase/xyz-configuration" --work-path $app
    .\scripts\Run.ps1 repo add repo-branch   "$cfgBase/xyz-configuration" --work-path $app  # --branch ignored for local
    .\scripts\Run.ps1 repo add repo-path     "$cfgBase/xyz-infrastructure" --path repos/infra --work-path $app
    .\scripts\Run.ps1 repo add repo-all      "$cfgBase/xyz-svc-traefik" --path repos/traefik --work-path $app
    .\scripts\Run.ps1 repo add repo-json     "$cfgBase/xyz-configuration" --work-path $app --output json
    .\scripts\Run.ps1 repo add repo-text     "$cfgBase/xyz-configuration" --work-path $app --output text
    .\scripts\Run.ps1 repo add repo-clone    "$cfgBase/xyz-infrastructure" --work-path $app  # --clone ignored for local
    .\scripts\Run.ps1 repo add repo-purge    "$cfgBase/xyz-svc-traefik" --work-path $app
    .\scripts\Run.ps1 repo add repo-purge-json "$cfgBase/xyz-svc-traefik" --work-path $app

    # repo add — local path failure cases
    .\scripts\Run.ps1 repo add repo-local    $cfgBase --work-path $app              # valid: config root itself
    .\scripts\Run.ps1 repo add repo-missing  C:/no/such/path --work-path $app       # non-existent path — should fail
    .\scripts\Run.ps1 repo add repo-file     "$cfgBase/xyz-configuration/README.md" --work-path $app  # file not directory — should fail

    # duplicate name — should fail
    .\scripts\Run.ps1 repo add repo-default  "$cfgBase/xyz-infrastructure" --work-path $app

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

    # cleanup remaining repos
    .\scripts\Run.ps1 repo remove repo-branch --work-path $app
    .\scripts\Run.ps1 repo remove repo-path   --work-path $app
    .\scripts\Run.ps1 repo remove repo-all    --work-path $app
    .\scripts\Run.ps1 repo remove repo-clone  --work-path $app
    .\scripts\Run.ps1 repo remove repo-local  --work-path $app

    # Have one registered repo present to confirm that cleanup doesn't interfere with repo management
    .\scripts\Run.ps1 repo add traefik "$cfgBase/xyz-svc-traefik" --work-path $app
    .\scripts\Run.ps1 repo remove traefik --work-path $app

    # git integration smoke test — add, sync (clone), remove with purge
    .\scripts\Run.ps1 repo add traefik-git https://github.com/huybrechtsxyz/xyz-traefik.git --work-path $app
    .\scripts\Run.ps1 repo sync --name traefik-git --work-path $app
    .\scripts\Run.ps1 repo remove traefik-git --work-path $app --purge
}

# ==============================================================================
# [REFERENCE] profile — manage profiles in the solution
# ==============================================================================

function Test-ProfileCommands {
    .\scripts\Run.ps1 profile -h
    .\scripts\Run.ps1 profile

    # profile add — first profile auto-activates
    # Extra profiles reserved so each remove variant has its own distinct target
    .\scripts\Run.ps1 profile add -h
    .\scripts\Run.ps1 profile add production     --work-path $app          # auto-activates
    .\scripts\Run.ps1 profile add development    --work-path $app
    .\scripts\Run.ps1 profile add staging        --work-path $app
    .\scripts\Run.ps1 profile add profile-remove --work-path $app          # plain remove
    .\scripts\Run.ps1 profile add profile-json   --work-path $app          # remove --output json
    .\scripts\Run.ps1 profile add profile-text   --work-path $app          # remove --output text

    # duplicate — should fail
    .\scripts\Run.ps1 profile add production  --work-path $app
    .\scripts\Run.ps1 profile add production  --work-path $app --output json
    .\scripts\Run.ps1 profile add production  --work-path $app --output text

    # profile list — all output formats and filter by name
    .\scripts\Run.ps1 profile list -h
    .\scripts\Run.ps1 profile list --work-path $app
    .\scripts\Run.ps1 profile list --work-path $app --output console
    .\scripts\Run.ps1 profile list --work-path $app --output json
    .\scripts\Run.ps1 profile list --work-path $app --output text
    .\scripts\Run.ps1 profile list --name production --work-path $app
    .\scripts\Run.ps1 profile list --name production --work-path $app --output json
    .\scripts\Run.ps1 profile list --name no-such-profile --work-path $app  # should fail

    # profile show — display ref paths grouped by type
    .\scripts\Run.ps1 profile show -h
    .\scripts\Run.ps1 profile show production --work-path $app
    .\scripts\Run.ps1 profile show production --work-path $app --output json
    .\scripts\Run.ps1 profile show production --work-path $app --output text
    .\scripts\Run.ps1 profile show no-such-profile --work-path $app         # should fail

    # profile activate — active profile is now: production
    .\scripts\Run.ps1 profile activate -h
    .\scripts\Run.ps1 profile activate development --work-path $app
    .\scripts\Run.ps1 profile activate development --work-path $app --output json
    .\scripts\Run.ps1 profile activate production  --work-path $app --output json
    .\scripts\Run.ps1 profile activate no-such-profile --work-path $app     # should fail

    # profile remove — active profile is now: production
    # Each variant targets a distinct non-active profile to avoid double-remove
    .\scripts\Run.ps1 profile remove -h
    .\scripts\Run.ps1 profile remove profile-remove --work-path $app
    .\scripts\Run.ps1 profile remove profile-json   --work-path $app --output json
    .\scripts\Run.ps1 profile remove profile-text   --work-path $app --output text
    .\scripts\Run.ps1 profile remove production     --work-path $app         # active — should fail
    .\scripts\Run.ps1 profile remove no-such-profile --work-path $app        # should fail

    # cleanup: switch away from production so it can be removed
    .\scripts\Run.ps1 profile activate development --work-path $app
    .\scripts\Run.ps1 profile remove production    --work-path $app
    .\scripts\Run.ps1 profile remove staging       --work-path $app
    .\scripts\Run.ps1 profile remove development   --work-path $app          # active — should fail

    # Have one profile present to confirm that cleanup doesn't interfere with profile management
    .\scripts\Run.ps1 profile add production --work-path $app
}


# ==============================================================================
# [REFERENCE] ref — manage named file references within profiles
# ==============================================================================

function Test-RefCommands {
    .\scripts\Run.ps1 ref -h
    .\scripts\Run.ps1 ref

    # Precondition from Test-ProfileCommands: active=development, production also present

    # ref envfile — .env file references
    # Unique names per variant so each add/remove pair is independent
    .\scripts\Run.ps1 ref envfile -h
    .\scripts\Run.ps1 ref envfile add base       "@infra/environments/base.env"  --profile production --work-path $app
    .\scripts\Run.ps1 ref envfile add prd        "@infra/environments/prd.env"   --profile production --work-path $app
    .\scripts\Run.ps1 ref envfile add envf-json  "@infra/environments/json.env"  --profile production --work-path $app --output json
    .\scripts\Run.ps1 ref envfile add envf-text  "@infra/environments/text.env"  --profile production --work-path $app --output text
    .\scripts\Run.ps1 ref envfile add base       ".env.local"                    --work-path $app       # active profile (development)
    .\scripts\Run.ps1 ref envfile add base       "@infra/base.env"               --profile production --work-path $app  # duplicate — should fail

    .\scripts\Run.ps1 ref envfile list --profile production --work-path $app
    .\scripts\Run.ps1 ref envfile list --work-path $app                          # active profile (development)
    .\scripts\Run.ps1 ref envfile list --profile production --work-path $app --output console
    .\scripts\Run.ps1 ref envfile list --profile production --work-path $app --output json
    .\scripts\Run.ps1 ref envfile list --profile production --work-path $app --output text
    .\scripts\Run.ps1 ref envfile list --profile no-such-profile --work-path $app  # should fail

    .\scripts\Run.ps1 ref envfile show base      --profile production --work-path $app
    .\scripts\Run.ps1 ref envfile show base      --profile production --work-path $app --output json
    .\scripts\Run.ps1 ref envfile show no-such   --profile production --work-path $app  # should fail

    .\scripts\Run.ps1 ref envfile remove prd       --profile production --work-path $app
    .\scripts\Run.ps1 ref envfile remove envf-json --profile production --work-path $app --output json
    .\scripts\Run.ps1 ref envfile remove envf-text --profile production --work-path $app --output text
    .\scripts\Run.ps1 ref envfile remove base      --profile production --work-path $app
    .\scripts\Run.ps1 ref envfile remove base      --work-path $app               # active profile (development)
    .\scripts\Run.ps1 ref envfile remove no-such   --profile production --work-path $app  # should fail

    # ref configfile — YAML/TOML configuration file references
    .\scripts\Run.ps1 ref configfile -h
    .\scripts\Run.ps1 ref configfile add main      "config/app.yaml"    --profile production --work-path $app
    .\scripts\Run.ps1 ref configfile add secondary "config/extra.yaml"  --profile production --work-path $app
    .\scripts\Run.ps1 ref configfile add cfg-json  "config/json.yaml"   --profile production --work-path $app --output json
    .\scripts\Run.ps1 ref configfile add cfg-text  "config/text.yaml"   --profile production --work-path $app --output text
    .\scripts\Run.ps1 ref configfile add main      "config/app.yaml"    --profile production --work-path $app  # duplicate — should fail

    .\scripts\Run.ps1 ref configfile list --profile production --work-path $app
    .\scripts\Run.ps1 ref configfile list --profile production --work-path $app --output json
    .\scripts\Run.ps1 ref configfile list --profile production --work-path $app --output text
    .\scripts\Run.ps1 ref configfile list --profile no-such-profile --work-path $app  # should fail

    .\scripts\Run.ps1 ref configfile show main     --profile production --work-path $app
    .\scripts\Run.ps1 ref configfile show main     --profile production --work-path $app --output json
    .\scripts\Run.ps1 ref configfile show no-such  --profile production --work-path $app  # should fail

    .\scripts\Run.ps1 ref configfile remove secondary --profile production --work-path $app
    .\scripts\Run.ps1 ref configfile remove cfg-json  --profile production --work-path $app --output json
    .\scripts\Run.ps1 ref configfile remove cfg-text  --profile production --work-path $app --output text
    .\scripts\Run.ps1 ref configfile remove main      --profile production --work-path $app
    .\scripts\Run.ps1 ref configfile remove no-such   --profile production --work-path $app  # should fail

    # ref datafile — data/seed file references
    .\scripts\Run.ps1 ref datafile -h
    .\scripts\Run.ps1 ref datafile add seed      "data/seed.sql"   --profile production --work-path $app
    .\scripts\Run.ps1 ref datafile add dat-json  "data/json.sql"   --profile production --work-path $app --output json
    .\scripts\Run.ps1 ref datafile add dat-text  "data/text.sql"   --profile production --work-path $app --output text
    .\scripts\Run.ps1 ref datafile add seed      "data/seed.sql"   --profile production --work-path $app  # duplicate — should fail

    .\scripts\Run.ps1 ref datafile list --profile production --work-path $app
    .\scripts\Run.ps1 ref datafile list --profile production --work-path $app --output json
    .\scripts\Run.ps1 ref datafile list --profile production --work-path $app --output text

    .\scripts\Run.ps1 ref datafile show seed     --profile production --work-path $app
    .\scripts\Run.ps1 ref datafile show seed     --profile production --work-path $app --output json
    .\scripts\Run.ps1 ref datafile show no-such  --profile production --work-path $app  # should fail

    .\scripts\Run.ps1 ref datafile remove dat-json  --profile production --work-path $app --output json
    .\scripts\Run.ps1 ref datafile remove dat-text  --profile production --work-path $app --output text
    .\scripts\Run.ps1 ref datafile remove seed      --profile production --work-path $app
    .\scripts\Run.ps1 ref datafile remove no-such   --profile production --work-path $app  # should fail

    # ref secretfile — secret/vault file references
    .\scripts\Run.ps1 ref secretfile -h
    .\scripts\Run.ps1 ref secretfile add vault     "@infra/secrets/vault.yaml"  --profile production --work-path $app
    .\scripts\Run.ps1 ref secretfile add sec-json  "@infra/secrets/json.yaml"   --profile production --work-path $app --output json
    .\scripts\Run.ps1 ref secretfile add sec-text  "@infra/secrets/text.yaml"   --profile production --work-path $app --output text
    .\scripts\Run.ps1 ref secretfile add vault     "@infra/secrets/vault.yaml"  --profile production --work-path $app  # duplicate — should fail

    .\scripts\Run.ps1 ref secretfile list --profile production --work-path $app
    .\scripts\Run.ps1 ref secretfile list --profile production --work-path $app --output json
    .\scripts\Run.ps1 ref secretfile list --profile production --work-path $app --output text

    .\scripts\Run.ps1 ref secretfile show vault    --profile production --work-path $app
    .\scripts\Run.ps1 ref secretfile show vault    --profile production --work-path $app --output json
    .\scripts\Run.ps1 ref secretfile show no-such  --profile production --work-path $app  # should fail

    .\scripts\Run.ps1 ref secretfile remove sec-json  --profile production --work-path $app --output json
    .\scripts\Run.ps1 ref secretfile remove sec-text  --profile production --work-path $app --output text
    .\scripts\Run.ps1 ref secretfile remove vault     --profile production --work-path $app
    .\scripts\Run.ps1 ref secretfile remove no-such   --profile production --work-path $app  # should fail
}

# ==============================================================================
# [REFERENCE] context — manage team-shared template variables (solution.json)
# ==============================================================================

function Test-ContextCommands {
    .\scripts\Run.ps1 context -h
    .\scripts\Run.ps1 context

    # context set — write a template variable
    .\scripts\Run.ps1 context set -h
    .\scripts\Run.ps1 context set owner myteam             --work-path $app
    .\scripts\Run.ps1 context set environment production   --work-path $app
    .\scripts\Run.ps1 context set region eu-west-1         --work-path $app
    .\scripts\Run.ps1 context set owner updated-team       --work-path $app  # overwrite existing key
    .\scripts\Run.ps1 context set owner updated-team       --work-path $app --output json
    .\scripts\Run.ps1 context set owner updated-team       --work-path $app --output text

    # context list — show all current template variables
    .\scripts\Run.ps1 context list -h
    .\scripts\Run.ps1 context list --work-path $app
    .\scripts\Run.ps1 context list --work-path $app --output console
    .\scripts\Run.ps1 context list --work-path $app --output json
    .\scripts\Run.ps1 context list --work-path $app --output text

    # context unset — remove a template variable (silent when key absent)
    .\scripts\Run.ps1 context unset -h
    .\scripts\Run.ps1 context unset region              --work-path $app
    .\scripts\Run.ps1 context unset region              --work-path $app  # already gone — silent success
    .\scripts\Run.ps1 context unset no-such-key         --work-path $app  # never existed — silent success
    .\scripts\Run.ps1 context unset environment         --work-path $app --output json
    .\scripts\Run.ps1 context unset owner               --work-path $app --output text

    # list after cleanup — should be empty
    .\scripts\Run.ps1 context list --work-path $app
}

# ==============================================================================
# [REFERENCE] Status - inspect workspace health and configuration (solution, profile, repositories, etc.)
# ==============================================================================

function Test-StatusCommand {
    .\scripts\Run.ps1 status -h
    .\scripts\Run.ps1 status --work-path $app
    .\scripts\Run.ps1 status --work-path $app --output console
    .\scripts\Run.ps1 status --work-path $app --output json
    .\scripts\Run.ps1 status --work-path $app --output text
    .\scripts\Run.ps1 status --work-path $app --verbose
    .\scripts\Run.ps1 status --work-path $app --quiet

    # no initialized solution — should fail (INIT_REQUIRED = True)
    .\scripts\Run.ps1 status --work-path "$app-missing"
}

# ==============================================================================
# [REFERENCE] tools — manage and inspect external tool integrations
# ==============================================================================

function Test-ToolsCommand {
    .\scripts\Run.ps1 tools -h
    .\scripts\Run.ps1 tools

    # tools status — list all known integrations and their availability
    .\scripts\Run.ps1 tools status -h
    .\scripts\Run.ps1 tools status --work-path $app
    .\scripts\Run.ps1 tools status --work-path $app --verbose

    # tools check — deep-check a single integration by name
    .\scripts\Run.ps1 tools check -h
    .\scripts\Run.ps1 tools check git           --work-path $app
    .\scripts\Run.ps1 tools check docker        --work-path $app
    .\scripts\Run.ps1 tools check terraform     --work-path $app
    .\scripts\Run.ps1 tools check bitwarden     --work-path $app
    .\scripts\Run.ps1 tools check hashicorp_vault  --work-path $app
    .\scripts\Run.ps1 tools check hashicorp_consul --work-path $app
    .\scripts\Run.ps1 tools check azure_keyvault   --work-path $app
    .\scripts\Run.ps1 tools check azure_appconfig  --work-path $app
    .\scripts\Run.ps1 tools check no-such-tool  --work-path $app    # should fail

    # tools install — show download URL, env vars, and auth methods (no actual install)
    .\scripts\Run.ps1 tools install -h
    .\scripts\Run.ps1 tools install git           --work-path $app
    .\scripts\Run.ps1 tools install terraform     --work-path $app
    .\scripts\Run.ps1 tools install docker        --work-path $app
    .\scripts\Run.ps1 tools install bitwarden     --work-path $app
    .\scripts\Run.ps1 tools install hashicorp_vault  --work-path $app
    .\scripts\Run.ps1 tools install hashicorp_consul --work-path $app
    .\scripts\Run.ps1 tools install azure_keyvault   --work-path $app
    .\scripts\Run.ps1 tools install azure_appconfig  --work-path $app

    # --env-file — write a commented template for the user to keep on their machine
    .\scripts\Run.ps1 tools install terraform    --env-file "$app/terraform.env" --work-path $app
    .\scripts\Run.ps1 tools install hashicorp_vault --env-file "$app/vault.env"  --work-path $app

    .\scripts\Run.ps1 tools install no-such-tool --work-path $app   # should fail
}

# ==============================================================================
# [REFERENCE] Audit - observe and audit platform activity: execution history, configuration changes, etc.
# ==============================================================================

function Test-AuditCommand {
    .\scripts\Run.ps1 audit -h
    .\scripts\Run.ps1 audit

    # audit list — show execution log entries
    .\scripts\Run.ps1 audit list -h
    .\scripts\Run.ps1 audit list --work-path $app
    .\scripts\Run.ps1 audit list --work-path $app --output console
    .\scripts\Run.ps1 audit list --work-path $app --output json
    .\scripts\Run.ps1 audit list --work-path $app --output text
    .\scripts\Run.ps1 audit list --work-path $app --lines 10
    .\scripts\Run.ps1 audit list --work-path $app --lines 100
    .\scripts\Run.ps1 audit list --work-path $app --minutes 10
    .\scripts\Run.ps1 audit list --work-path $app --level DEBUG
    .\scripts\Run.ps1 audit list --work-path $app --level INFO
    .\scripts\Run.ps1 audit list --work-path $app --level WARNING
    .\scripts\Run.ps1 audit list --work-path $app --level ERROR
    .\scripts\Run.ps1 audit list --work-path $app --last
    .\scripts\Run.ps1 audit list --work-path $app --last --output json
    .\scripts\Run.ps1 audit list --work-path $app --verbose
    .\scripts\Run.ps1 audit list --work-path $app --quiet

    # no initialized solution — should fail (INIT_REQUIRED = True)
    .\scripts\Run.ps1 audit list --work-path "$app-missing"

    # audit log — manage logging.yaml configuration
    .\scripts\Run.ps1 audit log -h
    .\scripts\Run.ps1 audit log

    # audit log list — show full logging.yaml content
    .\scripts\Run.ps1 audit log list -h
    .\scripts\Run.ps1 audit log list --work-path $app
    .\scripts\Run.ps1 audit log list --work-path $app --output json
    .\scripts\Run.ps1 audit log list --work-path $app --output text

    # audit log get — retrieve a value by dot-notation key
    .\scripts\Run.ps1 audit log get -h
    .\scripts\Run.ps1 audit log get level                          --work-path $app
    .\scripts\Run.ps1 audit log get handlers.console.level         --work-path $app
    .\scripts\Run.ps1 audit log get loggers.xyz_platform.level     --work-path $app
    .\scripts\Run.ps1 audit log get no.such.key                    --work-path $app  # should fail

    # audit log set — write a value; 'level' shorthand sets handler + logger level at once
    .\scripts\Run.ps1 audit log set -h
    .\scripts\Run.ps1 audit log set level DEBUG                    --work-path $app
    .\scripts\Run.ps1 audit log set level INFO                     --work-path $app
    .\scripts\Run.ps1 audit log set level WARNING                  --work-path $app
    .\scripts\Run.ps1 audit log set handlers.console.level ERROR   --work-path $app

    # audit log unset — remove a key from logging.yaml
    .\scripts\Run.ps1 audit log unset -h
    .\scripts\Run.ps1 audit log unset level                        --work-path $app
    .\scripts\Run.ps1 audit log unset handlers.console.level       --work-path $app

    # audit log reset — restore to package default
    .\scripts\Run.ps1 audit log reset -h
    .\scripts\Run.ps1 audit log reset --work-path $app
}

# ==============================================================================
# [REFERENCE] New - scaffold a new platform configuration file from a template
# ==============================================================================

function Test-NewCommand {
    .\scripts\Run.ps1 new -h

    # --list — show all available templates (no workspace required)
    .\scripts\Run.ps1 new --list
    .\scripts\Run.ps1 new --list --work-path $app   # workspace templates merged when .platform/templates/ exists

    # scaffold each built-in template type; NAME is written into meta.name
    .\scripts\Run.ps1 new configuration  my-config    --work-path $app
    .\scripts\Run.ps1 new firewall       my-firewall  --work-path $app
    .\scripts\Run.ps1 new module         my-module    --work-path $app
    .\scripts\Run.ps1 new namespace      my-namespace --work-path $app
    .\scripts\Run.ps1 new provider       my-provider  --work-path $app
    .\scripts\Run.ps1 new resource       my-resource  --work-path $app
    .\scripts\Run.ps1 new workspace      my-workspace --work-path $app

    # output formats — confirm rendering echoes in the requested format
    .\scripts\Run.ps1 new namespace my-namespace --work-path $app --output console
    .\scripts\Run.ps1 new namespace my-namespace --work-path $app --output json
    .\scripts\Run.ps1 new namespace my-namespace --work-path $app --output text

    # --path — write YAML to a specific file
    .\scripts\Run.ps1 new namespace my-namespace --work-path $app --path "$app/my-namespace.yaml"

    # --overwrite — re-scaffold over an existing file
    .\scripts\Run.ps1 new namespace my-namespace --work-path $app --path "$app/my-namespace.yaml" --overwrite

    # --set — override individual template variables
    .\scripts\Run.ps1 new namespace my-namespace --work-path $app --set "description=test namespace"
    .\scripts\Run.ps1 new namespace my-namespace --work-path $app --set "description=foo" --set "version=2.0.0"

    # missing TEMPLATE — should fail (exit 2)
    .\scripts\Run.ps1 new --work-path $app

    # missing NAME — should fail (exit 2)
    .\scripts\Run.ps1 new namespace --work-path $app

    # unknown template — should fail (exit 1)
    .\scripts\Run.ps1 new no-such-template my-name --work-path $app

    # write-without-overwrite when file already exists — should fail
    .\scripts\Run.ps1 new namespace my-namespace --work-path $app --path "$app/my-namespace.yaml"
}

# ==============================================================================
# [REFERENCE] validate - Validate a platform YAML file against its kind-specific schema.
# ==============================================================================

function Test-ValidateCommand {
    .\scripts\Run.ps1 validate -h

    # validate each supported kind using known-good test data files
    .\scripts\Run.ps1 validate "tests/data/configurations/configuration-standard.yaml"
    .\scripts\Run.ps1 validate "tests/data/firewalls/firewall-standard.yaml"
    .\scripts\Run.ps1 validate "tests/data/modules/module-standard.yaml"
    .\scripts\Run.ps1 validate "tests/data/namespaces/namespace-standard.yaml"
    .\scripts\Run.ps1 validate "tests/data/providers/provider-standard.yaml"
    .\scripts\Run.ps1 validate "tests/data/resources/resource-standard.yaml"
    .\scripts\Run.ps1 validate "tests/data/workspaces/workspace-standard.yaml"

    # output formats
    .\scripts\Run.ps1 validate "tests/data/namespaces/namespace-standard.yaml" --output console
    .\scripts\Run.ps1 validate "tests/data/namespaces/namespace-standard.yaml" --output json
    .\scripts\Run.ps1 validate "tests/data/namespaces/namespace-standard.yaml" --output text

    # --deep — Phase 2 cross-reference validation (requires initialized workspace + active profile)
    .\scripts\Run.ps1 validate "tests/data/namespaces/namespace-standard.yaml" --deep --work-path $app

    # --verbose / --quiet
    .\scripts\Run.ps1 validate "tests/data/namespaces/namespace-standard.yaml" --verbose
    .\scripts\Run.ps1 validate "tests/data/namespaces/namespace-standard.yaml" --quiet

    # invalid files — should fail (exit 3)
    .\scripts\Run.ps1 validate "tests/data/configurations/configuration-invalid.yaml"
    .\scripts\Run.ps1 validate "tests/data/firewalls/firewall-invalid.yaml"
    .\scripts\Run.ps1 validate "tests/data/modules/module-invalid.yaml"
    .\scripts\Run.ps1 validate "tests/data/namespaces/namespace-invalid.yaml"
    .\scripts\Run.ps1 validate "tests/data/providers/provider-invalid.yaml"
    .\scripts\Run.ps1 validate "tests/data/resources/resource-invalid.yaml"
    .\scripts\Run.ps1 validate "tests/data/workspaces/workspace-invalid.yaml"

    # unknown kind — should fail
    .\scripts\Run.ps1 validate "tests/data/unknown/unknown-standard.yaml"

    # file not found — should fail (exit 1)
    .\scripts\Run.ps1 validate "tests/data/no-such-file.yaml"

    # --deep with missing workspace — should fail
    .\scripts\Run.ps1 validate "tests/data/namespaces/namespace-standard.yaml" --deep --work-path "$app-missing"
}


#   build     Build platform and Terraform artifacts.
#   deploy    Deploy platform using provisioners.

#   validate  
#   values    Inspect and manage deployment values (variables, secrets,...

# ==============================================================================
# End of reference commands
# ==============================================================================

Remove-Item -Path $app -Recurse -Force -ErrorAction SilentlyContinue

