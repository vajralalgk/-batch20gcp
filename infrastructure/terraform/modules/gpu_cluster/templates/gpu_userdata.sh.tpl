#!/bin/bash
set -o xtrace

# Bootstrap EKS node and join the cluster
/etc/eks/bootstrap.sh '${cluster_name}' \
  --apiserver-endpoint '${cluster_endpoint}' \
  --b64-cluster-ca '${cluster_ca}' \
  --kubelet-extra-args '${kubelet_extra_args}' \
  --use-max-pods false

# Configure NVIDIA runtime
nvidia-smi -pm 1
nvidia-smi --auto-boost-default=0

%{ if enable_mps }
# Enable Multi-Process Service for GPU sharing
nvidia-cuda-mps-control -d
%{ endif }

%{ if gpu_time_slicing > 0 }
# Configure GPU time-slicing
cat <<'GPUCONFIG' > /etc/nvidia/gpu-time-slicing-config.yaml
version: v1
sharing:
  timeSlicing:
    resources:
    - name: nvidia.com/gpu
      replicas: ${gpu_time_slicing}
GPUCONFIG
%{ endif }

# Configure NVMe instance store for model caching
if [ -b /dev/nvme1n1 ]; then
  mkfs.xfs /dev/nvme1n1
  mkdir -p /mnt/model-cache
  mount /dev/nvme1n1 /mnt/model-cache
  chmod 755 /mnt/model-cache
fi

# Set up CloudWatch agent for GPU metrics
cat <<'CWCONFIG' > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
{
  "metrics": {
    "namespace": "Netflix/GPUInference",
    "metrics_collected": {
      "nvidia_gpu": {
        "measurement": [
          "utilization_gpu",
          "utilization_memory",
          "memory_total",
          "memory_used",
          "temperature_gpu",
          "power_draw"
        ],
        "metrics_collection_interval": 10
      }
    },
    "append_dimensions": {
      "Region": "${region}"
    }
  }
}
CWCONFIG

/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config \
  -m ec2 \
  -s \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
