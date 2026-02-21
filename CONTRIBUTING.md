# Contributing to Enterprise Cloud Transformation Platform (ECTP)

**Author:** Gopi Krishna Vajrala
**Version:** 1.0.0

---

## Code of Conduct

All contributors are expected to adhere to professional conduct standards. We are committed to providing a welcoming and inclusive experience for everyone.

---

## Development Workflow

### 1. Branch Strategy

We follow a Git Flow branching model:

```
main (production)
  └── dev (integration)
       ├── feature/ECTP-001-cloud-migration
       ├── feature/ECTP-002-servicenow-integration
       ├── bugfix/ECTP-010-fix-auth-token
       └── hotfix/ECTP-020-critical-db-fix
```

### 2. Getting Started

```bash
# Fork and clone the repository
git clone <your-fork-url>
cd ECTP

# Create a feature branch from dev
git checkout dev
git pull origin dev
git checkout -b feature/ECTP-XXX-your-feature

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Make your changes
# ...

# Run tests
pytest tests/ -v --cov=src --cov-report=term-missing

# Run linting
flake8 src/ tests/
black src/ tests/ --check
mypy src/

# Commit your changes
git add .
git commit -m "feat(scope): description of change"

# Push and create PR
git push origin feature/ECTP-XXX-your-feature
```

### 3. Commit Message Convention

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

**Types:**
| Type | Description |
|------|------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation changes |
| `style` | Code style (formatting, semicolons) |
| `refactor` | Code refactoring |
| `perf` | Performance improvement |
| `test` | Adding or updating tests |
| `chore` | Build process, tooling |
| `ci` | CI/CD changes |

**Scopes:**
| Scope | Description |
|-------|------------|
| `core` | Core shared modules |
| `aws` | AWS integration |
| `servicenow` | ServiceNow integration |
| `ellucian` | Ellucian integration |
| `infra` | Infrastructure code |
| `ci` | CI/CD pipelines |
| `docs` | Documentation |
| `api` | API layer |
| `security` | Security related |

### 4. Pull Request Process

1. Ensure all tests pass locally
2. Update documentation if needed
3. Add relevant test cases
4. Request review from at least 2 team members
5. Address all review comments
6. Squash commits before merging

### 5. Code Review Checklist

- [ ] Code follows project style guidelines
- [ ] Tests are included and passing
- [ ] Documentation is updated
- [ ] No security vulnerabilities introduced
- [ ] No hardcoded secrets or credentials
- [ ] Error handling is appropriate
- [ ] Logging follows project standards
- [ ] Performance implications considered

---

## Code Standards

### Python
- Follow PEP 8
- Use type hints for all function signatures
- Maximum line length: 120 characters
- Use `black` for formatting
- Use `mypy` for type checking
- Docstrings for all public functions (Google style)

### Terraform
- Follow HashiCorp style conventions
- All resources must be tagged
- Use modules for reusable components
- Validate with `terraform validate` and `tflint`

### General
- No hardcoded values - use configuration
- No secrets in code - use AWS Secrets Manager
- All external calls must have timeout and retry logic
- All functions must have error handling

---

## Release Process

1. Features merged to `dev`
2. Integration testing on `dev`
3. Release branch created: `release/v1.x.x`
4. QA and UAT testing
5. Merge to `main` with version tag
6. Deploy to production
7. Post-release validation

---

## Questions?

Contact: Gopi Krishna Vajrala (Project Author & Architect)
