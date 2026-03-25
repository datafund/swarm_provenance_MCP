# Swarm Provenance MCP — Docker MCP Registry

Reference files for submission to the [Docker MCP Registry](https://github.com/docker/mcp-registry).

## Files

| File | Purpose |
|------|---------|
| `server.yaml` | Server metadata: title, description, category, env vars |
| `tools.json` | Pre-populated tool definitions for registry listing |
| `readme.md` | This file |

## Submission

To submit this server to the Docker MCP Registry:

1. Fork https://github.com/docker/mcp-registry
2. Copy `server.yaml` and `tools.json` to `mcp-registry/servers/swarm-provenance-mcp/`
3. Open a PR against the upstream repo

## Prerequisites

The Docker image must be published to GHCR first. The image is **automatically rebuilt** on every push to `main` and on version tags:

| Trigger | Tags produced |
|---------|---------------|
| Push to `main` | `latest`, `sha-<7char>` |
| Tag `v1.2.3` | `1.2.3`, `1.2`, `latest`, `sha-<7char>` |

```bash
# Force a publish via version tag
git tag v0.1.0
git push origin v0.1.0
```

The image is available at `ghcr.io/datafund/swarm-provenance-mcp:latest`.
Pin a specific build with `ghcr.io/datafund/swarm-provenance-mcp:sha-<commit>`.
