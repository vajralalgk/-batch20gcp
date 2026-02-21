# Enterprise Cloud Transformation Platform (ECTP)
# Administrator Operations Runbook

**Document ID:** ECTP-RUNBOOK-002
**Author:** Gopi Krishna Vajrala
**Version:** 2.0.0
**Date:** 2026-02-16
**Classification:** Internal - Confidential
**Status:** Approved

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 2.0.0 | 2026-02-16 | Gopi Krishna Vajrala | Comprehensive rewrite with full operational procedures |
| 1.0.0 | 2026-02-16 | Gopi Krishna Vajrala | Initial release |

---

## Quick Reference Card

| Item | Detail |
|------|--------|
| **Health Checks** | `https://<env>.ectp.example.edu/health` |
| **Dashboards** | AWS Console > CloudWatch > ECTP Dashboards |
| **Alerts** | PagerDuty > ECTP Service |
| **Logs** | CloudWatch Log Group: `/ectp/<env>/api` |
| **Deployments** | GitHub Actions > ECTP Workflows |
| **Incidents** | ServiceNow > ECTP Queue |
| **Escalation** | Gopi Krishna Vajrala (Platform Architect) |
| **Maintenance** | Saturday 02:00-06:00 ET (Production) |

---

## 1. Daily Operations Checklist

### 1.1 Morning Health Check (08:00 AM ET)

```bash
# ------------------------------------------------------------------
# ECTP Daily Morning Health Check
# Author: Gopi Krishna Vajrala
# ------------------------------------------------------------------

ENVIRONMENT="production"
BASE_URL="https://api.ectp.example.edu"
CLUSTER="ectp-prod-cluster"

echo "ECTP Daily Health Check - $(date)"

# 1. API Health
echo -n "[1/10] API Health: "
curl -s ${BASE_URL}/health | jq -r '.status'

# 2. API Readiness
echo -n "[2/10] Readiness: "
curl -s ${BASE_URL}/health/ready | jq -r '.status'

# 3. ECS Service Status
echo "[3/10] ECS Tasks:"
aws ecs describe-services --cluster ${CLUSTER} \
  --services ectp-api-production \
  --query 'services[0].{desired:desiredCount,running:runningCount}' --output table

# 4. RDS Status
echo -n "[4/10] RDS: "
aws rds describe-db-instances --db-instance-identifier ectp-prod-db \
  --query 'DBInstances[0].DBInstanceStatus' --output text

# 5. ElastiCache Status
echo -n "[5/10] Redis: "
aws elasticache describe-cache-clusters --cache-cluster-id ectp-prod-cache \
  --query 'CacheClusters[0].CacheClusterStatus' --output text

# 6. CloudWatch Alarms in ALARM state
echo "[6/10] Active Alarms:"
aws cloudwatch describe-alarms --state-value ALARM \
  --query 'MetricAlarms[?starts_with(AlarmName,`ectp`)].AlarmName' --output table

# 7. Error Count (last 24h)
echo -n "[7/10] Errors (24h): "
aws logs filter-log-events --log-group-name "/ectp/production/api" \
  --start-time $(date -u -d '24 hours ago' +%s)000 \
  --filter-pattern "ERROR" --query 'events | length(@)' --output text

# 8. SSL Certificate
echo "[8/10] SSL Certificate:"
aws acm describe-certificate --certificate-arn $CERT_ARN \
  --query 'Certificate.{Status:Status,Expiry:NotAfter}' --output table

# 9. Cost Today
echo "[9/10] Today's Cost:"
aws ce get-cost-and-usage \
  --time-period Start=$(date +%Y-%m-%d),End=$(date -d tomorrow +%Y-%m-%d) \
  --granularity DAILY --metrics BlendedCost \
  --query 'ResultsByTime[0].Total.BlendedCost' --output table

# 10. GuardDuty (High/Critical)
echo -n "[10/10] GuardDuty High Findings: "
aws guardduty list-findings --detector-id $DETECTOR_ID \
  --finding-criteria '{"Criterion":{"severity":{"Gte":7}}}' \
  --query 'FindingIds | length(@)' --output text
```

### 1.2 Daily Task Schedule

