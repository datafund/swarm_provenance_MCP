FROM python:3.10-slim

ARG VERSION=0.1.0

LABEL org.opencontainers.image.title="Swarm Provenance MCP" \
      org.opencontainers.image.description="MCP server for Swarm postage stamp management and provenance data storage" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.vendor="Datafund" \
      org.opencontainers.image.source="https://github.com/datafund/swarm_provenance_MCP" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1
ENV SWARM_GATEWAY_URL=https://provenance-gateway.datafund.io
ENV DEFAULT_STAMP_AMOUNT=2000000000
ENV DEFAULT_STAMP_DEPTH=17

RUN groupadd --gid 1000 mcp \
    && useradd --uid 1000 --gid mcp --create-home mcp

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
RUN pip install --no-cache-dir .

COPY swarm_provenance_mcp/ swarm_provenance_mcp/
RUN pip install --no-cache-dir --no-deps .

USER mcp

ENTRYPOINT ["swarm-provenance-mcp"]
