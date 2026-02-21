# Netflix Real-Time LLM Personalization & Inference Platform

**Enterprise-grade, multi-region GPU inference platform for real-time content personalization**

[![CI Pipeline](https://img.shields.io/badge/CI-passing-brightgreen)]()
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)]()
[![GPU Optimized](https://img.shields.io/badge/GPU-A100%20Optimized-green)]()

**Author:** Gopi Krishna Vajrala
**Role:** ML Infrastructure & Distributed Systems Architect

---

## Architecture Overview

```
User Request --> [Route53 Geo DNS] --> [Regional API Gateway] --> [Load Balancer]
                                                                       |
                                            +--------------------------+--------------------------+
                                            |                          |                          |
                                     [us-east-1]                [us-west-2]                [eu-west-1]
                                            |                          |                          |
                                     [Inference Router]          [Inference Router]         [Inference Router]
                                            |                          |                          |
                                  +----+----+----+           +----+----+----+          +----+----+----+
                                  |    |    |    |           |    |    |    |          |    |    |    |
                                  v    v    v    v           v    v    v    v          v    v    v    v
                                [  Triton Cluster  ]       [  Triton Cluster  ]      [  Triton Cluster  ]
                                [ 4xA100 TP=4 each ]       [ 4xA100 TP=4 each]      [ 4xA100 TP=4 each]
                                [ TensorRT-LLM 13B ]       [ TensorRT-LLM 13B]      [ TensorRT-LLM 13B]
                                [ INT8 Quantized   ]       [ INT8 Quantized  ]       [ INT8 Quantized  ]
                                         |                          |                         |
                              +----------+----------+    +----------+----------+   +----------+----------+
                              |          |          |    |          |          |   |          |          |
                        [KV Cache] [Redis Sess] [Features] [KV Cache] [Redis] [Features] [KV Cache] [Redis]
                                         |                          |                         |
                                         +------------+-------------+-------------------------+
                                                      |
                                              [Control Plane]
                                         [Autoscaler | Fleet Scheduler | Capacity Model]
                                                      |
                                              [Observability]
                                         [Prometheus | DCGM | Grafana]
```

## Key Capabilities

| Capability | Description |
|------------|-------------|
| Hybrid Personalization | Traditional embeddings + LLM re-ranking + session memory |
| Real-Time GPU Inference | TensorRT-LLM optimized 13B model, INT8 quantized, TP=4 |
| Multi-Region Active-Active | 3 regions, < 15s failover, geo-aware routing |
| GPU Optimization | 80%+ utilization, dynamic batching, KV cache management |
| Cost-Aware Scaling | Throughput-per-dollar modeling, GPU SKU evaluation |
| Production Observability | DCGM GPU metrics, SM occupancy, tail latency tracking |

## Performance Targets

| Metric | Target |
|--------|--------|
| p95 Latency | < 200ms |
| p99 Latency | < 250ms |
| GPU Utilization | 80%+ |
| Throughput | 3,000 tokens/sec |
| SLA | 99.95% |
| Failover Time | < 15 sec |
| Peak Handling | 3x load spike (Friday 8 PM) |

## Project Structure

```
src/
├── api/                    # FastAPI application, routes, middleware
│   ├── main.py             # Application entrypoint
│   ├── routes/             # API route handlers
│   └── middleware/         # Request/response middleware
├── inference/              # Model serving infrastructure
│   ├── triton_client.py    # Triton Inference Server client
│   ├── tensorrt_engine.py  # TensorRT-LLM engine management
│   ├── dynamic_batcher.py  # Adaptive batch size optimization
│   └── model_registry.py   # Model versioning and deployment
├── personalization/        # Recommendation engine
│   ├── embedding_store.py  # User/content embedding management
│   ├── llm_reranker.py     # LLM-powered re-ranking
│   ├── session_memory.py   # Context-aware session tracking
│   └── feature_engine.py   # Real-time feature computation
├── gpu/                    # GPU resource management
│   ├── kv_cache_manager.py # Token-level TTL, partition-aware eviction
│   ├── memory_pool.py      # GPU memory fragmentation tracking
│   ├── sm_monitor.py       # Streaming Multiprocessor occupancy
│   └── device_manager.py   # Multi-GPU coordination (TP=4)
├── control_plane/          # Platform orchestration
│   ├── autoscaler.py       # GPU-aware autoscaling logic
│   ├── capacity_model.py   # Capacity planning and forecasting
│   ├── fleet_scheduler.py  # GPU fleet scheduling
│   └── circuit_breaker.py  # Failure isolation
├── cost/                   # Cost optimization
│   ├── optimizer.py        # Throughput-per-dollar modeling
│   ├── sku_evaluator.py    # GPU SKU comparison (A100 vs A10G)
│   └── budget_tracker.py   # Real-time cost monitoring
├── observability/          # Monitoring and alerting
│   ├── metrics_collector.py # Prometheus metrics
│   ├── dcgm_exporter.py   # NVIDIA DCGM GPU metrics
│   ├── latency_tracker.py  # p95/p99 tail latency
│   └── health_checker.py   # Multi-layer health checks
├── gateway/                # Edge and routing
│   ├── geo_router.py       # Geo-aware request routing
│   ├── load_balancer.py    # Inference-aware load balancing
│   ├── rate_limiter.py     # Adaptive rate limiting
│   └── request_hedger.py   # Cross-region request hedging
└── security/               # Security infrastructure
    ├── iam_manager.py      # IAM policy enforcement
    ├── encryption.py       # Model artifact encryption
    └── vpc_isolator.py     # Network isolation

infrastructure/
├── terraform/
│   ├── modules/
│   │   ├── gpu_cluster/    # A100 GPU cluster provisioning
│   │   ├── networking/     # Multi-region VPC, peering
│   │   ├── inference/      # Triton server infrastructure
│   │   ├── cache/          # Redis cluster, KV cache
│   │   ├── monitoring/     # Prometheus, Grafana, DCGM
│   │   └── security/       # IAM, KMS, security groups
│   └── environments/
│       ├── us-east-1/
│       ├── us-west-2/
│       └── eu-west-1/
└── kubernetes/
    ├── inference/          # Triton server deployments
    ├── monitoring/         # Prometheus + Grafana
    └── autoscaling/        # GPU-aware HPA

monitoring/
├── dashboards/             # Grafana dashboard JSON
├── alerts/                 # Alert rules and SLOs
└── dcgm/                   # GPU metric configs

tests/
├── unit/                   # Unit tests
├── integration/            # Integration tests
├── performance/            # Load and latency tests
└── gpu/                    # GPU-specific tests
```

## Quick Start

```bash
pip install -r requirements.txt
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000
pytest tests/ -v
```

## Technology Stack

| Layer | Technology |
|-------|-----------|
| API Framework | FastAPI + Uvicorn |
| Inference Server | NVIDIA Triton Inference Server |
| Model Optimization | TensorRT-LLM, INT8 Quantization |
| GPU Hardware | NVIDIA A100 80GB (TP=4), A10G |
| Session Memory | Redis Cluster (ElastiCache) |
| Feature Store | DynamoDB + ElastiCache |
| Infrastructure | Terraform, Kubernetes (EKS), Docker |
| Monitoring | Prometheus, DCGM Exporter, Grafana |
| Cloud | AWS Multi-Region (us-east-1, us-west-2, eu-west-1) |
| CI/CD | GitHub Actions |
| Security | IAM, KMS, VPC Isolation, mTLS |
