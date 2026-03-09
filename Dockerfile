FROM python:3.10-slim

ARG VERSION=0.1.0

LABEL org.opencontainers.image.title="swarm-provenance-mcp" \
      org.opencontainers.image.description="MCP server for Swarm postage stamp management" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/datafund/swarm_provenance_MCP" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./

RUN pip install --no-cache-dir .

COPY swarm_provenance_mcp/ swarm_provenance_mcp/

RUN pip install --no-cache-dir .

RUN useradd --create-home mcp
USER mcp

ENTRYPOINT ["swarm-provenance-mcp"]
