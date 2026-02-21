#!/usr/bin/env python3
"""
Netflix Real-Time LLM Personalization & Inference Platform
Architecture Roadmap Generator

Author: Gopi Krishna Vajrala

This script generates:
  1. Text-based architecture diagrams for the LLM inference platform
  2. Capacity planning calculations for GPU fleet sizing
  3. Cost projections for multi-region deployment

Usage:
  python generate_architecture_roadmap.py
  python generate_architecture_roadmap.py --output /path/to/output
  python generate_architecture_roadmap.py --format json
"""

import json
import math
import os
import sys
from datetime import datetime, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OUTPUT_DIR = os.environ.get(
    "ROADMAP_OUTPUT_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "generated"),
)

PLATFORM_NAME = "Netflix Real-Time LLM Personalization & Inference Platform"
AUTHOR = "Gopi Krishna Vajrala"

# ---------------------------------------------------------------------------
# Architecture Diagram Generator (Text-Based)
# ---------------------------------------------------------------------------


def generate_system_architecture() -> str:
    """Generate the high-level system architecture diagram as ASCII art."""
    return f"""\
================================================================================
  {PLATFORM_NAME}
  System Architecture Overview
  Author: {AUTHOR}
  Generated: {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}
================================================================================

  TRAFFIC FLOW
  ============

  Netflix Clients (300M+ subscribers)
       |
       v
  +------------------------------------------------------------------+
  |                     GLOBAL LOAD BALANCER                          |
  |    AWS Global Accelerator / CloudFront Edge                       |
  |    - Anycast IP routing                                           |
  |    - TLS 1.3 termination                                         |
  |    - Geographic traffic steering                                  |
  +----+------------------------+------------------------+------------+
       |                        |                        |
       v                        v                        v
  +-----------+           +-----------+           +-----------+
  | us-east-1 |           | us-west-2 |           | eu-west-1 |
  | (Primary) |           |(Secondary)|           | (Europe)  |
  +-----------+           +-----------+           +-----------+
       |                        |                        |
       v                        v                        v

  PER-REGION ARCHITECTURE (replicated in each region)
  ===================================================

  +------------------------------------------------------------------+
  |                        API GATEWAY                                |
  |    Kong / AWS API Gateway                                         |
  |    - Rate limiting (10K req/s per client)                         |
  |    - JWT / mTLS authentication                                    |
  |    - Request routing (REST + gRPC)                                |
  |    - Request/response logging                                     |
  +----+----------------------------+--------------------------------+
       |                            |
       v                            v
  +-------------------+    +-------------------+
  | REST Endpoint     |    | gRPC Endpoint     |
  | /v1/predict       |    | Predict.Infer()   |
  | /v1/recommend     |    | Predict.Batch()   |
  | /v1/embed         |    | Model.Status()    |
  | /v1/models        |    | Health.Check()    |
  +--------+----------+    +--------+----------+
           |                         |
           +----------+--------------+
                      |
                      v
  +------------------------------------------------------------------+
  |                   INFERENCE ORCHESTRATOR                          |
  |    - Request batching (dynamic batch sizing)                      |
  |    - Model routing (A/B test, shadow, canary)                     |
  |    - Feature assembly (user profile + context)                    |
  |    - Cache check (prediction cache, embedding cache)              |
  |    - Latency budget management (50ms total budget)                |
  +---+------------------+--------------------+----------------------+
      |                  |                    |
      v                  v                    v
  +----------+    +-------------+    +------------------+
  | Feature  |    | Prediction  |    | Model            |
  | Store    |    | Cache       |    | Registry         |
  | (Redis   |    | (Redis      |    | (S3 + DynamoDB)  |
  |  Cluster)|    |  Cluster)   |    |                  |
  +----------+    +-------------+    +------------------+
                                              |
      +---------------------------------------+
      |
      v
  +------------------------------------------------------------------+
  |              GPU INFERENCE CLUSTER (EKS)                          |
  |                                                                    |
  |  +--------------------+  +--------------------+                    |
  |  | Model Server Pod   |  | Model Server Pod   |   x N replicas    |
  |  | (NVIDIA Triton)    |  | (NVIDIA Triton)    |                    |
  |  |                    |  |                    |                    |
  |  | +----------------+ |  | +----------------+ |                    |
  |  | | LLM Model      | |  | | LLM Model      | |                    |
  |  | | (TensorRT opt) | |  | | (TensorRT opt) | |                    |
  |  | +----------------+ |  | +----------------+ |                    |
  |  | | Embedding Model| |  | | Embedding Model| |                    |
  |  | | (ONNX Runtime) | |  | | (ONNX Runtime) | |                    |
  |  | +----------------+ |  | +----------------+ |                    |
  |  | | Ranking Model  | |  | | Ranking Model  | |                    |
  |  | | (TensorRT opt) | |  | | (TensorRT opt) | |                    |
  |  | +----------------+ |  | +----------------+ |                    |
  |  +--------------------+  +--------------------+                    |
  |                                                                    |
  |  GPU Nodes: NVIDIA A100/H100 (80GB HBM)                          |
  |  GPU per Node: 4-8                                                 |
  |  Total GPU Memory per Region: 2.5 TB+                             |
  +------------------------------------------------------------------+
      |
      v
  +------------------------------------------------------------------+
  |                   OBSERVABILITY STACK                              |
  |                                                                    |
  |  +---------------+  +---------------+  +-------------------+      |
  |  | Prometheus    |  | Grafana       |  | OpenTelemetry     |      |
  |  | (Metrics)     |  | (Dashboards)  |  | (Distributed      |      |
  |  |               |  |               |  |  Tracing)         |      |
  |  +---------------+  +---------------+  +-------------------+      |
  |                                                                    |
  |  +---------------+  +---------------+  +-------------------+      |
  |  | CloudWatch    |  | PagerDuty     |  | Netflix Atlas     |      |
  |  | (AWS Metrics) |  | (Alerting)    |  | (Custom Metrics)  |      |
  |  +---------------+  +---------------+  +-------------------+      |
  +------------------------------------------------------------------+

  DATA PIPELINE (Offline)
  =======================

  +------------------------------------------------------------------+
  |                    ML TRAINING PIPELINE                            |
  |                                                                    |
  |  User Interactions  -->  Feature Engineering  -->  Model Training  |
  |  (Kafka/Kinesis)        (Spark on EMR)            (SageMaker)     |
  |                                                                    |
  |  Model Validation  -->  Model Registry  -->  Model Deployment     |
  |  (A/B test eval)        (S3 + DynamoDB)      (EKS Rolling)       |
  +------------------------------------------------------------------+

================================================================================
"""


