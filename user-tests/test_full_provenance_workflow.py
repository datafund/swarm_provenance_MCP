"""End-to-end provenance workflow — UX audit test.

Run: CHAIN_ENABLED=true python user-tests/test_full_provenance_workflow.py
  or: CHAIN_ENABLED=true python -m user-tests.test_full_provenance_workflow

Requires: pip install -e .
          .env with SWARM_GATEWAY_URL (defaults to production gateway)
          Optionally: CHAIN_ENABLED=true, PROVENANCE_WALLET_KEY=0x...
"""

import asyncio
import json
import os
import sys
import time
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure repo root is on sys.path so imports work when run as a script
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from swarm_provenance_mcp.server import (
    handle_health_check,
    handle_purchase_stamp,
    handle_check_stamp_health,
    handle_upload_data,
    handle_download_data,
    handle_chain_health,
    handle_chain_balance,
    handle_anchor_hash,
    handle_verify_hash,
    handle_get_provenance,
    handle_record_transform,
    handle_get_provenance_chain,
    CHAIN_AVAILABLE,
    chain_client,
)
from swarm_provenance_mcp.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class StepResult:
    """Capture result of a single test step."""

    def __init__(self, step: int, name: str, tool: str):
        self.step = step
        self.name = name
        self.tool = tool
        self.passed: Optional[bool] = None
        self.response_text: str = ""
        self.is_error: bool = False
        self.ux_issues: List[str] = []
        self.notes: str = ""
        self.duration_ms: float = 0

    @property
    def status(self) -> str:
        if self.passed is None:
            return "SKIP"
        return "PASS" if self.passed else "FAIL"


def extract_text(result) -> Tuple[str, bool]:
    """Extract text content and isError from a CallToolResult."""
    text = ""
    for content in result.content:
        if hasattr(content, "text"):
            text += content.text
    is_error = getattr(result, "isError", False) or False
    return text, is_error


def print_step_header(step: int, name: str, tool: str):
    print(f"\n{'='*72}")
    print(f"  Step {step}: {name}")
    print(f"  Tool: {tool}")
    print(f"{'='*72}")


def print_response(text: str, is_error: bool):
    label = "ERROR RESPONSE" if is_error else "RESPONSE"
    print(f"\n--- {label} ---")
    print(text)
    print(f"--- /{label} ---")


def print_verdict(result: StepResult):
    icon = {"PASS": "+", "FAIL": "!", "SKIP": "-"}[result.status]
    print(f"\n  [{icon}] {result.status} ({result.duration_ms:.0f}ms)")
    if result.ux_issues:
        for issue in result.ux_issues:
            print(f"      UX: {issue}")
    if result.notes:
        print(f"      Note: {result.notes}")


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

async def step_health_check(results: List[StepResult]) -> StepResult:
    """Step 1: health_check — verify gateway + chain status."""
    r = StepResult(1, "Health Check", "health_check")
    print_step_header(r.step, r.name, r.tool)

    start = time.monotonic()
    result = await handle_health_check({})
    r.duration_ms = (time.monotonic() - start) * 1000

    r.response_text, r.is_error = extract_text(result)
    print_response(r.response_text, r.is_error)

    # Checks
    r.passed = not r.is_error and "Gateway" in r.response_text

    if "ready: true" not in r.response_text and "ready: false" not in r.response_text:
        r.ux_issues.append("Missing 'ready' boolean in response")

    if settings.chain_enabled and "Chain" not in r.response_text:
        r.ux_issues.append("Chain enabled but no chain status in health_check output")

    if settings.chain_enabled and chain_client and "Wallet:" in r.response_text:
        # If wallet is configured, health_check should warn about empty/low balance
        if "CRITICAL" not in r.response_text and "WARNING" not in r.response_text:
            # Only flag if wallet is actually empty (we check balance > 0 wouldn't trigger)
            pass  # Can't know balance here; just verify field presence

    if "_companion_servers" not in r.response_text:
        r.ux_issues.append("Missing _companion_servers section")

    if "_next:" not in r.response_text:
        r.ux_issues.append("Missing _next hint")

    print_verdict(r)
    return r


