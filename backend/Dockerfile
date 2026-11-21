# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
# Cache mount keeps already-downloaded wheels across build attempts, so a flaky/slow PyPI
# connection can make progress on retry instead of starting the big wheels from scratch.
RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout 180 --retries 10 -r requirements.txt
COPY . .

# Do not run as root inside the container.
RUN useradd --system desk
USER desk

# Apply database migrations, then start the API. Only the reverse proxy can reach port 8000.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