def generate_inference_pipeline_diagram() -> str:
    """Generate the real-time inference pipeline diagram."""
    return f"""\
================================================================================
  Real-Time Inference Pipeline
  Latency Budget: 50ms end-to-end (p99)
  Author: {AUTHOR}
================================================================================

  REQUEST FLOW (50ms latency budget)
  ===================================

  Client Request
       |
       | (0ms) TLS termination + routing
       v
  +------------------+
  | API Gateway      |  Budget: 2ms
  | - Auth (JWT)     |
  | - Rate limit     |
  +--------+---------+
           |
           | (2ms)
           v
  +------------------+
  | Request Router   |  Budget: 1ms
  | - Model selection|
  | - A/B routing    |
  +--------+---------+
           |
           | (3ms)
           v
  +------------------+
  | Feature Assembly |  Budget: 5ms
  | - User profile   |  (Redis cache hit: 1ms)
  | - Context        |  (Cache miss: 5ms)
  | - History        |
  +--------+---------+
           |
           | (8ms)
           v
  +------------------+
  | Cache Check      |  Budget: 1ms
  | - Prediction     |  Hit rate target: 30%
  | - Embedding      |
  +--------+---------+
           |
           | (9ms) Cache miss path
           v
  +------------------+
  | GPU Inference    |  Budget: 30ms
  | - LLM forward   |  (Batch size: 8-32)
  |   pass           |  (TensorRT FP16)
  | - Embedding gen  |
  | - Ranking score  |
  +--------+---------+
           |
           | (39ms)
           v
  +------------------+
  | Post-Processing  |  Budget: 3ms
  | - Score norm     |
  | - Diversity      |
  | - Business rules |
  +--------+---------+
           |
           | (42ms)
           v
  +------------------+
  | Response + Log   |  Budget: 3ms
  | - Cache update   |
  | - Event emit     |
  +--------+---------+
           |
           | (45ms) Total p99 target
           v
  Client Response

  LATENCY BUDGET ALLOCATION
  ==========================
  Component              Budget    Target p99
  ----------------------------------------
  API Gateway              2ms        1ms
  Request Router           1ms       0.5ms
  Feature Assembly         5ms        3ms
  Cache Check              1ms       0.5ms
  GPU Inference           30ms       25ms
  Post-Processing          3ms        2ms
  Response + Logging       3ms        2ms
  Network overhead         5ms        3ms
  ----------------------------------------
  TOTAL                   50ms       37ms

================================================================================
"""


