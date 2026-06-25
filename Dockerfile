FROM python:3.11-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

FROM python:3.11-slim
RUN useradd -m -s /bin/bash janus
COPY --from=builder /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
COPY --from=builder /usr/local/bin/janus /usr/local/bin/janus
WORKDIR /app
USER janus
EXPOSE 20128
CMD ["janus", "serve", "--host", "0.0.0.0", "--port", "20128", "--config", "/home/janus/.janus/config.yaml"]
