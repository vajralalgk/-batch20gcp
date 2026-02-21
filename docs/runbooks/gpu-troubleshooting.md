# Netflix LLM Platform - GPU Troubleshooting Runbook

**Document ID:** NFLX-LLM-RUNBOOK-002
**Author:** Gopi Krishna Vajrala
**Version:** 1.0.0
**Last Updated:** 2026-02-21
**Audience:** Platform Engineers, SRE Team, ML Infrastructure Team

---

## Table of Contents

1. [GPU OOM Debugging](#1-gpu-oom-debugging)
2. [KV Cache Pressure Resolution](#2-kv-cache-pressure-resolution)
3. [SM Occupancy Optimization](#3-sm-occupancy-optimization)
4. [Temperature Throttling](#4-temperature-throttling)
5. [ECC Error Handling](#5-ecc-error-handling)
6. [NVLink Failures](#6-nvlink-failures)
7. [Triton Inference Server Issues](#7-triton-inference-server-issues)
8. [Common GPU Alerts and Responses](#8-common-gpu-alerts-and-responses)

---

## 1. GPU OOM Debugging

### 1.1 Symptoms

- Triton returns `CUDA out of memory` errors
- Pod restarts with OOMKilled status
- Inference requests fail with HTTP 500 or gRPC RESOURCE_EXHAUSTED
- DCGM metrics show GPU memory utilization > 95%

### 1.2 Diagnosis

```bash
# Step 1: Check GPU memory usage on affected node
NODE=<affected-node-name>
kubectl debug node/$NODE -- nvidia-smi

# Expected output shows memory usage per GPU:
# | GPU | Memory-Usage     |
# |  0  | 72000MiB / 81920MiB |  <-- Near capacity

# Step 2: Check per-process GPU memory allocation
kubectl debug node/$NODE -- nvidia-smi pmon -s m -c 1

# Step 3: Check Triton memory allocation
POD=<affected-pod-name>
kubectl exec -n llm-inference $POD -- curl -sf http://localhost:8001/v2/models/stats | \
  jq '.model_stats[] | {name, version, memory_usage}'

# Step 4: Check KV cache memory consumption
kubectl exec -n llm-inference $POD -- curl -sf http://localhost:8080/metrics | \
  grep -E "kv_cache_size_bytes|kv_cache_max_bytes|kv_cache_utilization"

# Step 5: Check for memory leaks (growing memory over time)
kubectl exec -n llm-inference $POD -- curl -sf http://localhost:8080/metrics | \
  grep "gpu_memory_used_bytes"
# Compare with value from 1 hour ago in Grafana
```

### 1.3 Resolution

**Immediate (relieve pressure):**

```bash
# Option 1: Reduce KV cache allocation
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
# The deployment controller will create a replacement
```

**Root Cause Fixes:**

| Cause | Fix |
|-------|-----|
| KV cache too large | Reduce `kv_cache_free_gpu_mem_fraction` from 0.5 to 0.3 |
| Batch size too high for sequence length | Reduce `max_batch_size` or add sequence length limits |
| Memory fragmentation | Schedule periodic pod restarts (every 24h) |
| Model too large for available GPUs | Increase tensor parallelism (TP=4 -> TP=8) |
| Memory leak in custom preprocessing | Profile with `torch.cuda.memory_stats()`, fix leak |

### 1.4 Memory Layout Reference

```
NVIDIA A100 80GB Memory Budget:
  Model Weights (70B, TP=4, FP16):  ~17 GB
  Activation Memory:                 ~5 GB
  KV Cache (PagedAttention):         ~4 GB (configurable)
  CUDA Kernels + Runtime:            ~1 GB
  Reserved/Fragmentation:            ~3 GB
  ──────────────────────────────────────────
  Total Used:                        ~30 GB
  Available Headroom:                ~50 GB

  WARNING: If total used approaches 75 GB, OOM risk is HIGH.
```

---

## 2. KV Cache Pressure Resolution

### 2.1 Symptoms

- KV cache utilization > 90% (alert: `kv_cache_pressure_high`)
- Increased cache eviction rate
- Higher time-to-first-token (TTFT) due to cache misses
- Cache hit rate dropping below 75%

### 2.2 Diagnosis

```bash
# Check KV cache metrics
POD=<pod-name>
kubectl exec -n llm-inference $POD -- curl -sf http://localhost:8080/metrics | \
  grep -E "kv_cache"

# Key metrics to check:
# kv_cache_utilization_percent   - Should be < 85%
# kv_cache_hit_rate_percent      - Should be > 80%
# kv_cache_eviction_rate_per_sec - Should be < 100/s
# kv_cache_blocks_used           - Compare with max blocks
# kv_cache_blocks_free           - Should have 15%+ free

# Check sequence length distribution (long sequences consume more cache)
kubectl exec -n llm-inference $POD -- curl -sf http://localhost:8080/metrics | \
  grep "inference_sequence_length"
```

### 2.3 Resolution

```bash
# Option 1: Increase KV cache size (if GPU memory allows)
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
  --wait

# Option 4: Scale out (add more GPU nodes to distribute load)
aws eks update-nodegroup-config \
  --cluster-name netflix-llm-us-east-1 \
  --nodegroup-name gpu-p4d-24xlarge \
  --scaling-config desiredSize=$((CURRENT + 1)) \
  --region us-east-1
```

### 2.4 KV Cache Tuning Guide

| Scenario | Recommended Config |
|----------|-------------------|
| Short sequences (< 256 tokens) | `kv_cache_free_gpu_mem_fraction=0.3`, block_size=16 |
| Mixed sequences (256-2048) | `kv_cache_free_gpu_mem_fraction=0.5`, block_size=16 |
| Long sequences (2048-4096) | `kv_cache_free_gpu_mem_fraction=0.6`, block_size=32 |
| High prefix sharing (>80%) | Enable prefix caching, reduce total cache size by 20% |

---

## 3. SM Occupancy Optimization

### 3.1 Symptoms

- GPU utilization below expected levels (< 60% at batch-32+)
- High SM (Streaming Multiprocessor) idle time
- Inference throughput plateaus despite available batch capacity
- DCGM metric `sm_occupancy` below 70%

### 3.2 Diagnosis

```bash
# Check SM occupancy via DCGM
kubectl exec -n monitoring <dcgm-exporter-pod> -- dcgmi dmon -e 1001,1002,1003,1004,1005

# Fields:
# 1001: SM Active (%)
# 1002: SM Occupancy (%)
# 1003: Tensor Core Active (%)
# 1004: DRAM Active (%)
# 1005: PCIe TX Bytes

# Check if the bottleneck is compute or memory bound
kubectl exec -n llm-inference $POD -- curl -sf http://localhost:8080/metrics | \
  grep -E "gpu_sm_active|gpu_memory_bandwidth|tensor_core_utilization"

# Profile a single inference request (requires Nsight access)
# nsys profile --trace=cuda,nvtx,osrt --output=inference_profile ...
```

### 3.3 Resolution

```
Low SM Occupancy Causes and Fixes:

1. Batch size too small
   Symptom: SM occupancy < 40% with batch-1 or batch-4
   Fix: Increase dynamic batching max_queue_delay to accumulate larger batches
   Action:
     helm upgrade ... --set triton.dynamicBatching.maxQueueDelayMs=100

2. Memory bandwidth bottleneck
   Symptom: DRAM Active > 80%, SM Active < 50%
   Fix: Use FP8 quantization (requires H100) or INT8 weight-only quantization
   Action: Rebuild TensorRT-LLM engine with INT8 weights

3. PCIe bottleneck (TP communication)
   Symptom: PCIe utilization high, NVLink utilization low
   Fix: Verify NVLink is active (not falling back to PCIe)
   Action: Check nvidia-smi nvlink --status for all GPUs

4. Kernel launch overhead
   Symptom: Many small kernels with gaps between them
   Fix: Enable CUDA graph capture in TensorRT-LLM
   Action: Set enable_cuda_graph=true in model config

5. Decode phase inefficiency
   Symptom: Low SM occupancy during auto-regressive decode
   Fix: Enable continuous batching to keep SMs busy
   Action: Verify batch_scheduler_policy=max_utilization
```

---

## 4. Temperature Throttling

### 4.1 Symptoms

- GPU clock speed dropping below base clock
- Alert: `gpu_temperature_critical` (> 83C)
- Inference latency increasing without load increase
- DCGM metric `gpu_temp` consistently above 80C

### 4.2 Diagnosis

```bash
# Check GPU temperatures across all GPUs
kubectl debug node/$NODE -- nvidia-smi --query-gpu=index,temperature.gpu,clocks.current.sm,clocks.max.sm,power.draw,power.limit --format=csv

# Example output:
# 0, 78, 1410, 1410, 350, 400  <-- Normal
# 1, 85, 1200, 1410, 380, 400  <-- THROTTLED (clock reduced)
# 2, 72, 1410, 1410, 340, 400  <-- Normal

# Check if thermal throttling is active
kubectl debug node/$NODE -- nvidia-smi --query-gpu=clocks_throttle_reasons.active --format=csv
# Expected: "Not Active" or "None"
# Problem: "SW Thermal Slowdown" or "HW Thermal Slowdown"

# Check power throttling
kubectl debug node/$NODE -- nvidia-smi --query-gpu=clocks_throttle_reasons.sw_power_cap --format=csv
```

### 4.3 Resolution

**Immediate:**

```bash
# Option 1: Reduce power limit (reduces heat output, slight performance impact)
kubectl debug node/$NODE -- nvidia-smi -i <gpu-id> -pl 350
# Reduces from 400W to 350W (~12% performance reduction, significant thermal relief)

# Option 2: Reduce workload on affected GPU
# Cordon the node and gradually drain workloads
kubectl cordon $NODE
kubectl drain $NODE --ignore-daemonsets --delete-emptydir-data --grace-period=120
```

**Root Cause Investigation:**

```
Temperature Throttling Checklist:
  [ ] Check data center ambient temperature (should be < 25C)
  [ ] Verify server fans are operational
  [ ] Check for dust blockage in GPU heatsinks
  [ ] Verify GPU thermal paste is not degraded (> 2 years)
  [ ] Check if adjacent GPUs are creating hot spots
  [ ] Verify NVLink connections (loose cables cause hot spots)
  [ ] Check EFA adapter heat (can warm nearby GPUs)

  Action: Open ticket with AWS support for hardware inspection
  Severity: P2 (degraded performance, not outage)
```

---

## 5. ECC Error Handling

### 5.1 Symptoms

- DCGM alert: `gpu_ecc_errors_detected`
- Inference results contain incorrect data (silent corruption)
- GPU marked as unhealthy by Kubernetes
- nvidia-smi shows non-zero ECC error counts

### 5.2 Diagnosis

```bash
# Check ECC error counts
kubectl debug node/$NODE -- nvidia-smi --query-gpu=index,ecc.errors.corrected.volatile.total,ecc.errors.uncorrectable.volatile.total,ecc.errors.corrected.aggregate.total,ecc.errors.uncorrectable.aggregate.total --format=csv

# Fields:
# corrected.volatile    - Corrected errors since last reset (normal if < 10)
# uncorrectable.volatile - Uncorrectable errors since reset (CRITICAL if > 0)
# corrected.aggregate   - Total corrected errors lifetime
# uncorrectable.aggregate - Total uncorrectable errors lifetime (may indicate failing GPU)

# Check specific memory location of errors
kubectl debug node/$NODE -- nvidia-smi --query-gpu=ecc.errors.corrected.volatile.sram,ecc.errors.corrected.volatile.dram --format=csv
```

### 5.3 Resolution

```
ECC Error Severity Matrix:

Corrected Errors (single-bit):
  Count < 10 (volatile): NORMAL - Monitor, no action needed
  Count 10-100 (volatile): WARNING - Increase monitoring frequency
  Count > 100 (volatile): CONCERN - Schedule GPU replacement in next window
  Count > 1000 (aggregate): REPLACE - GPU memory is degrading

Uncorrectable Errors (double-bit):
  Count > 0: CRITICAL - Immediate action required
```

**For uncorrectable ECC errors:**

```bash
# Step 1: Cordon and drain the affected node immediately
kubectl cordon $NODE
kubectl drain $NODE --ignore-daemonsets --delete-emptydir-data --grace-period=60

# Step 2: Attempt GPU reset
kubectl debug node/$NODE -- nvidia-smi --gpu-reset -i <gpu-id>

# Step 3: Clear volatile ECC counters
kubectl debug node/$NODE -- nvidia-smi --reset-ecc-errors=volatile -i <gpu-id>

# Step 4: Run GPU diagnostics
kubectl debug node/$NODE -- nvidia-smi -q -d ECC

# Step 5: If errors persist after reset, request node replacement
# Open AWS support case for hardware replacement
# Reference: AWS EC2 GPU instance hardware issue

# Step 6: Provision replacement node
aws eks update-nodegroup-config \
  --cluster-name netflix-llm-us-east-1 \
  --nodegroup-name gpu-p4d-24xlarge \
  --scaling-config desiredSize=$((CURRENT + 1)) \
  --region us-east-1

# Step 7: Once replacement is ready, terminate the faulty node
aws ec2 terminate-instances --instance-ids <faulty-instance-id> --region us-east-1
```

### 5.4 Preventive Measures

```bash
# Enable ECC monitoring in DCGM (should already be configured)
# dcgmi policy --set 1,1 -e  # Enable ECC error policy

# Add ECC error alerts to Prometheus
# Alert: gpu_ecc_uncorrectable_errors > 0 for 1 minute -> PagerDuty P1
# Alert: gpu_ecc_corrected_errors > 100 per hour -> Slack warning
```

---

## 6. NVLink Failures

### 6.1 Symptoms

- Tensor parallelism performance degradation (2-5x slower all-reduce)
- DCGM alert: `nvlink_bandwidth_degraded`
- nvidia-smi shows NVLink errors or inactive links
- Inference latency spikes during batch operations

### 6.2 Diagnosis

```bash
# Check NVLink status for all GPUs
kubectl debug node/$NODE -- nvidia-smi nvlink --status

# Check NVLink bandwidth utilization
kubectl debug node/$NODE -- nvidia-smi nvlink -gt d  # Data throughput
kubectl debug node/$NODE -- nvidia-smi nvlink -gt r  # Raw throughput

# Check for NVLink errors
kubectl debug node/$NODE -- nvidia-smi nvlink -e

# Verify GPU-to-GPU topology
kubectl debug node/$NODE -- nvidia-smi topo -m
# Expected: NV12 between GPUs in same TP group
# Problem: If showing PHB or SYS instead of NV12
```

### 6.3 Resolution

```bash
# Step 1: If NVLink is degraded but functional
# Monitor and schedule replacement in next maintenance window
# The system will fall back to PCIe for affected links (slower but functional)

# Step 2: If NVLink is completely failed
# Cordon the node to prevent new scheduling
kubectl cordon $NODE

# Step 3: Verify TP can still function with PCIe fallback
# Performance will be degraded ~2-3x for TP communication
# This is acceptable short-term

# Step 4: Schedule node replacement
# Same procedure as ECC error handling (Section 5.3, Step 5-7)
```

---

## 7. Triton Inference Server Issues

### 7.1 Model Loading Failures

```bash
# Check Triton logs for loading errors
kubectl logs -n llm-inference $POD --container triton-inference-server --tail=100 | \
  grep -E "ERROR|FAIL|model.*load"

# Common causes:
# 1. Incorrect model repository path
# 2. TensorRT engine incompatible with GPU architecture
# 3. Insufficient GPU memory for model weights
# 4. CUDA driver version mismatch

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

# If batch sizes are consistently at 1:
# - Check if max_queue_delay is too low
# - Check if traffic is too low for batching
# - Check if requests have incompatible sequence lengths

# If queue is growing unbounded:
# - GPU inference is slower than request arrival rate
# - Scale out (add more GPU nodes)
# - Reduce max_batch_size to process batches faster
```

---

## 8. Common GPU Alerts and Responses

| Alert | Severity | Response |
|-------|----------|----------|
| `gpu_memory_utilization > 95%` | P2 | Follow Section 1 (OOM Debugging) |
| `gpu_temperature > 83C` | P2 | Follow Section 4 (Temperature Throttling) |
| `gpu_ecc_uncorrectable > 0` | P1 | Follow Section 5 (ECC Error Handling), immediate cordon |
| `kv_cache_pressure > 90%` | P2 | Follow Section 2 (KV Cache Pressure) |
| `gpu_sm_occupancy < 40%` | P3 | Follow Section 3 (SM Occupancy), investigate batching |
| `nvlink_errors > 0` | P2 | Follow Section 6 (NVLink Failures) |
| `triton_model_load_failed` | P1 | Follow Section 7.1, check model repository |
| `gpu_power_usage > 380W` | P3 | Monitor, may lead to thermal throttling |
| `gpu_fan_speed > 90%` | P3 | Monitor, indicates thermal stress |
| `inference_latency_p99 > 100ms` | P1 | Check GPU metrics, batching, KV cache |

---

**Document Revision History:**

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | 2026-02-21 | Gopi Krishna Vajrala | Initial GPU troubleshooting runbook |