async def step_purchase_stamp(results: List[StepResult]) -> StepResult:
    """Step 2: purchase_stamp — buy a stamp."""
    r = StepResult(2, "Purchase Stamp", "purchase_stamp")
    print_step_header(r.step, r.name, r.tool)

    start = time.monotonic()
    result = await handle_purchase_stamp({})
    r.duration_ms = (time.monotonic() - start) * 1000

    r.response_text, r.is_error = extract_text(result)
    print_response(r.response_text, r.is_error)

    # Extract batch ID for later steps
    batch_id = None
    for line in r.response_text.split("\n"):
        if "Batch ID:" in line or "Stamp ID:" in line:
            # Extract the 64-char hex ID from backtick-wrapped text
            import re
            match = re.search(r"`([a-fA-F0-9]{64})`", line)
            if match:
                batch_id = match.group(1)
                break

    r.passed = not r.is_error and batch_id is not None

    if batch_id:
        r.notes = f"stamp_id={batch_id[:16]}..."
    else:
        r.ux_issues.append("Could not extract stamp batch ID from response")

    # UX checks
    if r.passed and "check_stamp_health" not in r.response_text:
        r.ux_issues.append("Missing _next hint to check_stamp_health")

    if r.passed and "pool" not in r.response_text.lower() and "propagat" not in r.response_text.lower():
        r.ux_issues.append("No pool/propagation awareness messaging")

    print_verdict(r)

    # Store batch_id for subsequent steps
    r._stamp_id = batch_id
    return r


async def step_check_stamp_health(results: List[StepResult], stamp_id: str) -> StepResult:
    """Step 3: check_stamp_health — poll until ready."""
    r = StepResult(3, "Check Stamp Health (poll)", "check_stamp_health")
    print_step_header(r.step, r.name, r.tool)

    max_polls = 10
    poll_interval = 15
    can_upload = False

    for attempt in range(1, max_polls + 1):
        print(f"\n  Poll {attempt}/{max_polls}...")
        start = time.monotonic()
        result = await handle_check_stamp_health({"stamp_id": stamp_id})
        elapsed = (time.monotonic() - start) * 1000
        r.duration_ms += elapsed

        r.response_text, r.is_error = extract_text(result)
        print_response(r.response_text, r.is_error)

        if "can_upload" in r.response_text.lower() or "ready for uploads" in r.response_text.lower():
            can_upload = True
            break

        if attempt < max_polls:
            print(f"  Waiting {poll_interval}s before next poll...")
            await asyncio.sleep(poll_interval)

    r.passed = can_upload

    if not can_upload:
        r.ux_issues.append(f"Stamp not ready after {max_polls} polls ({max_polls * poll_interval}s)")

    if "propagat" not in r.response_text.lower() and not can_upload:
        r.ux_issues.append("No propagation guidance when stamp not ready")

    if "_next:" not in r.response_text:
        r.ux_issues.append("Missing _next hint")

    r.notes = f"polls={attempt}, can_upload={can_upload}"
    print_verdict(r)
    return r


async def step_upload_data(results: List[StepResult], stamp_id: str) -> StepResult:
    """Step 4: upload_data — upload test payload."""
    r = StepResult(4, "Upload Data", "upload_data")
    print_step_header(r.step, r.name, r.tool)

    test_payload = json.dumps({
        "test": "provenance-ux-audit",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": "End-to-end provenance workflow test data",
    })

    start = time.monotonic()
    result = await handle_upload_data({
        "data": test_payload,
        "stamp_id": stamp_id,
        "content_type": "application/json",
    })
    r.duration_ms = (time.monotonic() - start) * 1000

    r.response_text, r.is_error = extract_text(result)
    print_response(r.response_text, r.is_error)

    # Extract reference hash
    import re
    ref_match = re.search(r"Reference Hash:\s*`([a-fA-F0-9]{64})`", r.response_text)
    ref_hash = ref_match.group(1) if ref_match else None

    r.passed = not r.is_error and ref_hash is not None

    if ref_hash:
        r.notes = f"reference={ref_hash[:16]}..."
    else:
        r.ux_issues.append("Could not extract reference hash from response")

    if r.passed and "download_data" not in r.response_text:
        r.ux_issues.append("Missing _next hint to download_data")

    print_verdict(r)
    r._reference = ref_hash
    r._payload = test_payload
    return r


