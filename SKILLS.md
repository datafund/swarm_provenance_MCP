# Provenance Skills Guide

A practical guide for AI agents and developers working with the Swarm Provenance MCP server. Covers concepts, workflows, critical rules, and error recovery for decentralized data provenance.

---

## What is Data Provenance?

Data provenance tracks the **origin, ownership, and transformation history** of data. This system implements provenance across three layers:

| Layer | What it does | Where |
|-------|-------------|-------|
| **Swarm Storage** | Content-addressed decentralized storage | Swarm network |
| **On-Chain Anchoring** | Immutable ownership + timestamp record | Base Sepolia (DataProvenance contract) |
| **Transformation Lineage** | Links between original and derived data | On-chain event logs |

**Why it matters:** Anyone can verify who stored data, when, and how it was transformed — without trusting a central authority.

---

## Capability Modes

Not every setup has all features. What you can do depends on your configuration:

| Mode | Requirements | Available Tools |
|------|-------------|-----------------|
| **Storage Only** | Gateway URL | `purchase_stamp`, `upload_data`, `download_data`, `check_stamp_health`, `get_stamp_status`, `list_stamps`, `extend_stamp`, `health_check`, `get_wallet_info`, `get_notary_info` |
| **Read-Only Chain** | + `CHAIN_ENABLED=true` | All storage tools + `chain_health`, `verify_hash`, `get_provenance`, `get_provenance_chain` |
| **Full Provenance** | + `PROVENANCE_WALLET_KEY` (funded) | All tools including `anchor_hash`, `record_transform`, `chain_balance` |

Check your mode: run `health_check` — it reports both gateway and chain status.

---

## Critical Rules

These rules prevent broken on-chain records. Violating them creates data that cannot be corrected (blockchain is immutable).

### 1. `record_transform` auto-registers the new hash

`record_transform` registers `new_hash` on-chain automatically as part of the transformation. **Never call `anchor_hash` on a hash that will be used as `new_hash` in `record_transform`** — doing so creates a standalone record instead of a proper transformation link.

### 2. The original must be anchored first

`record_transform` requires `original_hash` to already be registered on-chain. Always `anchor_hash` the original data before recording any transformations from it.

### 3. Only the owner can record transformations

The wallet that anchored the original hash (or an authorized delegate) is the only one that can call `record_transform` on it. Other wallets will get a revert.

### 4. Stamps and gas are separate payment systems

- **Stamps** (BZZ tokens) pay for Swarm storage — purchased via `purchase_stamp`
- **Gas** (ETH) pays for blockchain transactions — needed for `anchor_hash` and `record_transform`

These are independent. Having stamps does not help with gas, and vice versa. Check both: `check_stamp_health` for stamps, `chain_balance` for ETH.

### 5. Lineage is a DAG, not a chain

One original hash can have **multiple transformations** (branching). Each transformation creates a new leaf. The structure is a directed acyclic graph (DAG), not a linear chain. Use `get_provenance_chain` to traverse the full tree.

### 6. Status changes are one-way

When `restrict_original=true` is passed to `record_transform`, the original data status changes to RESTRICTED. This **cannot be undone**. Only restrict when you are certain the original should no longer be directly accessed.

---

## Workflows

### A: Store and Anchor (Basic Provenance)

Register data on Swarm with an immutable on-chain ownership record.

```
1. health_check              → verify gateway + chain connectivity
2. chain_balance              → confirm wallet has ETH for gas
3. purchase_stamp             → get a stamp for Swarm storage (check propagationStatus in response)
4. check_stamp_health         → poll until can_upload: true (uses propagationStatus + estimatedReadyAt)
5. upload_data                → store data, receive reference hash
6. anchor_hash(swarm_hash)    → register hash on-chain
7. verify_hash(swarm_hash)    → confirm registration succeeded
```

### B: Record a Transformation

Link transformed data to its original, creating verifiable lineage.

**Prerequisite:** The original hash must already be anchored (Workflow A).

```
1. upload_data                → upload the transformed data, get new_hash
2. record_transform           → link original_hash → new_hash with description
   (original_hash, new_hash, description)
3. get_provenance_chain       → verify the lineage is recorded correctly
```

**Remember:** Do NOT call `anchor_hash` on `new_hash` — `record_transform` handles registration.

### C: Multi-Step Pipeline

Chain multiple transformations (e.g., raw → cleaned → anonymized → aggregated).

```
1. Anchor the root data       → Workflow A for the original dataset
2. Transform step 1           → upload cleaned data, record_transform(original, cleaned, "Cleaned")
3. Transform step 2           → upload anonymized data, record_transform(cleaned, anonymized, "Anonymized PII")
4. Transform step 3           → upload aggregated data, record_transform(anonymized, aggregated, "Aggregated by region")
5. get_provenance_chain       → verify the full lineage tree
```

Each step's `new_hash` becomes the next step's `original_hash`. The auto-registration in `record_transform` ensures each intermediate hash is properly linked.

### D: Verify Existing Provenance (Read-Only)

Inspect provenance records without a wallet.

```
1. verify_hash(swarm_hash)        → check if hash is registered, get basic info
2. get_provenance(swarm_hash)     → full record: owner, timestamp, status, transformations
3. get_provenance_chain(hash)     → follow transformation lineage tree
4. download_data(reference)       → retrieve the actual data from Swarm
```

