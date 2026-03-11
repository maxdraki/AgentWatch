# AgentWatch Dashboard — Docker image
#
# Build:   docker build -t agentwatch .
# Run:     docker run -p 8470:8470 agentwatch
# With auth:
#   docker run -p 8470:8470 -e AGENTWATCH_AUTH_TOKEN=secret agentwatch
# Persistent data:
#   docker run -p 8470:8470 -v agentwatch-data:/data agentwatch

FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/

RUN pip install --no-cache-dir build && \
    python -m build --wheel && \
    pip install --no-cache-dir dist/*.whl[server]

FROM python:3.12-slim

LABEL org.opencontainers.image.title="AgentWatch" \
      org.opencontainers.image.description="Lightweight observability for autonomous AI agents" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.source="https://github.com/agentwatch/agentwatch" \
      org.opencontainers.image.licenses="MIT"

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=builder /usr/local/bin/agentwatch /usr/local/bin/agentwatch

# Create non-root user
RUN useradd -r -m -d /home/agentwatch agentwatch && \
    mkdir -p /data && \
    chown agentwatch:agentwatch /data

USER agentwatch
WORKDIR /home/agentwatch

# Default database location inside the container
ENV AGENTWATCH_DB_PATH=/data/agentwatch.db

EXPOSE 8470

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8470/api/health')" || exit 1

ENTRYPOINT ["agentwatch"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8470", "--db", "/data/agentwatch.db"]
