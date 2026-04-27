# syntax=docker/dockerfile:1
# Multi-stage: build Vite UI into backend/static, then run FastAPI.

FROM node:20-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN mkdir -p ../backend/static && npm run build

FROM python:3.11-slim-bookworm
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY data ./data
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY --from=frontend /app/backend/static ./backend/static

WORKDIR /app/backend
ENV PYTHONUNBUFFERED=1
# SQLite in containers: mount a volume and set MINIOBSERVE_DB=/data/logs.db
ENV MINIOBSERVE_DB=/data/logs.db

EXPOSE 7823
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7823", "--proxy-headers"]
