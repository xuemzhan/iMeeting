FROM python:3.12-slim

ARG PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/huggingface \
    MODELSCOPE_CACHE=/models/modelscope \
    TORCH_HOME=/models/torch

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ffmpeg \
        libgomp1 \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/pyproject.toml backend/README.md ./
COPY backend/src ./src
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./

RUN python -m pip install --upgrade pip \
    && python -m pip install torch torchaudio --index-url "${PYTORCH_INDEX_URL}" \
    && python -m pip install -e ".[asr]"

RUN useradd --create-home --uid 10001 imeeting \
    && mkdir -p \
        /app/backend/data/audio \
        /app/backend/data/backups \
        /app/backend/data/exports \
        /app/backend/data/logs \
        /app/backend/data/metrics \
        /data/audio \
        /data/backups \
        /data/exports \
        /data/logs \
        /data/metrics \
        /models \
    && chown -R imeeting:imeeting /app/backend /data /models

USER imeeting

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/api/v1/live" || exit 1

CMD ["python", "-m", "imeeting.main"]