| Time | Task | Responsible |
|------|------|-------------|
| 08:00 | Run morning health check script | On-call SRE |
| 08:15 | Review CloudWatch dashboards for anomalies | On-call SRE |
| 08:30 | Review overnight deployment results | DevOps Engineer |
| 09:00 | Check ServiceNow queue for new incidents | Cloud Operations |
| 10:00 | Review cost anomaly alerts | FinOps Analyst |
| 12:00 | Midday health verification | On-call SRE |
| 15:00 | Review security scan results | Security Engineer |
| 17:00 | End-of-day status report | On-call SRE |

### 1.3 Weekly and Monthly Tasks

**Weekly:**
- Monday: Capacity metrics review and scaling plan
- Tuesday: Security vulnerability review
- Wednesday: Cost optimization review
- Thursday: Backup verification and restore test
- Friday: Weekly operations report and retrospective

**Monthly:**
- Access review and IAM recertification (by 15th)
- DR failover test on non-production (by 20th)
- Capacity planning review (by 25th)
- Cost governance report to leadership (last business day)
- SSL/TLS certificate renewal review (by 5th)
- Patch management review (by 10th)

---

## 2. Incident Response Procedures

### 2.1 Severity Definitions

| Severity | Definition | Response | Escalation | Communication |
|----------|-----------|----------|------------|---------------|
| **P1 Critical** | Full outage, data breach, compliance violation | 15 min | 30 min to IT Director | Bridge call |
| **P2 High** | Major feature unavailable, >50% performance degradation | 30 min | 2 hours | Slack + email |
| **P3 Medium** | Minor feature impacted, workaround available | 4 hours | 24 hours | Slack |
| **P4 Low** | Cosmetic issue, minor inconvenience | Next biz day | 5 biz days | Ticket update |

### 2.2 P1 Critical Incident Procedure

```
TIMELINE:
  0-5 min   --> Acknowledge alert, join bridge call, assign IC
  5-15 min  --> Assess impact, check recent deployments
  15-30 min --> Investigate logs/metrics, identify root cause
  30-45 min --> Apply fix or rollback
  45-60 min --> Verify recovery, stakeholder notification
  +48 hours --> Blameless post-mortem
```

| Step | Action | Target |
|------|--------|--------|
| 1 | Acknowledge alert in PagerDuty | 0-5 min |
| 2 | Join incident bridge call | 5 min |
| 3 | Assign Incident Commander (IC) | 5 min |
| 4 | Assess scope via health checks | 5-10 min |
| 5 | Check for recent deployments | 10-15 min |
| 6 | If deployment suspected: IMMEDIATE ROLLBACK | 15 min |
| 7 | Investigate logs and CloudWatch metrics | 15-30 min |
| 8 | Apply fix or workaround | 30-45 min |
| 9 | Verify recovery via all health endpoints | 45-50 min |
| 10 | Send resolution notification | 50-60 min |
| 11 | Schedule blameless post-mortem | Within 48h |

### 2.3 Communication Templates

**P1 Initial:**
```
Subject: [P1 INCIDENT] ECTP - <Brief Description>
Impact: <User impact description>
Status: Investigating
IC: <Name>
Bridge: <Call details>
Next Update: 15 minutes
```

**P1 Resolution:**
```
Subject: [P1 RESOLVED] ECTP - <Brief Description>
Resolution: <What was done>
Root Cause: <Preliminary cause>
Duration: <Start> to <End>
Post-Mortem: Scheduled <Date>
```

---

## 3. Scaling Operations

### 3.1 Horizontal Scaling (ECS)

```bash
# Scale to desired count
aws ecs update-service --cluster ectp-prod-cluster \
  --service ectp-api-production --desired-count 6

# Wait for stability
aws ecs wait services-stable --cluster ectp-prod-cluster \
  --services ectp-api-production

# Verify
aws ecs describe-services --cluster ectp-prod-cluster \
  --services ectp-api-production \
  --query 'services[0].{desired:desiredCount,running:runningCount}' --output table
```

### 3.2 Vertical Scaling (RDS)

```bash
# Change instance class (may cause brief Multi-AZ failover)
aws rds modify-db-instance \
  --db-instance-identifier ectp-prod-db \
  --db-instance-class db.r6g.2xlarge \
  --apply-immediately
```

### 3.3 Auto-Scaling Thresholds

