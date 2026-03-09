# Testing Guide for Swarm Provenance MCP

This document describes the comprehensive testing strategy designed to catch breaking changes early and ensure system reliability.

## Test Categories

### 1. **Smoke Tests** (`test_integration_smoke.py`)
Quick tests to verify basic functionality and catch obvious breakages:
- Gateway API endpoint availability
- MCP framework compatibility
- Basic data round-trip integrity
- Configuration stability

### 2. **Schema Compliance Tests** (`test_schema_compliance.py`)
Ensure MCP tool schemas remain valid and compatible:
- JSON schema validation
- Tool parameter consistency
- Gateway client method signatures
- Protocol compliance

### 3. **Performance Tests** (`test_performance_regression.py`)
Monitor system performance and resource usage:
- Response time baselines
- Memory usage tracking
- Concurrency handling
- Resource leak detection

### 4. **Security Tests** (`test_security_safety.py`)
Prevent security vulnerabilities and ensure safe data handling:
- Input validation and sanitization
- Error message security
- Configuration safety
- Dependency vulnerability tracking

## Running Tests

### Local Testing

#### Quick Smoke Test (< 30 seconds)
```bash
python run_regression_tests.py --quick
```

#### Security-Focused Testing
```bash
python run_regression_tests.py --security
```

#### Performance Testing
```bash
python run_regression_tests.py --performance
```

#### Full Test Suite
```bash
python run_regression_tests.py --full
```

### Individual Test Categories

```bash
# Schema compliance
pytest tests/test_schema_compliance.py -v

# Performance (fast tests only)
pytest tests/test_performance_regression.py -m "not slow" -v

# Security tests
pytest tests/test_security_safety.py -v

# Integration smoke tests
pytest tests/test_integration_smoke.py -v

# Docker tests (requires Docker)
pytest tests/test_docker.py -v
```

### CI/CD Integration

Tests run automatically on:
- Every push to `main` or `develop`
- Every pull request
- Daily at 2 AM UTC (scheduled)
- Manual trigger via GitHub Actions

## Test Markers

Use pytest markers to run specific test categories:

```bash
# Fast tests only
pytest -m "not slow"

# Integration tests only
pytest -m integration

# Security tests only
pytest -m security

# Performance tests only
pytest -m performance
```

## Expected Baselines

### Performance Baselines
- Server creation: < 1 second
- Tool handler response: < 100ms (without network)
- Gateway client initialization: < 100ms
- Memory increase: < 50MB for basic objects

### Security Requirements
- No secrets in logs
- Input validation for all user data
- Size limits enforced (4KB for uploads)
- Safe error handling without information leakage

### Reliability Requirements
- All tool schemas must be valid JSON Schema
- Required parameters properly declared
- Error responses follow MCP protocol
- No resource leaks (memory, file descriptors, connections)

### 5. **Docker Tests** (`test_docker.py`)
Validate Docker containerization is correct:
- Build succeeds with and without VERSION arg
- Container runs as non-root `mcp` user
- PYTHONUNBUFFERED is set for stdio reliability
- Environment variables are passed through
- No ports exposed (stdio server)
- .dockerignore excludes tests, .git, .env
- Package source is present in image
- OCI metadata labels are set correctly

> **Note**: Docker tests require Docker to be installed and running. They are automatically skipped if Docker is unavailable.

## Adding New Tests

### When to Add Regression Tests

1. **After fixing a bug** - Add a test that would have caught the bug
2. **When adding new features** - Add tests for the new functionality
3. **When dependencies change** - Add compatibility tests
4. **When performance requirements change** - Update baseline expectations

### Test File Organization

- `test_integration_smoke.py` - Basic functionality verification
- `test_schema_compliance.py` - API and schema validation
- `test_performance_regression.py` - Performance monitoring
- `test_security_safety.py` - Security and safety validation
- `test_docker.py` - Docker build, runtime, and context validation

### Writing New Tests

Follow these patterns:

```python
class TestNewFeature:
    """Test category description."""

    def test_specific_behavior(self):
        """Test that specific behavior works correctly."""
        # Arrange
        setup_code()

        # Act
        result = perform_action()

        # Assert
        assert result == expected, "Clear failure message"
```

### Performance Test Guidelines

```python
def test_performance_baseline(self):
    """Test performance meets baseline."""
    start_time = time.time()

    perform_operation()

    duration = time.time() - start_time
    assert duration < BASELINE_TIME, f"Operation too slow: {duration:.3f}s"
```

### Security Test Guidelines

```python
def test_input_validation(self):
    """Test input validation prevents security issues."""
    malicious_inputs = [
        "'; DROP TABLE users; --",
        "<script>alert('xss')</script>",
        "../../../etc/passwd"
    ]

    for malicious_input in malicious_inputs:
        # Should handle safely, not crash or execute
        with pytest.raises(ValueError):  # or handle gracefully
            vulnerable_function(malicious_input)
```

## Continuous Monitoring

### Automated Alerts

Tests are configured to alert on:
- Performance degradation > 50%
- Security test failures
- Schema compliance issues
- Resource leak detection

### Manual Review Triggers

Review tests when:
- Test success rate drops below 95%
- New vulnerabilities discovered in dependencies
- Major version updates of dependencies
- Performance characteristics change significantly

## Troubleshooting

### Common Issues

#### Test Environment Setup
```bash
# Install test dependencies
pip install -e ".[test]"

# Or install all dev dependencies
pip install -e ".[dev]"
```

#### Performance Test Failures
- Check system load during test execution
- Verify no other resource-intensive processes running
- Consider adjusting baselines for slower hardware

#### Integration Test Failures
- Verify gateway service is running (for integration tests)
- Check network connectivity
- Validate environment variables are set correctly

#### Security Test Warnings
- Review any new dependencies for known vulnerabilities
- Update security baselines if legitimate changes
- Investigate any unexpected security test failures immediately

### Getting Help

- Check test output for specific failure details
- Review GitHub Actions logs for CI failures
- Consult the main README.md for general setup issues
- File issues on GitHub for persistent test failures

## Maintenance

### Regular Tasks

- **Weekly**: Review test success rates and performance trends
- **Monthly**: Update security dependency baselines
- **Quarterly**: Review and update performance baselines
- **Per release**: Verify all tests pass and update documentation

### Baseline Updates

Update performance baselines when:
1. Hardware specifications change
2. Dependencies are updated significantly
3. Code optimizations are implemented
4. Test consensus shows baselines are too strict/loose

Document baseline changes in commit messages and version notes.