async def step_download_data(results: List[StepResult], reference: str, expected_payload: str) -> StepResult:
    """Step 5: download_data — verify round-trip integrity."""
    r = StepResult(5, "Download Data (round-trip)", "download_data")
    print_step_header(r.step, r.name, r.tool)

    start = time.monotonic()
    result = await handle_download_data({"reference": reference})
    r.duration_ms = (time.monotonic() - start) * 1000

    r.response_text, r.is_error = extract_text(result)
    print_response(r.response_text, r.is_error)

    # Check that key fields from our upload are present in the downloaded data
    expected_data = json.loads(expected_payload)
    integrity_ok = all(
        key in r.response_text for key in expected_data.keys()
    )

    r.passed = not r.is_error and integrity_ok

    if not integrity_ok:
        r.ux_issues.append("Downloaded data does not contain expected fields from upload")

    print_verdict(r)
    return r


async def step_chain_health(results: List[StepResult]) -> StepResult:
    """Step 6: chain_health — verify RPC connectivity (read-only)."""
    r = StepResult(6, "Chain Health", "chain_health")
    print_step_header(r.step, r.name, r.tool)

    start = time.monotonic()
    result = await handle_chain_health({})
    r.duration_ms = (time.monotonic() - start) * 1000

    r.response_text, r.is_error = extract_text(result)
    print_response(r.response_text, r.is_error)

    r.passed = not r.is_error and "Connected: true" in r.response_text

    if r.passed and "Latest Block" not in r.response_text:
        r.ux_issues.append("Missing block number in chain_health response")

    if r.passed and "Contract:" not in r.response_text:
        r.ux_issues.append("Missing contract address in chain_health response")

    if "_next:" not in r.response_text:
        r.ux_issues.append("Missing _next hint")

    print_verdict(r)
    return r


async def step_chain_balance_no_wallet(results: List[StepResult]) -> StepResult:
    """Step 7: chain_balance — expect failure (no wallet configured)."""
    r = StepResult(7, "Chain Balance (no wallet)", "chain_balance")
    print_step_header(r.step, r.name, r.tool)

    start = time.monotonic()
    result = await handle_chain_balance({})
    r.duration_ms = (time.monotonic() - start) * 1000

    r.response_text, r.is_error = extract_text(result)
    print_response(r.response_text, r.is_error)

    # We EXPECT this to fail when no wallet is configured
    has_wallet = chain_client is not None

    if has_wallet:
        # If wallet IS present, this should succeed
        r.passed = not r.is_error
        r.notes = "Wallet is configured — balance check succeeded"
    else:
        # No wallet: should fail with informative error
        r.passed = r.is_error  # We expect an error

        if "PROVENANCE_WALLET_KEY" not in r.response_text:
            r.ux_issues.append("Error does not mention PROVENANCE_WALLET_KEY")
            r.passed = False

        if "read-only" not in r.response_text.lower() and "verify_hash" not in r.response_text:
            r.ux_issues.append("Error does not list read-only alternatives")

        if "_next:" not in r.response_text:
            r.ux_issues.append("Missing _next recovery hint")

        r.notes = "No wallet — error message quality check"

    print_verdict(r)
    return r


async def step_anchor_hash_no_wallet(results: List[StepResult], swarm_hash: str) -> StepResult:
    """Step 8: anchor_hash — test error quality (no wallet or empty wallet)."""
    r = StepResult(8, "Anchor Hash (error quality)", "anchor_hash")
    print_step_header(r.step, r.name, r.tool)

    has_wallet = chain_client is not None

    if has_wallet:
        # Wallet is configured — skip this step (write tests happen in step 11).
        # Using a dummy hash avoids wasting gas on an error-quality test.
        dummy_hash = "ff" * 32  # never-uploaded hash
        start = time.monotonic()
        # We just verify anchor_hash doesn't crash with a valid but non-uploaded hash
        result = await handle_anchor_hash({"swarm_hash": dummy_hash})
        r.duration_ms = (time.monotonic() - start) * 1000
        r.response_text, r.is_error = extract_text(result)
        print_response(r.response_text, r.is_error)
        # Either succeeds (anchors dummy) or fails — both are fine for this test
        r.passed = True
        r.notes = "Wallet configured — write tests in step 11 (used dummy hash)"
    else:
        start = time.monotonic()
        result = await handle_anchor_hash({"swarm_hash": swarm_hash})
        r.duration_ms = (time.monotonic() - start) * 1000

        r.response_text, r.is_error = extract_text(result)
        print_response(r.response_text, r.is_error)

        r.passed = r.is_error

        if "PROVENANCE_WALLET_KEY" not in r.response_text:
            r.ux_issues.append("Error does not mention PROVENANCE_WALLET_KEY")
            r.passed = False

        if "read-only" not in r.response_text.lower():
            r.ux_issues.append("Error does not mention read-only alternatives")

        r.notes = "No wallet — error message quality check"

    print_verdict(r)
    return r


