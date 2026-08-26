FROM python:3.11-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

FROM python:3.11-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/bash janus
COPY --from=builder /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
COPY --from=builder /usr/local/bin/janus /usr/local/bin/janus
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
WORKDIR /app
EXPOSE 20128
# Uses the runtime Python (the slim image ships no curl/wget) to probe the
# unauthenticated /v1/health endpoint. Non-2xx or a refused connection makes
# the script exit non-zero, which Docker treats as unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:20128/v1/health', timeout=3).read()" || exit 1
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["janus", "serve", "--host", "0.0.0.0", "--port", "20128", "--config", "/home/janus/.janus/config.yaml"]