def generate_gpu_cluster_topology() -> str:
    """Generate the GPU cluster topology diagram."""
    return f"""\
================================================================================
  GPU Cluster Topology - Per Region
  Author: {AUTHOR}
================================================================================

  EKS CLUSTER: netflix-llm-<region>
  ==================================

  +------------------------------------------------------------------+
  |  NODE POOL: gpu-inference (Managed Node Group)                    |
  |  Instance: p4d.24xlarge (8x A100 80GB) or p5.48xlarge (8x H100) |
  |                                                                    |
  |  +-----------------------------+  +-----------------------------+ |
  |  | Node 1 (p4d.24xlarge)       |  | Node 2 (p4d.24xlarge)       | |
  |  | CPU: 96 vCPU                |  | CPU: 96 vCPU                | |
  |  | RAM: 1152 GB                |  | RAM: 1152 GB                | |
  |  | GPU: 8x A100 80GB          |  | GPU: 8x A100 80GB          | |
  |  | NVLink: 600 GB/s            |  | NVLink: 600 GB/s            | |
  |  | EFA: 400 Gbps               |  | EFA: 400 Gbps               | |
  |  |                             |  |                             | |
  |  | Pods:                       |  | Pods:                       | |
  |  |  [triton-0] GPU 0-1        |  |  [triton-4] GPU 0-1        | |
  |  |  [triton-1] GPU 2-3        |  |  [triton-5] GPU 2-3        | |
  |  |  [triton-2] GPU 4-5        |  |  [triton-6] GPU 4-5        | |
  |  |  [triton-3] GPU 6-7        |  |  [triton-7] GPU 6-7        | |
  |  +-----------------------------+  +-----------------------------+ |
  |                                                                    |
  |  ... (N nodes based on capacity plan)                             |
  +------------------------------------------------------------------+

  +------------------------------------------------------------------+
  |  NODE POOL: cpu-services (Managed Node Group)                     |
  |  Instance: m6i.4xlarge                                            |
  |                                                                    |
  |  +-----------------------------+  +-----------------------------+ |
  |  | Feature Store (Redis)       |  | API Gateway (Kong)          | |
  |  | Prediction Cache (Redis)    |  | Inference Orchestrator      | |
  |  | Metrics (Prometheus)        |  | Model Registry Agent        | |
  |  +-----------------------------+  +-----------------------------+ |
  +------------------------------------------------------------------+

  STORAGE
  =======
  +-----------------------------+  +-----------------------------+
  | S3: Model Artifacts         |  | DynamoDB: Model Metadata    |
  | - LLM weights (10-70GB ea) |  | - Model versions            |
  | - Embedding models (1-5GB) |  | - A/B test configs          |
  | - Ranking models (0.5-2GB) |  | - Feature schemas           |
  | Encryption: SSE-KMS        |  | Encryption: AWS-owned CMK   |
  +-----------------------------+  +-----------------------------+

================================================================================
"""


