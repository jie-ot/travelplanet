FROM node:22-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_DEFAULT_INDEX=https://mirrors.cloud.tencent.com/pypi/simple

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        python3 \
        python3-pip \
        python3-venv \
    && python3 -m pip install --break-system-packages --no-cache-dir \
        --index-url https://mirrors.cloud.tencent.com/pypi/simple uv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY . .
RUN mkdir -p /app/data /app/static /app/logs

EXPOSE 8000

CMD ["sh", "-c", "/app/.venv/bin/python -m alembic upgrade head && exec /app/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]
