<!-- =============================================================================
  Enterprise Cloud Transformation Platform (ECTP) - Pull Request Template
  =============================================================================
  Author: Gopi Krishna Vajrala
  Description: Standardized pull request template for the ECTP project.
               This template ensures every PR includes a clear description,
               change type classification, testing evidence, and a comprehensive
               checklist covering code quality, security, and documentation.
               Consistent PR formatting enables faster and more thorough reviews.
  ============================================================================= -->

<!-- ===========================================================================
  SECTION: PR Description
  Purpose: Provide reviewers with a clear understanding of WHAT changed and
           WHY it changed. Link to the related issue for full context.
           Good descriptions reduce review time and prevent misunderstandings.
  =========================================================================== -->
## Description
<!-- Provide a clear and concise description of the changes in this PR. -->
<!-- Explain the motivation and context: WHY was this change necessary? -->
<!-- Link to the related issue using GitHub's closing keywords. -->

**Related Issue:** <!-- Fixes #123, Closes #456, or Relates to #789 -->

### Summary of Changes
<!-- Bullet-point summary of the key changes made in this PR. -->
<!-- Group changes logically (e.g., API changes, database changes, tests). -->
- <!-- Change 1: e.g., Added pagination support to /api/v1/users endpoint -->
- <!-- Change 2: e.g., Created PaginationParams schema for query validation -->
- <!-- Change 3: e.g., Added unit and integration tests for pagination logic -->

<!-- ===========================================================================
  SECTION: Type of Change
  Purpose: Classify the PR so reviewers know what kind of review is needed.
           A bug fix needs different attention than a new feature or refactor.
           This also helps with changelog generation and release notes.
  =========================================================================== -->
## Type of Change
<!-- Select all that apply by replacing [ ] with [x]. -->
<!-- Multiple selections are allowed for PRs that span categories. -->
- [ ] **Bug fix** - Non-breaking change that fixes an issue
- [ ] **New feature** - Non-breaking change that adds functionality
- [ ] **Breaking change** - Fix or feature that would cause existing functionality to change
- [ ] **Refactoring** - Code changes that neither fix a bug nor add a feature
- [ ] **Performance improvement** - Changes that improve performance metrics
- [ ] **Documentation** - Documentation-only changes
- [ ] **CI/CD** - Changes to CI/CD pipeline or deployment configuration
- [ ] **Infrastructure** - Changes to Terraform, Kubernetes, or Docker configs
- [ ] **Dependencies** - Dependency updates or additions
- [ ] **Security** - Security fix or hardening

<!-- ===========================================================================
  SECTION: How Has This Been Tested?
  Purpose: Provide evidence that the changes work correctly and don't break
           existing functionality. Describe the testing strategy so reviewers
           can assess coverage and suggest additional test scenarios.
  =========================================================================== -->
## Testing
<!-- Describe the tests you ran to verify your changes. -->
<!-- Include the test commands, environments, and any manual testing done. -->
<!-- Provide evidence: test output screenshots, coverage reports, etc. -->

### Test Strategy
<!-- Describe your approach to testing these changes. -->
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
- [ ] Manual testing performed
- [ ] End-to-end tests validated

### Test Commands Run
<!-- List the exact commands you used to verify the changes. -->
```bash
# Example commands:
# pytest tests/unit/ -v --cov=src --cov-fail-under=80
# pytest tests/integration/ -v
# curl -X GET http://localhost:8000/api/v1/health
```

### Test Results
<!-- Paste or describe the test results here. -->
<!-- Include coverage percentage if applicable. -->


<!-- ===========================================================================
  SECTION: API Changes
  Purpose: If this PR modifies API endpoints, document the changes clearly
           so reviewers can assess backward compatibility, API design, and
           documentation needs. Skip this section for non-API changes.
  =========================================================================== -->
## API Changes (if applicable)
<!-- Document any API endpoint changes, new endpoints, or modified contracts. -->
<!-- Skip this section entirely if no API changes were made. -->

| Method | Endpoint | Change Type | Description |
|--------|----------|-------------|-------------|
| <!-- GET/POST/PUT/DELETE --> | <!-- /api/v1/resource --> | <!-- New/Modified/Removed --> | <!-- Brief description --> |

<!-- ===========================================================================
  SECTION: Database Changes
  Purpose: Database migrations deserve special attention because they affect
           data integrity and can be difficult to reverse. Document all schema
           changes so DBAs and reviewers can assess impact.
  =========================================================================== -->
## Database Changes (if applicable)
<!-- Document any database schema changes, migrations, or data modifications. -->
<!-- Skip this section entirely if no database changes were made. -->
- [ ] Migration file(s) created
- [ ] Migration is reversible (has a downgrade path)
- [ ] No data loss in migration
- [ ] Indexes added for new query patterns
- [ ] Migration tested against a copy of production data