# ---------------------------------------------------------------------------
# Capacity Planning Calculator
# ---------------------------------------------------------------------------


def calculate_capacity_plan(
    peak_rps: int = 500000,
    avg_rps: int = 100000,
    p99_latency_ms: float = 50.0,
    gpu_inference_ms: float = 25.0,
    batch_size: int = 16,
    gpu_utilization_target: float = 0.70,
    gpus_per_node: int = 8,
    regions: int = 3,
    redundancy_factor: float = 1.5,
) -> dict[str, Any]:
    """
    Calculate GPU fleet capacity requirements.

    Args:
        peak_rps: Peak requests per second (global)
        avg_rps: Average requests per second (global)
        p99_latency_ms: Target p99 latency in milliseconds
        gpu_inference_ms: GPU inference time per batch
        batch_size: Dynamic batch size
        gpu_utilization_target: Target GPU utilization (0-1)
        gpus_per_node: Number of GPUs per node
        regions: Number of deployment regions
        redundancy_factor: Over-provisioning factor for failover
    """
    # Throughput per GPU
    batches_per_second_per_gpu = 1000.0 / gpu_inference_ms
    requests_per_second_per_gpu = batches_per_second_per_gpu * batch_size
    effective_rps_per_gpu = requests_per_second_per_gpu * gpu_utilization_target

    # Cache hit rate reduces GPU load
    cache_hit_rate = 0.30  # 30% prediction cache hit rate
    gpu_rps_needed = peak_rps * (1 - cache_hit_rate)

    # Per-region calculations
    rps_per_region = gpu_rps_needed / regions
    gpus_per_region = math.ceil(rps_per_region / effective_rps_per_gpu)
    gpus_per_region_redundant = math.ceil(gpus_per_region * redundancy_factor)
    nodes_per_region = math.ceil(gpus_per_region_redundant / gpus_per_node)

    # Total fleet
    total_gpus = gpus_per_region_redundant * regions
    total_nodes = nodes_per_region * regions

    # Memory calculations
    model_memory_gb = 45  # LLM + embedding + ranking models
    kv_cache_gb = 20  # KV cache per GPU for inference
    overhead_gb = 5  # CUDA context, framework overhead
    memory_per_gpu_gb = model_memory_gb + kv_cache_gb + overhead_gb
    total_gpu_memory_tb = (total_gpus * 80) / 1024  # A100 80GB

    return {
        "inputs": {
            "peak_rps_global": peak_rps,
            "avg_rps_global": avg_rps,
            "p99_latency_target_ms": p99_latency_ms,
            "gpu_inference_ms": gpu_inference_ms,
            "batch_size": batch_size,
            "gpu_utilization_target": gpu_utilization_target,
            "cache_hit_rate": cache_hit_rate,
            "regions": regions,
            "redundancy_factor": redundancy_factor,
        },
        "per_gpu": {
            "batches_per_second": round(batches_per_second_per_gpu, 1),
            "requests_per_second": round(requests_per_second_per_gpu, 1),
            "effective_rps": round(effective_rps_per_gpu, 1),
        },
        "per_region": {
            "rps_after_cache": round(rps_per_region, 0),
            "gpus_minimum": gpus_per_region,
            "gpus_with_redundancy": gpus_per_region_redundant,
            "nodes": nodes_per_region,
        },
        "fleet_total": {
            "total_gpus": total_gpus,
            "total_nodes": total_nodes,
            "total_gpu_memory_tb": round(total_gpu_memory_tb, 1),
        },
        "memory_per_gpu": {
            "model_weights_gb": model_memory_gb,
            "kv_cache_gb": kv_cache_gb,
            "overhead_gb": overhead_gb,
            "total_required_gb": memory_per_gpu_gb,
            "gpu_memory_gb": 80,
            "headroom_gb": 80 - memory_per_gpu_gb,
        },
    }


