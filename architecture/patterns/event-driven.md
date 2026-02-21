# Event-Driven Architecture Pattern

**Author:** Gopi Krishna Vajrala

## Overview
ECTP uses event-driven architecture for decoupled, scalable communication between services.

## Event Flow
```
Event Source → SQS/SNS → Event Consumer → Action
    │                         │
    ▼                         ▼
CloudWatch Alarm    → SQS → Incident Service → Create ServiceNow Ticket
Migration Complete  → SNS → CMDB Sync        → Update ServiceNow CMDB
Cost Anomaly        → SNS → Alert Service     → Send Notification
Enrollment Change   → SQS → Scaling Service   → Adjust Infrastructure
```

## Benefits
- **Decoupling:** Services don't need to know about each other
- **Scalability:** Queue-based processing handles traffic spikes
- **Reliability:** SQS provides at-least-once delivery with DLQ
- **Auditability:** All events are logged and traceable

## AWS Services Used
| Service | Role | Why |
|---------|------|-----|
| SQS | Message queue | Reliable point-to-point delivery |
| SNS | Pub/Sub | One-to-many event broadcasting |
| EventBridge | Event bus | Cross-service event routing with filtering |
| Step Functions | Orchestration | Complex multi-step workflows |
