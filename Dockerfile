# Build the browser app once; the runtime serves these files from the API origin.
FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Keep the same Python minor version and virtualenv path in both Python stages.
FROM python:3.12-slim-bookworm AS python-build
COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src/ src/
RUN uv sync --locked --no-dev

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" APP_ENV=production \
    MODEL_MODE=mock ALLOW_LIVE_IN_SESSIONS=0 DATA_DIR=/tmp/invoice-flow \
    STORAGE_PERSISTENCE=ephemeral
WORKDIR /app
RUN useradd --system --uid 10001 --create-home appuser
COPY --from=python-build --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=python-build --chown=appuser:appuser /app/src /app/src
COPY --chown=appuser:appuser main.py ./
COPY --chown=appuser:appuser config/ config/
COPY --chown=appuser:appuser data/invoices/ data/invoices/
COPY --from=frontend --chown=appuser:appuser /build/frontend/dist frontend/dist/
USER appuser
EXPOSE 10000
# One server process owns the local queue and SQLite database.
CMD ["sh", "-c", "exec python -m invoice_flow.server --host 0.0.0.0 --port ${PORT:-10000}"]