def format_capacity_plan(plan: dict[str, Any]) -> str:
    """Format capacity plan as a readable text report."""
    inputs = plan["inputs"]
    per_gpu = plan["per_gpu"]
    per_region = plan["per_region"]
    fleet = plan["fleet_total"]
    mem = plan["memory_per_gpu"]

    return f"""\
================================================================================
  GPU Fleet Capacity Plan
  {PLATFORM_NAME}
  Author: {AUTHOR}
  Generated: {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}
================================================================================

  INPUT PARAMETERS
  ================
  Peak RPS (global):        {inputs['peak_rps_global']:>12,} req/s
  Average RPS (global):     {inputs['avg_rps_global']:>12,} req/s
  P99 Latency Target:       {inputs['p99_latency_target_ms']:>12.0f} ms
  GPU Inference Time:        {inputs['gpu_inference_ms']:>12.0f} ms
  Dynamic Batch Size:        {inputs['batch_size']:>12}
  GPU Utilization Target:    {inputs['gpu_utilization_target']:>12.0%}
  Cache Hit Rate:            {inputs['cache_hit_rate']:>12.0%}
  Deployment Regions:        {inputs['regions']:>12}
  Redundancy Factor:         {inputs['redundancy_factor']:>12.1f}x

  PER-GPU THROUGHPUT
  ==================
  Batches/second:            {per_gpu['batches_per_second']:>12.1f}
  Requests/second (raw):     {per_gpu['requests_per_second']:>12.1f}
  Requests/second (eff):     {per_gpu['effective_rps']:>12.1f}

  PER-REGION REQUIREMENTS ({inputs['regions']} regions)
  ==============================
  RPS after cache:           {per_region['rps_after_cache']:>12,.0f} req/s
  GPUs (minimum):            {per_region['gpus_minimum']:>12}
  GPUs (with redundancy):    {per_region['gpus_with_redundancy']:>12}
  Nodes (p4d.24xlarge):      {per_region['nodes']:>12}

  TOTAL FLEET
  ===========
  Total GPUs:                {fleet['total_gpus']:>12}
  Total Nodes:               {fleet['total_nodes']:>12}
  Total GPU Memory:          {fleet['total_gpu_memory_tb']:>12.1f} TB

  GPU MEMORY BUDGET (per GPU - A100 80GB)
  ========================================
  Model Weights:             {mem['model_weights_gb']:>12} GB
  KV Cache:                  {mem['kv_cache_gb']:>12} GB
  Framework Overhead:        {mem['overhead_gb']:>12} GB
  Total Required:            {mem['total_required_gb']:>12} GB
  GPU Memory Available:      {mem['gpu_memory_gb']:>12} GB
  Headroom:                  {mem['headroom_gb']:>12} GB

================================================================================
"""


# ---------------------------------------------------------------------------
# Cost Projection Generator
# ---------------------------------------------------------------------------


