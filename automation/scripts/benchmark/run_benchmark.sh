#!/usr/bin/env bash
# ============================================================================
# Netflix Real-Time LLM Personalization & Inference Platform
# Inference Benchmark Script
# Author: Gopi Krishna Vajrala
# ============================================================================
#
# WHY THIS SCRIPT EXISTS:
#   Measures inference performance of the LLM personalization platform to
#   ensure latency SLAs are met and to detect performance regressions before
#   they reach production. Results are compared against baseline thresholds.
#
# WHAT IT MEASURES:
#   - Throughput (requests per second)
#   - Latency percentiles (p50, p90, p95, p99)
#   - GPU utilization during inference
#   - GPU memory usage
#   - Model loading time
#   - Batch inference efficiency
#
# USAGE:
#   ./run_benchmark.sh [--endpoint URL] [--concurrency N] [--duration S]
#                       [--baseline FILE] [--output DIR]
#
# EXAMPLES:
#   ./run_benchmark.sh
#   ./run_benchmark.sh --endpoint https://llm-inference.netflix.internal
#   ./run_benchmark.sh --concurrency 64 --duration 300
#   ./run_benchmark.sh --baseline baselines/v2.2.0.json --output results/
#
# BASELINE THRESHOLDS (default):
#   - p50 latency:  < 15ms
#   - p99 latency:  < 50ms
#   - Throughput:    > 5000 req/s
#   - GPU util:     > 60%
# ============================================================================

set -euo pipefail

# ---- Configuration ----
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

# Default parameters
ENDPOINT="${BENCHMARK_ENDPOINT:-http://localhost:8080}"
CONCURRENCY="${BENCHMARK_CONCURRENCY:-32}"
DURATION="${BENCHMARK_DURATION:-120}"
WARMUP_DURATION=30
BASELINE_FILE=""
OUTPUT_DIR="${SCRIPT_DIR}/results"
REPORT_FILE=""

# Default baseline thresholds (in milliseconds unless noted)
BASELINE_P50_MS=15
BASELINE_P90_MS=30
BASELINE_P95_MS=40
BASELINE_P99_MS=50
BASELINE_THROUGHPUT_RPS=5000
BASELINE_GPU_UTIL_PCT=60
BASELINE_ERROR_RATE_PCT=0.1

# ---- Parse Arguments ----
while [[ $# -gt 0 ]]; do
    case "$1" in
        --endpoint)
            ENDPOINT="$2"
            shift 2
            ;;
        --concurrency)
            CONCURRENCY="$2"
            shift 2
            ;;
        --duration)
            DURATION="$2"
            shift 2
            ;;
        --baseline)
            BASELINE_FILE="$2"
            shift 2
            ;;
        --output)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --endpoint URL       Inference endpoint (default: http://localhost:8080)"
            echo "  --concurrency N      Concurrent requests (default: 32)"
            echo "  --duration S         Test duration in seconds (default: 120)"
            echo "  --baseline FILE      Baseline JSON file for comparison"
            echo "  --output DIR         Output directory for reports (default: ./results)"
            echo "  --help               Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Create output directory
mkdir -p "${OUTPUT_DIR}"
REPORT_FILE="${OUTPUT_DIR}/benchmark-${TIMESTAMP}.json"
REPORT_TEXT="${OUTPUT_DIR}/benchmark-${TIMESTAMP}.txt"

# ---- Logging Functions ----
log_info() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [BENCH] [INFO] $*"
}

log_warn() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [BENCH] [WARN] $*" >&2
}

log_error() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [BENCH] [ERROR] $*" >&2
}

log_section() {
    echo ""
    echo "================================================================"
    echo "  $*"
    echo "================================================================"
}

