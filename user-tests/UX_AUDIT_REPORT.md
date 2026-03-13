# UX Audit Report — End-to-End Provenance Workflow

**Date:** 2026-03-12 15:31:05 UTC
**Gateway:** https://provenance-gateway.datafund.io
**Chain:** enabled (base-sepolia)
**Wallet:** configured

## Summary

| Metric | Count |
|--------|-------|
| Total steps | 16 |
| Passed | 16 |
| Failed | 0 |

## Step Results

| Step | Tool | Status | Time | Notes |
|------|------|--------|------|-------|
| 1 | `health_check` | PASS | 1562ms |  |
| 2 | `purchase_stamp` | PASS | 16617ms | stamp_id=10e5b3253d93913b... |
| 3 | `check_stamp_health` | PASS | 458ms | polls=6, can_upload=True |
| 4 | `upload_data` | PASS | 341ms | reference=d4c0d6c4af367b8c... |
| 5 | `download_data` | PASS | 354ms |  |
| 6 | `chain_health` | PASS | 431ms |  |
| 7 | `chain_balance` | PASS | 309ms | Wallet is configured — balance check succeeded |
| 8 | `anchor_hash` | PASS | 418ms | Wallet configured — write tests in step 11 (used dummy hash) |
| 9 | `verify_hash` | PASS | 388ms |  |
| 10 | `get_provenance` | PASS | 377ms |  |
| 11 | `anchor_hash` | PASS | 2489ms | hash1=d4c0d6c4af367b8c... |
| 11 | `record_transform` | PASS | 3797ms | hash1→hash2 (a9b22bfc1edf8446...) |
| 11 | `record_transform` | PASS | 2663ms | hash2→hash3 (543e6b9ec0c663bf...) |
| 11 | `verify_hash` | PASS | 787ms |  |
| 12 | `get_provenance_chain` | PASS | 2309ms | Lineage entries: 3 |
| 12 | `chain_balance` | PASS | 461ms | Final balance after 5 on-chain transactions |

## UX Issues

No UX issues found.

## Detailed Responses

### Step 1: Health Check (`health_check`)

**Status:** PASS | **Error:** False | **Time:** 1562ms

```
✅ Gateway operational

🌐 Gateway: https://provenance-gateway.datafund.io
⚡ Response Time: 596ms

💰 Payment: free tier (3 write req/min, reads unlimited)

📋 Stamps: 540 usable / 542 total

ready: true

⛓️  Chain: base-sepolia (connected)
   Contract: 0x9a3c6F47B69211F05891CCb7aD33596290b9fE64
   Wallet: 0x1f82225723ED636b3463Cf2574b555D00D750Eb0
_companion_servers:
  - swarm_connect gateway: https://provenance-gateway.datafund.io (required, connected)
  - fds-id MCP: optional (identity/signing for provenance chain anchoring)

_next: upload_data
_related: list_stamps, purchase_stamp, get_wallet_info
```

### Step 2: Purchase Stamp (`purchase_stamp`)

**Status:** PASS | **Error:** False | **Time:** 16617ms

```
🎉 Stamp purchased successfully!

📋 Your Stamp Details:
   Batch ID: `10e5b3253d93913b3ab7b27c8bb60a48610907a40cbafb59146d42834a77c7ac`
   Amount: 2,000,000,000 wei
   Depth: 17

✅ Stamp ID: `10e5b3253d93913b3ab7b27c8bb60a48610907a40cbafb59146d42834a77c7ac`
⏱️  Your stamp may be ready immediately (from the gateway pool) or may need
   up to 2 minutes to propagate on the blockchain.
   → Use check_stamp_health to confirm it's ready before uploading.

_next: check_stamp_health
_related: get_stamp_status, upload_data, list_stamps
```

### Step 3: Check Stamp Health (poll) (`check_stamp_health`)

**Status:** PASS | **Error:** False | **Time:** 458ms

```
✅ Stamp 10e5b3253d93913b... is healthy and ready for uploads.


Status:
   Utilization: 0.0% (ok)
   TTL: 157,297 seconds (1.8 days)
   Expires: 2026-03-14-11-10


_next: upload_data
_related: get_stamp_status, extend_stamp
```

### Step 4: Upload Data (`upload_data`)