def calculate_cost_projection(
    capacity_plan: dict[str, Any],
    months: int = 24,
) -> dict[str, Any]:
    """
    Generate cost projections for the LLM inference platform.

    Cost components:
    - GPU instances (p4d.24xlarge or p5.48xlarge)
    - CPU instances for services
    - Storage (S3, EBS, DynamoDB)
    - Data transfer
    - Networking (VPC, NAT, load balancers)
    - Observability (CloudWatch, Prometheus, Grafana)
    - Licensing and support
    """
    regions = capacity_plan["inputs"]["regions"]
    nodes_per_region = capacity_plan["per_region"]["nodes"]
    total_nodes = capacity_plan["fleet_total"]["total_nodes"]

    # Hourly costs (on-demand pricing, US regions, approximate)
    p4d_hourly = 32.77  # p4d.24xlarge on-demand
    p4d_reserved_1yr = 20.81  # 1-year reserved (no upfront)
    p4d_reserved_3yr = 13.39  # 3-year reserved (no upfront)
    m6i_4xl_hourly = 0.768  # m6i.4xlarge for CPU services

    # Number of CPU service nodes per region
    cpu_nodes_per_region = 6  # Redis, API GW, orchestrator, monitoring

    hours_per_month = 730

    monthly_projections = []
    cumulative_cost = 0

    for month in range(1, months + 1):
        # GPU scaling: start at 60% capacity, ramp to 100% by month 6
        scale_factor = min(1.0, 0.6 + (month - 1) * 0.08)

        # Use reserved pricing after month 3
        if month <= 3:
            gpu_hourly_rate = p4d_hourly  # On-demand during ramp-up
        elif month <= 12:
            gpu_hourly_rate = p4d_reserved_1yr  # 1-year reserved
        else:
            gpu_hourly_rate = p4d_reserved_3yr  # 3-year reserved

        active_nodes = math.ceil(total_nodes * scale_factor)

        # Compute costs
        gpu_compute = active_nodes * gpu_hourly_rate * hours_per_month
        cpu_compute = (cpu_nodes_per_region * regions) * m6i_4xl_hourly * hours_per_month

        # Storage costs
        s3_model_storage = 500  # Model artifacts (~10TB at $0.023/GB)
        ebs_storage = active_nodes * 150  # 2TB gp3 per node
        dynamodb = 200 * regions  # DynamoDB tables

        # Data transfer
        # Cross-region replication + client egress
        data_transfer = 2000 * regions  # Approximate

        # Networking
        nat_gateway = 45 * 3 * regions  # 3 NAT GWs per region
        load_balancer = 25 * regions  # NLB per region

        # Observability
        cloudwatch = 300 * regions
        prometheus_grafana = 500  # Self-hosted on EKS

        # Support
        aws_support = (gpu_compute + cpu_compute) * 0.03  # Business support

        total_monthly = (
            gpu_compute
            + cpu_compute
            + s3_model_storage
            + ebs_storage
            + dynamodb
            + data_transfer
            + nat_gateway
            + load_balancer
            + cloudwatch
            + prometheus_grafana
            + aws_support
        )

        cumulative_cost += total_monthly

        monthly_projections.append(
            {
                "month": month,
                "date": (datetime(2026, 1, 1) + timedelta(days=30 * (month - 1))).strftime(
                    "%Y-%m"
                ),
                "scale_factor": round(scale_factor, 2),
                "active_gpu_nodes": active_nodes,
                "pricing_tier": (
                    "on-demand" if month <= 3 else "1yr-reserved" if month <= 12 else "3yr-reserved"
                ),
                "costs": {
                    "gpu_compute": round(gpu_compute, 2),
                    "cpu_compute": round(cpu_compute, 2),
                    "storage": round(s3_model_storage + ebs_storage + dynamodb, 2),
                    "data_transfer": round(data_transfer, 2),
                    "networking": round(nat_gateway + load_balancer, 2),
                    "observability": round(cloudwatch + prometheus_grafana, 2),
                    "support": round(aws_support, 2),
                    "total": round(total_monthly, 2),
                },
                "cumulative_total": round(cumulative_cost, 2),
            }
        )

    # Summary
    year1_cost = sum(m["costs"]["total"] for m in monthly_projections[:12])
    year2_cost = sum(m["costs"]["total"] for m in monthly_projections[12:24])

    return {
        "metadata": {
            "generated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "author": AUTHOR,
            "platform": PLATFORM_NAME,
            "projection_months": months,
        },
        "summary": {
            "year_1_total": round(year1_cost, 2),
            "year_2_total": round(year2_cost, 2),
            "two_year_total": round(year1_cost + year2_cost, 2),
            "avg_monthly_year_1": round(year1_cost / 12, 2),
            "avg_monthly_year_2": round(year2_cost / 12, 2),
            "cost_per_million_requests_year_1": round(
                year1_cost
                / (capacity_plan["inputs"]["avg_rps_global"] * 3600 * 24 * 365 / 1_000_000),
                2,
            ),
        },
        "monthly_projections": monthly_projections,
    }