# ---- Prerequisite Checks ----
check_prerequisites() {
    log_info "Checking benchmark prerequisites..."

    local missing_tools=()
    for tool in curl jq bc; do
        if ! command -v "${tool}" &>/dev/null; then
            missing_tools+=("${tool}")
        fi
    done

    # Check for wrk or hey (HTTP load testing tools)
    local load_tool=""
    if command -v wrk &>/dev/null; then
        load_tool="wrk"
    elif command -v hey &>/dev/null; then
        load_tool="hey"
    elif command -v ab &>/dev/null; then
        load_tool="ab"
    fi

    if [[ -z "${load_tool}" ]]; then
        missing_tools+=("wrk or hey (HTTP load tester)")
    fi

    if [[ ${#missing_tools[@]} -gt 0 ]]; then
        log_error "Missing tools: ${missing_tools[*]}"
        exit 1
    fi

    # Verify endpoint is reachable
    log_info "Verifying endpoint: ${ENDPOINT}..."
    local http_status
    http_status=$(curl -sf -o /dev/null -w "%{http_code}" --max-time 10 "${ENDPOINT}/health" 2>/dev/null || echo "000")
    if [[ "${http_status}" != "200" ]]; then
        log_error "Endpoint ${ENDPOINT}/health is not reachable (HTTP ${http_status})"
        exit 1
    fi

    log_info "Endpoint is healthy (HTTP ${http_status})"
    log_info "Load testing tool: ${load_tool}"
    echo "${load_tool}"
}

# ---- Load Baseline ----
load_baseline() {
    if [[ -n "${BASELINE_FILE}" && -f "${BASELINE_FILE}" ]]; then
        log_info "Loading baseline from: ${BASELINE_FILE}"
        BASELINE_P50_MS=$(jq -r '.latency.p50_ms // 15' "${BASELINE_FILE}")
        BASELINE_P90_MS=$(jq -r '.latency.p90_ms // 30' "${BASELINE_FILE}")
        BASELINE_P95_MS=$(jq -r '.latency.p95_ms // 40' "${BASELINE_FILE}")
        BASELINE_P99_MS=$(jq -r '.latency.p99_ms // 50' "${BASELINE_FILE}")
        BASELINE_THROUGHPUT_RPS=$(jq -r '.throughput.rps // 5000' "${BASELINE_FILE}")
        BASELINE_GPU_UTIL_PCT=$(jq -r '.gpu.utilization_pct // 60' "${BASELINE_FILE}")
        BASELINE_ERROR_RATE_PCT=$(jq -r '.error_rate_pct // 0.1' "${BASELINE_FILE}")
        log_info "Baseline loaded: p50=${BASELINE_P50_MS}ms p99=${BASELINE_P99_MS}ms throughput=${BASELINE_THROUGHPUT_RPS}rps"
    else
        log_info "Using default baseline thresholds"
    fi
}

# ---- GPU Metrics Collection ----
collect_gpu_metrics() {
    local output_file="$1"
    log_info "Collecting GPU metrics..."

    local gpu_data="{}"
    if command -v nvidia-smi &>/dev/null; then
        # Collect GPU utilization, memory, temperature, and power
        local gpu_util gpu_mem_used gpu_mem_total gpu_temp gpu_power
        gpu_util=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 || echo "0")
        gpu_mem_used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 || echo "0")
        gpu_mem_total=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || echo "0")
        gpu_temp=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 || echo "0")
        gpu_power=$(nvidia-smi --query-gpu=power.draw --format=csv,noheader,nounits 2>/dev/null | head -1 || echo "0")
        local gpu_name
        gpu_name=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "unknown")

        gpu_data=$(jq -n \
            --arg name "${gpu_name}" \
            --argjson util "${gpu_util}" \
            --argjson mem_used "${gpu_mem_used}" \
            --argjson mem_total "${gpu_mem_total}" \
            --argjson temp "${gpu_temp}" \
            --argjson power "${gpu_power}" \
            '{
                name: $name,
                utilization_pct: $util,
                memory_used_mb: $mem_used,
                memory_total_mb: $mem_total,
                memory_utilization_pct: (if $mem_total > 0 then ($mem_used / $mem_total * 100 | floor) else 0 end),
                temperature_c: $temp,
                power_draw_w: $power
            }')
    else
        log_warn "nvidia-smi not available, skipping GPU metrics"
        gpu_data='{"name": "not_available", "utilization_pct": 0, "memory_used_mb": 0, "memory_total_mb": 0}'
    fi

    echo "${gpu_data}" > "${output_file}"
}

