# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Prevent python from buffering stdout/stderr and writing bytecode
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    HF_HOME="/home/orbit/.cache/huggingface" \
    OMP_NUM_THREADS=2

# Create non-root system user
RUN groupadd -r orbit && useradd -r -g orbit -d /home/orbit -m -s /sbin/nologin orbit

WORKDIR /app

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /bin/uv

# Copy dependency locks
COPY pyproject.toml uv.lock ./

# Install dependencies using pinned uv.lock with CPU-only torch
RUN uv sync --frozen --no-dev --no-install-project

# Pre-download sentence-transformers model at build time
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Copy application runtime code and data
COPY orbit/ /app/orbit/
COPY services/ /app/services/
COPY scenarios/ /app/scenarios/
COPY kb/ /app/kb/
COPY data/ /app/data/

# Prepare state directory and permissions
RUN mkdir -p /app/data/state && \
    chown -R orbit:orbit /app && \
    chown -R orbit:orbit /home/orbit

USER orbit

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)" || exit 1

CMD ["uvicorn", "orbit.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
