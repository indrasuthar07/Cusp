FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/services/worker \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY packages/cusp-sdk-python/pyproject.toml packages/cusp-sdk-python/pyproject.toml
COPY services/gateway/pyproject.toml services/gateway/pyproject.toml
COPY services/worker/pyproject.toml services/worker/pyproject.toml
COPY packages/cusp-sdk-python ./packages/cusp-sdk-python
COPY services/worker ./services/worker
RUN uv sync --frozen --package worker --no-dev --no-install-project

CMD ["python", "-m", "app.main"]