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