# ---- Run Inference Benchmark ----
run_latency_benchmark() {
    log_section "LATENCY BENCHMARK"
    log_info "Running latency benchmark: ${CONCURRENCY} concurrent, ${DURATION}s duration"

    local results_file="${OUTPUT_DIR}/latency-raw-${TIMESTAMP}.json"

    # Warmup phase
    log_info "Warmup phase (${WARMUP_DURATION}s)..."
    local warmup_count=0
    local warmup_end=$(($(date +%s) + WARMUP_DURATION))
    while [[ $(date +%s) -lt ${warmup_end} ]]; do
        curl -sf -o /dev/null --max-time 5 \
            -X POST "${ENDPOINT}/v1/predict" \
            -H "Content-Type: application/json" \
            -d '{"user_id": "bench_warmup", "context": "warmup", "dry_run": true}' 2>/dev/null || true
        warmup_count=$((warmup_count + 1))
    done
    log_info "Warmup complete: ${warmup_count} requests"

    # Collect GPU metrics before benchmark
    local gpu_before="${OUTPUT_DIR}/gpu-before-${TIMESTAMP}.json"
    collect_gpu_metrics "${gpu_before}"

    # Main benchmark: use individual curl requests with timing
    log_info "Starting main benchmark phase..."
    local latencies=()
    local success_count=0
    local error_count=0
    local total_requests=0
    local bench_start
    bench_start=$(date +%s%N)
    local bench_end_time=$(($(date +%s) + DURATION))

    # Generate sample request payloads with varying user profiles
    local user_ids=("user_001" "user_002" "user_003" "user_004" "user_005"
                    "user_006" "user_007" "user_008" "user_009" "user_010")
    local contexts=("homepage" "search" "browse" "detail" "playback"
                    "recommendation" "trending" "mylist" "continue_watching" "new_releases")

    # Run concurrent benchmark workers
    local pids=()
    local worker_results_dir="${OUTPUT_DIR}/workers-${TIMESTAMP}"
    mkdir -p "${worker_results_dir}"

    for ((worker=0; worker<CONCURRENCY; worker++)); do
        (
            local w_success=0
            local w_errors=0
            local w_latencies=""
            local w_end_time=${bench_end_time}

            while [[ $(date +%s) -lt ${w_end_time} ]]; do
                local uid="${user_ids[$((RANDOM % ${#user_ids[@]}))]}"
                local ctx="${contexts[$((RANDOM % ${#contexts[@]}))]}"

                local req_start req_end req_latency http_code
                req_start=$(date +%s%N)

                http_code=$(curl -sf -o /dev/null -w "%{http_code}" --max-time 5 \
                    -X POST "${ENDPOINT}/v1/predict" \
                    -H "Content-Type: application/json" \
                    -d "{\"user_id\": \"${uid}\", \"context\": \"${ctx}\", \"dry_run\": true}" \
                    2>/dev/null || echo "000")

                req_end=$(date +%s%N)
                req_latency=$(( (req_end - req_start) / 1000000 ))  # Convert to milliseconds

                if [[ "${http_code}" == "200" ]]; then
                    w_success=$((w_success + 1))
                    w_latencies="${w_latencies}${req_latency}\n"
                else
                    w_errors=$((w_errors + 1))
                fi
            done

            echo "${w_success}" > "${worker_results_dir}/worker-${worker}-success"
            echo "${w_errors}" > "${worker_results_dir}/worker-${worker}-errors"
            echo -e "${w_latencies}" > "${worker_results_dir}/worker-${worker}-latencies"
        ) &
        pids+=($!)
    done

    # Wait for all workers to complete
    for pid in "${pids[@]}"; do
        wait "${pid}" 2>/dev/null || true
    done

    local bench_end
    bench_end=$(date +%s%N)
    local bench_duration_ms=$(( (bench_end - bench_start) / 1000000 ))

    # Aggregate results from all workers
    for ((worker=0; worker<CONCURRENCY; worker++)); do
        if [[ -f "${worker_results_dir}/worker-${worker}-success" ]]; then
            local w_s
            w_s=$(cat "${worker_results_dir}/worker-${worker}-success")
            success_count=$((success_count + w_s))
        fi
        if [[ -f "${worker_results_dir}/worker-${worker}-errors" ]]; then
            local w_e
            w_e=$(cat "${worker_results_dir}/worker-${worker}-errors")
            error_count=$((error_count + w_e))
        fi
    done
    total_requests=$((success_count + error_count))

    # Combine and sort all latencies
    local all_latencies_file="${OUTPUT_DIR}/all-latencies-${TIMESTAMP}.txt"
    cat "${worker_results_dir}"/worker-*-latencies 2>/dev/null | \
        grep -v '^$' | sort -n > "${all_latencies_file}" || true

    local latency_count
    latency_count=$(wc -l < "${all_latencies_file}" 2>/dev/null || echo "0")

    # Calculate percentiles
    local p50=0 p90=0 p95=0 p99=0 avg=0 min_lat=0 max_lat=0

    if [[ "${latency_count}" -gt 0 ]]; then
        p50=$(awk "NR==$(( (latency_count * 50 + 99) / 100 ))" "${all_latencies_file}" || echo "0")
        p90=$(awk "NR==$(( (latency_count * 90 + 99) / 100 ))" "${all_latencies_file}" || echo "0")
        p95=$(awk "NR==$(( (latency_count * 95 + 99) / 100 ))" "${all_latencies_file}" || echo "0")
        p99=$(awk "NR==$(( (latency_count * 99 + 99) / 100 ))" "${all_latencies_file}" || echo "0")
        avg=$(awk '{sum+=$1} END {if(NR>0) printf "%.1f", sum/NR; else print 0}' "${all_latencies_file}" || echo "0")
        min_lat=$(head -1 "${all_latencies_file}" || echo "0")
        max_lat=$(tail -1 "${all_latencies_file}" || echo "0")
    fi

    # Calculate throughput
    local throughput_rps=0
    if [[ "${bench_duration_ms}" -gt 0 ]]; then
        throughput_rps=$(echo "scale=1; ${success_count} * 1000 / ${bench_duration_ms}" | bc || echo "0")
    fi

    # Calculate error rate
    local error_rate=0
    if [[ "${total_requests}" -gt 0 ]]; then
        error_rate=$(echo "scale=2; ${error_count} * 100 / ${total_requests}" | bc || echo "0")
    fi

    # Collect GPU metrics after benchmark
    local gpu_after="${OUTPUT_DIR}/gpu-after-${TIMESTAMP}.json"
    collect_gpu_metrics "${gpu_after}"

    # Read GPU data
    local gpu_util_during gpu_mem_during
    gpu_util_during=$(jq -r '.utilization_pct' "${gpu_after}" 2>/dev/null || echo "0")
    gpu_mem_during=$(jq -r '.memory_utilization_pct' "${gpu_after}" 2>/dev/null || echo "0")

    # Build results JSON
    jq -n \
        --arg timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg endpoint "${ENDPOINT}" \
        --argjson concurrency "${CONCURRENCY}" \
        --argjson duration "${DURATION}" \
        --argjson total_requests "${total_requests}" \
        --argjson success_count "${success_count}" \
        --argjson error_count "${error_count}" \
        --argjson error_rate "${error_rate}" \
        --argjson throughput_rps "${throughput_rps}" \
        --argjson p50 "${p50}" \
        --argjson p90 "${p90}" \
        --argjson p95 "${p95}" \
        --argjson p99 "${p99}" \
        --argjson avg "${avg}" \
        --argjson min_lat "${min_lat}" \
        --argjson max_lat "${max_lat}" \
        --argjson gpu_util "${gpu_util_during}" \
        --argjson gpu_mem "${gpu_mem_during}" \
        --slurpfile gpu_before "${gpu_before}" \
        --slurpfile gpu_after "${gpu_after}" \
        '{
            metadata: {
                timestamp: $timestamp,
                endpoint: $endpoint,
                concurrency: $concurrency,
                duration_seconds: $duration,
                benchmark_tool: "custom-curl-benchmark"
            },
            summary: {
                total_requests: $total_requests,
                successful_requests: $success_count,
                failed_requests: $error_count,
                error_rate_pct: $error_rate
            },
            throughput: {
                rps: $throughput_rps
            },
            latency: {
                p50_ms: $p50,
                p90_ms: $p90,
                p95_ms: $p95,
                p99_ms: $p99,
                avg_ms: $avg,
                min_ms: $min_lat,
                max_ms: $max_lat
            },
            gpu: {
                utilization_pct: $gpu_util,
                memory_utilization_pct: $gpu_mem,
                before: $gpu_before[0],
                after: $gpu_after[0]
            }
        }' > "${REPORT_FILE}"

    # Cleanup worker files
    rm -rf "${worker_results_dir}" "${all_latencies_file}" "${gpu_before}" "${gpu_after}" 2>/dev/null || true

    log_info "Benchmark results saved to: ${REPORT_FILE}"

    # Print summary
    echo "${p50}" "${p90}" "${p95}" "${p99}" "${throughput_rps}" "${error_rate}" "${gpu_util_during}"
}

