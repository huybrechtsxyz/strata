FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy only what's needed for an install to leverage Docker layer caching
COPY pyproject.toml .
COPY src/ src/

# Install system build deps, install the package, then clean up
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && pip install --upgrade pip \
    && pip install --no-cache-dir . \
    && apt-get purge -y --auto-remove build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

# Run as non-root
RUN useradd -m appuser
USER appuser

CMD ["python", "-m", "xyz_platform"]