async def step_verify_hash(results: List[StepResult]) -> StepResult:
    """Step 9: verify_hash — read-only with a never-anchored hash."""
    r = StepResult(9, "Verify Hash (read-only)", "verify_hash")
    print_step_header(r.step, r.name, r.tool)

    # Use a deterministic never-anchored hash to test the "not registered" path
    unanchored_hash = "00" * 32

    start = time.monotonic()
    result = await handle_verify_hash({"swarm_hash": unanchored_hash})
    r.duration_ms = (time.monotonic() - start) * 1000

    r.response_text, r.is_error = extract_text(result)
    print_response(r.response_text, r.is_error)

    r.passed = not r.is_error and "not registered" in r.response_text.lower()

    if r.passed and "_next:" not in r.response_text:
        r.ux_issues.append("Missing _next hint")

    if not r.is_error and "not registered" in r.response_text.lower():
        if "anchor_hash" not in r.response_text:
            r.ux_issues.append("'Not registered' response does not suggest anchor_hash")

    print_verdict(r)
    return r


async def step_get_provenance(results: List[StepResult]) -> StepResult:
    """Step 10: get_provenance — read-only with a never-anchored hash."""
    r = StepResult(10, "Get Provenance (read-only)", "get_provenance")
    print_step_header(r.step, r.name, r.tool)

    # Use a deterministic never-anchored hash
    unanchored_hash = "00" * 32

    start = time.monotonic()
    result = await handle_get_provenance({"swarm_hash": unanchored_hash})
    r.duration_ms = (time.monotonic() - start) * 1000

    r.response_text, r.is_error = extract_text(result)
    print_response(r.response_text, r.is_error)

    not_found = "not registered" in r.response_text.lower() or "not found" in r.response_text.lower()
    r.passed = not_found

    if r.is_error and not_found:
        r.ux_issues.append("'Not registered' returned as isError — should be a normal response guiding to anchor_hash")

    if "_next:" not in r.response_text:
        r.ux_issues.append("Missing _next hint")

    print_verdict(r)
    return r


RPC_PROPAGATION_DELAY = 5  # seconds to wait for public RPC to reflect writes
RATE_LIMIT_COOLDOWN = 65  # seconds to wait for a clean rate-limit window (free tier: 3 write req/min)
RATE_LIMIT_DELAY = 25  # seconds between gateway writes within a window


