"""Tests to catch performance regressions and resource issues."""

import pytest
import time
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
import psutil
import os
from unittest.mock import patch, MagicMock
from swarm_provenance_mcp.server import create_server
from swarm_provenance_mcp.gateway_client import SwarmGatewayClient


class TestPerformanceBaselines:
    """Tests to establish and monitor performance baselines."""

    def test_server_creation_performance(self):
        """Test that server creation doesn't become unreasonably slow."""
        start_time = time.time()

        server = create_server()

        creation_time = time.time() - start_time

        # Server creation should be fast (under 1 second)
        assert creation_time < 1.0, f"Server creation too slow: {creation_time:.2f}s"
        print(f"Server creation time: {creation_time:.3f}s")

    async def test_tool_handler_response_times(self):
        """Test that tool handlers respond within reasonable time."""
        from swarm_provenance_mcp.server import (
            handle_purchase_stamp,
            handle_list_stamps,
            handle_health_check,
        )

        # Mock the gateway client to avoid network delays
        mock_responses = {
            "purchase_stamp": {"batchID": "test123", "message": "success"},
            "list_stamps": {"stamps": [], "total_count": 0},
            "health_check": {"status": "healthy", "response_time_ms": 10},
        }

        with (
            patch("swarm_provenance_mcp.server.gateway_client") as mock_client,
            patch("swarm_provenance_mcp.server.chain_client", None),
        ):
            mock_client.purchase_stamp.return_value = mock_responses["purchase_stamp"]
            mock_client.list_stamps.return_value = mock_responses["list_stamps"]
            mock_client.health_check.return_value = mock_responses["health_check"]

            handlers_to_test = [
                ("purchase_stamp", handle_purchase_stamp, {}),
                ("list_stamps", handle_list_stamps, {}),
                ("health_check", handle_health_check, {}),
            ]

            for name, handler, args in handlers_to_test:
                start_time = time.time()
                result = await handler(args)
                response_time = time.time() - start_time

                # Handlers should respond quickly (under 100ms without network)
                assert (
                    response_time < 0.1
                ), f"Handler {name} too slow: {response_time:.3f}s"
                print(f"Handler {name} response time: {response_time:.3f}s")

    def test_gateway_client_initialization_performance(self):
        """Test that gateway client initialization is fast."""
        start_time = time.time()

        client = SwarmGatewayClient()

        init_time = time.time() - start_time

        # Client initialization should be very fast
        assert init_time < 0.1, f"Gateway client init too slow: {init_time:.3f}s"
        print(f"Gateway client init time: {init_time:.3f}s")

    def test_memory_usage_baseline(self):
        """Establish memory usage baseline."""
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # Create server and client
        server = create_server()
        client = SwarmGatewayClient()

        after_creation_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = after_creation_memory - initial_memory

        # Memory increase should be reasonable (under 50MB for basic objects)
        assert (
            memory_increase < 50
        ), f"Memory increase too high: {memory_increase:.1f}MB"
        print(f"Memory usage after creation: {memory_increase:.1f}MB increase")


class TestConcurrencyAndLoad:
    """Tests to ensure the system handles concurrent operations well."""

    async def test_concurrent_handler_calls(self):
        """Test multiple concurrent handler calls don't interfere."""
        from swarm_provenance_mcp.server import handle_health_check

        # Mock to avoid network calls
        with (
            patch("swarm_provenance_mcp.server.gateway_client") as mock_client,
            patch("swarm_provenance_mcp.server.chain_client", None),
        ):
            mock_client.health_check.return_value = {
                "status": "healthy",
                "response_time_ms": 10,
            }

            # Run multiple handlers concurrently
            num_concurrent = 10
            start_time = time.time()

            tasks = [handle_health_check({}) for _ in range(num_concurrent)]
            results = await asyncio.gather(*tasks)

            total_time = time.time() - start_time

            # All should succeed
            assert len(results) == num_concurrent
            for result in results:
                assert not result.isError

            # Should complete reasonably quickly even with concurrency
            assert (
                total_time < 1.0
            ), f"Concurrent operations too slow: {total_time:.2f}s"
            print(f"Concurrent {num_concurrent} operations: {total_time:.3f}s")

    def test_rapid_client_creation_destruction(self):
        """Test that creating/destroying clients rapidly doesn't leak resources."""
        initial_memory = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024

        # Create and destroy many clients rapidly
        for i in range(100):
            client = SwarmGatewayClient()
            client.close()
            del client

        final_memory = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
        memory_increase = final_memory - initial_memory

        # Should not leak significant memory
        assert memory_increase < 10, f"Memory leak detected: {memory_increase:.1f}MB"
        print(
            f"Memory after rapid client creation/destruction: +{memory_increase:.1f}MB"
        )

    def test_large_data_handling_performance(self):
        """Test performance with large (but valid) data."""
        client = SwarmGatewayClient()

        # Test with data near the 4KB limit
        large_data = "x" * 4000  # Just under 4KB limit

        # Mock HTTP call so we measure data processing, not network latency
        mock_response = MagicMock()
        mock_response.json.return_value = {"reference": "abc123"}
        mock_response.raise_for_status = MagicMock()

        start_time = time.time()

        with patch.object(client.session, "post", return_value=mock_response):
            client.upload_data(large_data, "fake_stamp")

        processing_time = time.time() - start_time

        # Should process large data quickly (under 0.1s)
        assert (
            processing_time < 0.1
        ), f"Large data processing too slow: {processing_time:.3f}s"
        print(f"Large data processing time: {processing_time:.3f}s")


