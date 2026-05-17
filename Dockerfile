FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

EXPOSE 8000

# PER-118 followup: --reload removed from the Dockerfile CMD. On macOS
# + Docker Desktop the VirtioFS bind-mount triggers phantom WatchFiles
# events (git commit/add bump mtime, Spotlight indexer touches files,
# etc.). Combined with our slow lifespan startup (alembic + 5 seed
# functions, ~20 s), the worker process gets reload-killed before
# "Application startup complete" — backend ends up unhealthy for hours
# without anyone touching the code. Happened four times in a week.
#
# Hot-reload during dev is now opt-in via UVICORN_RELOAD=--reload in
# docker-compose.yml (commented). Restart manually instead:
#     docker compose restart backend
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 ${UVICORN_RELOAD:-}"]