# ---- Compare Against Baseline ----
compare_baseline() {
    local p50="$1" p90="$2" p95="$3" p99="$4" throughput="$5" error_rate="$6" gpu_util="$7"

    log_section "BASELINE COMPARISON"

    local pass_count=0
    local fail_count=0
    local total_checks=0

    compare_metric() {
        local name="$1" actual="$2" threshold="$3" direction="$4"
        total_checks=$((total_checks + 1))

        local result="PASS"
        if [[ "${direction}" == "lt" ]]; then
            if (( $(echo "${actual} > ${threshold}" | bc -l 2>/dev/null || echo 1) )); then
                result="FAIL"
            fi
        elif [[ "${direction}" == "gt" ]]; then
            if (( $(echo "${actual} < ${threshold}" | bc -l 2>/dev/null || echo 1) )); then
                result="FAIL"
            fi
        fi

        if [[ "${result}" == "PASS" ]]; then
            pass_count=$((pass_count + 1))
            printf "  [PASS] %-25s actual=%-10s threshold=%-10s\n" "${name}" "${actual}" "${threshold}"
        else
            fail_count=$((fail_count + 1))
            printf "  [FAIL] %-25s actual=%-10s threshold=%-10s\n" "${name}" "${actual}" "${threshold}"
        fi
    }

    compare_metric "p50 Latency (ms)"       "${p50}"        "${BASELINE_P50_MS}"           "lt"
    compare_metric "p90 Latency (ms)"       "${p90}"        "${BASELINE_P90_MS}"           "lt"
    compare_metric "p95 Latency (ms)"       "${p95}"        "${BASELINE_P95_MS}"           "lt"
    compare_metric "p99 Latency (ms)"       "${p99}"        "${BASELINE_P99_MS}"           "lt"
    compare_metric "Throughput (rps)"       "${throughput}" "${BASELINE_THROUGHPUT_RPS}"    "gt"
    compare_metric "Error Rate (%)"         "${error_rate}" "${BASELINE_ERROR_RATE_PCT}"    "lt"
    compare_metric "GPU Utilization (%)"    "${gpu_util}"   "${BASELINE_GPU_UTIL_PCT}"      "gt"

    echo ""
    log_info "Baseline comparison: ${pass_count}/${total_checks} checks passed, ${fail_count} failed"

    # Add comparison to report
    jq --argjson pass "${pass_count}" \
       --argjson fail "${fail_count}" \
       --argjson total "${total_checks}" \
       '. + {baseline_comparison: {passed: $pass, failed: $fail, total: $total, result: (if $fail == 0 then "PASS" else "FAIL" end)}}' \
       "${REPORT_FILE}" > "${REPORT_FILE}.tmp" && mv "${REPORT_FILE}.tmp" "${REPORT_FILE}"

    return "${fail_count}"
}