def format_cost_projection(projection: dict[str, Any]) -> str:
    """Format cost projection as a readable text report."""
    summary = projection["summary"]
    months = projection["monthly_projections"]

    lines = [
        "=" * 80,
        f"  Cost Projection Report",
        f"  {PLATFORM_NAME}",
        f"  Author: {AUTHOR}",
        f"  Generated: {projection['metadata']['generated']}",
        "=" * 80,
        "",
        "  COST SUMMARY",
        "  ============",
        f"  Year 1 Total:              ${summary['year_1_total']:>14,.2f}",
        f"  Year 2 Total:              ${summary['year_2_total']:>14,.2f}",
        f"  2-Year Total:              ${summary['two_year_total']:>14,.2f}",
        f"  Avg Monthly (Year 1):      ${summary['avg_monthly_year_1']:>14,.2f}",
        f"  Avg Monthly (Year 2):      ${summary['avg_monthly_year_2']:>14,.2f}",
        f"  Cost per 1M requests (Y1): ${summary['cost_per_million_requests_year_1']:>14,.2f}",
        "",
        "  MONTHLY BREAKDOWN",
        "  =================",
        f"  {'Month':<8} {'Date':<10} {'Scale':>6} {'Nodes':>6} {'Pricing':<14} "
        f"{'GPU':>12} {'Total':>12} {'Cumulative':>14}",
        "  " + "-" * 96,
    ]

    for m in months:
        c = m["costs"]
        lines.append(
            f"  {m['month']:<8} {m['date']:<10} {m['scale_factor']:>5.0%} "
            f"{m['active_gpu_nodes']:>6} {m['pricing_tier']:<14} "
            f"${c['gpu_compute']:>11,.0f} ${c['total']:>11,.0f} "
            f"${m['cumulative_total']:>13,.0f}"
        )

    lines.extend(
        [
            "",
            "  COST OPTIMIZATION STRATEGIES",
            "  ============================",
            "  1. Reserved Instances:  Switch from on-demand to 1yr RI after ramp-up (37% savings)",
            "  2. 3-Year Reserved:    Move stable workloads to 3yr RI (59% savings vs on-demand)",
            "  3. Spot Instances:     Use spot for dev/staging GPU nodes (70-90% savings)",
            "  4. Right-sizing:       Monitor GPU utilization, downsize if consistently < 60%",
            "  5. Cache Optimization: Improve cache hit rate from 30% to 50% to reduce GPU load",
            "  6. Model Optimization: Quantize to INT8 for 2x throughput with minimal quality loss",
            "  7. Dynamic Scaling:    Scale down to 40% capacity during off-peak hours (midnight-6am)",
            "  8. Graviton for CPU:   Move CPU services to Graviton3 for 20% cost reduction",
            "",
            "=" * 80,
        ]
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Roadmap Timeline
# ---------------------------------------------------------------------------


def generate_roadmap_timeline() -> str:
    """Generate the project roadmap timeline."""
    return f"""\
================================================================================
  Project Roadmap Timeline
  {PLATFORM_NAME}
  Author: {AUTHOR}
================================================================================

  2026                                          2027
  Q1         Q2         Q3         Q4           Q1         Q2         Q3
  |----------|----------|----------|------------|----------|----------|----->

  Phase 1: Foundation (Q1 2026)
  [===========]
  - GPU cluster provisioning (EKS + p4d nodes)
  - Model serving infrastructure (Triton Inference Server)
  - Feature store setup (Redis cluster)
  - CI/CD pipeline for model deployment
  - Baseline monitoring and alerting
  Budget: $850K | Team: 6 engineers

  Phase 2: Core Inference (Q2 2026)
                [===========]
  - Real-time personalization API (REST + gRPC)
  - Dynamic batching optimization
  - TensorRT model optimization (FP16)
  - A/B testing framework for models
  - Prediction caching layer
  Budget: $1.2M | Team: 8 engineers

  Phase 3: Scale & Optimize (Q3-Q4 2026)
                             [======================]
  - Multi-region deployment (us-east-1, us-west-2, eu-west-1)
  - Canary deployment automation
  - INT8 quantization for throughput
  - Advanced GPU scheduling (MIG, time-slicing)
  - Cost optimization (reserved instances, spot)
  Budget: $1.5M | Team: 10 engineers

  Phase 4: Advanced ML (Q1-Q2 2027)
                                                  [======================]
  - LLM fine-tuning pipeline for personalization
  - Multi-model ensemble serving
  - Embedding model co-location
  - Real-time feature computation
  - Shadow deployment testing
  Budget: $1.0M | Team: 10 engineers

  Phase 5: Innovation (Q3 2027+)
                                                                          [===>
  - Streaming inference (token-by-token)
  - On-device model distillation
  - Federated learning integration
  - Custom silicon evaluation (AWS Inferentia/Trainium)
  Budget: $800K | Team: 8 engineers

  TOTAL INVESTMENT:  $5.35M over 18 months
  EXPECTED ROI:      3.2x within 2 years (improved engagement metrics)

================================================================================
"""


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------


def main():
    """Generate all architecture and planning documents."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate architecture roadmap, capacity plan, and cost projections"
    )
    parser.add_argument("--output", default=OUTPUT_DIR, help="Output directory")
    parser.add_argument(
        "--format", choices=["text", "json", "all"], default="all", help="Output format"
    )
    parser.add_argument(
        "--peak-rps", type=int, default=500000, help="Peak requests per second"
    )
    parser.add_argument(
        "--regions", type=int, default=3, help="Number of deployment regions"
    )
    args = parser.parse_args()

    output_dir = args.output
    os.makedirs(output_dir, exist_ok=True)

    print(f"{'=' * 70}")
    print(f"  {PLATFORM_NAME}")
    print(f"  Architecture Roadmap Generator")
    print(f"  Author: {AUTHOR}")
    print(f"{'=' * 70}")
    print()

    # 1. Architecture Diagrams
    print("Generating architecture diagrams...")
    arch_diagram = generate_system_architecture()
    inference_diagram = generate_inference_pipeline_diagram()
    gpu_topology = generate_gpu_cluster_topology()
    roadmap = generate_roadmap_timeline()

    if args.format in ("text", "all"):
        with open(os.path.join(output_dir, "system-architecture.txt"), "w") as f:
            f.write(arch_diagram)
        with open(os.path.join(output_dir, "inference-pipeline.txt"), "w") as f:
            f.write(inference_diagram)
        with open(os.path.join(output_dir, "gpu-cluster-topology.txt"), "w") as f:
            f.write(gpu_topology)
        with open(os.path.join(output_dir, "roadmap-timeline.txt"), "w") as f:
            f.write(roadmap)
        print("  Architecture diagrams saved (text)")

    # 2. Capacity Plan
    print("Calculating capacity plan...")
    capacity_plan = calculate_capacity_plan(
        peak_rps=args.peak_rps,
        regions=args.regions,
    )
    capacity_text = format_capacity_plan(capacity_plan)

    if args.format in ("text", "all"):
        with open(os.path.join(output_dir, "capacity-plan.txt"), "w") as f:
            f.write(capacity_text)
        print("  Capacity plan saved (text)")

    if args.format in ("json", "all"):
        with open(os.path.join(output_dir, "capacity-plan.json"), "w") as f:
            json.dump(capacity_plan, f, indent=2)
        print("  Capacity plan saved (json)")

    # 3. Cost Projections
    print("Generating cost projections...")
    cost_projection = calculate_cost_projection(capacity_plan)
    cost_text = format_cost_projection(cost_projection)

    if args.format in ("text", "all"):
        with open(os.path.join(output_dir, "cost-projection.txt"), "w") as f:
            f.write(cost_text)
        print("  Cost projections saved (text)")

    if args.format in ("json", "all"):
        with open(os.path.join(output_dir, "cost-projection.json"), "w") as f:
            json.dump(cost_projection, f, indent=2)
        print("  Cost projections saved (json)")

    print()
    print(f"All files generated in: {output_dir}")
    print()

    # Print summary to stdout
    print(capacity_text)
    print(cost_text)


if __name__ == "__main__":
    main()
