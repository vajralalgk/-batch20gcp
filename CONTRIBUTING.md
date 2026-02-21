# Contributing to Netflix Real-Time LLM Personalization & Inference Platform

**Author:** Gopi Krishna Vajrala
**Version:** 1.0.0

Thank you for your interest in contributing to the Netflix LLM Platform. This document provides guidelines and procedures for contributing to the project.

---

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Getting Started](#getting-started)
3. [Development Environment](#development-environment)
4. [Branching Strategy](#branching-strategy)
5. [Coding Standards](#coding-standards)
6. [Testing Requirements](#testing-requirements)
7. [Pull Request Process](#pull-request-process)
8. [GPU-Specific Guidelines](#gpu-specific-guidelines)
9. [Documentation Standards](#documentation-standards)
10. [Release Process](#release-process)

---

## Code of Conduct

All contributors are expected to adhere to professional conduct standards. Be respectful, constructive, and collaborative. Focus feedback on code and ideas, not individuals.

---

## Getting Started

### Prerequisites

- Python 3.11+
- Docker and Docker Compose
- NVIDIA GPU drivers (for local GPU development)
- CUDA Toolkit 12.0+
- AWS CLI v2 (configured with appropriate credentials)
- kubectl and Helm v3.13+
- Terraform v1.6+

### Initial Setup

```bash
# Clone the repository
git clone <repository-url>
cd netflix-llm-platform

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Install pre-commit hooks
pip install pre-commit
pre-commit install

# Verify setup
python -m pytest tests/unit/ -v
ruff check .
```

---

## Development Environment

### Local Development (Without GPU)

For developing API layer, personalization engine, and non-GPU components:

```bash
# Start local services
docker compose up -d redis

# Run the FastAPI application
uvicorn src.main:app --reload --host 0.0.0.0 --port 8080

# Run tests
python -m pytest tests/unit/ -v --cov=src
```

### Local Development (With GPU)

For developing inference pipeline and GPU-related components:

```bash
# Pull Triton image
docker pull nvcr.io/nvidia/tritonserver:24.01-trtllm-python-py3

# Start Triton with local model repository
docker compose -f docker-compose.gpu.yml up -d

# Verify GPU access
nvidia-smi
python -c "import torch; print(torch.cuda.is_available())"
```

### Environment Variables

```bash
# Required for local development
export ENVIRONMENT=development
export LOG_LEVEL=DEBUG
export REDIS_URL=redis://localhost:6379
export TRITON_GRPC_URL=localhost:8001
export MODEL_REPOSITORY=/path/to/models
```

---

## Branching Strategy

### Branch Naming Convention

```
feature/<ticket-id>-<short-description>    # New features
bugfix/<ticket-id>-<short-description>     # Bug fixes
hotfix/<ticket-id>-<short-description>     # Production hotfixes
docs/<short-description>                    # Documentation changes
perf/<short-description>                    # Performance improvements
refactor/<short-description>                # Code refactoring
```

### Branch Workflow

```
main (protected)
  │
  ├── feature/LLM-123-kv-cache-optimization
  │     │
  │     └── PR -> main (requires 2 approvals + CI pass)
  │
  ├── bugfix/LLM-456-oom-handling
  │     │
  │     └── PR -> main (requires 1 approval + CI pass)
  │
  └── hotfix/LLM-789-triton-crash
        │
        └── PR -> main (requires 1 approval, expedited review)
```

### Rules

- The `main` branch is protected: no direct commits
- All changes require a pull request with passing CI
- Feature branches must be up to date with `main` before merging
- Squash merge is the default merge strategy
- Delete branches after merging

---

## Coding Standards

### Python Style

- Follow PEP 8 with enforcement via ruff
- Use type hints for all function signatures
- Maximum line length: 100 characters
- Use `async/await` for all I/O-bound operations
- Use Pydantic models for request/response schemas

### Ruff Configuration

The project uses ruff for linting and formatting. Configuration is in `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py311"
line-length = 100
select = ["E", "W", "F", "I", "N", "UP", "S", "B", "A", "C4", "SIM", "TCH"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
```

### Code Organization

```
src/
├── api/              # FastAPI routes and middleware
├── inference/        # Triton client and inference logic
├── personalization/  # User context and prompt assembly
├── cache/            # Caching layer (Redis, in-memory)
├── models/           # Pydantic models and schemas
├── config/           # Configuration management
├── observability/    # Metrics, tracing, logging
└── utils/            # Shared utilities
```

### Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Files | snake_case | `kv_cache_manager.py` |
| Classes | PascalCase | `InferenceEngine` |
| Functions | snake_case | `get_user_profile` |
| Constants | UPPER_SNAKE | `MAX_BATCH_SIZE` |
| Type variables | PascalCase | `InferenceResult` |

---

## Testing Requirements

### Test Structure

```
tests/
├── unit/                  # Fast, isolated tests (no external deps)
│   ├── test_api/
│   ├── test_inference/
│   ├── test_personalization/
│   └── test_cache/
├── integration/           # Tests with external services (Redis, mock Triton)
│   ├── test_api_integration/
│   └── test_cache_integration/
├── gpu/                   # GPU-specific tests (require NVIDIA GPU)
│   ├── test_triton_client/
│   └── test_kv_cache/
└── conftest.py            # Shared fixtures
```

### Coverage Requirements

- **Unit tests:** Minimum 80% code coverage
- **Integration tests:** Cover all API endpoints and service interactions
- **GPU tests:** Cover Triton client, dynamic batching, KV cache operations

### Writing Tests

```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_inference_endpoint_returns_200():
    """Test that the inference endpoint accepts valid requests."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post(
            "/v1/inference",
            json={
                "prompt": "Test prompt",
                "max_tokens": 10,
                "user_id": "test-user",
            },
        )
    assert response.status_code == 200
    assert "tokens" in response.json()
```

### Running Tests

```bash
# Unit tests with coverage
python -m pytest tests/unit/ -v --cov=src --cov-fail-under=80

# Integration tests
python -m pytest tests/integration/ -v

# GPU tests (requires NVIDIA GPU)
python -m pytest tests/gpu/ -v --gpu

# All tests
python -m pytest tests/ -v
```

---

## Pull Request Process

### Before Creating a PR

1. Ensure all tests pass locally: `python -m pytest tests/unit/ -v`
2. Run linting: `ruff check . && ruff format --check .`
3. Run security scan: `bandit -r src/ -ll`
4. Update documentation if behavior changes
5. Add/update tests for new functionality

### PR Template

```markdown
## Summary
Brief description of changes.

## Motivation
Why is this change needed?

## Changes
- Change 1
- Change 2

## Testing
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
- [ ] Manual testing performed (describe)

## Performance Impact
- Inference latency: No change / +Xms / -Xms
- GPU utilization: No change / +X% / -X%
- Memory usage: No change / +X MB / -X MB

## Rollback Plan
How to revert if issues are detected in production.

## Checklist
- [ ] Code follows project style guidelines
- [ ] Self-review completed
- [ ] Documentation updated
- [ ] No secrets or credentials in code
- [ ] No breaking API changes (or migration documented)
```

### Review Process

| Change Type | Required Approvals | Reviewers |
|------------|-------------------|-----------|
| Feature | 2 | Platform team + domain expert |
| Bug fix | 1 | Platform team member |
| Hotfix | 1 | Any senior engineer (expedited) |
| Documentation | 1 | Any team member |
| Infrastructure (Terraform) | 2 | Platform team + SRE |
| GPU/Model changes | 2 | ML infrastructure + platform team |

### Merge Requirements

- All CI checks pass (lint, test, security, docker build)
- Required approvals obtained
- No merge conflicts with `main`
- Branch is up to date with `main`
- PR description is complete

---

## GPU-Specific Guidelines

### GPU Resource Management

- Always specify GPU resource requests and limits in Kubernetes manifests
- Never allocate partial GPUs (always request whole GPU count)
- Test with representative batch sizes, not just batch-1
- Profile GPU memory usage before and after changes

### Triton Configuration Changes

- All `config.pbtxt` changes must be tested with a local Triton instance
- Dynamic batching parameter changes require benchmark validation
- Model repository structure changes require deployment guide updates

### Performance Benchmarks

For any change that affects the inference hot path:

1. Run the GPU benchmark pipeline before and after the change
2. Include benchmark results in the PR description
3. Acceptable regression thresholds:
   - Latency: < 5% P99 regression
   - Throughput: < 3% reduction
   - GPU utilization: < 5% reduction

---

## Documentation Standards

### When to Update Documentation

- New features: Update architecture docs and relevant ADRs
- Configuration changes: Update deployment guide and runbooks
- API changes: Ensure OpenAPI docs are auto-generated correctly
- Infrastructure changes: Update Terraform module documentation

### Documentation Files

| Document | Location | Update Trigger |
|----------|----------|---------------|
| Architecture | `docs/architecture/` | Major feature additions |
| ADRs | `docs/adr/` | Architectural decisions |
| Runbooks | `docs/runbooks/` | Operational procedure changes |
| Capacity Planning | `docs/capacity/` | Scaling or cost model changes |
| Patterns | `architecture/patterns/` | New pattern adoption |
| Changelog | `CHANGELOG.md` | Every release |

---

## Release Process

### Version Numbering

- **Major (X.0.0):** Breaking API changes, major architecture shifts
- **Minor (1.X.0):** New features, backward-compatible changes
- **Patch (1.0.X):** Bug fixes, performance improvements, documentation

### Release Checklist

1. Update `CHANGELOG.md` with release notes
2. Create release branch: `release/v1.x.x`
3. Run full test suite including GPU benchmarks
4. Create GitHub release with tag `v1.x.x`
5. CD pipeline deploys to production via canary
6. Validate deployment in all regions
7. Announce release in `#llm-platform` Slack channel

---

## Questions and Support

- **Slack:** #llm-platform-dev
- **Email:** llm-platform-team@netflix.com
- **On-call:** #llm-platform-oncall (PagerDuty)
