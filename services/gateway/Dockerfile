FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/services/gateway \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY packages/cusp-sdk-python/pyproject.toml packages/cusp-sdk-python/pyproject.toml
COPY services/gateway/pyproject.toml services/gateway/pyproject.toml
COPY services/worker/pyproject.toml services/worker/pyproject.toml
COPY packages/cusp-sdk-python ./packages/cusp-sdk-python
COPY services/gateway ./services/gateway
RUN uv sync --frozen --package gateway --no-dev --no-install-project

COPY docs ./docs

WORKDIR /app/services/gateway

EXPOSE 8000

CMD ["sh", "-c", "alembic -c alembic.ini upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]