**Status:** PASS | **Error:** False | **Time:** 341ms

```
🎉 Data uploaded successfully to Swarm!

📄 Upload Details:
   Size: 135 bytes
   Content Type: application/json
   Stamp Used: `10e5b3253d93913b3ab7b27c8bb60a48610907a40cbafb59146d42834a77c7ac`

🔗 Retrieval Information:
   Reference Hash: `d4c0d6c4af367b8cd5558aeb9936ae7e249f98453beaed8000eff161cfe8b5d3`
   💡 Copy this reference hash to download your data later using the 'download_data' tool.

_next: download_data
_related: upload_data, check_stamp_health
```

### Step 5: Download Data (round-trip) (`download_data`)

**Status:** PASS | **Error:** False | **Time:** 354ms

```
📥 Successfully downloaded JSON data from `d4c0d6c4af367b8cd5558aeb9936ae7e249f98453beaed8000eff161cfe8b5d3`:

📋 JSON Structure:
   test: provenance-ux-audit
   timestamp: 2026-03-12T15:29:15.015014+00:00
   message: End-to-end provenance workflow test data

💾 Size: 135 bytes

_next: upload_data
_related: list_stamps
```

### Step 6: Chain Health (`chain_health`)

**Status:** PASS | **Error:** False | **Time:** 431ms

```
⛓️  Chain RPC Health

   Connected: true
   Chain: base-sepolia
   Chain ID: 84532
   Latest Block: 38,780,534
   RPC Response: 286ms
   Contract: 0x9a3c6F47B69211F05891CCb7aD33596290b9fE64
   RPC: sepolia.base.org

_next: chain_balance
_related: health_check
```

### Step 7: Chain Balance (no wallet) (`chain_balance`)

**Status:** PASS | **Error:** False | **Time:** 309ms

```
⛓️  Chain Wallet Balance

   Wallet: 0x1f82225723ED636b3463Cf2574b555D00D750Eb0
   Balance: 0.009981587314013959 ETH (9,981,587,314,013,959 wei)
   Chain: base-sepolia
   Contract: 0x9a3c6F47B69211F05891CCb7aD33596290b9fE64
   RPC: sepolia.base.org

_next: anchor_hash
_related: chain_health, health_check
```

### Step 8: Anchor Hash (error quality) (`anchor_hash`)

**Status:** PASS | **Error:** False | **Time:** 418ms

```
⛓️  Hash already registered on-chain

   Swarm Hash: ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
   Owner: 0x1f82225723ED636b3463Cf2574b555D00D750Eb0
   Registered: 2026-03-12 12:02:10 UTC
   Data Type: swarm-provenance

_next: download_data
_related: chain_balance, health_check
```

### Step 9: Verify Hash (read-only) (`verify_hash`)

**Status:** PASS | **Error:** False | **Time:** 388ms

```
⛓️  Not found — hash is NOT registered on-chain

   Swarm Hash: 0000000000000000000000000000000000000000000000000000000000000000

_next: anchor_hash
_related: upload_data, health_check
```

### Step 10: Get Provenance (read-only) (`get_provenance`)

**Status:** PASS | **Error:** False | **Time:** 377ms

```
⛓️  Not found — hash is NOT registered on-chain

   Swarm Hash: 0000000000000000000000000000000000000000000000000000000000000000

_next: anchor_hash
_related: upload_data, health_check
```

### Step 11: Anchor original hash on-chain (`anchor_hash`)

**Status:** PASS | **Error:** False | **Time:** 2489ms

```
⛓️  Hash anchored on-chain

   Swarm Hash: d4c0d6c4af367b8cd5558aeb9936ae7e249f98453beaed8000eff161cfe8b5d3
   Data Type: ux-audit-original
   Owner: 0x1f82225723ED636b3463Cf2574b555D00D750Eb0
   Tx Hash: 618ab9bde43231cde822950829c9248fc744a0c0d89e6ccdae06d40d08e07fc4
   Block: 38,780,536
   Gas Used: 144,172
   Explorer: https://sepolia.basescan.org/tx/0x618ab9bde43231cde822950829c9248fc744a0c0d89e6ccdae06d40d08e07fc4

_next: download_data
_related: chain_balance, health_check
```

### Step 11: Upload + Transform (1->2) (`record_transform`)