| Resource | Scale Up | Scale Down | Min | Max |
|----------|----------|------------|-----|-----|
| ECS Tasks (CPU) | >70% for 2 min | <30% for 10 min | 3 | 12 |
| ECS Tasks (Memory) | >80% for 2 min | <40% for 10 min | 3 | 12 |

### 3.4 Pre-Event Scaling Checklist

For enrollment, registration, or other high-traffic events:

- [ ] Scale ECS to 2x desired count 24 hours before
- [ ] Verify RDS instance class and connection pool
- [ ] Increase ElastiCache node type if needed
- [ ] Pre-warm ALB with gradual traffic ramp
- [ ] Verify auto-scaling policies are active
- [ ] Notify operations team of expected patterns
- [ ] Prepare rollback plan

---

## 4. Backup and Restore Procedures

### 4.1 Backup Schedule

| Resource | Type | Frequency | Retention |
|----------|------|-----------|-----------|
| RDS PostgreSQL | Automated snapshot | Daily 2:00 AM ET | 35 days (prod) / 7 days (non-prod) |
| RDS PostgreSQL | Pre-deployment snapshot | Before each deploy | 90 days |
| RDS PostgreSQL | Cross-region copy | Daily | 14 days (us-west-2) |
| S3 Buckets | Cross-region replication | Continuous | Indefinite |
| Terraform State | S3 versioning | Every apply | 90 days |

### 4.2 Monthly Backup Verification

```bash
# 1. List recent snapshots
aws rds describe-db-snapshots --db-instance-identifier ectp-prod-db \
  --query 'DBSnapshots[-5:].{ID:DBSnapshotIdentifier,Created:SnapshotCreateTime}' --output table

# 2. Restore to temporary instance
SNAPSHOT=$(aws rds describe-db-snapshots --db-instance-identifier ectp-prod-db \
  --query 'DBSnapshots[-1].DBSnapshotIdentifier' --output text)

aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier ectp-backup-test-$(date +%Y%m%d) \
  --db-snapshot-identifier ${SNAPSHOT} --db-instance-class db.t3.medium --no-multi-az

# 3. Wait, verify data, then clean up
aws rds wait db-instance-available --db-instance-identifier ectp-backup-test-$(date +%Y%m%d)
# Run verification queries...
aws rds delete-db-instance --db-instance-identifier ectp-backup-test-$(date +%Y%m%d) --skip-final-snapshot
```

### 4.3 Restore Procedures

**Point-in-Time Recovery:**
```bash
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier ectp-prod-db \
  --target-db-instance-identifier ectp-prod-db-restored \
  --restore-time "2026-02-16T10:30:00Z" --db-instance-class db.r6g.xlarge --multi-az
```

**Snapshot Restore:**
```bash
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier ectp-prod-db-restored \
  --db-snapshot-identifier ectp-pre-deploy-20260216 --db-instance-class db.r6g.xlarge --multi-az

# Update connection string
aws ssm put-parameter --name "/ectp/production/rds-endpoint" \
  --value "$(aws rds describe-db-instances --db-instance-identifier ectp-prod-db-restored \
    --query 'DBInstances[0].Endpoint.Address' --output text)" \
  --type SecureString --overwrite
```

---

## 5. Monitoring Guide

### 5.1 Key Dashboards

| Dashboard | Purpose | Audience |
|-----------|---------|----------|
| ECTP-Overview | High-level platform health | All ops team |
| ECTP-API-Performance | Latency, throughput, errors | Developers + SRE |
| ECTP-Infrastructure | ECS, RDS, ElastiCache metrics | SRE team |
| ECTP-Cost | Real-time cost tracking | FinOps + leadership |
| ECTP-Security | GuardDuty, WAF, access patterns | Security team |

### 5.2 Alerting and Thresholds

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| API p99 Latency | >500ms | >1000ms | Scale out / optimize |
| API 5xx Rate | >1% | >5% | Investigate / rollback |
| ECS CPU | >70% | >85% | Auto-scale |
| RDS CPU | >70% | >85% | Scale / optimize queries |
| RDS Free Storage | <20% | <10% | Increase storage |
| Redis Memory | >75% | >90% | Scale / eviction review |

### 5.3 Alert Routing