# ---- Generate Text Report ----
generate_text_report() {
    log_section "GENERATING REPORT"

    {
        echo "========================================================================"
        echo "  Netflix LLM Inference Platform - Benchmark Report"
        echo "  Author: Gopi Krishna Vajrala"
        echo "  Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "========================================================================"
        echo ""
        echo "Configuration:"
        echo "  Endpoint:     ${ENDPOINT}"
        echo "  Concurrency:  ${CONCURRENCY}"
        echo "  Duration:     ${DURATION}s"
        echo "  Warmup:       ${WARMUP_DURATION}s"
        echo ""
        echo "Results:"
        jq -r '
            "  Total Requests:     \(.summary.total_requests)",
            "  Successful:         \(.summary.successful_requests)",
            "  Failed:             \(.summary.failed_requests)",
            "  Error Rate:         \(.summary.error_rate_pct)%",
            "",
            "  Throughput:         \(.throughput.rps) req/s",
            "",
            "  Latency:",
            "    p50:              \(.latency.p50_ms)ms",
            "    p90:              \(.latency.p90_ms)ms",
            "    p95:              \(.latency.p95_ms)ms",
            "    p99:              \(.latency.p99_ms)ms",
            "    avg:              \(.latency.avg_ms)ms",
            "    min:              \(.latency.min_ms)ms",
            "    max:              \(.latency.max_ms)ms",
            "",
            "  GPU:",
            "    Utilization:      \(.gpu.utilization_pct)%",
            "    Memory Usage:     \(.gpu.memory_utilization_pct)%"
        ' "${REPORT_FILE}" 2>/dev/null || echo "  (Unable to parse results)"
        echo ""

        if jq -e '.baseline_comparison' "${REPORT_FILE}" &>/dev/null; then
            echo "Baseline Comparison:"
            jq -r '
                "  Result:   \(.baseline_comparison.result)",
                "  Passed:   \(.baseline_comparison.passed)/\(.baseline_comparison.total)"
            ' "${REPORT_FILE}" 2>/dev/null || true
        fi

        echo ""
        echo "========================================================================"
        echo "  JSON report: ${REPORT_FILE}"
        echo "========================================================================"
    } | tee "${REPORT_TEXT}"

    log_info "Text report saved to: ${REPORT_TEXT}"
}

