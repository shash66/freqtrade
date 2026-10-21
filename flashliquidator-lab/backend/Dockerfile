FROM python:3.12-slim AS base
ARG HTTP_PROXY HTTPS_PROXY NO_PROXY
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN groupadd -r fll && useradd -r -g fll -d /app fll
WORKDIR /app
COPY pyproject.toml ./
# Optional corporate/sandbox proxy CA (BuildKit secret `proxy_ca`); production builds pass nothing and skip this.
RUN --mount=type=secret,id=proxy_ca,target=/tmp/proxy-ca.crt,required=false \
    if [ -s /tmp/proxy-ca.crt ]; then cp /tmp/proxy-ca.crt /usr/local/share/ca-certificates/proxy-ca.crt && update-ca-certificates >/dev/null \
      && export PIP_CERT=/etc/ssl/certs/ca-certificates.crt; fi; \
    pip install --upgrade pip && pip install . && pip install "pytest>=8" "pytest-asyncio>=0.23" "httpx>=0.27"
COPY . .
RUN chown -R fll:fll /app
USER fll
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=4).status == 200 else 1)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
