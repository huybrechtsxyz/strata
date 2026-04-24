# Git Integration

Purpose
- Repository operations used by the platform: clone, fetch, push, create branches, and generate diffs.

Prerequisites
- `git` CLI installed
- Credentials configured for remote (SSH key or credential helper)

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