# ============================================================
# Stage 1 – builder: compile source into a wheel
# ============================================================
FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml .
COPY src/ src/

RUN pip install --upgrade pip build \
    && python -m build --wheel --outdir /dist

# ============================================================
# Stage 2 – final: lean runtime image
# ============================================================
FROM python:3.12-slim

ARG VERSION=dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${VERSION}

LABEL org.opencontainers.image.title="xyz-platform" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/${GITHUB_REPOSITORY}"

WORKDIR /app

# Install the pre-built wheel from the builder stage
COPY --from=builder /dist/*.whl /tmp/
RUN pip install --upgrade pip \
    && pip install --no-cache-dir /tmp/*.whl \
    && rm /tmp/*.whl

# Run as non-root
RUN useradd -m appuser
USER appuser

CMD ["python", "-m", "xyz_platform"]
