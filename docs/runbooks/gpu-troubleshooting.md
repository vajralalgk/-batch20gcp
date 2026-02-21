# Netflix LLM Platform - GPU Troubleshooting Runbook

**Document ID:** NFLX-LLM-RUNBOOK-002
**Author:** Gopi Krishna Vajrala
**Version:** 2.0.0
**Last Updated:** 2026-02-21
**Audience:** Platform Engineers, SRE Team, ML Infrastructure Team

---

## Table of Contents

1. [GPU OOM Debugging](#1-gpu-oom-debugging)
2. [KV Cache Pressure Resolution](#2-kv-cache-pressure-resolution)
3. [SM Occupancy Optimization](#3-sm-occupancy-optimization)
4. [Temperature Throttling Handling](#4-temperature-throttling-handling)
5. [ECC Error Handling](#5-ecc-error-handling)
6. [NVLink Failures](#6-nvlink-failures)
7. [Triton Inference Server Issues](#7-triton-inference-server-issues)
8. [Common GPU Alerts and Responses](#8-common-gpu-alerts-and-responses)
9. [Escalation Matrix](#9-escalation-matrix)

---

## 1. GPU OOM Debugging

### 1.1 Symptoms

- Triton returns `CUDA out of memory` errors in pod logs
- Pod restarts with OOMKilled status (check `kubectl describe pod`)
- Inference requests fail with HTTP 500 or gRPC `RESOURCE_EXHAUSTED`
- DCGM metrics show GPU memory utilization consistently > 95%
- Prometheus alert: `gpu_memory_utilization_critical` firing

### 1.2 Immediate Triage (First 5 Minutes)

```bash
# Step 1: Identify which GPUs are under memory pressure
NODE=<affected-node-name>
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- nvidia-smi

# Expected output shows memory usage per GPU:
# | GPU | Memory-Usage            |
# |  0  | 72000MiB / 81920MiB    |  <-- Near capacity (88%)
# |  1  | 78500MiB / 81920MiB    |  <-- CRITICAL (96%)

# Step 2: Check per-process GPU memory allocation
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- nvidia-smi pmon -s m -c 1

# Step 3: Check Triton model memory allocation
POD=<affected-pod-name>
kubectl exec -n llm-inference $POD -- curl -sf http://localhost:8001/v2/models/stats | \
  jq '.model_stats[] | {name, version, memory_usage}'

# Step 4: Check KV cache memory consumption
kubectl exec -n llm-inference $POD -- curl -sf http://localhost:8080/metrics | \
  grep -E "kv_cache_size_bytes|kv_cache_max_bytes|kv_cache_utilization"

# Step 5: Check for memory fragmentation or leaks (compare with 1hr ago in Grafana)
kubectl exec -n llm-inference $POD -- curl -sf http://localhost:8080/metrics | \
  grep "gpu_memory_used_bytes"
```

### 1.3 Root Cause Analysis

```
OOM Decision Tree:

  Is GPU memory > 95%?
  ├── YES: Was there a sudden spike?
  │   ├── YES: Likely batch size spike or long-sequence burst
  │   │   └── Check: max sequence length in recent requests
  │   │       Action: Reduce max_batch_size or add sequence length limits
  │   └── NO: Gradual increase over hours/days?
  │       ├── YES: Memory leak or fragmentation
  │       │   └── Action: Schedule pod restart, profile with torch.cuda.memory_stats()
  │       └── NO: Consistent high usage since deploy?
  │           └── Model too large for GPU memory budget
  │               Action: Increase TP degree (TP=4 -> TP=8) or enable INT8
  └── NO: OOM during startup?
      └── Model loading exceeds available memory
          Action: Check model size vs GPU memory, verify TP config
```

### 1.4 Resolution Procedures

**Immediate Relief (Stop the Bleeding):**

```bash
# Option 1: Reduce KV cache allocation (fastest, no restart needed)
kubectl exec -n llm-inference $POD -- \
  curl -sf -X POST http://localhost:8001/v2/models/netflix_llm_70b/config \
  -d '{"parameters":{"kv_cache_free_gpu_mem_fraction":"0.3"}}'

# Option 2: Reduce max batch size temporarily
helm upgrade netflix-llm-platform deploy/helm/netflix-llm-platform \
  --namespace llm-inference \
  --reuse-values \
  --set triton.dynamicBatching.maxBatchSize=32 \
  --wait

# Option 3: Restart the affected pod (clears fragmented memory)
kubectl delete pod $POD -n llm-inference
# The deployment controller will create a replacement with fresh memory allocation

# Option 4: Emergency - Cordon and drain if OOM is affecting entire node
kubectl cordon $NODE
kubectl drain $NODE --ignore-daemonsets --delete-emptydir-data --grace-period=120
```

**Root Cause Fixes (Permanent Solutions):**

| Root Cause | Diagnostic Signal | Fix | Timeline |
|------------|-------------------|-----|----------|
| KV cache too large | `kv_cache_utilization > 90%` | Reduce `kv_cache_free_gpu_mem_fraction` from 0.5 to 0.3 | Immediate |
| Batch size too high for sequence length | Large batches with long sequences | Add sequence-length-aware batch limits | 1-2 days |
| Memory fragmentation | Gradual growth over 24+ hours | Schedule periodic pod restarts (every 24h) via CronJob | 1 hour |
| Model too large for available GPUs | OOM during model load | Increase tensor parallelism (TP=4 -> TP=8) | 4 hours |
| Memory leak in custom preprocessing | monotonic growth in `torch.cuda.memory_allocated()` | Profile with `torch.cuda.memory_stats()`, fix leak source | 1-3 days |

### 1.5 Memory Layout Reference (NVIDIA A100 80GB)

```
NVIDIA A100 80GB Memory Budget (70B Model, TP=4, INT8):

  Component                           Per-GPU     Notes
  ─────────────────────────────────   ─────────   ──────────────────────
  Model Weights (70B, TP=4, INT8):    ~9.5 GB     70B params / 4 GPUs, INT8
  Activation Memory:                  ~5.0 GB     Scales with batch size
  KV Cache (PagedAttention):          ~35.0 GB    Configurable, main knob
  CUDA Kernels + Runtime:             ~1.5 GB     Fixed overhead
  TensorRT-LLM Engine Buffers:       ~2.0 GB     Workspace memory
  Reserved / Fragmentation:           ~3.0 GB     Always reserved
  ──────────────────────────────────────────────────────────────────────
  Total Used (typical):               ~56.0 GB
  Available Headroom:                 ~24.0 GB

  THRESHOLDS:
    GREEN:   < 65 GB used (< 81%)   - Normal operation
    YELLOW:  65-72 GB used (81-90%) - Monitor closely
    RED:     72-78 GB used (90-95%) - Reduce batch size or KV cache
    CRITICAL: > 78 GB used (> 95%)  - OOM imminent, take action NOW
```

### 1.6 Post-Incident Checklist

```
[ ] Root cause identified and documented
[ ] Memory metrics baselined (before vs after fix)
[ ] Prometheus alert thresholds validated
[ ] Grafana dashboard updated if new failure mode discovered
[ ] Runbook updated if new debugging step discovered
[ ] Incident review scheduled if P1/P2
```

---

## 2. KV Cache Pressure Resolution

### 2.1 Symptoms

- KV cache utilization consistently > 90% (alert: `kv_cache_pressure_high`)
- Increased cache eviction rate (metric: `kv_cache_eviction_rate_per_sec > 100`)
- Higher time-to-first-token (TTFT) due to cache misses
- Cache hit rate dropping below 75% (metric: `kv_cache_hit_rate_percent`)
- Increased p99 latency without corresponding traffic increase

### 2.2 Diagnosis

```bash
# Step 1: Check KV cache metrics
POD=<pod-name>
kubectl exec -n llm-inference $POD -- curl -sf http://localhost:8080/metrics | \
  grep -E "kv_cache"

# Key metrics to evaluate:
# kv_cache_utilization_percent   - Should be < 85%
# kv_cache_hit_rate_percent      - Should be > 80%
# kv_cache_eviction_rate_per_sec - Should be < 100/s
# kv_cache_blocks_used           - Compare with max blocks
# kv_cache_blocks_free           - Should have 15%+ free
# kv_cache_prefix_hit_rate       - Measures prefix sharing effectiveness

# Step 2: Check sequence length distribution (long sequences consume more cache)
kubectl exec -n llm-inference $POD -- curl -sf http://localhost:8080/metrics | \
  grep "inference_sequence_length"

# Step 3: Check if prefix caching is working
kubectl exec -n llm-inference $POD -- curl -sf http://localhost:8080/metrics | \
  grep "prefix_cache"

# Step 4: Analyze traffic patterns for cache-hostile workloads
# Look for: high unique prefix ratio, very long sequences, burst patterns
kubectl exec -n llm-inference $POD -- curl -sf http://localhost:8080/metrics | \
  grep -E "request_prompt_length_bucket|unique_prefix_ratio"
```

### 2.3 Resolution

**Immediate Actions:**

```bash
# Option 1: Increase KV cache size (if GPU memory allows - check Section 1.5)
helm upgrade netflix-llm-platform deploy/helm/netflix-llm-platform \
  --namespace llm-inference \
  --reuse-values \
  --set kvCache.maxSizeGb=40 \
  --set triton.parameters.kv_cache_free_gpu_mem_fraction=0.6 \
  --wait

# Option 2: Reduce max sequence length (limits per-request cache usage)
helm upgrade netflix-llm-platform deploy/helm/netflix-llm-platform \
  --namespace llm-inference \
  --reuse-values \
  --set triton.maxSequenceLength=2048 \
  --wait

# Option 3: Enable more aggressive prefix caching
helm upgrade netflix-llm-platform deploy/helm/netflix-llm-platform \
  --namespace llm-inference \
  --reuse-values \
  --set kvCache.prefixCaching.enabled=true \
  --set kvCache.prefixCaching.minPrefixLength=64 \
  --set kvCache.prefixCaching.maxPrefixTokens=512 \
  --wait

# Option 4: Scale out (add more GPU nodes to distribute cache load)
aws eks update-nodegroup-config \
  --cluster-name netflix-llm-us-east-1 \
  --nodegroup-name gpu-p4d-24xlarge \
  --scaling-config desiredSize=$((CURRENT + 1)) \
  --region us-east-1
```

### 2.4 KV Cache Tuning Guide

| Scenario | Cache Fraction | Block Size | Prefix Caching | Expected Hit Rate |
|----------|---------------|-----------|----------------|-------------------|
| Short sequences (< 256 tokens) | 0.3 | 16 | Optional | 70-80% |
| Mixed sequences (256-2048 tokens) | 0.5 | 16 | Recommended | 75-85% |
| Long sequences (2048-4096 tokens) | 0.6 | 32 | Required | 80-90% |
| High prefix sharing (> 80% shared) | 0.4 | 16 | Required | 85-95% |
| Unique prompts (low sharing) | 0.5 | 16 | Minimal benefit | 60-70% |

### 2.5 PagedAttention Configuration

```
PagedAttention Block Layout:

  Each block = 16 tokens of KV state for all attention heads
  Block size in bytes = 16 * 2 * num_heads * head_dim * sizeof(dtype)

  For 70B model (TP=4):
    num_heads_per_gpu = 16 (64 total / 4 TP)
    head_dim = 128
    block_size = 16 * 2 * 16 * 128 * 2 bytes (FP16) = 131,072 bytes = 128 KB

  With 35 GB allocated to KV cache:
    max_blocks = 35 * 1024 * 1024 / 128 = 286,720 blocks
    max_tokens_cached = 286,720 * 16 = 4,587,520 tokens

  At avg 200 tokens/request:
    max_concurrent_requests ~= 22,937

  TUNING: If concurrent requests exceed this, either:
    1. Increase cache allocation (if GPU memory allows)
    2. Reduce max sequence length
    3. Scale out to more nodes
```

---

## 3. SM Occupancy Optimization

### 3.1 Symptoms

- GPU utilization below expected levels (< 60% at batch-32 or above)
- High SM (Streaming Multiprocessor) idle time visible in DCGM
- Inference throughput plateaus despite available batch capacity
- DCGM metric `sm_occupancy` below 70%
- Tensor Core utilization below 50% during inference

### 3.2 Diagnosis

```bash
# Step 1: Check SM occupancy via DCGM exporter
kubectl exec -n monitoring <dcgm-exporter-pod> -- dcgmi dmon -e 1001,1002,1003,1004,1005

# Fields:
# 1001: SM Active (%)        - Percentage of time at least one warp is active
# 1002: SM Occupancy (%)     - Ratio of active warps to max warps
# 1003: Tensor Core Active (%) - Time tensor cores are computing
# 1004: DRAM Active (%)      - Memory bandwidth utilization
# 1005: PCIe TX Bytes        - Data transfer to/from host

# Step 2: Identify bottleneck type
kubectl exec -n llm-inference $POD -- curl -sf http://localhost:8080/metrics | \
  grep -E "gpu_sm_active|gpu_memory_bandwidth|tensor_core_utilization"

# Step 3: Check batch formation effectiveness
kubectl exec -n llm-inference $POD -- curl -sf http://localhost:8001/metrics | \
  grep -E "batch_size|queue_duration|pending_request_count"

# Step 4: Check for CUDA graph usage (reduces kernel launch overhead)
kubectl logs -n llm-inference $POD --container triton-inference-server --tail=200 | \
  grep -i "cuda graph"
```

### 3.3 Resolution by Root Cause

```
Root Cause 1: Batch Size Too Small
─────────────────────────────────────
  Symptom:    SM occupancy < 40% with batch-1 or batch-4
  Diagnostic: Check queue depth (should be > 0 if traffic is sufficient)
  Fix:        Increase max_queue_delay to accumulate larger batches
  Command:    helm upgrade ... --set triton.dynamicBatching.maxQueueDelayMs=100
  Impact:     +10-15ms p99 latency, but 2-4x throughput improvement
  Tradeoff:   Latency vs throughput - acceptable for background requests

Root Cause 2: Memory Bandwidth Bottleneck
─────────────────────────────────────
  Symptom:    DRAM Active > 80%, SM Active < 50%
  Diagnostic: Model is memory-bound (large model weights, insufficient compute)
  Fix:        Use INT8 quantization to reduce memory traffic
  Command:    Rebuild TensorRT-LLM engine with --use_weight_only --weight_only_precision int8
  Impact:     25-40% throughput improvement, <0.5% quality degradation
  Alternative: Upgrade to H100 (3x memory bandwidth improvement)

Root Cause 3: PCIe Bottleneck (TP Communication)
─────────────────────────────────────
  Symptom:    PCIe utilization high, NVLink utilization low
  Diagnostic: TP all-reduce falling back to PCIe instead of NVLink
  Fix:        Verify NVLink is active between all GPU pairs
  Command:    nvidia-smi nvlink --status (should show Active for all links)
  Impact:     2-3x improvement in TP communication latency
  Escalation: If NVLink shows Inactive, see Section 6 (NVLink Failures)

Root Cause 4: Kernel Launch Overhead
─────────────────────────────────────
  Symptom:    Many small kernels with idle gaps between them
  Diagnostic: High kernel launch count per inference step
  Fix:        Enable CUDA graph capture in TensorRT-LLM
  Command:    Set enable_cuda_graph=true in model config
  Impact:     10-20% latency reduction for decode phase
  Note:       CUDA graphs require fixed batch sizes per graph

Root Cause 5: Decode Phase Inefficiency
─────────────────────────────────────
  Symptom:    Low SM occupancy specifically during auto-regressive decode
  Diagnostic: Decode is inherently memory-bound (1 token at a time per seq)
  Fix:        Enable continuous batching to overlap prefill and decode
  Command:    Verify batch_scheduler_policy=max_utilization in Triton config
  Impact:     30-50% throughput improvement by pipelining prefill + decode
```

### 3.4 SM Occupancy Benchmarks

| Batch Size | Expected SM Occupancy | Expected GPU Util | Action if Below |
|------------|----------------------|-------------------|-----------------|
| 1 | 15-25% | 20-30% | Increase batching delay |
| 8 | 40-55% | 45-60% | Check TP communication |
| 32 | 65-75% | 70-80% | Verify CUDA graphs enabled |
| 64 | 75-85% | 80-90% | Near optimal, check memory BW |
| 128 | 80-90% | 85-95% | At maximum, may need to reduce |

---

## 4. Temperature Throttling Handling

### 4.1 Symptoms

- GPU clock speed dropping below base clock (visible in `nvidia-smi`)
- Alert: `gpu_temperature_critical` firing (threshold: > 83C)
- Inference latency increasing without corresponding load increase
- DCGM metric `gpu_temp` consistently above 80C
- Power draw approaching or exceeding TDP limit

### 4.2 Diagnosis

```bash
# Step 1: Check GPU temperatures and clock speeds
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi --query-gpu=index,temperature.gpu,clocks.current.sm,clocks.max.sm,power.draw,power.limit \
  --format=csv

# Example output (healthy vs throttled):
# 0, 72, 1410, 1410, 340, 400  <-- HEALTHY (full clock speed)
# 1, 85, 1200, 1410, 380, 400  <-- THROTTLED (clock reduced 15%)
# 2, 78, 1410, 1410, 350, 400  <-- HEALTHY
# 3, 88, 1050, 1410, 390, 400  <-- SEVERELY THROTTLED (clock reduced 25%)

# Step 2: Check if thermal throttling is the cause
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi --query-gpu=clocks_throttle_reasons.active --format=csv
# Good: "Not Active" or "None"
# Bad:  "SW Thermal Slowdown" or "HW Thermal Slowdown"

# Step 3: Check power throttling separately
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi --query-gpu=clocks_throttle_reasons.sw_power_cap --format=csv

# Step 4: Check fan speeds (if applicable)
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi --query-gpu=fan.speed --format=csv

# Step 5: Check historical temperature trend in Grafana
# Dashboard: GPU Fleet Health -> Temperature Heatmap
# Look for: gradual temperature increase over days/weeks
```

### 4.3 Resolution

**Immediate Actions:**

```bash
# Option 1: Reduce power limit (reduces heat, slight performance impact)
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi -i <gpu-id> -pl 350
# Impact: ~12% performance reduction, significant thermal relief
# Reduces from 400W TDP to 350W

# Option 2: Reduce workload on affected GPU by cordoning the node
kubectl cordon $NODE
kubectl drain $NODE --ignore-daemonsets --delete-emptydir-data --grace-period=120
# Allow node to cool down, then uncordon after 10-15 minutes

# Option 3: If single GPU is affected, redistribute workload
# Modify Triton config to exclude the hot GPU from inference
kubectl exec -n llm-inference $POD -- \
  curl -sf -X POST http://localhost:8001/v2/models/netflix_llm_70b/config \
  -d '{"parameters":{"exclude_gpu_ids":"1"}}'
```

**Root Cause Investigation Checklist:**

```
Temperature Throttling Investigation:
  [ ] Check data center ambient temperature (should be < 25C / 77F)
  [ ] Verify server fans are operational and at expected RPM
  [ ] Check for dust blockage in GPU heatsinks (requires physical inspection)
  [ ] Verify GPU thermal paste is not degraded (> 2 years since application)
  [ ] Check if adjacent GPUs are creating thermal hot spots
  [ ] Verify NVLink cables are properly seated (loose cables cause hot spots)
  [ ] Check EFA adapter heat (can warm nearby GPU slots)
  [ ] Review workload intensity (sustained 100% utilization expected to be hot)

  If Physical Issue Suspected:
    Action: Open AWS support case for hardware inspection
    Severity: P2 (degraded performance, not outage)
    Template: "GPU thermal throttling on instance {instance-id}, GPU {gpu-id}"
```

### 4.4 Temperature Thresholds

| Temperature | Status | Clock Impact | Action |
|-------------|--------|-------------|--------|
| < 70C | GREEN | None | Normal operation |
| 70-75C | YELLOW | None | Monitor, check trend |
| 75-80C | ORANGE | 0-5% reduction | Investigate cooling |
| 80-83C | RED | 5-15% reduction | Reduce power limit |
| 83-90C | CRITICAL | 15-30% reduction | Cordon node, investigate |
| > 90C | EMERGENCY | > 30% or shutdown | Immediate drain, AWS ticket |

---

## 5. ECC Error Handling

### 5.1 Symptoms

- DCGM alert: `gpu_ecc_errors_detected` firing
- Inference results may contain incorrect data (silent data corruption risk)
- GPU marked as unhealthy by Kubernetes device plugin
- `nvidia-smi` shows non-zero ECC error counts
- Triton reports model inference errors with inconsistent outputs

### 5.2 Diagnosis

```bash
# Step 1: Check ECC error counts (volatile = since last reboot, aggregate = lifetime)
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi --query-gpu=index,ecc.errors.corrected.volatile.total,ecc.errors.uncorrectable.volatile.total,ecc.errors.corrected.aggregate.total,ecc.errors.uncorrectable.aggregate.total \
  --format=csv

# Output columns:
# corrected.volatile    - Single-bit errors corrected since last reboot (usually harmless)
# uncorrectable.volatile - Double-bit errors since reboot (CRITICAL - data corruption risk)
# corrected.aggregate   - Lifetime corrected errors (trend indicator)
# uncorrectable.aggregate - Lifetime uncorrectable errors (GPU health indicator)

# Step 2: Check which memory type has errors (SRAM vs DRAM)
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi --query-gpu=ecc.errors.corrected.volatile.sram,ecc.errors.corrected.volatile.dram \
  --format=csv
# SRAM errors: Compute/cache errors (more concerning)
# DRAM errors: HBM errors (more common, less immediately concerning if correctable)

# Step 3: Full ECC diagnostic report
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi -q -d ECC

# Step 4: Check retired pages (indicates persistent hardware issues)
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi --query-gpu=retired_pages.single_bit_ecc.count,retired_pages.double_bit.count \
  --format=csv
# High retired page count = failing GPU memory, plan replacement
```

### 5.3 Severity Classification and Response

```
ECC Error Severity Matrix:

  CORRECTABLE ERRORS (Single-Bit, Automatically Fixed):
  ──────────────────────────────────────────────────────
  Count < 10 (volatile):     NORMAL
    Action: No action needed, log for trending
    Monitor: Standard DCGM collection interval

  Count 10-100 (volatile):   WARNING
    Action: Increase monitoring to 1-minute intervals
    Monitor: Track rate of increase over 24 hours
    Note: If rate is stable, may be acceptable for GPU lifetime

  Count > 100 (volatile):    CONCERN
    Action: Schedule GPU node replacement in next maintenance window
    Monitor: Daily review by on-call engineer
    Timeline: Replace within 7 days

  Count > 1000 (aggregate):  REPLACE
    Action: GPU memory is degrading, plan proactive replacement
    Monitor: Track daily to ensure no uncorrectable errors emerge
    Timeline: Replace within 3 days

  UNCORRECTABLE ERRORS (Double-Bit, DATA CORRUPTION RISK):
  ──────────────────────────────────────────────────────
  Count > 0:                  CRITICAL - IMMEDIATE ACTION REQUIRED
    Action: See Section 5.4 Emergency Procedure
    Impact: Inference results may be WRONG (silent corruption)
    Timeline: Resolve within 1 hour
```

### 5.4 Emergency Procedure for Uncorrectable ECC Errors

```bash
# CRITICAL: Uncorrectable ECC errors mean inference results may be INCORRECT.
# Silent data corruption is worse than downtime. Act immediately.

# Step 1: IMMEDIATELY cordon and drain the affected node
kubectl cordon $NODE
kubectl drain $NODE --ignore-daemonsets --delete-emptydir-data --grace-period=60

# Step 2: Attempt GPU reset (may clear transient errors)
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi --gpu-reset -i <gpu-id>

# Step 3: Clear volatile ECC counters after reset
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi --reset-ecc-errors=volatile -i <gpu-id>

# Step 4: Run GPU diagnostics to determine if error persists
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi -q -d ECC

# Step 5: If errors persist after reset --> GPU is failing
# Do NOT put this node back into service

# Step 6: Provision replacement node
aws eks update-nodegroup-config \
  --cluster-name netflix-llm-us-east-1 \
  --nodegroup-name gpu-p4d-24xlarge \
  --scaling-config desiredSize=$((CURRENT + 1)) \
  --region us-east-1

# Step 7: Once replacement is serving traffic, terminate the faulty instance
aws ec2 terminate-instances --instance-ids <faulty-instance-id> --region us-east-1

# Step 8: Open AWS support case for hardware credit
# Reference: AWS EC2 GPU instance hardware issue
# Include: instance-id, GPU index, ECC error counts, nvidia-smi output
```

### 5.5 Preventive Monitoring Configuration

```bash
# DCGM ECC monitoring should be active on all GPU nodes

# Prometheus alerting rules (in prometheus-rules.yaml):
#
# Alert: Uncorrectable ECC errors detected (P1 - PagerDuty)
# - alert: GPUECCUncorrectableErrors
#   expr: dcgm_ecc_errors_uncorrectable_total > 0
#   for: 1m
#   labels:
#     severity: critical
#   annotations:
#     summary: "Uncorrectable ECC errors on GPU {{ $labels.gpu }}"
#
# Alert: High corrected ECC error rate (P3 - Slack)
# - alert: GPUECCCorrectedErrorsHigh
#   expr: rate(dcgm_ecc_errors_corrected_total[1h]) > 100
#   for: 15m
#   labels:
#     severity: warning
#
# Alert: Retired pages increasing (P2 - Slack + Ticket)
# - alert: GPURetiredPagesIncreasing
#   expr: dcgm_retired_pages_total > 10
#   labels:
#     severity: warning
```

---

## 6. NVLink Failures

### 6.1 Symptoms

- Tensor parallelism performance degradation (2-5x slower all-reduce operations)
- DCGM alert: `nvlink_bandwidth_degraded`
- `nvidia-smi` shows NVLink errors or inactive links
- Inference latency spikes specifically during batch operations (all-reduce phase)
- GPU topology shows PHB or SYS instead of NV12 connectivity

### 6.2 Diagnosis

```bash
# Step 1: Check NVLink status for all GPUs
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi nvlink --status

# Step 2: Check NVLink throughput
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi nvlink -gt d  # Data throughput
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi nvlink -gt r  # Raw throughput

# Step 3: Check for NVLink errors
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi nvlink -e

# Step 4: Verify GPU-to-GPU topology
kubectl debug node/$NODE -it --image=nvidia/cuda:12.2.0-base-ubuntu22.04 -- \
  nvidia-smi topo -m
# Expected: NV12 between GPUs in same TP group
# Problem: PHB (PCIe bridge) or SYS (system bus) instead of NV12
```

### 6.3 Resolution

```bash
# If NVLink is degraded but partially functional:
# 1. Monitor and schedule replacement in next maintenance window
# 2. System will fallback to PCIe for affected links (2-3x slower for TP comm)

# If NVLink is completely failed for a TP group:
kubectl cordon $NODE
# Performance will be severely degraded for tensor parallelism
# Schedule node replacement within 24 hours

# Replacement procedure is same as ECC errors (Section 5.4, Steps 6-7)
```

---

## 7. Triton Inference Server Issues

### 7.1 Model Loading Failures

```bash
# Check Triton logs for loading errors
kubectl logs -n llm-inference $POD --container triton-inference-server --tail=100 | \
  grep -E "ERROR|FAIL|model.*load"

# Common causes and fixes:
# 1. Incorrect model repository path -> Verify /models/ mount
# 2. TensorRT engine incompatible with GPU arch -> Rebuild engine for sm_80 (A100)
# 3. Insufficient GPU memory -> See Section 1 (OOM Debugging)
# 4. CUDA driver mismatch -> Check driver version matches container expectation

# Verify model repository
kubectl exec -n llm-inference $POD -- ls -la /models/netflix_llm_70b/

# Force model reload
kubectl exec -n llm-inference $POD -- \
  curl -sf -X POST http://localhost:8001/v2/repository/models/netflix_llm_70b/load
```

### 7.2 Dynamic Batching Issues

```bash
# Check batch formation metrics
kubectl exec -n llm-inference $POD -- curl -sf http://localhost:8001/metrics | \
  grep -E "batch_size|queue_duration|pending_request_count"

# If batches are consistently size 1:
# - max_queue_delay may be too low (increase to 50-100ms)
# - Traffic may be too low for effective batching
# - Sequence lengths may be too variable for same-batch grouping

# If queue is growing unbounded:
# - Inference is slower than request arrival rate
# - Scale out (add more GPU nodes)
# - Or reduce max_batch_size to process smaller batches faster
```

---

## 8. Common GPU Alerts and Responses

| Alert | Severity | Threshold | Response |
|-------|----------|-----------|----------|
| `gpu_memory_utilization > 95%` | P2 | 95% for 2 min | Follow Section 1 (OOM Debugging) |
| `gpu_temperature > 83C` | P2 | 83C for 5 min | Follow Section 4 (Temperature) |
| `gpu_ecc_uncorrectable > 0` | P1 | Any occurrence | Follow Section 5 (ECC) - immediate cordon |
| `kv_cache_pressure > 90%` | P2 | 90% for 5 min | Follow Section 2 (KV Cache) |
| `gpu_sm_occupancy < 40%` | P3 | 40% for 15 min | Follow Section 3 (SM Occupancy) |
| `nvlink_errors > 0` | P2 | Any occurrence | Follow Section 6 (NVLink) |
| `triton_model_load_failed` | P1 | Any occurrence | Follow Section 7.1 |
| `gpu_power_usage > 380W` | P3 | 380W for 10 min | Monitor, may lead to throttling |
| `inference_latency_p99 > 150ms` | P1 | 150ms for 3 min | Check GPU metrics, batching, KV cache |
| `gpu_utilization < 20%` | P3 | 20% for 30 min | Check if traffic is reaching GPU nodes |

---

## 9. Escalation Matrix

| Level | Contact | When |
|-------|---------|------|
| L1: On-Call SRE | PagerDuty rotation | All GPU alerts |
| L2: ML Platform Team | #ml-platform-oncall Slack | OOM not resolved in 15 min |
| L3: NVIDIA Support | Enterprise support portal | ECC errors, NVLink failures |
| L4: AWS Support | AWS support case | Instance-level hardware issues |
| L5: Engineering Leadership | VP Engineering | >15 min error budget consumed in single incident |

---

**Document Revision History:**

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | 2026-02-21 | Gopi Krishna Vajrala | Initial GPU troubleshooting runbook |
| 2.0.0 | 2026-02-21 | Gopi Krishna Vajrala | Added escalation matrix, expanded OOM decision tree, detailed ECC severity matrix |
