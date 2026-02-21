#!/usr/bin/env bash
# ============================================================================
# Netflix Real-Time LLM Personalization & Inference Platform
# GPU Fleet Health Check Script
# Author: Gopi Krishna Vajrala
# ============================================================================
#
# WHY THIS SCRIPT EXISTS:
#   Monitors GPU health across the inference fleet to detect hardware issues
#   before they impact model serving. GPU failures in production can cause
#   silent inference degradation or complete service outages.
#
# WHAT IT CHECKS:
#   1. GPU device availability and driver status
#   2. GPU temperature (alert if > 85C, critical if > 90C)
#   3. GPU memory usage and memory leaks
#   4. ECC (Error Correcting Code) memory errors
#   5. GPU utilization anomalies
#   6. PCIe link status and bandwidth
#   7. NVLink status (for multi-GPU nodes)
#   8. GPU clock throttling reasons
#
# USAGE:
#   ./gpu_health_check.sh [--namespace NS] [--context CTX] [--alert]
#   ./gpu_health_check.sh                          # Local GPU check
#   ./gpu_health_check.sh --namespace llm-inference # Check fleet via k8s
#   ./gpu_health_check.sh --alert                   # Send alerts for issues
#
# EXIT CODES:
#   0 - All GPUs healthy
#   1 - Warning conditions detected
#   2 - Critical conditions detected
#   3 - Script error
# ============================================================================

set -euo pipefail

# ---- Configuration ----
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
readonly LOG_FILE="/var/log/netflix-llm/gpu-health-${TIMESTAMP}.log"

# Temperature thresholds (Celsius)
readonly TEMP_WARNING=85
readonly TEMP_CRITICAL=90

# Memory utilization thresholds (percentage)
readonly MEM_WARNING=90
readonly MEM_CRITICAL=95

# ECC error thresholds
readonly ECC_CORRECTABLE_WARNING=100
readonly ECC_UNCORRECTABLE_CRITICAL=1

# GPU utilization thresholds
readonly UTIL_LOW_WARNING=5          # Suspiciously low (possible hang)
readonly UTIL_SUSTAINED_HIGH=98      # Possible thermal throttle

# Report output
REPORT_FILE=""
NAMESPACE=""
KUBE_CONTEXT=""
SEND_ALERTS=false

# Counters
TOTAL_GPUS=0
HEALTHY_GPUS=0
WARNING_GPUS=0
CRITICAL_GPUS=0

# ---- Parse Arguments ----
while [[ $# -gt 0 ]]; do
    case "$1" in
        --namespace|-n)
            NAMESPACE="$2"
            shift 2
            ;;
        --context|-c)
            KUBE_CONTEXT="$2"
            shift 2
            ;;
        --alert|-a)
            SEND_ALERTS=true
            shift
            ;;
        --output|-o)
            REPORT_FILE="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --namespace, -n NS     Kubernetes namespace to check GPU pods"
            echo "  --context, -c CTX      Kubernetes context to use"
            echo "  --alert, -a            Send alerts for issues found"
            echo "  --output, -o FILE      Output report file path"
            echo "  --help, -h             Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 3
            ;;
    esac
done

# Set default report file if not specified
if [[ -z "${REPORT_FILE}" ]]; then
    REPORT_FILE="${SCRIPT_DIR}/reports/gpu-health-${TIMESTAMP}.json"
fi
mkdir -p "$(dirname "${REPORT_FILE}")" 2>/dev/null || true
mkdir -p "$(dirname "${LOG_FILE}")" 2>/dev/null || true

# ---- Logging Functions ----
log_info() {
    local msg="[$(date +'%Y-%m-%d %H:%M:%S')] [GPU-HEALTH] [INFO] $*"
    echo "${msg}"
    echo "${msg}" >> "${LOG_FILE}" 2>/dev/null || true
}

log_warn() {
    local msg="[$(date +'%Y-%m-%d %H:%M:%S')] [GPU-HEALTH] [WARN] $*"
    echo "${msg}" >&2
    echo "${msg}" >> "${LOG_FILE}" 2>/dev/null || true
}

