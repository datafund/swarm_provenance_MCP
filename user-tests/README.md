# User Tests — End-to-End Provenance Workflow

Live tests that exercise the full provenance workflow against the real gateway and blockchain. These are **not** unit tests — they make real HTTP calls and (optionally) real on-chain transactions.

## What's Tested

| Step | Tool | What it checks |
|------|------|----------------|
| 1 | `health_check` | Gateway reachable, chain status, companion servers |
| 2 | `purchase_stamp` | Stamp purchase, batch ID returned, pool-aware messaging |
| 3 | `check_stamp_health` | Stamp propagation polling, `can_upload` readiness |
| 4 | `upload_data` | Upload test payload, get reference hash |
| 5 | `download_data` | Round-trip integrity — downloaded data matches upload |
| 6 | `chain_health` | RPC connectivity (read-only, no wallet needed) |
| 7 | `chain_balance` | Expect failure without wallet — verify error guidance |
| 8 | `anchor_hash` | Expect failure without wallet — verify error guidance |
| 9 | `verify_hash` | Read-only — reports "not registered" (correct) |
| 10 | `get_provenance` | Read-only — reports "not registered" |
| 11 | `anchor + transform x2` | **Only with wallet** — full write workflow |
| 12 | `get_provenance_chain` | **Only with wallet** — verify 3-event lineage |

## Prerequisites

```bash
# From repo root
pip install -e .

# Ensure .env exists with at minimum:
#   SWARM_GATEWAY_URL=https://provenance-gateway.datafund.io

# For chain tests (read-only tests work without wallet):
#   CHAIN_ENABLED=true

# For chain write tests (steps 11-12):
#   PROVENANCE_WALLET_KEY=0x...  (funded on Base Sepolia)
```

## Running

```bash
# From repo root — with chain enabled (recommended)
CHAIN_ENABLED=true python user-tests/test_full_provenance_workflow.py

# Or as a module
CHAIN_ENABLED=true python -m user-tests.test_full_provenance_workflow

# Without chain (skips steps 6-12)
python user-tests/test_full_provenance_workflow.py
```

## Output

The script prints each step with full response text and a pass/fail verdict. At the end it prints a summary table. Results are also written to `user-tests/UX_AUDIT_REPORT.md`.

## When to Run

- After making UX changes to tool responses, error messages, or `_next`/`_related` hints
- Before releases to verify the full workflow still works end-to-end
- When auditing the agent experience with the MCP tools