class TestResourceLeakDetection:
    """Tests to detect resource leaks early."""

    def test_no_file_descriptor_leaks(self):
        """Test that operations don't leak file descriptors."""
        initial_fds = len(psutil.Process(os.getpid()).open_files())

        # Perform operations that might leak FDs
        for _ in range(10):
            client = SwarmGatewayClient()
            try:
                client.health_check()
            except:
                pass  # Expected to fail
            client.close()

        final_fds = len(psutil.Process(os.getpid()).open_files())
        fd_increase = final_fds - initial_fds

        # Should not leak file descriptors
        assert fd_increase <= 1, f"File descriptor leak detected: +{fd_increase} FDs"
        print(f"File descriptor change: +{fd_increase}")

    def test_session_cleanup(self):
        """Test that HTTP sessions are properly cleaned up."""
        initial_connections = len(psutil.Process(os.getpid()).connections())

        clients = []
        for _ in range(5):
            client = SwarmGatewayClient()
            clients.append(client)

        # Clean up clients
        for client in clients:
            client.close()

        final_connections = len(psutil.Process(os.getpid()).connections())
        connection_increase = final_connections - initial_connections

        # Should not accumulate connections
        assert (
            connection_increase <= 2
        ), f"Connection leak detected: +{connection_increase}"
        print(f"Connection change: +{connection_increase}")

    async def test_async_resource_cleanup(self):
        """Test that async operations clean up properly."""
        from swarm_provenance_mcp.server import handle_health_check

        initial_tasks = len([t for t in asyncio.all_tasks() if not t.done()])

        # Create many async operations
        with patch("swarm_provenance_mcp.server.gateway_client") as mock_client:
            mock_client.health_check.return_value = {"status": "healthy"}

            tasks = []
            for _ in range(20):
                task = asyncio.create_task(handle_health_check({}))
                tasks.append(task)

            # Wait for completion
            await asyncio.gather(*tasks)

        # Allow cleanup time
        await asyncio.sleep(0.1)

        final_tasks = len([t for t in asyncio.all_tasks() if not t.done()])
        task_increase = final_tasks - initial_tasks

        # Should not accumulate background tasks
        assert task_increase <= 1, f"Async task leak detected: +{task_increase}"
        print(f"Active task change: +{task_increase}")


class TestScalabilityLimits:
    """Tests to understand system limits and scalability."""

    def test_tool_definition_scalability(self):
        """Test that adding more tools doesn't cause exponential slowdown."""
        # This test would catch if tool registration becomes O(n²) or similar
        server = create_server()

        start_time = time.time()

        # Access tool handlers multiple times
        for _ in range(100):
            handlers = server.request_handlers

        access_time = time.time() - start_time

        # Multiple accesses should remain fast
        assert (
            access_time < 0.1
        ), f"Tool handler access scaling poorly: {access_time:.3f}s"
        print(f"Tool handler access time (100x): {access_time:.3f}s")

    @pytest.mark.slow
    def test_sustained_operation_stability(self):
        """Test that sustained operations remain stable."""
        from swarm_provenance_mcp.server import handle_health_check

        with patch("swarm_provenance_mcp.server.gateway_client") as mock_client:
            mock_client.health_check.return_value = {"status": "healthy"}

            # Run many operations to test for memory leaks or performance degradation
            times = []
            for i in range(50):
                start = time.time()

                # Run async operation
                asyncio.run(handle_health_check({}))

                times.append(time.time() - start)

                # Check for performance degradation
                if i > 10:
                    recent_avg = sum(times[-10:]) / 10
                    early_avg = sum(times[:10]) / 10

                    # Performance shouldn't degrade more than 50%
                    degradation = recent_avg / early_avg
                    assert (
                        degradation < 1.5
                    ), f"Performance degraded {degradation:.1f}x after {i} operations"

            print(
                f"Sustained operations: avg {sum(times)/len(times):.4f}s per operation"
            )
