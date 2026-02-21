# Circuit Breaker Pattern

**Author:** Gopi Krishna Vajrala

## Overview
The Circuit Breaker pattern prevents cascading failures when external services (ServiceNow, Ellucian) are unavailable.

## States
```
CLOSED (normal) → requests pass through
    │
    ▼ (failures exceed threshold)
OPEN (failing) → requests fail immediately
    │
    ▼ (timeout expires)
HALF-OPEN (testing) → limited requests pass through
    │
    ├──▶ CLOSED (if successful)
    └──▶ OPEN (if still failing)
```

## Configuration
| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Failure Threshold | 5 failures | Avoids false positives from transient errors |
| Reset Timeout | 30 seconds | Gives service time to recover |
| Half-Open Requests | 3 | Tests recovery without overwhelming service |

## Used In
- ServiceNow API calls
- Ellucian Ethos API calls
- AWS API calls (beyond boto3 built-in retries)
