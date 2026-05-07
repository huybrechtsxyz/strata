# Git Integration

Purpose
- Repository operations used by the platform: clone, fetch, push, create branches, and generate diffs.

Prerequisites
- `git` CLI installed
- Credentials configured for remote (SSH key or credential helper)

Connection parameters

- Required:
	- `git` CLI available in `PATH`.

- Common environment variables used by `git` operations (optional):
	- `GIT_SSH_COMMAND` — custom SSH command (e.g., `ssh -i /path/to/key`)
	- `GIT_ASKPASS` — helper program for interactive credential prompts in non-interactive CI
	- `GIT_CONFIG` / `GIT_DIR` — alternative config or repo directory locations

How xyz-platform connects
- xyz-platform calls `git` commands from the process environment. Ensure the user or service account running the platform has appropriate credentials (SSH key or PAT) and that the shell environment exposes any custom `GIT_*` variables needed for authentication.

Common Commands
- Clone: `git clone <repo-url>`
- Branch: `git checkout -b squad/123-fix-thing`
- Commit: `git add . && git commit -m "message"`
- Push: `git push origin <branch>`

CI Tips
- Use `git fetch --depth=1` for shallow clones in CI to save time
- Configure `user.name` and `user.email` for CI commits

Troubleshooting
- Authentication errors: check token/SSH key
- Large repos: use sparse-checkout or shallow clones

Docs
- https://git-scm.com/docs