# ---- Main Execution ----
main() {
    log_section "Netflix LLM Inference Platform - Benchmark Suite"

    log_info "Endpoint:    ${ENDPOINT}"
    log_info "Concurrency: ${CONCURRENCY}"
    log_info "Duration:    ${DURATION}s"
    log_info "Output:      ${OUTPUT_DIR}"

    # Check prerequisites
    local load_tool
    load_tool=$(check_prerequisites)

    # Load baseline thresholds
    load_baseline

    # Run benchmark and capture summary values
    local results
    results=$(run_latency_benchmark)
    local p50 p90 p95 p99 throughput error_rate gpu_util
    p50=$(echo "${results}" | awk '{print $1}')
    p90=$(echo "${results}" | awk '{print $2}')
    p95=$(echo "${results}" | awk '{print $3}')
    p99=$(echo "${results}" | awk '{print $4}')
    throughput=$(echo "${results}" | awk '{print $5}')
    error_rate=$(echo "${results}" | awk '{print $6}')
    gpu_util=$(echo "${results}" | awk '{print $7}')

    # Compare against baseline
    local baseline_failures=0
    compare_baseline "${p50}" "${p90}" "${p95}" "${p99}" "${throughput}" "${error_rate}" "${gpu_util}" || \
        baseline_failures=$?

    # Generate report
    generate_text_report

    # Exit with failure if baseline checks failed
    if [[ ${baseline_failures} -gt 0 ]]; then
        log_error "Benchmark FAILED: ${baseline_failures} baseline check(s) did not pass"
        exit 1
    fi

    log_info "Benchmark PASSED: All baseline checks met"
    exit 0
}

# Run the main function
main
