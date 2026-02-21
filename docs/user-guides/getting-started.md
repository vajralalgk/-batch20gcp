# Netflix Real-Time LLM Personalization & Inference Platform
# Getting Started Guide

**Author:** Gopi Krishna Vajrala
**Version:** 2.0.0
**Audience:** ML Engineers, Data Scientists, Platform Engineers

---

## Welcome

The Netflix Real-Time LLM Personalization & Inference Platform provides a unified GPU-accelerated inference service for deploying machine learning models at scale. This guide walks you through setting up your development environment, deploying your first model, and making inference requests.

**What you will accomplish in this guide:**
- Set up your local development environment
- Connect to the inference cluster
- Deploy a model to the staging environment
- Make your first prediction request
- Monitor inference performance

---

## Architecture Overview

```
CLIENT REQUEST FLOW
================================================================

  Your Service (gRPC/REST)
       |
       v
  API Gateway (Kong)        -- Auth, rate limiting, routing
       |
       v
  Inference Orchestrator    -- Batching, model routing, caching
       |
       +--------> Feature Store (Redis)    -- User features
       |
       +--------> Prediction Cache (Redis) -- Cached results
       |
       v
  GPU Inference Server      -- NVIDIA Triton on A100/H100
  (NVIDIA Triton)
       |
       v
  Response to Client        -- Prediction + metadata

================================================================
```

---

## Prerequisites

| Tool | Required Version | Purpose | Required? |
|------|-----------------|---------|-----------|
| **Python** | 3.11+ | Client SDK and tooling | Required |
| **kubectl** | 1.28+ | Kubernetes cluster access | Required |
| **AWS CLI** | 2.x | AWS authentication | Required |
| **Docker** | 24.x+ | Local development and testing | Required |
| **grpcurl** | Latest | gRPC endpoint testing | Recommended |
| **nvidia-smi** | Latest | GPU verification (if local GPU) | Optional |
| **helm** | 3.x | Deploying charts to EKS | Recommended |

---

## Step 1: Configure AWS Access

Request access to the Netflix LLM inference platform through your team lead. You will receive an IAM role appropriate for your function (see `security/policies/iam-policy-template.json`).

```bash
# Configure AWS CLI with your profile
aws configure --profile netflix-llm

# Verify access
aws sts get-caller-identity --profile netflix-llm
```

---

## Step 2: Connect to the EKS Cluster

```bash
# Update kubeconfig for the inference cluster
# Available regions: us-east-1, us-west-2, eu-west-1
aws eks update-kubeconfig \
    --name netflix-llm-us-east-1 \
    --region us-east-1 \
    --profile netflix-llm

# Verify cluster access
kubectl get nodes -l nvidia.com/gpu=true

# Check GPU node status
kubectl get pods -n llm-inference -l app=llm-inference-server
```

Expected output:
```
NAME                                    READY   STATUS    RESTARTS   AGE
llm-inference-server-7f8a9b0c-abc12    1/1     Running   0          2d
llm-inference-server-7f8a9b0c-def34    1/1     Running   0          2d
...
```

---

## Step 3: Set Up Local Development Environment

```bash
# Clone the repository
git clone <repository-url>
cd netflix-llm-inference

# Create virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install the inference client SDK and development tools
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Copy environment configuration
cp .env.example .env

# Edit .env with your settings:
#   INFERENCE_ENDPOINT=https://llm-inference-use1.netflix.internal
#   FEATURE_STORE_HOST=redis-features.netflix.internal
#   MODEL_REGISTRY_TABLE=netflix-llm-model-registry
```

**Important:** Never commit your `.env` file to version control. It is listed in `.gitignore`.

---

## Step 4: Make Your First Prediction Request

### Using REST (curl)

```bash
# Health check
curl -s https://llm-inference-use1.netflix.internal/health | jq .

# Single prediction request
curl -s -X POST https://llm-inference-use1.netflix.internal/v1/predict \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${NETFLIX_JWT_TOKEN}" \
  -d '{
    "user_id": "u-12345678",
    "context": {
      "page": "homepage",
      "device": "smart_tv",
      "time_of_day": "evening"
    },
    "content_ids": ["tt-001", "tt-002", "tt-003"],
    "options": {
      "num_results": 10,
      "dry_run": true
    }
  }' | jq .
```

### Using the Python SDK

```python
from netflix_llm import InferenceClient

# Initialize client (reads config from .env)
client = InferenceClient(
    endpoint="https://llm-inference-use1.netflix.internal",
    timeout_ms=50,
)

# Single prediction
response = client.predict(
    user_id="u-12345678",
    context={"page": "homepage", "device": "smart_tv"},
    content_ids=["tt-001", "tt-002", "tt-003"],
    num_results=10,
)

print(f"Top recommendation: {response.predictions[0].content_id}")
print(f"Score: {response.predictions[0].score}")
print(f"Inference time: {response.metadata.inference_time_ms}ms")
print(f"Model version: {response.metadata.model_version}")
```

### Using gRPC