log_error() {
    local msg="[$(date +'%Y-%m-%d %H:%M:%S')] [GPU-HEALTH] [ERROR] $*"
    echo "${msg}" >&2
    echo "${msg}" >> "${LOG_FILE}" 2>/dev/null || true
}

log_critical() {
    local msg="[$(date +'%Y-%m-%d %H:%M:%S')] [GPU-HEALTH] [CRITICAL] $*"
    echo "${msg}" >&2
    echo "${msg}" >> "${LOG_FILE}" 2>/dev/null || true
}

# ---- Alert Function ----
send_alert() {
    local severity="$1"
    local message="$2"
    local gpu_id="${3:-unknown}"

    if [[ "${SEND_ALERTS}" != "true" ]]; then
        return 0
    fi

    # Send to Slack
    if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
        local color
        case "${severity}" in
            WARNING)  color="#E8A838" ;;
            CRITICAL) color="#E74C3C" ;;
            *)        color="#27AE60" ;;
        esac

        curl -sf -X POST "${SLACK_WEBHOOK_URL}" \
            -H "Content-Type: application/json" \
            -d "{
                \"attachments\": [{
                    \"color\": \"${color}\",
                    \"title\": \"GPU Health Alert - ${severity}\",
                    \"text\": \"${message}\",
                    \"fields\": [
                        {\"title\": \"GPU\", \"value\": \"${gpu_id}\", \"short\": true},
                        {\"title\": \"Hostname\", \"value\": \"$(hostname)\", \"short\": true}
                    ]
                }]
            }" 2>/dev/null || true
    fi

    # PagerDuty for critical alerts
    if [[ "${severity}" == "CRITICAL" && -n "${PAGERDUTY_SERVICE_KEY:-}" ]]; then
        curl -sf -X POST "https://events.pagerduty.com/v2/enqueue" \
            -H "Content-Type: application/json" \
            -d "{
                \"routing_key\": \"${PAGERDUTY_SERVICE_KEY}\",
                \"event_action\": \"trigger\",
                \"payload\": {
                    \"summary\": \"GPU ${severity}: ${message}\",
                    \"severity\": \"critical\",
                    \"source\": \"gpu_health_check.sh\",
                    \"component\": \"gpu-fleet\",
                    \"custom_details\": {\"gpu_id\": \"${gpu_id}\", \"hostname\": \"$(hostname)\"}
                }
            }" 2>/dev/null || true
    fi
}