**Status:** PASS | **Error:** False | **Time:** 3797ms

```
⛓️  Transformation recorded on-chain

   Original: d4c0d6c4af367b8cd5558aeb9936ae7e249f98453beaed8000eff161cfe8b5d3
   Transformed: a9b22bfc1edf84461d39fef309b1df0bdfe9c44185ea9720530d1c5fcb6be7d7
   Description: Anonymized PII from raw sensor data
   Tx Hash: 84495b9125f927b21d403e46ae7b8e84def13f229f1d780ebc201ed7e4402672
   Block: 38,780,571
   Gas Used: 241,464
   Explorer: https://sepolia.basescan.org/tx/0x84495b9125f927b21d403e46ae7b8e84def13f229f1d780ebc201ed7e4402672

_next: get_provenance
_related: download_data, chain_balance
```

### Step 11: Upload + Transform (2->3) (`record_transform`)

**Status:** PASS | **Error:** False | **Time:** 2663ms

```
⛓️  Transformation recorded on-chain

   Original: a9b22bfc1edf84461d39fef309b1df0bdfe9c44185ea9720530d1c5fcb6be7d7
   Transformed: 543e6b9ec0c663bfe804b3064a97cad1c115c53fe22b0378e4bd26ecd360f033
   Description: Filtered for EU region
   Tx Hash: 0101b7902f48402d06e2357421e95883948fb4af04cdf3738cc2adcf62411a75
   Block: 38,780,585
   Gas Used: 196,466
   Explorer: https://sepolia.basescan.org/tx/0x0101b7902f48402d06e2357421e95883948fb4af04cdf3738cc2adcf62411a75

_next: get_provenance
_related: download_data, chain_balance
```

### Step 11: Verify anchored hash (`verify_hash`)

**Status:** PASS | **Error:** False | **Time:** 787ms

```
⛓️  Verified — hash IS registered on-chain

   Swarm Hash: d4c0d6c4af367b8cd5558aeb9936ae7e249f98453beaed8000eff161cfe8b5d3
   Owner: 0x1f82225723ED636b3463Cf2574b555D00D750Eb0
   Registered: 2026-03-12 15:29:20 UTC
   Data Type: ux-audit-original
   Status: ACTIVE

_next: download_data
_related: anchor_hash, health_check
```

### Step 12: Get Provenance Chain (3-record lineage) (`get_provenance_chain`)

**Status:** PASS | **Error:** False | **Time:** 2309ms

```
⛓️  Provenance Chain (3 entries)

d4c0d6c4af367b8cd5558aeb9936ae7e249f98453beaed8000eff161cfe8b5d3
   Owner: 0x1f82225723ED636b3463Cf2574b555D00D750Eb0
   Registered: 2026-03-12 15:29:20 UTC
   Type: ux-audit-original
   Status: ACTIVE
   Transform: Anonymized PII from raw sensor data
  └─ a9b22bfc1edf84461d39fef309b1df0bdfe9c44185ea9720530d1c5fcb6be7d7
     Owner: 0x1f82225723ED636b3463Cf2574b555D00D750Eb0
     Registered: 2026-03-12 15:30:30 UTC
     Type: ux-audit-original
     Status: ACTIVE
     Transform: Filtered for EU region
    └─ 543e6b9ec0c663bfe804b3064a97cad1c115c53fe22b0378e4bd26ecd360f033
       Owner: 0x1f82225723ED636b3463Cf2574b555D00D750Eb0
       Registered: 2026-03-12 15:30:58 UTC
       Type: ux-audit-original
       Status: ACTIVE

_next: get_provenance
_related: download_data, record_transform
```

### Step 12: Chain Balance (final) (`chain_balance`)

**Status:** PASS | **Error:** False | **Time:** 461ms

```
⛓️  Chain Wallet Balance

   Wallet: 0x1f82225723ED636b3463Cf2574b555D00D750Eb0
   Balance: 0.009976232374376547 ETH (9,976,232,374,376,547 wei)
   Chain: base-sepolia
   Contract: 0x9a3c6F47B69211F05891CCb7aD33596290b9fE64
   RPC: sepolia.base.org

_next: anchor_hash
_related: chain_health, health_check
```

## Recommendations

All checks passed — no recommendations at this time.
