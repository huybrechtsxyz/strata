# Profiles

How to model dev / staging / production environments with profiles.

## What a Profile Is

A profile is a named slot that binds a set of file references (env files, config
files, data files, secret files) together under one name. When a profile is
active, every command that loads configuration uses that profile's refs.

Only one profile is active at a time. The active profile is stored in
`solution.json` and persists across sessions.

## Typical Setup

```
xyz profile add dev
xyz profile add stg
xyz profile add prd
xyz profile activate dev

# Add refs per profile:
xyz ref envfile add --profile dev --name base --path ./envs/dev.env
xyz ref envfile add --profile stg --name base --path ./envs/stg.env
xyz ref configfile add --profile dev --name app --path ./config/app-dev.yaml
```

## Switching Profiles

```
xyz profile activate stg     # switch to staging
xyz profile list             # see which is active
```

Switching profiles changes the ambient context for all subsequent commands in
the session. Nothing is re-run automatically — the next command that loads
configuration will pick up the new active profile.

## Profile vs. Creating a New One

- **Switch** an existing profile when you want to work in a different environment.
- **Create** a new profile when you need a new environment slot (e.g., a feature
  branch environment) that has its own distinct set of refs.

## Convention

Name profiles to match your deployment environments: `dev`, `stg`, `prd`. This
makes it unambiguous which profile maps to which target.

See also: `xyz help --topic refs`, `xyz help --topic environments`
