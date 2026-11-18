# Pro-sight API. Build from the backend/ folder:  docker build -t prosight-api .
FROM python:3.12-slim

# LightGBM needs the GNU OpenMP runtime on Linux.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

# Dependencies first (cached), then the app.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

# The SQLite database is created (with demo data) on first start. On hosts without a persistent
# disk it resets when the service restarts; imported data then has to be uploaded again.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uv run --no-sync uvicorn prosight.main:app --host 0.0.0.0 --port ${PORT}"]