```
P1 Critical --> PagerDuty phone call --> On-call SRE
              + Slack #ectp-incidents
              + Email to IT Director

P2 High     --> PagerDuty push notification --> On-call SRE
              + Slack #ectp-ops

P3 Medium   --> Slack #ectp-ops
              + ServiceNow auto-ticket

P4 Low      --> Slack #ectp-monitoring
```

### 5.4 Log Analysis

```bash
# Search errors (last hour)
aws logs filter-log-events --log-group-name "/ectp/production/api" \
  --start-time $(date -u -d '1 hour ago' +%s)000 --filter-pattern "ERROR" --limit 50

# Search by correlation ID
aws logs filter-log-events --log-group-name "/ectp/production/api" \
  --filter-pattern '{ $.correlation_id = "CORRELATION_ID" }'

# Search by user
aws logs filter-log-events --log-group-name "/ectp/production/api" \
  --filter-pattern '{ $.user_id = "USR-123" }' --start-time $(date -u -d '24 hours ago' +%s)000
```

---

## 6. Common Troubleshooting Scenarios

| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| 502 Bad Gateway | All ECS tasks unhealthy | Check stopped task reasons, force new deployment |
| DB connection exhaustion | Long-running queries, leak | Kill long queries, increase pool size |
| Redis cache miss spike | Key eviction, insufficient memory | Increase TTL, scale up node |
| High API latency | Slow queries, cold starts | RDS Performance Insights, scale out |
| ServiceNow 401 | OAuth token expired | Rotate credentials in Secrets Manager |
| Terraform state lock | Concurrent run or crash | `terraform force-unlock LOCK_ID` |
| Cost spike | Runaway auto-scaling | Review scaling policies, set max limits |
| SSL certificate expiring | Auto-renewal failed | Manual renewal via ACM |

---

## 7. Maintenance Windows

| Window | Time (ET) | Frequency | Activities |
|--------|-----------|-----------|------------|
| QA Deploy | Mon-Fri 2:00-4:00 AM | Daily | Auto-deploy from release branch |
| UAT Deploy | Tuesday 6:00-8:00 AM | Weekly | Manual deploy for acceptance |
| Prod Deploy | Saturday 2:00-6:00 AM | Bi-weekly | Approved production releases |
| RDS Maintenance | Sunday 4:00-5:00 AM | As needed | AWS patches |
| Security Patching | 3rd Saturday 2:00-6:00 AM | Monthly | OS and dependency patches |

**Production Maintenance Checklist:**
- [ ] ServiceNow change ticket approved
- [ ] Stakeholder notification sent (48h notice)
- [ ] Rollback plan documented
- [ ] Pre-deployment RDS snapshot created
- [ ] All tests passing on UAT
- [ ] Execute deployment
- [ ] Run post-deployment validation
- [ ] Monitor 30 minutes post-deploy
- [ ] Send completion notification

**Emergency Maintenance:**
- IT Director approval required (verbal, followed by written)
- ServiceNow emergency change ticket within 24 hours
- Post-change review at next Governance Board meeting

---

## 8. Emergency Contacts

### 8.1 Escalation Matrix

| Level | Contact | Role | Method | Response |
|-------|---------|------|--------|----------|
| L1 | On-Call SRE | First responder | PagerDuty | 15 min |
| L2 | Gopi Krishna Vajrala | Platform Architect | Phone/Slack | 30 min |
| L3 | IT Director | Department Head | Phone/Email | 1 hour |
| L4 | CIO | Executive Sponsor | Phone | 2 hours (P1 only) |

### 8.2 Vendor Support

| Vendor | Level | SLA |
|--------|-------|-----|
| AWS | Enterprise Support | 15 min (Critical) |
| ServiceNow | Premium Support | 1 hour (P1) |
| Ellucian | Standard Support | 4 hours (P1) |

### 8.3 On-Call Rotation

| Week | Primary | Secondary |
|------|---------|-----------|
| Odd weeks | SRE Engineer A | SRE Engineer B |
| Even weeks | SRE Engineer B | SRE Engineer A |

**On-Call Expectations:**
- Acknowledge P1/P2 alerts within 15 minutes
- Laptop and VPN access available at all times
- Escalate to L2 if unresolved within 30 minutes
- Document all actions during on-call shifts
- Handoff notes at rotation change

---

**Document Author:** Gopi Krishna Vajrala
**Review Status:** Approved
**Next Review Date:** 2026-08-16
