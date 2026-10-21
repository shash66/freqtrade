FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/* \
    && groupadd -r fll && useradd -r -g fll -d /app fll
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install . && pip install "pytest>=8" "pytest-asyncio>=0.23" "httpx>=0.27"
COPY . .
RUN chown -R fll:fll /app
USER fll
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:8000/api/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