---

## Data Flow

```
                    ┌─────────────────────────────────────────────┐
                    │              AI Agent / User                │
                    └──────────────────┬──────────────────────────┘
                                       │
                                  MCP Protocol
                                       │
                    ┌──────────────────▼──────────────────────────┐
                    │         Swarm Provenance MCP Server         │
                    │                                             │
                    │  ┌─────────────┐    ┌───────────────────┐   │
                    │  │   Gateway    │    │   Chain Client     │   │
                    │  │   Client     │    │   (optional)       │   │
                    │  └──────┬──────┘    └────────┬──────────┘   │
                    └─────────┼────────────────────┼──────────────┘
                              │                    │
                   ┌──────────▼─────────┐ ┌───────▼──────────────┐
                   │  swarm_connect     │ │  Base Sepolia RPC    │
                   │  FastAPI Gateway   │ │                      │
                   └──────────┬─────────┘ │  DataProvenance      │
                              │           │  Smart Contract      │
                   ┌──────────▼─────────┐ └──────────────────────┘
                   │   Swarm Network    │
                   │   (Bee nodes)      │
                   └────────────────────┘
```

## Transformation Chain

```
  ┌───────────────────┐     record_transform      ┌───────────────────┐
  │  hash_0 (original)│ ──────────────────────────▶│  hash_1 (cleaned) │
  │                   │   "Removed duplicates"     │                   │
  │  owner: 0xABC...  │                            │  owner: 0xABC...  │
  │  status: ACTIVE   │                            │  status: ACTIVE   │
  │  anchored: block N│                            │  anchored: block M│
  └───────────────────┘                            └─────────┬─────────┘
                                                             │
                                                   record_transform
                                                   "Anonymized PII"
                                                             │
                                                   ┌─────────▼─────────┐
                                                   │  hash_2 (anon)    │
                                                   │                   │
                                                   │  owner: 0xABC...  │
                                                   │  status: ACTIVE   │
                                                   │  anchored: block P│
                                                   └───────────────────┘

  get_provenance_chain(hash_0) returns: hash_0 → hash_1 → hash_2
  get_provenance_chain(hash_2) returns: hash_0 → hash_1 → hash_2  (walks backward to root)
  get_provenance_chain(hash_1) returns: hash_0 → hash_1 → hash_2  (walks both directions)
```

---

## Error Recovery

| Error | Cause | Recovery |
|-------|-------|----------|
| "Not usable" / "NOT_FOUND" | Stamp not yet propagated | Check `propagationStatus` and `estimatedReadyAt` in the response. Poll `check_stamp_health` every 15s until `propagationStatus: "ready"` |
| "insufficient funds" | Wallet ETH too low for gas | Run `chain_balance` for funding guidance (faucet/bridge URLs) |
| "already registered" revert | Hash was already anchored | Not an error — `anchor_hash` returns the existing record |
| "already exists" / "already registered" revert on `record_transform` | `new_hash` was pre-anchored via `anchor_hash` | Do NOT anchor `new_hash` before `record_transform` — it auto-registers. Re-upload the data to get a fresh hash. |
| "Transformation already recorded" on `record_transform` | Same `(original → new)` pair was already recorded | Not an error — `record_transform` returns the existing link without spending gas. Use `get_provenance_chain` to verify. |
| "data not registered" | `original_hash` not anchored | Call `anchor_hash` on the original first, then retry `record_transform` |
| "not owner" / "unauthorized" | Wrong wallet for this data | Only the anchoring wallet (or delegate) can transform |
| Size exceeded (4KB) | Upload payload too large | Split or compress data before upload |
| Gateway unreachable | Network or gateway issue | Run `health_check`, check `SWARM_GATEWAY_URL` config |
| RPC timeout | Blockchain node unresponsive | Run `chain_health`, try again or check `CHAIN_RPC_URL` |

---

## Glossary

| Term | Definition |
|------|-----------|
| **Swarm hash** | 64-character hex content address returned by `upload_data` |
| **Stamp** | Prepaid storage ticket (BZZ) — controls capacity (depth) and duration (TTL) |
| **Anchor** | Registering a Swarm hash on the blockchain via `anchor_hash` |
| **Transformation** | On-chain link between an original hash and a derived hash, recorded via `record_transform` |
| **Lineage / Provenance chain** | The full tree of transformations reachable from any hash (walks both directions), retrieved via `get_provenance_chain` |
| **DAG** | Directed Acyclic Graph — the structure of transformation lineage (branching, no cycles) |
| **Gas** | ETH spent to execute blockchain transactions (`anchor_hash`, `record_transform`) |
| **Base Sepolia** | Testnet for the Base L2 chain — where the DataProvenance contract is deployed |
| **DataProvenance contract** | Smart contract that stores provenance records and emits `DataTransformed` events |
| **Owner** | The wallet address that anchored a hash — has exclusive rights to record transformations |
| **RESTRICTED status** | Irreversible status set via `restrict_original=true` — signals data should not be accessed directly |
| **Owned stamp** | A stamp purchased by your wallet — exclusive access, predictable utilization, production-ready |
| **Public stamp** | A stamp available to all gateway users (accessMode: "shared" in API) — utilization is unpredictable, suitable for testing |
