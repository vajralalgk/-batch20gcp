# Netflix LLM Platform - Deployment Guide

**Document ID:** NFLX-LLM-RUNBOOK-001
**Author:** Gopi Krishna Vajrala
**Version:** 1.0.0
**Last Updated:** 2026-02-21
**Audience:** Platform Engineers, SRE Team

---

## Table of Contents

1. [Overview](#1-overview)
2. [Prerequisites](#2-prerequisites)
3. [Multi-Region Deployment Procedure](#3-multi-region-deployment-procedure)
4. [Canary Deployment Steps](#4-canary-deployment-steps)
5. [GPU Node Provisioning](#5-gpu-node-provisioning)
6. [Model Deployment to Triton](#6-model-deployment-to-triton)
7. [Rollback Procedure](#7-rollback-procedure)
8. [Post-Deployment Validation](#8-post-deployment-validation)
9. [Emergency Procedures](#9-emergency-procedures)

---

## 1. Overview

This runbook covers the end-to-end deployment procedure for the Netflix Real-Time LLM Personalization & Inference Platform. Deployments follow a progressive rollout strategy:

1. Deploy to us-east-1 (primary) with canary
2. Validate canary metrics against SLOs
3. Progressively shift traffic (10% -> 25% -> 50% -> 75% -> 100%)
4. Deploy to us-west-2 and eu-west-1 in parallel
5. Run cross-region smoke tests
6. Confirm deployment success

**Estimated deployment time:** 45-60 minutes (full three-region deployment)

---

## 2. Prerequisites

### 2.1 Access Requirements

```bash
# Verify AWS CLI access for all regions
aws sts get-caller-identity --region us-east-1
aws sts get-caller-identity --region us-west-2
aws sts get-caller-identity --region eu-west-1

# Verify kubectl access to all EKS clusters
aws eks update-kubeconfig --name netflix-llm-us-east-1 --region us-east-1
aws eks update-kubeconfig --name netflix-llm-us-west-2 --region us-west-2
aws eks update-kubeconfig --name netflix-llm-eu-west-1 --region eu-west-1

# Verify Helm is installed
helm version  # Requires v3.13.0+

# Verify GPU node health
kubectl get nodes -l nvidia.com/gpu.present=true --context netflix-llm-us-east-1
```

### 2.2 Pre-Deployment Checklist

- [ ] CI pipeline passed (lint, test, security scan, docker build)
- [ ] Docker image available in GHCR with correct tag
- [ ] GPU nodes are healthy in all target regions (check DCGM metrics)
- [ ] ElastiCache clusters are reachable and within memory limits
- [ ] DynamoDB Global Tables are synced (replication lag < 2s)
- [ ] No active incidents in any target region
- [ ] Deployment window approved (check #llm-platform-ops Slack channel)
- [ ] Rollback plan reviewed by on-call engineer

---

## 3. Multi-Region Deployment Procedure

### 3.1 Automated Deployment (Recommended)

The CD pipeline handles multi-region deployment automatically.

```bash
# Trigger deployment via GitHub Actions
gh workflow run cd-deploy.yml \
  --ref main \
  -f target-region=all \
  -f canary-weight=10 \
  -f skip-canary=false \
  -f dry-run=false

# Monitor deployment progress
gh run watch
```

### 3.2 Manual Deployment (If automation is unavailable)

#### Step 1: Deploy to us-east-1

```bash
export REGION=us-east-1
export CLUSTER=netflix-llm-us-east-1
export NAMESPACE=llm-inference
export IMAGE_TAG=<commit-sha-short>

# Switch to us-east-1 cluster
aws eks update-kubeconfig --name $CLUSTER --region $REGION
kubectl config use-context arn:aws:eks:${REGION}:<account-id>:cluster/${CLUSTER}

# Verify cluster health before deployment
kubectl get nodes -l nvidia.com/gpu.present=true
kubectl top nodes

# Deploy canary (10% traffic)
helm upgrade --install netflix-llm-platform-canary deploy/helm/netflix-llm-platform \
  --namespace $NAMESPACE \
  --set image.repository=ghcr.io/<org>/netflix-llm-platform \
  --set image.tag=$IMAGE_TAG \
  --set region=$REGION \
  --set canary.enabled=true \
  --set canary.weight=10 \
  --set gpu.enabled=true \
  --set gpu.type=nvidia-a100 \
  --set gpu.count=8 \
  --set gpu.tensorParallelism=4 \
  --set triton.enabled=true \
  --set triton.dynamicBatching.maxBatchSize=64 \
  --set kvCache.enabled=true \
  --set kvCache.maxSizeGb=32 \
  --timeout 600s \
  --wait \
  --atomic

# Verify canary pods are running
kubectl get pods -n $NAMESPACE -l track=canary -o wide
```

#### Step 2: Validate Canary

See [Section 4: Canary Deployment Steps](#4-canary-deployment-steps).

#### Step 3: Promote to Production in us-east-1

```bash
# Full production deployment
helm upgrade --install netflix-llm-platform deploy/helm/netflix-llm-platform \
  --namespace $NAMESPACE \
  --set image.repository=ghcr.io/<org>/netflix-llm-platform \
  --set image.tag=$IMAGE_TAG \
  --set region=$REGION \
  --set canary.enabled=false \
  --set gpu.enabled=true \
  --set gpu.type=nvidia-a100 \
  --set gpu.count=8 \
  --set gpu.tensorParallelism=4 \
  --set triton.enabled=true \
  --set triton.dynamicBatching.maxBatchSize=64 \
  --set kvCache.enabled=true \
  --set kvCache.maxSizeGb=32 \
  --timeout 600s \
  --wait \
  --atomic

# Clean up canary
helm uninstall netflix-llm-platform-canary -n $NAMESPACE
```

#### Step 4: Deploy to Secondary Regions

```bash
# Deploy to us-west-2
export REGION=us-west-2
export CLUSTER=netflix-llm-us-west-2
aws eks update-kubeconfig --name $CLUSTER --region $REGION

helm upgrade --install netflix-llm-platform deploy/helm/netflix-llm-platform \
  --namespace $NAMESPACE \
  --set image.tag=$IMAGE_TAG \
  --set region=$REGION \
  # ... (same GPU/Triton settings as above)
  --timeout 600s --wait --atomic

# Deploy to eu-west-1
export REGION=eu-west-1
export CLUSTER=netflix-llm-eu-west-1
aws eks update-kubeconfig --name $CLUSTER --region $REGION

helm upgrade --install netflix-llm-platform deploy/helm/netflix-llm-platform \
  --namespace $NAMESPACE \
  --set image.tag=$IMAGE_TAG \
  --set region=$REGION \
  --set dataResidency.enabled=true \
  --set dataResidency.region=eu \
  # ... (same GPU/Triton settings as above)
  --timeout 600s --wait --atomic
```

---

## 4. Canary Deployment Steps

### 4.1 Traffic Shifting Schedule

| Step | Canary Weight | Duration | Validation |
|------|--------------|----------|------------|
| 1 | 10% | 2 minutes | Latency, error rate, GPU utilization |
| 2 | 25% | 2 minutes | Latency, error rate, inference accuracy |
| 3 | 50% | 2 minutes | Latency, error rate, memory pressure |
| 4 | 75% | 2 minutes | Latency, error rate, full load test |
| 5 | 100% | 5 minutes | Full SLO validation |

### 4.2 Canary Validation Criteria

**Automatic Rollback Triggers (any single condition):**
- P99 latency > 120ms (20% above SLO)
- Error rate > 1%
- GPU utilization > 95% for 2+ minutes
- KV cache pressure > 95%
- Any pod in CrashLoopBackOff

**Manual Review Required:**
- P99 latency between 90-120ms
- Error rate between 0.5-1%
- GPU utilization between 85-95%

### 4.3 Monitoring Canary Health

```bash
# Check canary pod status
kubectl get pods -n llm-inference -l track=canary -o wide

# Check canary metrics
CANARY_POD=$(kubectl get pods -n llm-inference -l track=canary -o jsonpath='{.items[0].metadata.name}')

# Health check
kubectl exec -n llm-inference $CANARY_POD -- curl -sf http://localhost:8080/health/ready

# Inference latency
kubectl exec -n llm-inference $CANARY_POD -- curl -sf http://localhost:8080/metrics | \
  grep -E "inference_latency_seconds|http_request_duration"

# GPU metrics
kubectl exec -n llm-inference $CANARY_POD -- curl -sf http://localhost:8080/metrics | \
  grep -E "gpu_utilization|gpu_memory_used|kv_cache"

# Triton model status
kubectl exec -n llm-inference $CANARY_POD -- curl -sf http://localhost:8080/v2/models/stats
```

---

## 5. GPU Node Provisioning

### 5.1 Adding GPU Nodes

```bash
# Scale GPU node group via EKS managed node group
aws eks update-nodegroup-config \
  --cluster-name netflix-llm-us-east-1 \
  --nodegroup-name gpu-p4d-24xlarge \
  --scaling-config minSize=4,maxSize=10,desiredSize=6 \
  --region us-east-1

# Monitor node provisioning
watch -n 10 'kubectl get nodes -l nvidia.com/gpu.present=true -o wide'

# Verify GPU availability on new nodes
kubectl describe node <new-node-name> | grep -A 5 "nvidia.com/gpu"
```

### 5.2 GPU Node Health Verification

```bash
# Check NVIDIA driver and GPU status on all nodes
for NODE in $(kubectl get nodes -l nvidia.com/gpu.present=true -o name); do
  echo "=== $NODE ==="
  kubectl debug $NODE -- nvidia-smi --query-gpu=name,temperature.gpu,memory.used,memory.total,ecc.errors.corrected.aggregate.total --format=csv 2>/dev/null || echo "Cannot access node"
done

# Verify NVIDIA device plugin is running
kubectl get pods -n kube-system -l app=nvidia-device-plugin-daemonset

# Verify DCGM exporter is running
kubectl get pods -n monitoring -l app=dcgm-exporter
```

### 5.3 Model Loading on New Nodes

When new GPU nodes join the cluster, Triton pods are scheduled automatically. Model loading takes 5-10 minutes:

```bash
# Monitor model loading progress
kubectl logs -n llm-inference -l app=netflix-llm-platform -f --since=5m | grep -E "Loading|Ready|Error"

# Verify model is loaded and ready
kubectl exec -n llm-inference <pod-name> -- curl -sf http://localhost:8001/v2/models/netflix_llm_70b | jq .state
# Expected: "READY"
```

---

## 6. Model Deployment to Triton

### 6.1 Deploying a New Model Version

```bash
# 1. Upload new model to S3 model repository
aws s3 sync ./model_repository/netflix_llm_70b/2/ \
  s3://netflix-llm-models/model_repository/netflix_llm_70b/2/ \
  --region us-east-1

# 2. Replicate to all regions (handled by S3 Cross-Region Replication)
# Verify replication status
aws s3api head-object \
  --bucket netflix-llm-models-us-west-2 \
  --key model_repository/netflix_llm_70b/2/model.plan \
  --region us-west-2

# 3. Trigger model reload in Triton
# Option A: Via Triton Model Control API
kubectl exec -n llm-inference <pod-name> -- \
  curl -sf -X POST http://localhost:8001/v2/repository/models/netflix_llm_70b/load

# Option B: Via rolling restart (loads latest model version)
kubectl rollout restart deployment/netflix-llm-platform -n llm-inference

# 4. Verify new model version is loaded
kubectl exec -n llm-inference <pod-name> -- \
  curl -sf http://localhost:8001/v2/models/netflix_llm_70b/versions | jq .
```

### 6.2 A/B Testing Model Versions

```bash
# Deploy version 2 alongside version 1
helm upgrade --install netflix-llm-platform deploy/helm/netflix-llm-platform \
  --namespace llm-inference \
  --set triton.models.netflix_llm_70b.versions={1,2} \
  --set triton.models.netflix_llm_70b.defaultVersion=1 \
  --set abTest.enabled=true \
  --set abTest.versionB=2 \
  --set abTest.trafficSplit=10 \
  --wait
```

---

## 7. Rollback Procedure

### 7.1 Automated Rollback

```bash
# Via CD pipeline
gh workflow run cd-deploy.yml \
  --ref main \
  -f target-region=all

# The rollback job is triggered separately
gh workflow run cd-deploy.yml -f target-region=all
```

### 7.2 Manual Rollback

```bash
# Step 1: Identify current and previous revision
helm history netflix-llm-platform -n llm-inference --max 5

# Step 2: Rollback to previous revision
PREV_REVISION=<previous-revision-number>
helm rollback netflix-llm-platform $PREV_REVISION -n llm-inference --wait --timeout 600s

# Step 3: Clean up any canary releases
helm uninstall netflix-llm-platform-canary -n llm-inference 2>/dev/null || true

# Step 4: Verify rollback
kubectl rollout status deployment/netflix-llm-platform -n llm-inference --timeout=600s
kubectl get pods -n llm-inference -l app=netflix-llm-platform -o wide
```

### 7.3 Emergency Rollback (Skip All Validation)

```bash
# EMERGENCY: Immediate rollback, all regions simultaneously
for REGION in us-east-1 us-west-2 eu-west-1; do
  echo "Rolling back $REGION..."
  aws eks update-kubeconfig --name netflix-llm-${REGION} --region $REGION
  helm rollback netflix-llm-platform 0 -n llm-inference --wait --timeout 300s &
done
wait
echo "Emergency rollback complete for all regions"

# Verify all regions
for REGION in us-east-1 us-west-2 eu-west-1; do
  aws eks update-kubeconfig --name netflix-llm-${REGION} --region $REGION
  echo "=== $REGION ==="
  kubectl get pods -n llm-inference -l app=netflix-llm-platform --no-headers | wc -l
  kubectl get pods -n llm-inference -l app=netflix-llm-platform -o jsonpath='{range .items[*]}{.status.phase}{"\n"}{end}' | sort | uniq -c
done
```

---

## 8. Post-Deployment Validation

### 8.1 Health Check Checklist

```bash
# 1. All pods running
kubectl get pods -n llm-inference -l app=netflix-llm-platform -o wide

# 2. All GPUs allocated
kubectl describe pods -n llm-inference -l app=netflix-llm-platform | grep "nvidia.com/gpu"

# 3. Triton models loaded
kubectl exec -n llm-inference <pod-name> -- curl -sf http://localhost:8001/v2/models/netflix_llm_70b

# 4. Health endpoints passing
kubectl exec -n llm-inference <pod-name> -- curl -sf http://localhost:8080/health/ready
kubectl exec -n llm-inference <pod-name> -- curl -sf http://localhost:8080/health/live
kubectl exec -n llm-inference <pod-name> -- curl -sf http://localhost:8080/health/gpu

# 5. End-to-end inference test
kubectl exec -n llm-inference <pod-name> -- curl -sf \
  -X POST http://localhost:8080/v1/inference \
  -H "Content-Type: application/json" \
  -d '{"prompt":"test deployment validation","max_tokens":10,"user_id":"deployment-test"}'

# 6. Metrics flowing
kubectl exec -n llm-inference <pod-name> -- curl -sf http://localhost:8080/metrics | head -20
```

### 8.2 SLO Verification (Wait 10 Minutes)

After deployment, monitor for 10 minutes to confirm SLO compliance:

- P99 latency < 100ms
- Error rate < 0.1%
- GPU utilization 60-85%
- KV cache hit rate > 80%
- All regions serving traffic

---

## 9. Emergency Procedures

### 9.1 GPU Node Failure During Deployment

```bash
# Cordon the failing node
kubectl cordon <node-name>

# Drain workloads gracefully
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data --timeout=120s

# Request replacement node
aws eks update-nodegroup-config \
  --cluster-name netflix-llm-us-east-1 \
  --nodegroup-name gpu-p4d-24xlarge \
  --scaling-config desiredSize=$((CURRENT_SIZE + 1)) \
  --region us-east-1
```

### 9.2 Cross-Region Deployment Failure

If a secondary region deployment fails but the primary succeeded:

1. Do NOT rollback the primary region
2. Investigate the secondary region failure
3. Ensure Route53 health checks are routing away from the failed region
4. Fix the issue and redeploy to the failed region only

### 9.3 Contact Information

| Role | Contact | Escalation Time |
|------|---------|----------------|
| On-Call Engineer | #llm-platform-oncall (PagerDuty) | Immediate |
| Platform Lead | Gopi Krishna Vajrala | 15 minutes |
| SRE Lead | #sre-escalation (Slack) | 30 minutes |
| VP Engineering | Direct page via PagerDuty | 60 minutes (P1 only) |

---

**Document Revision History:**

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | 2026-02-21 | Gopi Krishna Vajrala | Initial deployment guide |