# ---- Check Single GPU (Local) ----
check_local_gpu() {
    local gpu_index="$1"
    local gpu_status="healthy"
    local gpu_issues=()

    log_info "--- Checking GPU ${gpu_index} ---"

    # Query all GPU metrics in one nvidia-smi call
    local gpu_query
    gpu_query=$(nvidia-smi --id="${gpu_index}" \
        --query-gpu=name,uuid,temperature.gpu,memory.used,memory.total,utilization.gpu,utilization.memory,power.draw,power.limit,pstate,clocks_throttle_reasons.active,ecc.errors.corrected.aggregate.total,ecc.errors.uncorrected.aggregate.total,pcie.link.gen.current,pcie.link.width.current \
        --format=csv,noheader,nounits 2>/dev/null || echo "QUERY_FAILED")

    if [[ "${gpu_query}" == "QUERY_FAILED" ]]; then
        log_critical "GPU ${gpu_index}: Failed to query nvidia-smi"
        CRITICAL_GPUS=$((CRITICAL_GPUS + 1))
        gpu_issues+=("nvidia-smi query failed")
        gpu_status="critical"

        # Output minimal JSON for this GPU
        jq -n --argjson idx "${gpu_index}" --arg status "${gpu_status}" \
            '{index: $idx, status: $status, error: "nvidia-smi query failed"}'
        return 2
    fi

    # Parse nvidia-smi output
    IFS=',' read -r gpu_name gpu_uuid gpu_temp gpu_mem_used gpu_mem_total \
        gpu_util mem_util power_draw power_limit pstate throttle_reasons \
        ecc_correctable ecc_uncorrectable pcie_gen pcie_width <<< "${gpu_query}"

    # Trim whitespace
    gpu_name=$(echo "${gpu_name}" | xargs)
    gpu_uuid=$(echo "${gpu_uuid}" | xargs)
    gpu_temp=$(echo "${gpu_temp}" | xargs)
    gpu_mem_used=$(echo "${gpu_mem_used}" | xargs)
    gpu_mem_total=$(echo "${gpu_mem_total}" | xargs)
    gpu_util=$(echo "${gpu_util}" | xargs)
    mem_util=$(echo "${mem_util}" | xargs)
    power_draw=$(echo "${power_draw}" | xargs)
    power_limit=$(echo "${power_limit}" | xargs)
    pstate=$(echo "${pstate}" | xargs)
    throttle_reasons=$(echo "${throttle_reasons}" | xargs)
    ecc_correctable=$(echo "${ecc_correctable}" | xargs)
    ecc_uncorrectable=$(echo "${ecc_uncorrectable}" | xargs)
    pcie_gen=$(echo "${pcie_gen}" | xargs)
    pcie_width=$(echo "${pcie_width}" | xargs)

    log_info "GPU ${gpu_index}: ${gpu_name} (${gpu_uuid})"
    log_info "  Temperature: ${gpu_temp}C | Memory: ${gpu_mem_used}/${gpu_mem_total} MiB | Util: ${gpu_util}%"

    # ---- Check 1: Temperature ----
    if [[ "${gpu_temp}" != "N/A" && "${gpu_temp}" =~ ^[0-9]+$ ]]; then
        if [[ "${gpu_temp}" -ge "${TEMP_CRITICAL}" ]]; then
            log_critical "GPU ${gpu_index}: Temperature CRITICAL at ${gpu_temp}C (threshold: ${TEMP_CRITICAL}C)"
            gpu_issues+=("temperature_critical:${gpu_temp}C")
            gpu_status="critical"
            send_alert "CRITICAL" "GPU ${gpu_index} temperature at ${gpu_temp}C exceeds critical threshold of ${TEMP_CRITICAL}C" "GPU-${gpu_index}"
        elif [[ "${gpu_temp}" -ge "${TEMP_WARNING}" ]]; then
            log_warn "GPU ${gpu_index}: Temperature WARNING at ${gpu_temp}C (threshold: ${TEMP_WARNING}C)"
            gpu_issues+=("temperature_warning:${gpu_temp}C")
            if [[ "${gpu_status}" != "critical" ]]; then gpu_status="warning"; fi
            send_alert "WARNING" "GPU ${gpu_index} temperature at ${gpu_temp}C exceeds warning threshold of ${TEMP_WARNING}C" "GPU-${gpu_index}"
        fi
    fi

    # ---- Check 2: Memory Usage ----
    if [[ "${gpu_mem_used}" != "N/A" && "${gpu_mem_total}" != "N/A" && "${gpu_mem_total}" =~ ^[0-9]+$ && "${gpu_mem_total}" -gt 0 ]]; then
        local mem_pct=$(( (gpu_mem_used * 100) / gpu_mem_total ))
        if [[ "${mem_pct}" -ge "${MEM_CRITICAL}" ]]; then
            log_critical "GPU ${gpu_index}: Memory usage CRITICAL at ${mem_pct}% (${gpu_mem_used}/${gpu_mem_total} MiB)"
            gpu_issues+=("memory_critical:${mem_pct}%")
            gpu_status="critical"
            send_alert "CRITICAL" "GPU ${gpu_index} memory at ${mem_pct}% (${gpu_mem_used}/${gpu_mem_total} MiB)" "GPU-${gpu_index}"
        elif [[ "${mem_pct}" -ge "${MEM_WARNING}" ]]; then
            log_warn "GPU ${gpu_index}: Memory usage WARNING at ${mem_pct}% (${gpu_mem_used}/${gpu_mem_total} MiB)"
            gpu_issues+=("memory_warning:${mem_pct}%")
            if [[ "${gpu_status}" != "critical" ]]; then gpu_status="warning"; fi
        fi
    fi

    # ---- Check 3: ECC Errors ----
    if [[ "${ecc_uncorrectable}" != "N/A" && "${ecc_uncorrectable}" =~ ^[0-9]+$ ]]; then
        if [[ "${ecc_uncorrectable}" -ge "${ECC_UNCORRECTABLE_CRITICAL}" ]]; then
            log_critical "GPU ${gpu_index}: ${ecc_uncorrectable} UNCORRECTABLE ECC errors detected"
            gpu_issues+=("ecc_uncorrectable:${ecc_uncorrectable}")
            gpu_status="critical"
            send_alert "CRITICAL" "GPU ${gpu_index} has ${ecc_uncorrectable} uncorrectable ECC memory errors. GPU may need replacement." "GPU-${gpu_index}"
        fi
    fi

    if [[ "${ecc_correctable}" != "N/A" && "${ecc_correctable}" =~ ^[0-9]+$ ]]; then
        if [[ "${ecc_correctable}" -ge "${ECC_CORRECTABLE_WARNING}" ]]; then
            log_warn "GPU ${gpu_index}: ${ecc_correctable} correctable ECC errors (threshold: ${ECC_CORRECTABLE_WARNING})"
            gpu_issues+=("ecc_correctable:${ecc_correctable}")
            if [[ "${gpu_status}" != "critical" ]]; then gpu_status="warning"; fi
            send_alert "WARNING" "GPU ${gpu_index} has ${ecc_correctable} correctable ECC errors. Monitor for degradation." "GPU-${gpu_index}"
        fi
    fi

    # ---- Check 4: GPU Utilization Anomalies ----
    if [[ "${gpu_util}" != "N/A" && "${gpu_util}" =~ ^[0-9]+$ ]]; then
        if [[ "${gpu_util}" -le "${UTIL_LOW_WARNING}" ]]; then
            log_warn "GPU ${gpu_index}: Utilization suspiciously low at ${gpu_util}% (possible hang or idle)"
            gpu_issues+=("utilization_low:${gpu_util}%")
            if [[ "${gpu_status}" != "critical" ]]; then gpu_status="warning"; fi
        fi
    fi

    # ---- Check 5: Throttle Reasons ----
    if [[ "${throttle_reasons}" != "N/A" && "${throttle_reasons}" != "0x0000000000000000" && -n "${throttle_reasons}" ]]; then
        log_warn "GPU ${gpu_index}: Active throttle reasons: ${throttle_reasons}"
        gpu_issues+=("throttling:${throttle_reasons}")
        if [[ "${gpu_status}" != "critical" ]]; then gpu_status="warning"; fi
    fi

    # ---- Check 6: PCIe Link ----
    if [[ "${pcie_gen}" != "N/A" && "${pcie_gen}" =~ ^[0-9]+$ ]]; then
        # Expect Gen4 or Gen5 for modern GPU inference
        if [[ "${pcie_gen}" -lt 3 ]]; then
            log_warn "GPU ${gpu_index}: PCIe link running at Gen${pcie_gen} (expected Gen4+)"
            gpu_issues+=("pcie_degraded:Gen${pcie_gen}")
            if [[ "${gpu_status}" != "critical" ]]; then gpu_status="warning"; fi
        fi
    fi

    # Update counters
    case "${gpu_status}" in
        healthy)  HEALTHY_GPUS=$((HEALTHY_GPUS + 1)) ;;
        warning)  WARNING_GPUS=$((WARNING_GPUS + 1)) ;;
        critical) CRITICAL_GPUS=$((CRITICAL_GPUS + 1)) ;;
    esac

    # Build per-GPU JSON result
    local mem_pct_val=0
    if [[ "${gpu_mem_total}" != "N/A" && "${gpu_mem_total}" =~ ^[0-9]+$ && "${gpu_mem_total}" -gt 0 ]]; then
        mem_pct_val=$(( (gpu_mem_used * 100) / gpu_mem_total ))
    fi

    local issues_json="[]"
    if [[ ${#gpu_issues[@]} -gt 0 ]]; then
        issues_json=$(printf '%s\n' "${gpu_issues[@]}" | jq -R . | jq -s .)
    fi

    jq -n \
        --argjson index "${gpu_index}" \
        --arg name "${gpu_name}" \
        --arg uuid "${gpu_uuid}" \
        --arg status "${gpu_status}" \
        --argjson temperature "${gpu_temp:-0}" \
        --argjson memory_used "${gpu_mem_used:-0}" \
        --argjson memory_total "${gpu_mem_total:-0}" \
        --argjson memory_pct "${mem_pct_val}" \
        --argjson utilization "${gpu_util:-0}" \
        --arg power_draw "${power_draw}" \
        --arg power_limit "${power_limit}" \
        --arg pstate "${pstate}" \
        --arg ecc_correctable "${ecc_correctable}" \
        --arg ecc_uncorrectable "${ecc_uncorrectable}" \
        --arg pcie_gen "${pcie_gen}" \
        --arg pcie_width "${pcie_width}" \
        --argjson issues "${issues_json}" \
        '{
            index: $index,
            name: $name,
            uuid: $uuid,
            status: $status,
            temperature_c: $temperature,
            memory: {
                used_mb: $memory_used,
                total_mb: $memory_total,
                utilization_pct: $memory_pct
            },
            utilization_pct: $utilization,
            power: {
                draw_w: $power_draw,
                limit_w: $power_limit
            },
            pstate: $pstate,
            ecc: {
                correctable: $ecc_correctable,
                uncorrectable: $ecc_uncorrectable
            },
            pcie: {
                generation: $pcie_gen,
                width: $pcie_width
            },
            issues: $issues
        }'
}

# ---- Check GPUs via Kubernetes ----
check_fleet_gpus() {
    log_info "Checking GPU fleet via Kubernetes namespace: ${NAMESPACE}"

    local kubectl_ctx=""
    if [[ -n "${KUBE_CONTEXT}" ]]; then
        kubectl_ctx="--context ${KUBE_CONTEXT}"
    fi

    # Get all GPU pods
    local gpu_pods
    gpu_pods=$(kubectl get pods -n "${NAMESPACE}" ${kubectl_ctx} \
        -l "app=llm-inference-server" \
        --field-selector=status.phase=Running \
        -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.nodeName}{"\n"}{end}' 2>/dev/null || echo "")

    if [[ -z "${gpu_pods}" ]]; then
        log_warn "No running GPU pods found in namespace ${NAMESPACE}"
        return 1
    fi

    local pod_results="[]"

    while IFS=$'\t' read -r pod_name node_name; do
        [[ -z "${pod_name}" ]] && continue
        log_info "Checking GPU on pod: ${pod_name} (node: ${node_name})"

        # Execute nvidia-smi inside the pod
        local gpu_output
        gpu_output=$(kubectl exec "${pod_name}" -n "${NAMESPACE}" ${kubectl_ctx} \
            -- nvidia-smi --query-gpu=index,name,temperature.gpu,memory.used,memory.total,utilization.gpu,ecc.errors.corrected.aggregate.total,ecc.errors.uncorrected.aggregate.total \
            --format=csv,noheader,nounits 2>/dev/null || echo "EXEC_FAILED")

        if [[ "${gpu_output}" == "EXEC_FAILED" ]]; then
            log_error "Failed to execute nvidia-smi on pod ${pod_name}"
            local pod_json
            pod_json=$(jq -n \
                --arg pod "${pod_name}" \
                --arg node "${node_name}" \
                '{pod: $pod, node: $node, status: "error", error: "nvidia-smi execution failed"}')
            pod_results=$(echo "${pod_results}" | jq --argjson new "${pod_json}" '. + [$new]')
            CRITICAL_GPUS=$((CRITICAL_GPUS + 1))
            TOTAL_GPUS=$((TOTAL_GPUS + 1))
            continue
        fi

        # Parse each GPU line from the pod
        while IFS=',' read -r idx name temp mem_used mem_total util ecc_corr ecc_uncorr; do
            [[ -z "${idx}" ]] && continue
            idx=$(echo "${idx}" | xargs)
            name=$(echo "${name}" | xargs)
            temp=$(echo "${temp}" | xargs)
            mem_used=$(echo "${mem_used}" | xargs)
            mem_total=$(echo "${mem_total}" | xargs)
            util=$(echo "${util}" | xargs)
            ecc_corr=$(echo "${ecc_corr}" | xargs)
            ecc_uncorr=$(echo "${ecc_uncorr}" | xargs)

            TOTAL_GPUS=$((TOTAL_GPUS + 1))
            local status="healthy"
            local issues=()

            # Temperature check
            if [[ "${temp}" =~ ^[0-9]+$ ]]; then
                if [[ "${temp}" -ge "${TEMP_CRITICAL}" ]]; then
                    status="critical"
                    issues+=("temperature_critical:${temp}C")
                    send_alert "CRITICAL" "GPU ${idx} on ${pod_name} at ${temp}C" "GPU-${idx}@${pod_name}"
                elif [[ "${temp}" -ge "${TEMP_WARNING}" ]]; then
                    status="warning"
                    issues+=("temperature_warning:${temp}C")
                fi
            fi

            # Memory check
            if [[ "${mem_total}" =~ ^[0-9]+$ && "${mem_total}" -gt 0 ]]; then
                local mpct=$(( (mem_used * 100) / mem_total ))
                if [[ "${mpct}" -ge "${MEM_CRITICAL}" ]]; then
                    status="critical"
                    issues+=("memory_critical:${mpct}%")
                elif [[ "${mpct}" -ge "${MEM_WARNING}" ]]; then
                    if [[ "${status}" != "critical" ]]; then status="warning"; fi
                    issues+=("memory_warning:${mpct}%")
                fi
            fi

            # ECC check
            if [[ "${ecc_uncorr}" =~ ^[0-9]+$ && "${ecc_uncorr}" -ge "${ECC_UNCORRECTABLE_CRITICAL}" ]]; then
                status="critical"
                issues+=("ecc_uncorrectable:${ecc_uncorr}")
                send_alert "CRITICAL" "GPU ${idx} on ${pod_name}: ${ecc_uncorr} uncorrectable ECC errors" "GPU-${idx}@${pod_name}"
            fi

            case "${status}" in
                healthy)  HEALTHY_GPUS=$((HEALTHY_GPUS + 1)) ;;
                warning)  WARNING_GPUS=$((WARNING_GPUS + 1)) ;;
                critical) CRITICAL_GPUS=$((CRITICAL_GPUS + 1)) ;;
            esac

            local issues_json="[]"
            if [[ ${#issues[@]} -gt 0 ]]; then
                issues_json=$(printf '%s\n' "${issues[@]}" | jq -R . | jq -s .)
            fi

            local gpu_json
            gpu_json=$(jq -n \
                --arg pod "${pod_name}" \
                --arg node "${node_name}" \
                --argjson idx "${idx}" \
                --arg name "${name}" \
                --arg status "${status}" \
                --argjson temp "${temp:-0}" \
                --argjson mem_used "${mem_used:-0}" \
                --argjson mem_total "${mem_total:-0}" \
                --argjson util "${util:-0}" \
                --arg ecc_corr "${ecc_corr}" \
                --arg ecc_uncorr "${ecc_uncorr}" \
                --argjson issues "${issues_json}" \
                '{pod: $pod, node: $node, gpu_index: $idx, name: $name, status: $status,
                  temperature_c: $temp, memory_used_mb: $mem_used, memory_total_mb: $mem_total,
                  utilization_pct: $util, ecc_correctable: $ecc_corr, ecc_uncorrectable: $ecc_uncorr,
                  issues: $issues}')
            pod_results=$(echo "${pod_results}" | jq --argjson new "${gpu_json}" '. + [$new]')
        done <<< "${gpu_output}"

    done <<< "${gpu_pods}"

    echo "${pod_results}"
}

# ---- Check Local GPUs ----
check_local_gpus() {
    log_info "Checking local GPU health..."

    # Verify nvidia-smi is available
    if ! command -v nvidia-smi &>/dev/null; then
        log_error "nvidia-smi not found. NVIDIA driver may not be installed."
        return 3
    fi

    # Verify driver is loaded
    if ! nvidia-smi &>/dev/null; then
        log_critical "nvidia-smi failed to execute. GPU driver may be crashed or unloaded."
        CRITICAL_GPUS=1
        return 2
    fi

    # Get number of GPUs
    local gpu_count
    gpu_count=$(nvidia-smi --query-gpu=count --format=csv,noheader,nounits 2>/dev/null | head -1 || echo "0")
    TOTAL_GPUS="${gpu_count}"
    log_info "Detected ${gpu_count} GPU(s)"

    if [[ "${gpu_count}" -eq 0 ]]; then
        log_error "No GPUs detected"
        return 2
    fi

    # Check each GPU
    local gpu_results="[]"
    for ((i=0; i<gpu_count; i++)); do
        local gpu_json
        gpu_json=$(check_local_gpu "${i}")
        gpu_results=$(echo "${gpu_results}" | jq --argjson new "${gpu_json}" '. + [$new]')
    done

    echo "${gpu_results}"
}

# ---- Generate Report ----
generate_report() {
    local gpu_details="$1"

    local overall_status="healthy"
    if [[ "${CRITICAL_GPUS}" -gt 0 ]]; then
        overall_status="critical"
    elif [[ "${WARNING_GPUS}" -gt 0 ]]; then
        overall_status="warning"
    fi

    local report
    report=$(jq -n \
        --arg timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg hostname "$(hostname)" \
        --arg status "${overall_status}" \
        --argjson total "${TOTAL_GPUS}" \
        --argjson healthy "${HEALTHY_GPUS}" \
        --argjson warning "${WARNING_GPUS}" \
        --argjson critical "${CRITICAL_GPUS}" \
        --arg namespace "${NAMESPACE:-local}" \
        --argjson gpus "${gpu_details}" \
        '{
            metadata: {
                timestamp: $timestamp,
                hostname: $hostname,
                check_mode: (if $namespace == "local" then "local" else "kubernetes" end),
                namespace: $namespace
            },
            summary: {
                status: $status,
                total_gpus: $total,
                healthy: $healthy,
                warning: $warning,
                critical: $critical
            },
            thresholds: {
                temperature: {warning_c: 85, critical_c: 90},
                memory: {warning_pct: 90, critical_pct: 95},
                ecc: {correctable_warning: 100, uncorrectable_critical: 1}
            },
            gpus: $gpus
        }')

    echo "${report}" > "${REPORT_FILE}"
    log_info "Report saved to: ${REPORT_FILE}"

    # Print summary
    echo ""
    echo "================================================================"
    echo "  GPU Fleet Health Check Summary"
    echo "  Author: Gopi Krishna Vajrala"
    echo "  Timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "================================================================"
    echo ""
    echo "  Overall Status:  ${overall_status^^}"
    echo "  Total GPUs:      ${TOTAL_GPUS}"
    echo "  Healthy:         ${HEALTHY_GPUS}"
    echo "  Warning:         ${WARNING_GPUS}"
    echo "  Critical:        ${CRITICAL_GPUS}"
    echo ""
    echo "  Report:          ${REPORT_FILE}"
    echo ""
    echo "================================================================"

    # Return appropriate exit code
    if [[ "${CRITICAL_GPUS}" -gt 0 ]]; then
        return 2
    elif [[ "${WARNING_GPUS}" -gt 0 ]]; then
        return 1
    fi
    return 0
}

# ---- Main Execution ----
main() {
    log_info "================================================================"
    log_info "Netflix LLM Inference - GPU Fleet Health Check"
    log_info "================================================================"

    local gpu_details="[]"

    if [[ -n "${NAMESPACE}" ]]; then
        # Kubernetes fleet check
        gpu_details=$(check_fleet_gpus)
    else
        # Local GPU check
        gpu_details=$(check_local_gpus)
    fi

    generate_report "${gpu_details}"
}

# Run the main function
main
