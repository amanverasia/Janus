FROM python:3.11-slim AS builder
WORKDIR /build
COPY pyproject.toml src/ ./
RUN pip install --no-cache-dir . && pip wheel . --no-deps -o /wheels

FROM python:3.11-slim
RUN useradd -m -s /bin/bash janus
WORKDIR /app
COPY --from=builder /wheels/*.whl /tmp/
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
USER janus
EXPOSE 20128
CMD ["janus", "serve", "--host", "0.0.0.0", "--port", "20128", "--config", "/home/janus/.janus/config.yaml"]
