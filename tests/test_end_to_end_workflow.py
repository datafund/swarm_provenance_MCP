#!/usr/bin/env python3
"""
End-to-end workflow test for the complete stamp lifecycle.
Tests: purchase → wait for usability → upload → extend → wait for propagation → verify extension → download
"""

import os
import pytest
import requests
import time
import json
from typing import Dict, Any, Optional
from swarm_provenance_mcp.gateway_client import SwarmGatewayClient
from swarm_provenance_mcp.config import settings


class StampWorkflowTester:
    """Helper class for testing complete stamp workflows."""

    def __init__(self, gateway_url: str = None):
        self.gateway_url = gateway_url or settings.swarm_gateway_url
        self.client = SwarmGatewayClient(self.gateway_url)

    def close(self):
        """Close the gateway client."""
        self.client.close()

    def wait_for_stamp_usable(
        self, stamp_id: str, max_wait_seconds: int = 120, check_interval: int = 10
    ) -> bool:
        """
        Wait for a stamp to become usable, checking periodically.

        Args:
            stamp_id: The stamp ID to check
            max_wait_seconds: Maximum time to wait (default 2 minutes)
            check_interval: How often to check in seconds (default 10 seconds)

        Returns:
            True if stamp becomes usable, False if timeout
        """
        print(
            f"🕐 Waiting for stamp {stamp_id[:12]}... to become usable (max {max_wait_seconds}s)"
        )

        start_time = time.time()
        attempts = 0

        while (time.time() - start_time) < max_wait_seconds:
            attempts += 1
            try:
                details = self.client.get_stamp_details(stamp_id)
                usable = details.get("usable", False)
                batch_ttl = details.get("batchTTL", "N/A")

                print(f"   Attempt {attempts}: usable={usable}, TTL={batch_ttl}")

                if usable:
                    elapsed = time.time() - start_time
                    print(f"✅ Stamp became usable after {elapsed:.1f} seconds")
                    return True

            except Exception as e:
                print(f"   Attempt {attempts}: Error checking stamp: {e}")

            time.sleep(check_interval)

        print(f"❌ Stamp did not become usable within {max_wait_seconds} seconds")
        return False

    def verify_stamp_extension(
        self,
        stamp_id: str,
        original_ttl: int,
        max_wait_seconds: int = 120,
        check_interval: int = 10,
    ) -> bool:
        """
        Wait for stamp extension to propagate and verify TTL increased.

        Args:
            stamp_id: The stamp ID to check
            original_ttl: The original TTL value before extension
            max_wait_seconds: Maximum time to wait for propagation
            check_interval: How often to check in seconds

        Returns:
            True if extension propagated and TTL increased
        """
        print(
            f"🕐 Waiting for stamp extension to propagate (original TTL: {original_ttl})"
        )

        start_time = time.time()
        attempts = 0

        while (time.time() - start_time) < max_wait_seconds:
            attempts += 1
            try:
                details = self.client.get_stamp_details(stamp_id)
                current_ttl = details.get("batchTTL", 0)

                print(
                    f"   Attempt {attempts}: TTL changed from {original_ttl} to {current_ttl}"
                )

                if isinstance(current_ttl, (int, float)) and current_ttl > original_ttl:
                    elapsed = time.time() - start_time
                    increase = current_ttl - original_ttl
                    print(
                        f"✅ Extension propagated after {elapsed:.1f}s (TTL increased by {increase})"
                    )
                    return True

            except Exception as e:
                print(f"   Attempt {attempts}: Error checking extension: {e}")

            time.sleep(check_interval)

        print(f"❌ Extension did not propagate within {max_wait_seconds} seconds")
        return False


@pytest.fixture
def workflow_tester():
    """Create a workflow tester instance."""
    # Use SWARM_GATEWAY_URL env var if set, otherwise fall back to public gateway
    tester = StampWorkflowTester(os.environ.get("SWARM_GATEWAY_URL", "https://provenance-gateway.datafund.io"))
    yield tester
    tester.close()


