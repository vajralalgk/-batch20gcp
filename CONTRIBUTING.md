# Contributing to Enterprise GCP Batch Automation Platform

**Author:** Gopi Krishna Vajrala

Thank you for your interest in contributing to this project. This document provides guidelines and standards for contributing to ensure consistency and quality across the codebase.

---

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Getting Started](#getting-started)
3. [Development Workflow](#development-workflow)
4. [Coding Standards](#coding-standards)
5. [Commit Message Convention](#commit-message-convention)
6. [Pull Request Process](#pull-request-process)
7. [Code Review Standards](#code-review-standards)
8. [Testing Requirements](#testing-requirements)
9. [Documentation Standards](#documentation-standards)

---

## Code of Conduct

- Treat all contributors with respect
- Focus on constructive feedback during reviews
- Report security vulnerabilities privately to the project maintainer
- Follow organizational policies for data handling and access management

## Getting Started

1. Fork or clone the repository
2. Create a feature branch from `develop`
3. Set up your local development environment (see README.md)
4. Make your changes following the standards below
5. Submit a pull request

## Development Workflow

```
main (production)
  └── develop (integration)
       ├── feature/TICKET-123-description
       ├── bugfix/TICKET-456-description
       └── release/v1.2.0
```

### Branch Naming Convention

- `feature/<ticket-id>-<short-description>` - New features
- `bugfix/<ticket-id>-<short-description>` - Bug fixes
- `hotfix/<ticket-id>-<short-description>` - Production emergency fixes
- `release/v<major>.<minor>.<patch>` - Release branches

## Coding Standards

### Python

- Follow PEP 8 style guidelines
- Use type hints for all function signatures
- Maximum line length: 120 characters
- Use docstrings for all public classes and functions
- Use f-strings for string formatting
- Prefer composition over inheritance

### Terraform

- Use consistent naming: `<resource>_<purpose>_<environment>`
- Always include `description` for variables
- Use modules for reusable components
- Tag all resources with required tags (project, environment, owner, cost-center)

### General

- No hardcoded secrets or credentials
- Use environment variables or secret managers for sensitive values
- Handle errors explicitly - no silent failures
- Log meaningful messages at appropriate levels

## Commit Message Convention

Follow the Conventional Commits specification:

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Types

| Type | Description |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation changes |
| `style` | Code style changes (formatting, no logic change) |
| `refactor` | Code refactoring |
| `test` | Adding or updating tests |
| `chore` | Build, CI, or tooling changes |
| `perf` | Performance improvements |
| `security` | Security improvements |

### Example

```
feat(scheduler): add retry logic with exponential backoff

Implement configurable retry mechanism for failed batch jobs.
Default retry count is 3 with exponential backoff starting at 1s.

Resolves: BATCH-1234
```

## Pull Request Process

1. Ensure all tests pass: `pytest tests/ -v`
2. Ensure code formatting passes: `black --check src/ tests/`
3. Ensure linting passes: `flake8 src/ tests/`
4. Update documentation if behavior changes
5. Request review from at least 2 team members
6. Squash commits before merging to `develop`

### PR Template

```markdown
## Summary
Brief description of changes.

## Type of Change
- [ ] Feature
- [ ] Bug Fix
- [ ] Documentation
- [ ] Refactoring

## Testing
Describe testing performed.

## Checklist
- [ ] Code follows project standards
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] No secrets or credentials committed
- [ ] Terraform plan reviewed (if applicable)
```

## Code Review Standards

Reviewers should check for:

- Correctness and completeness
- Security vulnerabilities (OWASP Top 10)
- Performance implications
- Test coverage (minimum 80%)
- Documentation accuracy
- Adherence to coding standards
- No hardcoded values or credentials

## Testing Requirements

| Test Type | Coverage Target | When to Run |
|-----------|----------------|-------------|
| Unit | >= 80% | Every commit |
| Integration | >= 70% | Every PR |
| E2E | Critical paths | Pre-release |
| Security | All endpoints | Weekly + Pre-release |

## Documentation Standards

- All public APIs must have docstrings
- Architecture changes require an ADR (Architecture Decision Record)
- Configuration changes must update the deployment guide
- New features require user-guide updates

---

*Maintained by Gopi Krishna Vajrala*
