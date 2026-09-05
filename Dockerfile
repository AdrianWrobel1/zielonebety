# Stage 1: Builder
FROM python:3.11-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt || true

# Stage 2: Production Runner
FROM python:3.11-slim AS runner

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN groupadd -g 10001 appuser && \
    useradd -u 10000 -g appuser -s /bin/sh appuser && \
    chown -R appuser:appuser /app

COPY --from=builder /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH

COPY --chown=appuser:appuser api /app/api
COPY --chown=appuser:appuser core /app/core
COPY --chown=appuser:appuser database /app/database
COPY --chown=appuser:appuser domain /app/domain
COPY --chown=appuser:appuser normalization /app/normalization
COPY --chown=appuser:appuser notifications /app/notifications
COPY --chown=appuser:appuser orchestration /app/orchestration
COPY --chown=appuser:appuser providers /app/providers
COPY --chown=appuser:appuser reference_odds /app/reference_odds
COPY --chown=appuser:appuser scanner /app/scanner
COPY --chown=appuser:appuser valuebets /app/valuebets
COPY --chown=appuser:appuser web /app/web

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python", "-m", "uvicorn", "api.fastapi_app:app", "--host", "0.0.0.0", "--port", "8000"]