```bash
# Install grpcurl if not already installed
# brew install grpcurl  (macOS)
# go install github.com/fullstorydev/grpcurl/cmd/grpcurl@latest  (Go)

# gRPC prediction request
grpcurl -d '{
  "user_id": "u-12345678",
  "context": {"page": "homepage", "device": "smart_tv"},
  "content_ids": ["tt-001", "tt-002", "tt-003"],
  "num_results": 10
}' \
  llm-inference-use1.netflix.internal:8443 \
  netflix.llm.InferenceService/Predict
```

---

## Step 5: Deploy a Model (Staging)

### Register Your Model

```bash
# Upload model artifacts to S3
aws s3 cp ./model_artifacts/ \
    s3://netflix-llm-models-staging/my-model/v1.0.0/ \
    --recursive \
    --profile netflix-llm

# Register in model registry
python scripts/register_model.py \
    --name "my-recommendation-model" \
    --version "v1.0.0" \
    --artifact-path "s3://netflix-llm-models-staging/my-model/v1.0.0/" \
    --framework "tensorrt" \
    --gpu-memory-mb 8000 \
    --max-batch-size 32
```

### Deploy to Staging

```bash
# Deploy model to staging (shadow mode)
python scripts/deploy_model.py \
    --model "my-recommendation-model" \
    --version "v1.0.0" \
    --environment staging \
    --mode shadow \
    --region us-east-1

# Check deployment status
kubectl get pods -n llm-inference-staging \
    -l model=my-recommendation-model
```

### Validate in Staging

```bash
# Run benchmark against staging
./automation/scripts/benchmark/run_benchmark.sh \
    --endpoint https://llm-inference-staging.netflix.internal \
    --concurrency 16 \
    --duration 60
```

---

## Step 6: Monitor Your Model

### Grafana Dashboards

| Dashboard | URL | What It Shows |
|-----------|-----|---------------|
| Inference Overview | `https://grafana.netflix.internal/d/llm-inference` | Request rate, latency, error rate |
| GPU Fleet Status | `https://grafana.netflix.internal/d/gpu-fleet` | GPU utilization, temperature, memory |
| Model Performance | `https://grafana.netflix.internal/d/model-perf` | Per-model latency, throughput, cache hit rate |
| A/B Test Results | `https://grafana.netflix.internal/d/ab-test` | Experiment metrics, engagement lift |

### Key Metrics to Watch

| Metric | Healthy Range | Alert Threshold |
|--------|--------------|-----------------|
| p99 latency | < 50ms | > 50ms |
| Error rate | < 0.01% | > 0.1% |
| GPU utilization | 50-80% | < 20% or > 95% |
| Cache hit rate | > 30% | < 15% |
| Model load time | < 60s | > 120s |

### CLI Monitoring

```bash
# Check GPU health across the fleet
./automation/scripts/gpu/gpu_health_check.sh \
    --namespace llm-inference

# View real-time inference logs
kubectl logs -f deployment/llm-inference-server \
    -n llm-inference \
    --tail=100

# Check model status
curl -s https://llm-inference-use1.netflix.internal/v1/models | jq .
```

---

## Common Tasks

### Rollback a Model

```bash
# Rollback to previous version
./automation/scripts/rollback/rollback.sh us-east-1 v2.2.0
```

### Run GPU Health Check

```bash
# Check all GPUs in the inference namespace
./automation/scripts/gpu/gpu_health_check.sh \
    --namespace llm-inference \
    --alert
```

### View API Documentation

| Resource | URL |
|----------|-----|
| REST API (Swagger) | `https://llm-inference-use1.netflix.internal/docs` |
| gRPC Proto Definitions | `proto/inference_service.proto` |
| API Design Standards | `docs/design/api-design-standards.md` |

---

## Project Structure

```
netflix-llm-inference/
|-- api/                        # REST and gRPC API definitions
|-- automation/
|   |-- scripts/
|       |-- benchmark/          # Inference benchmarking
|       |-- deployment/         # Multi-region deployment
|       |-- gpu/                # GPU health monitoring
|       |-- rollback/           # Rollback automation
|-- deployment/
|   |-- docker/                 # Dockerfiles for inference images
|   |-- helm/                   # Helm charts for EKS deployment
|   |-- terraform/              # Infrastructure as Code
|-- docs/
|   |-- design/                 # API and architecture standards
|   |-- governance/             # Project governance documents
|   |-- user-guides/            # This guide and others
|-- inference/                  # Core inference engine code
|-- scripts/                    # Utility scripts
|-- security/
|   |-- policies/               # IAM policies, security policy
|   |-- scanning/               # Security scanning config
|-- tests/                      # Unit and integration tests
```

---

## Getting Help

| Resource | Contact |
|----------|---------|
| Platform Architecture | Gopi Krishna Vajrala (Platform Architect) |
| Documentation | `/docs` directory in repository |
| Architecture Decisions | `/docs/adr` directory |
| Slack Channel | `#llm-inference-platform` |
| On-Call (PagerDuty) | `llm-inference-oncall` escalation policy |
| Bug Reports | File issue in repository with `[BUG]` prefix |

---

**Author:** Gopi Krishna Vajrala
**Last Updated:** 2026-02-21
