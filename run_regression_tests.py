#!/usr/bin/env python3
"""
Local test runner for regression and safety tests.

Usage:
    python run_regression_tests.py [--quick] [--full] [--security] [--performance]
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path


def run_command(cmd, description, continue_on_error=False):
    """Run a command and report results."""
    print(f"\n{'='*60}")
    print(f"Running: {description}")
    print(f"Command: {' '.join(cmd)}")
    print(f"{'='*60}")

    start_time = time.time()

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        duration = time.time() - start_time

        print(f"✅ {description} PASSED ({duration:.1f}s)")
        if result.stdout:
            print(f"Output:\n{result.stdout}")

        return True

    except subprocess.CalledProcessError as e:
        duration = time.time() - start_time

        if continue_on_error:
            print(f"⚠️  {description} FAILED ({duration:.1f}s) - continuing...")
        else:
            print(f"❌ {description} FAILED ({duration:.1f}s)")

        if e.stdout:
            print(f"Output:\n{e.stdout}")
        if e.stderr:
            print(f"Error:\n{e.stderr}")

        return not continue_on_error


def main():
    parser = argparse.ArgumentParser(description="Run regression and safety tests")
    parser.add_argument("--quick", action="store_true",
                       help="Run only quick smoke tests")
    parser.add_argument("--full", action="store_true",
                       help="Run all tests including slow ones")
    parser.add_argument("--security", action="store_true",
                       help="Run only security tests")
    parser.add_argument("--performance", action="store_true",
                       help="Run only performance tests")
    parser.add_argument("--integration", action="store_true",
                       help="Run integration tests (requires gateway)")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Verbose output")

    args = parser.parse_args()

    # Change to project directory
    project_dir = Path(__file__).parent
    subprocess.run(["pip", "install", "-e", "."], cwd=project_dir, check=True)

    base_cmd = ["python", "-m", "pytest"]
    if args.verbose:
        base_cmd.extend(["-v", "--tb=short"])

    success_count = 0
    total_tests = 0

    print("🧪 Starting Regression Test Suite")
    print(f"Project directory: {project_dir}")

    # Quick smoke tests
    if args.quick or not any([args.full, args.security, args.performance, args.integration]):
        total_tests += 1
        if run_command(
            base_cmd + ["tests/test_schema_compliance.py::TestMCPToolSchemaCompliance::test_all_tools_have_valid_json_schemas"],
            "Quick Schema Validation",
            continue_on_error=True
        ):
            success_count += 1

        total_tests += 1
        if run_command(
            base_cmd + ["tests/test_security_safety.py::TestInputValidationSecurity::test_upload_data_injection_protection"],
            "Quick Security Check",
            continue_on_error=True
        ):
            success_count += 1

    # Security tests
    if args.security or args.full:
        total_tests += 1
        if run_command(
            base_cmd + ["tests/test_security_safety.py"],
            "Security and Safety Tests",
            continue_on_error=True
        ):
            success_count += 1

    # Performance tests
    if args.performance or args.full:
        total_tests += 1
        if run_command(
            base_cmd + [
                "tests/test_performance_regression.py",
                "-m",
                "not slow",
                "--run-performance",
            ],
            "Performance Baseline Tests",
            continue_on_error=True
        ):
            success_count += 1

        if args.full:
            total_tests += 1
            if run_command(
                base_cmd + [
                    "tests/test_performance_regression.py",
                    "-m",
                    "slow",
                    "--run-performance",
                ],
                "Sustained Load Tests",
                continue_on_error=True
            ):
                success_count += 1

    # Schema compliance
    if args.full or not args.quick:
        total_tests += 1
        if run_command(
            base_cmd + ["tests/test_schema_compliance.py"],
            "Schema Compliance Tests",
            continue_on_error=True
        ):
            success_count += 1

    # Integration tests
    if args.integration or args.full:
        total_tests += 1
        if run_command(
            base_cmd + ["tests/test_integration_smoke.py"],
            "Integration Smoke Tests",
            continue_on_error=True
        ):
            success_count += 1

    # Standard unit tests
    if args.full:
        total_tests += 1
        if run_command(
            base_cmd + ["tests/test_tool_execution.py", "tests/test_tool_definitions.py"],
            "Core Unit Tests",
            continue_on_error=True
        ):
            success_count += 1

    # Summary
    print(f"\n{'='*60}")
    print(f"TEST SUMMARY")
    print(f"{'='*60}")
    print(f"Passed: {success_count}/{total_tests}")

    if success_count == total_tests:
        print("🎉 ALL TESTS PASSED!")
        return 0
    else:
        print(f"⚠️  {total_tests - success_count} test suite(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())