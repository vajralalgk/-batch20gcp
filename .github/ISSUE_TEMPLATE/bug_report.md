<!-- =============================================================================
  Enterprise Cloud Transformation Platform (ECTP) - Bug Report Template
  =============================================================================
  Author: Gopi Krishna Vajrala
  Description: Standardized bug report template for the ECTP project.
               This template guides contributors to provide all the information
               needed for efficient bug triage, reproduction, and resolution.
               Consistent formatting helps the development team prioritize and
               address issues systematically.
  ============================================================================= -->

<!-- name: Template identifier used by GitHub to distinguish between issue types -->
---
name: Bug Report
<!-- about: Brief description shown in the issue template chooser -->
about: Report a bug or unexpected behavior in the Enterprise Cloud Transformation Platform
<!-- title: Pre-filled title prefix to standardize issue naming conventions -->
title: "[BUG] "
<!-- labels: Automatically applied labels for categorization and filtering -->
labels: bug, triage
<!-- assignees: Default assignees (left empty for manual assignment during triage) -->
assignees: ""
---

<!-- ===========================================================================
  SECTION: Bug Description
  Purpose: Provide a clear, concise summary of the bug. This helps the triage
           team quickly understand the issue without reading the entire report.
  Guidelines: Describe WHAT is wrong, not HOW to fix it. Be specific.
  =========================================================================== -->
## Bug Description
<!-- Provide a clear and concise description of the bug you encountered. -->
<!-- Focus on what the incorrect behavior is and why it's problematic. -->


<!-- ===========================================================================
  SECTION: Steps to Reproduce
  Purpose: Enable developers to consistently reproduce the bug on their machines.
           Without reliable reproduction steps, bugs are extremely difficult to fix.
  Guidelines: Number each step. Include exact values, URLs, and configurations.
  =========================================================================== -->
## Steps to Reproduce
<!-- List the exact steps to reproduce the behavior. Be as specific as possible. -->
<!-- Include API endpoint, request body, headers, or UI interactions. -->
1. <!-- Step 1: e.g., Send a POST request to /api/v1/users with the following body... -->
2. <!-- Step 2: e.g., Observe the response status code and body... -->
3. <!-- Step 3: e.g., Check the application logs for errors... -->
4. <!-- Step 4: e.g., See the error described below... -->

<!-- ===========================================================================
  SECTION: Expected Behavior
  Purpose: Clarify what SHOULD happen so developers understand the gap between
           expected and actual behavior. This defines the acceptance criteria for
           the fix.
  =========================================================================== -->
## Expected Behavior
<!-- Describe what you expected to happen when performing the steps above. -->
<!-- Reference API documentation or specifications if applicable. -->


<!-- ===========================================================================
  SECTION: Actual Behavior
  Purpose: Document what ACTUALLY happens, including error messages, incorrect
           responses, or unexpected side effects. This is compared against the
           expected behavior to understand the bug.
  =========================================================================== -->
## Actual Behavior
<!-- Describe what actually happened instead of the expected behavior. -->
<!-- Include exact error messages, HTTP status codes, and response bodies. -->


<!-- ===========================================================================
  SECTION: Environment Information
  Purpose: Bugs can be environment-specific. This information helps developers
           reproduce the issue in the correct context and identify environment-
           related root causes.
  =========================================================================== -->
## Environment
<!-- Fill in the details about your environment where the bug was observed. -->
<!-- This information is critical for reproducing the issue accurately. -->
- **ECTP Version/Tag:** <!-- e.g., v2.3.1, commit SHA abc1234, or 'latest' -->
- **Environment:** <!-- e.g., dev, qa, uat, prod, local -->
- **OS:** <!-- e.g., Ubuntu 22.04, macOS 14.2, Windows 11 -->
- **Python Version:** <!-- e.g., 3.11.7 -->
- **Docker Version:** <!-- e.g., 24.0.7 (if applicable) -->
- **Browser:** <!-- e.g., Chrome 120, Firefox 121 (if applicable for UI bugs) -->

<!-- ===========================================================================
  SECTION: Logs and Screenshots
  Purpose: Provide concrete evidence of the bug. Logs and screenshots make it
           faster for developers to identify the root cause without needing to
           reproduce the issue first.
  Guidelines: Redact sensitive information (passwords, tokens, PII).
  =========================================================================== -->
## Logs / Screenshots
<!-- Paste relevant log output, stack traces, or screenshots below. -->
<!-- IMPORTANT: Redact any sensitive information such as API keys, passwords, -->
<!-- personal data, or internal URLs before pasting. -->
<!-- Use code blocks (```) for log output to preserve formatting. -->

```
<!-- Paste logs or error output here -->
```

<!-- ===========================================================================
  SECTION: Severity Assessment
  Purpose: Help the triage team prioritize this bug relative to others.
           Severity is based on impact to users and business operations.
  Options:
    Critical: System is down or data is being corrupted
    High: Major feature is broken with no workaround
    Medium: Feature is broken but has a reasonable workaround
    Low: Minor issue with minimal impact on functionality
  =========================================================================== -->
## Severity
<!-- Select the severity level by replacing [ ] with [x] for the appropriate level. -->
- [ ] **Critical** - System outage, data loss, or security vulnerability
- [ ] **High** - Major feature broken, no workaround available
- [ ] **Medium** - Feature impaired, workaround exists
- [ ] **Low** - Minor cosmetic or non-functional issue

<!-- ===========================================================================
  SECTION: Additional Context
  Purpose: Capture any other information that might be relevant, such as:
           - Whether the bug is intermittent or consistent
           - Related issues or PRs
           - Potential root cause theories
           - Business impact or affected customers
  =========================================================================== -->
## Additional Context
<!-- Add any other context about the problem here. -->
<!-- Include related issue numbers, frequency of occurrence, or potential root cause. -->


<!-- ===========================================================================
  SECTION: Possible Solution (Optional)
  Purpose: If the reporter has ideas about the fix, this saves developer time
           during investigation. This section is entirely optional.
  =========================================================================== -->
## Possible Solution (Optional)
<!-- If you have suggestions on how to fix the bug, describe them here. -->
<!-- This is optional but can help speed up the resolution process. -->

