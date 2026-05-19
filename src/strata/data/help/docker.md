# Docker Integration

Prerequisites
- Docker Engine installed and running on host
- User in `docker` group or run with appropriate privileges

Common Use Cases
- Build images for platform services
- Run local development containers for testing
- Push images to registry (Docker Hub, Azure ACR, etc.)

Configuration
- `DOCKER_HOST` (optional) if using remote daemon
- `DOCKER_TLS_VERIFY` and certs for secure remote daemons

Connection parameters

- Required:
	- `docker` CLI available in `PATH`.

- Optional environment variables that affect connectivity:
	- `DOCKER_HOST` — remote daemon endpoint (e.g., `tcp://192.168.1.100:2376`)
	- `DOCKER_TLS_VERIFY` — enable TLS verification for remote daemon (`1`/`0`)
	- `DOCKER_CERT_PATH` — path to TLS certificates when using TLS

How strata connects
- strata invokes the `docker` CLI under the current environment. Configure the environment variables above or the Docker daemon so `docker` commands succeed from the shell that runs the platform.

Commands
- Build: `docker build -t myrepo/myimage:tag .`
- Run: `docker run --rm -p 8080:80 myrepo/myimage:tag`
- Push: `docker push myrepo/myimage:tag`

Tips
- Use `.dockerignore` to exclude build artifacts
- Keep images small (multi-stage builds)
- For CI, authenticate to registry and tag with CI build id

Docs
- https://docs.docker.com/