async def step_wallet_write_workflow(results: List[StepResult], swarm_hash: str) -> List[StepResult]:
    """Steps 11-12: Full write workflow (only when wallet is available).

    Reuses the uploaded hash from step 4 as hash1 (avoids an extra gateway
    write), uploads 2 more payloads to Swarm, anchors all 3 on-chain,
    records two transformations (hash1->hash2, hash2->hash3), then queries
    get_provenance_chain to verify the full 3-record lineage tree.

    Includes rate-limit cooldowns and RPC propagation delays.
    """
    step_results = []
    import re

    # We need a stamp for uploads — reuse the one from step 2
    stamp_id = None
    for prev in results:
        if hasattr(prev, "_stamp_id") and prev._stamp_id:
            stamp_id = prev._stamp_id
            break

    if not stamp_id:
        r_skip = StepResult(11, "Write workflow", "upload_data")
        r_skip.passed = False
        r_skip.notes = "No stamp_id available from step 2 — skipping"
        step_results.append(r_skip)
        return step_results

    # --- Step 11a: Anchor hash1 (reuse step 4 upload) ---
    r_anchor = StepResult(11, "Anchor original hash on-chain", "anchor_hash")
    print_step_header(r_anchor.step, "Anchor original hash (reuse step 4 upload)", r_anchor.tool)

    start = time.monotonic()

    # Hash 1: reuse the Swarm hash already uploaded in step 4 — no gateway write needed
    hash1 = swarm_hash
    print(f"\n  Hash 1 (original): reusing step 4 upload {hash1[:16]}...")

    result_a1 = await handle_anchor_hash({"swarm_hash": hash1, "data_type": "ux-audit-original"})
    text_a1, err_a1 = extract_text(result_a1)
    print_response(text_a1, err_a1)

    if err_a1 and "already registered" not in text_a1.lower():
        r_anchor.duration_ms = (time.monotonic() - start) * 1000
        r_anchor.response_text = text_a1
        r_anchor.is_error = True
        r_anchor.passed = False
        r_anchor.notes = "Failed to anchor hash1"
        print_verdict(r_anchor)
        step_results.append(r_anchor)
        return step_results

    r_anchor.duration_ms = (time.monotonic() - start) * 1000
    r_anchor.response_text = text_a1
    r_anchor.passed = True
    r_anchor.notes = f"hash1={hash1[:16]}..."

    if "Tx Hash:" not in text_a1 and "already registered" not in text_a1.lower():
        r_anchor.ux_issues.append("Missing Tx Hash in anchor response")
    if "Explorer:" not in text_a1 and "already registered" not in text_a1.lower():
        r_anchor.ux_issues.append("Missing Explorer URL in anchor response")

    print_verdict(r_anchor)
    step_results.append(r_anchor)

    # Cool down to let the rate-limit window from steps 2+4 expire
    print(f"\n  Rate limit cooldown ({RATE_LIMIT_COOLDOWN}s) — waiting for clean window...")
    await asyncio.sleep(RATE_LIMIT_COOLDOWN)

    # --- Step 11b: Upload hash2 + record_transform hash1 -> hash2 ---
    # The contract requires that new_hash does NOT already exist —
    # record_transform registers the new hash as part of the transform.
    r_t1 = StepResult(11, "Upload + Transform (1->2)", "record_transform")
    print_step_header(r_t1.step, "Upload hash2 + record_transform (1->2)", r_t1.tool)

    start = time.monotonic()

    payload2 = json.dumps({
        "test": "provenance-chain-test",
        "version": 2,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": "Anonymized — PII removed from sensor data",
    })
    result_up2 = await handle_upload_data({
        "data": payload2, "stamp_id": stamp_id, "content_type": "application/json",
    })
    text_up2, err_up2 = extract_text(result_up2)
    ref_match2 = re.search(r"Reference Hash:\s*`([a-fA-F0-9]{64})`", text_up2)
    hash2 = ref_match2.group(1) if ref_match2 else None

    if not hash2:
        r_t1.duration_ms = (time.monotonic() - start) * 1000
        r_t1.response_text = text_up2
        r_t1.is_error = True
        r_t1.passed = False
        r_t1.notes = "Failed to upload hash2"
        print_verdict(r_t1)
        step_results.append(r_t1)
        return step_results

    print(f"\n  Hash 2 (anonymized): uploaded {hash2[:16]}...")

    result_t1 = await handle_record_transform({
        "original_hash": hash1,
        "new_hash": hash2,
        "description": "Anonymized PII from raw sensor data",
    })
    r_t1.duration_ms = (time.monotonic() - start) * 1000
    r_t1.response_text, r_t1.is_error = extract_text(result_t1)
    print_response(r_t1.response_text, r_t1.is_error)

    r_t1.passed = not r_t1.is_error
    if r_t1.passed and "Tx Hash:" not in r_t1.response_text:
        r_t1.ux_issues.append("Missing tx hash in transform response")

    r_t1.notes = f"hash1→hash2 ({hash2[:16]}...)"
    print_verdict(r_t1)
    step_results.append(r_t1)

    if not r_t1.passed:
        return step_results

    print(f"\n  Rate limit pause ({RATE_LIMIT_DELAY}s)...")
    await asyncio.sleep(RATE_LIMIT_DELAY)

    # --- Step 11c: Upload hash3 + record_transform hash2 -> hash3 ---
    r_t2 = StepResult(11, "Upload + Transform (2->3)", "record_transform")
    print_step_header(r_t2.step, "Upload hash3 + record_transform (2->3)", r_t2.tool)

    start = time.monotonic()

    payload3 = json.dumps({
        "test": "provenance-chain-test",
        "version": 3,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": "Filtered — EU region only from anonymized data",
    })
    result_up3 = await handle_upload_data({
        "data": payload3, "stamp_id": stamp_id, "content_type": "application/json",
    })
    text_up3, err_up3 = extract_text(result_up3)
    ref_match3 = re.search(r"Reference Hash:\s*`([a-fA-F0-9]{64})`", text_up3)
    hash3 = ref_match3.group(1) if ref_match3 else None

    if not hash3:
        r_t2.duration_ms = (time.monotonic() - start) * 1000
        r_t2.response_text = text_up3
        r_t2.is_error = True
        r_t2.passed = False
        r_t2.notes = "Failed to upload hash3"
        print_verdict(r_t2)
        step_results.append(r_t2)
        return step_results

    print(f"\n  Hash 3 (EU filtered): uploaded {hash3[:16]}...")

    result_t2 = await handle_record_transform({
        "original_hash": hash2,
        "new_hash": hash3,
        "description": "Filtered for EU region",
    })
    r_t2.duration_ms = (time.monotonic() - start) * 1000
    r_t2.response_text, r_t2.is_error = extract_text(result_t2)
    print_response(r_t2.response_text, r_t2.is_error)

    r_t2.passed = not r_t2.is_error
    if r_t2.passed and "Tx Hash:" not in r_t2.response_text:
        r_t2.ux_issues.append("Missing tx hash in transform response")

    r_t2.notes = f"hash2→hash3 ({hash3[:16]}...)"
    print_verdict(r_t2)
    step_results.append(r_t2)

    if not r_t2.passed:
        return step_results

    # --- Wait for RPC propagation before lineage query ---
    print(f"\n  Waiting {RPC_PROPAGATION_DELAY}s for RPC propagation...")
    await asyncio.sleep(RPC_PROPAGATION_DELAY)

    # --- Step 11d: verify the original hash shows as registered ---
    r_verify = StepResult(11, "Verify anchored hash", "verify_hash")
    print_step_header(r_verify.step, r_verify.name, r_verify.tool)

    start = time.monotonic()
    result_v = await handle_verify_hash({"swarm_hash": hash1})
    r_verify.duration_ms = (time.monotonic() - start) * 1000

    r_verify.response_text, r_verify.is_error = extract_text(result_v)
    print_response(r_verify.response_text, r_verify.is_error)

    r_verify.passed = not r_verify.is_error and "registered" in r_verify.response_text.lower()
    if "not registered" in r_verify.response_text.lower():
        r_verify.passed = False
        r_verify.ux_issues.append("Hash not visible after anchoring — RPC propagation delay")

    print_verdict(r_verify)
    step_results.append(r_verify)

    # --- Step 12: get_provenance_chain from hash1 ---
    r_chain = StepResult(12, "Get Provenance Chain (3-record lineage)", "get_provenance_chain")
    print_step_header(r_chain.step, r_chain.name, r_chain.tool)

    start = time.monotonic()
    result_chain = await handle_get_provenance_chain({"swarm_hash": hash1})
    r_chain.duration_ms = (time.monotonic() - start) * 1000

    r_chain.response_text, r_chain.is_error = extract_text(result_chain)
    print_response(r_chain.response_text, r_chain.is_error)

    r_chain.passed = not r_chain.is_error

    # Check that we see multiple entries in the chain
    if "entries" in r_chain.response_text or "entry" in r_chain.response_text:
        count_match = re.search(r"(\d+)\s+entr", r_chain.response_text)
        if count_match:
            entry_count = int(count_match.group(1))
            r_chain.notes = f"Lineage entries: {entry_count}"
            if entry_count < 3:
                r_chain.ux_issues.append(f"Expected 3 lineage entries, got {entry_count}")
        else:
            r_chain.notes = "Could not parse entry count"
    elif "no provenance chain" in r_chain.response_text.lower():
        r_chain.ux_issues.append("Provenance chain not found — RPC propagation delay may affect reads")
    else:
        r_chain.ux_issues.append("Response does not indicate number of lineage entries")

    # Verify all 3 hashes appear in the chain
    for h_label, h_val in [("hash1", hash1), ("hash2", hash2), ("hash3", hash3)]:
        if h_val not in r_chain.response_text:
            r_chain.ux_issues.append(f"{h_label} ({h_val[:16]}...) missing from lineage")

    if "_next:" not in r_chain.response_text:
        r_chain.ux_issues.append("Missing _next hint")

    print_verdict(r_chain)
    step_results.append(r_chain)

    # --- Step 12b: check balance at the end ---
    r_bal = StepResult(12, "Chain Balance (final)", "chain_balance")
    print_step_header(r_bal.step, r_bal.name, r_bal.tool)

    start = time.monotonic()
    result_bal = await handle_chain_balance({})
    r_bal.duration_ms = (time.monotonic() - start) * 1000

    r_bal.response_text, r_bal.is_error = extract_text(result_bal)
    print_response(r_bal.response_text, r_bal.is_error)

    r_bal.passed = not r_bal.is_error
    r_bal.notes = "Final balance after 5 on-chain transactions"
    if "CRITICAL" in r_bal.response_text or "WARNING" in r_bal.response_text:
        r_bal.notes += " — low balance warning"

    print_verdict(r_bal)
    step_results.append(r_bal)

    return step_results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_audit():
    """Run the full end-to-end provenance workflow audit."""
    print("\n" + "=" * 72)
    print("  PROVENANCE UX AUDIT — End-to-End Workflow Test")
    print("=" * 72)
    print(f"\n  Gateway:  {settings.swarm_gateway_url}")
    print(f"  Chain:    {'enabled' if settings.chain_enabled else 'disabled'}")
    print(f"  Chain OK: {CHAIN_AVAILABLE}")
    print(f"  Wallet:   {'configured' if chain_client else 'not configured'}")
    print(f"  Time:     {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

    results: List[StepResult] = []

    # Step 1: health_check
    r1 = await step_health_check(results)
    results.append(r1)

    if not r1.passed:
        print("\n  Gateway not healthy — aborting remaining steps.")
        print_summary(results)
        return results

    # Step 2: purchase_stamp
    r2 = await step_purchase_stamp(results)
    results.append(r2)

    if not r2.passed or not r2._stamp_id:
        print("\n  Stamp purchase failed — aborting remaining steps.")
        print_summary(results)
        return results

    stamp_id = r2._stamp_id

    # Step 3: check_stamp_health (poll until ready)
    r3 = await step_check_stamp_health(results, stamp_id)
    results.append(r3)

    if not r3.passed:
        print("\n  Stamp not ready — aborting upload steps.")
        print_summary(results)
        return results

    # Step 4: upload_data
    r4 = await step_upload_data(results, stamp_id)
    results.append(r4)

    if not r4.passed or not r4._reference:
        print("\n  Upload failed — aborting remaining steps.")
        print_summary(results)
        return results

    reference = r4._reference
    payload = r4._payload

    # Step 5: download_data
    r5 = await step_download_data(results, reference, payload)
    results.append(r5)

    # --- Chain steps (6-12) ---
    if not settings.chain_enabled or not CHAIN_AVAILABLE:
        print(f"\n{'='*72}")
        print("  Chain not enabled/available — skipping steps 6-12.")
        print(f"  Set CHAIN_ENABLED=true to run chain tests.")
        print(f"{'='*72}")
        print_summary(results)
        return results

    # Step 6: chain_health
    r6 = await step_chain_health(results)
    results.append(r6)

    # Step 7: chain_balance (expect failure without wallet)
    r7 = await step_chain_balance_no_wallet(results)
    results.append(r7)

    # Step 8: anchor_hash (expect failure without wallet)
    r8 = await step_anchor_hash_no_wallet(results, reference)
    results.append(r8)

    # Step 9: verify_hash (read-only, never-anchored hash)
    r9 = await step_verify_hash(results)
    results.append(r9)

    # Step 10: get_provenance (read-only, never-anchored hash)
    r10 = await step_get_provenance(results)
    results.append(r10)

    # Steps 11-12: write workflow (only if wallet is available)
    if chain_client:
        print(f"\n{'='*72}")
        print("  Wallet detected — running write workflow (steps 11-12)")
        print(f"{'='*72}")
        write_results = await step_wallet_write_workflow(results, reference)
        results.extend(write_results)
    else:
        print(f"\n{'='*72}")
        print("  No wallet configured — skipping write workflow (steps 11-12)")
        print(f"  Set PROVENANCE_WALLET_KEY=0x... for full write test")
        print(f"{'='*72}")

    print_summary(results)
    write_report(results)
    return results


def print_summary(results: List[StepResult]):
    """Print final summary table."""
    print(f"\n\n{'='*72}")
    print("  SUMMARY")
    print(f"{'='*72}\n")

    total = len(results)
    passed = sum(1 for r in results if r.passed is True)
    failed = sum(1 for r in results if r.passed is False)
    skipped = sum(1 for r in results if r.passed is None)
    all_ux = []

    print(f"  {'Step':<6} {'Status':<8} {'Tool':<25} {'Time':>8}  Notes")
    print(f"  {'─'*6} {'─'*8} {'─'*25} {'─':>8}  {'─'*30}")

    for r in results:
        icon = {"PASS": "+", "FAIL": "!", "SKIP": "-"}.get(r.status, "?")
        time_str = f"{r.duration_ms:.0f}ms"
        note = r.notes[:40] if r.notes else ""
        print(f"  {r.step:<6} [{icon}] {r.status:<4} {r.tool:<25} {time_str:>8}  {note}")
        all_ux.extend(r.ux_issues)

    print(f"\n  Total: {total}  Passed: {passed}  Failed: {failed}  Skipped: {skipped}")

    if all_ux:
        print(f"\n  UX Issues Found ({len(all_ux)}):")
        for i, issue in enumerate(all_ux, 1):
            print(f"    {i}. {issue}")

    print()


def write_report(results: List[StepResult]):
    """Write UX audit report to user-tests/UX_AUDIT_REPORT.md."""
    report_path = Path(__file__).parent / "UX_AUDIT_REPORT.md"

    lines = []
    lines.append("# UX Audit Report — End-to-End Provenance Workflow")
    lines.append("")
    lines.append(f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"**Gateway:** {settings.swarm_gateway_url}")
    lines.append(f"**Chain:** {'enabled' if settings.chain_enabled else 'disabled'} ({settings.chain_name})")
    lines.append(f"**Wallet:** {'configured' if chain_client else 'not configured'}")
    lines.append("")

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r.passed is True)
    failed = sum(1 for r in results if r.passed is False)
    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total steps | {total} |")
    lines.append(f"| Passed | {passed} |")
    lines.append(f"| Failed | {failed} |")
    lines.append("")

    # Step-by-step results
    lines.append("## Step Results")
    lines.append("")
    lines.append("| Step | Tool | Status | Time | Notes |")
    lines.append("|------|------|--------|------|-------|")

    for r in results:
        notes = r.notes.replace("|", "\\|") if r.notes else ""
        lines.append(f"| {r.step} | `{r.tool}` | {r.status} | {r.duration_ms:.0f}ms | {notes} |")

    lines.append("")

    # UX Issues
    all_ux = []
    for r in results:
        for issue in r.ux_issues:
            all_ux.append((r.step, r.tool, issue))

    lines.append("## UX Issues")
    lines.append("")

    if all_ux:
        lines.append(f"Found **{len(all_ux)}** UX issues:")
        lines.append("")
        lines.append("| # | Step | Tool | Issue |")
        lines.append("|---|------|------|-------|")
        for i, (step, tool, issue) in enumerate(all_ux, 1):
            lines.append(f"| {i} | {step} | `{tool}` | {issue} |")
    else:
        lines.append("No UX issues found.")

    lines.append("")

    # Detailed responses
    lines.append("## Detailed Responses")
    lines.append("")

    for r in results:
        lines.append(f"### Step {r.step}: {r.name} (`{r.tool}`)")
        lines.append("")
        lines.append(f"**Status:** {r.status} | **Error:** {r.is_error} | **Time:** {r.duration_ms:.0f}ms")
        lines.append("")
        lines.append("```")
        lines.append(r.response_text)
        lines.append("```")
        lines.append("")
        if r.ux_issues:
            lines.append("**UX Issues:**")
            for issue in r.ux_issues:
                lines.append(f"- {issue}")
            lines.append("")

    # Recommendations
    lines.append("## Recommendations")
    lines.append("")

    if all_ux:
        lines.append("Based on the issues found above:")
        lines.append("")
        for i, (step, tool, issue) in enumerate(all_ux, 1):
            lines.append(f"{i}. **Step {step} ({tool}):** {issue}")
        lines.append("")
    else:
        lines.append("All checks passed — no recommendations at this time.")

    lines.append("")

    report_path.write_text("\n".join(lines))
    print(f"  Report written to: {report_path}")


if __name__ == "__main__":
    asyncio.run(run_audit())