<!-- ===========================================================================
  SECTION: Screenshots / Evidence
  Purpose: Visual evidence of changes makes reviews faster and more thorough.
           Include screenshots for UI changes, API response examples for
           backend changes, or before/after metrics for performance changes.
  =========================================================================== -->
## Screenshots / Evidence (if applicable)
<!-- Add screenshots, API response examples, or performance metrics. -->
<!-- Use a before/after format for visual changes. -->

| Before | After |
|--------|-------|
| <!-- Screenshot or description --> | <!-- Screenshot or description --> |

<!-- ===========================================================================
  SECTION: Deployment Notes
  Purpose: If this PR requires special deployment steps beyond the standard
           CI/CD pipeline, document them here. This prevents deployment
           failures and ensures smooth rollouts across environments.
  =========================================================================== -->
## Deployment Notes
<!-- Document any special deployment considerations or steps. -->
<!-- Skip this section if the standard deployment pipeline handles everything. -->
- [ ] No special deployment steps required
- [ ] Environment variables added/changed (list below)
- [ ] Database migration required before deployment
- [ ] Feature flag configuration needed
- [ ] Infrastructure changes required (Terraform/K8s)
- [ ] Third-party service configuration needed

<!-- List any new environment variables or configuration changes: -->
<!-- | Variable | Description | Required Environments | -->
<!-- |----------|-------------|----------------------| -->

<!-- ===========================================================================
  SECTION: Pre-Merge Checklist
  Purpose: Comprehensive quality gate that must be satisfied before merging.
           Each item represents a best practice that prevents common issues.
           Reviewers should verify each checked item during their review.
  =========================================================================== -->
## Pre-Merge Checklist
<!-- Complete this checklist before requesting review. -->
<!-- Reviewers: Verify each checked item during your review. -->

### Code Quality
<!-- Code quality standards that every PR must meet. -->
- [ ] My code follows the ECTP style guidelines and coding standards
- [ ] I have performed a self-review of my own code
- [ ] I have commented my code, particularly in hard-to-understand areas
- [ ] My changes generate no new warnings or errors
- [ ] I have added type hints to all new functions and methods
- [ ] Code complexity is within acceptable limits (cyclomatic complexity < 10)

### Testing
<!-- Testing requirements that ensure changes are properly validated. -->
- [ ] I have added tests that prove my fix is effective or my feature works
- [ ] New and existing unit tests pass locally
- [ ] New and existing integration tests pass locally
- [ ] Code coverage meets the minimum threshold (80%)
- [ ] Edge cases and error scenarios are covered by tests

### Security
<!-- Security considerations to prevent vulnerabilities from being introduced. -->
- [ ] No sensitive data (API keys, passwords, tokens) is committed
- [ ] Input validation is implemented for all new user inputs
- [ ] SQL injection protection is maintained (parameterized queries)
- [ ] Authentication and authorization are properly enforced
- [ ] No new security vulnerabilities introduced (Bandit scan passes)

### Documentation
<!-- Documentation updates to keep docs in sync with code changes. -->
- [ ] I have updated the API documentation (OpenAPI/Swagger) if applicable
- [ ] I have updated the README or relevant documentation if applicable
- [ ] Inline code comments are clear and up to date
- [ ] CHANGELOG has been updated (for user-facing changes)

### Backward Compatibility
<!-- Ensure changes don't break existing consumers or integrations. -->
- [ ] This change is backward compatible with existing API consumers
- [ ] Database migrations are backward compatible (can run alongside old code)
- [ ] No breaking changes to public interfaces or contracts
- [ ] If breaking changes exist, they are documented and communicated

<!-- ===========================================================================
  SECTION: Reviewer Notes
  Purpose: Guide reviewers to focus on specific areas of the PR that need
           careful attention. This helps reviewers use their time efficiently
           and ensures critical changes get adequate scrutiny.
  =========================================================================== -->
## Reviewer Notes
<!-- Highlight specific areas where you'd like focused reviewer attention. -->
<!-- Point out any trade-offs, design decisions, or areas of uncertainty. -->
<!-- Mention any risks or concerns you have about these changes. -->


<!-- ===========================================================================
  SECTION: Post-Merge Actions
  Purpose: Document any actions that need to happen after the PR is merged,
           such as monitoring, feature flag activation, or stakeholder
           communication. This ensures nothing falls through the cracks.
  =========================================================================== -->
## Post-Merge Actions (if applicable)
<!-- List any actions needed after merging this PR. -->
- [ ] Monitor deployment logs for errors
- [ ] Verify feature in dev/qa environment after deployment
- [ ] Notify stakeholders of the change
- [ ] Update feature flag configuration (if applicable)
- [ ] Monitor application metrics and error rates