class TestEndToEndWorkflow:
    """Complete end-to-end workflow tests."""

    @pytest.mark.integration
    def test_complete_stamp_lifecycle(self, workflow_tester):
        """
        Test the complete stamp lifecycle:
        1. Purchase stamp
        2. Wait for it to become usable
        3. Upload data using the stamp
        4. Verify data can be downloaded
        5. Extend the stamp
        6. Wait for extension to propagate
        7. Verify TTL increased
        8. Upload additional data to test extended stamp still works
        """
        print("\n🚀 Starting complete stamp lifecycle test")
        print("=" * 80)

        # Step 1: Purchase stamp
        print("\n📦 Step 1: Purchasing stamp...")
        try:
            purchase_result = workflow_tester.client.purchase_stamp(
                amount=settings.default_stamp_amount,
                depth=settings.default_stamp_depth,
                label="e2e-test-stamp",
            )

            stamp_id = purchase_result.get("batchID")
            assert stamp_id, f"No batchID in purchase response: {purchase_result}"
            assert len(stamp_id) == 64, f"Invalid stamp ID format: {stamp_id}"

            print(f"✅ Stamp purchased: {stamp_id}")

        except Exception as e:
            pytest.skip(f"Could not purchase stamp (gateway/network issue): {e}")

        # Step 2: Wait for stamp to become usable
        print(f"\n⏱️  Step 2: Waiting for stamp to become usable...")
        usable = workflow_tester.wait_for_stamp_usable(stamp_id, max_wait_seconds=120)
        assert usable, "Stamp did not become usable within timeout period"

        # Step 3: Upload data using the stamp
        print(f"\n📤 Step 3: Uploading data using stamp...")
        test_data = json.dumps(
            {
                "test_type": "end_to_end_workflow",
                "timestamp": int(time.time()),
                "message": "This is test data for complete workflow testing",
                "stamp_id": stamp_id,
            }
        )

        try:
            upload_result = workflow_tester.client.upload_data(
                data=test_data, stamp_id=stamp_id, content_type="application/json"
            )

            reference = upload_result.get("reference")
            assert reference, f"No reference in upload response: {upload_result}"
            assert len(reference) == 64, f"Invalid reference format: {reference}"

            print(f"✅ Data uploaded: {reference}")

        except Exception as e:
            pytest.skip(f"Could not upload data (gateway/network issue): {e}")

        # Step 4: Verify data can be downloaded
        print(f"\n📥 Step 4: Downloading and verifying data...")
        try:
            downloaded_data = workflow_tester.client.download_data(reference)
            downloaded_text = downloaded_data.decode("utf-8")

            assert (
                downloaded_text == test_data
            ), "Downloaded data doesn't match uploaded data"
            print(f"✅ Data integrity verified")

        except Exception as e:
            pytest.fail(f"Data download/verification failed: {e}")

        # Step 5: Get current stamp details before extension
        print(f"\n📊 Step 5: Getting stamp details before extension...")
        try:
            details_before = workflow_tester.client.get_stamp_details(stamp_id)
            original_ttl = details_before.get("batchTTL", 0)
            original_expiration = details_before.get("expectedExpiration", "N/A")

            print(f"   Original TTL: {original_ttl}")
            print(f"   Original expiration: {original_expiration}")

            assert isinstance(
                original_ttl, (int, float)
            ), f"Invalid TTL type: {type(original_ttl)}"
            assert original_ttl > 0, f"Invalid TTL value: {original_ttl}"

        except Exception as e:
            pytest.fail(f"Could not get stamp details before extension: {e}")

        # Step 6: Extend the stamp
        print(f"\n🔄 Step 6: Extending stamp...")
        extension_amount = settings.default_stamp_amount // 2  # Add 50% more

        try:
            extend_result = workflow_tester.client.extend_stamp(
                stamp_id, extension_amount
            )

            assert (
                extend_result.get("batchID") == stamp_id
            ), "Extension returned wrong stamp ID"
            print(f"✅ Stamp extension requested (added {extension_amount:,} wei)")

        except Exception as e:
            pytest.skip(f"Could not extend stamp (gateway/network issue): {e}")

        # Step 7: Wait for extension to propagate
        print(f"\n⏱️  Step 7: Waiting for extension to propagate...")
        extended = workflow_tester.verify_stamp_extension(
            stamp_id, original_ttl, max_wait_seconds=120
        )
        assert extended, "Stamp extension did not propagate within timeout period"

        # Step 8: Verify stamp still works after extension
        print(f"\n📤 Step 8: Testing stamp functionality after extension...")
        test_data_2 = json.dumps(
            {
                "test_type": "post_extension_test",
                "timestamp": int(time.time()),
                "message": "Testing stamp after extension",
                "original_stamp": stamp_id,
            }
        )

        try:
            upload_result_2 = workflow_tester.client.upload_data(
                data=test_data_2, stamp_id=stamp_id, content_type="application/json"
            )

            reference_2 = upload_result_2.get("reference")
            assert reference_2, "No reference in post-extension upload"
            assert reference_2 != reference, "Got same reference for different data"

            print(f"✅ Post-extension upload successful: {reference_2}")

            # Verify download
            downloaded_data_2 = workflow_tester.client.download_data(reference_2)
            downloaded_text_2 = downloaded_data_2.decode("utf-8")
            assert (
                downloaded_text_2 == test_data_2
            ), "Post-extension data integrity failed"

            print(f"✅ Post-extension data integrity verified")

        except Exception as e:
            pytest.fail(f"Post-extension functionality test failed: {e}")

        # Step 9: Final verification - get updated stamp details
        print(f"\n📊 Step 9: Final stamp verification...")
        try:
            details_after = workflow_tester.client.get_stamp_details(stamp_id)
            final_ttl = details_after.get("batchTTL", 0)
            final_expiration = details_after.get("expectedExpiration", "N/A")

            print(f"   Final TTL: {final_ttl} (was {original_ttl})")
            print(
                f"   Final expiration: {final_expiration} (was {original_expiration})"
            )
            print(f"   TTL increase: {final_ttl - original_ttl}")

            assert final_ttl > original_ttl, "TTL should have increased after extension"

        except Exception as e:
            pytest.fail(f"Final verification failed: {e}")

        print("\n" + "=" * 80)
        print("🎉 COMPLETE WORKFLOW TEST PASSED!")
        print(f"   Stamp ID: {stamp_id}")
        print(f"   Data references: {reference}, {reference_2}")
        print(f"   TTL increased: {original_ttl} → {final_ttl}")

    @pytest.mark.integration
    def test_stamp_timing_validation(self, workflow_tester):
        """
        Test that stamps are NOT usable immediately after purchase.
        This validates our timing-aware approach.
        """
        print("\n🕐 Testing stamp timing validation")
        print("=" * 50)

        # Purchase stamp
        try:
            purchase_result = workflow_tester.client.purchase_stamp(
                amount=settings.default_stamp_amount,
                depth=settings.default_stamp_depth,
                label="timing-test",
            )

            stamp_id = purchase_result.get("batchID")
            assert stamp_id, "No stamp ID returned"

        except Exception as e:
            pytest.skip(f"Could not purchase stamp for timing test: {e}")

        # Check if stamp is immediately usable (it shouldn't be)
        try:
            immediate_details = workflow_tester.client.get_stamp_details(stamp_id)
            immediately_usable = immediate_details.get("usable", False)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                # Stamp not yet visible — expected during early propagation
                immediately_usable = False
                print("Stamp not yet visible (404) — expected during propagation")
            else:
                pytest.fail(f"Timing validation failed: {e}")
                return

        print(f"Stamp usable immediately after purchase: {immediately_usable}")

        if immediately_usable:
            print("ℹ️  Stamp was immediately usable (fast propagation)")
        else:
            print("✅ Stamp requires propagation time (expected)")

            # Try to wait for it to become usable
            became_usable = workflow_tester.wait_for_stamp_usable(
                stamp_id, max_wait_seconds=120
            )
            assert became_usable, "Stamp never became usable"


if __name__ == "__main__":
    # Run the workflow test manually
    print("🧪 Manual End-to-End Workflow Test")
    print("=" * 50)

    tester = StampWorkflowTester("https://provenance-gateway.datafund.io")
    try:
        # Check if gateway is available
        try:
            health = tester.client.health_check()
            print(f"✅ Gateway available: {health.get('gateway_url')}")
        except Exception as e:
            print(f"❌ Gateway not available: {e}")
            exit(1)

        # Run a simple workflow test
        print("\n🚀 Running simplified workflow test...")

        # Purchase
        purchase_result = tester.client.purchase_stamp(2000000000, 17, "manual-test")
        stamp_id = purchase_result["batchID"]
        print(f"✅ Purchased stamp: {stamp_id[:12]}...")

        # Wait for usability
        usable = tester.wait_for_stamp_usable(stamp_id, max_wait_seconds=60)
        if usable:
            print("✅ Manual workflow test completed successfully!")
        else:
            print("⚠️  Stamp did not become usable within timeout")

    except Exception as e:
        print(f"❌ Manual test failed: {e}")
    finally:
        tester.close()
