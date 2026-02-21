###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# GPU Cluster Module - EKS Managed Node Group with NVIDIA A100 GPUs
###############################################################################

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# -----------------------------------------------------------------------------
# Data Sources
# -----------------------------------------------------------------------------

data "aws_region" "current" {}

data "aws_ssm_parameter" "gpu_ami" {
  name = "/aws/service/eks/optimized-ami/${var.eks_cluster_version}/amazon-linux-2-gpu/recommended/image_id"
}

data "aws_eks_cluster" "target" {
  name = var.eks_cluster_name
}

# -----------------------------------------------------------------------------
# Placement Group - Cluster strategy for low-latency GPU-to-GPU communication
# -----------------------------------------------------------------------------

resource "aws_placement_group" "gpu_cluster" {
  name         = "${var.project_name}-${var.environment}-gpu-placement-${var.region_short}"
  strategy     = "cluster"
  spread_level = null

  tags = merge(var.common_tags, {
    Name        = "${var.project_name}-${var.environment}-gpu-placement-${var.region_short}"
    Component   = "gpu-cluster"
    CostCenter  = "ml-inference"
  })
}

# -----------------------------------------------------------------------------
# Launch Template for GPU Instances
# -----------------------------------------------------------------------------

resource "aws_launch_template" "gpu_nodes" {
  name_prefix   = "${var.project_name}-${var.environment}-gpu-"
  image_id      = var.custom_ami_id != "" ? var.custom_ami_id : data.aws_ssm_parameter.gpu_ami.value
  instance_type = var.gpu_instance_type

  vpc_security_group_ids = var.security_group_ids

  placement {
    group_name = aws_placement_group.gpu_cluster.name
  }

  # EFA (Elastic Fabric Adapter) for high-bandwidth GPU communication
  dynamic "network_interfaces" {
    for_each = var.enable_efa ? [1] : []
    content {
      device_index                = 0
      associate_public_ip_address = false
      security_groups             = var.security_group_ids
      interface_type              = "efa"
      subnet_id                   = var.subnet_ids[0]
    }
  }

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      volume_size           = var.root_volume_size
      volume_type           = "gp3"
      iops                  = 16000
      throughput            = 1000
      encrypted             = true
      kms_key_id            = var.kms_key_arn
      delete_on_termination = true
    }
  }

  # NVMe instance store for model caching
  block_device_mappings {
    device_name  = "/dev/xvdb"
    virtual_name = "ephemeral0"
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "enabled"
  }

  monitoring {
    enabled = true
  }

  user_data = base64encode(templatefile("${path.module}/templates/gpu_userdata.sh.tpl", {
    cluster_name           = var.eks_cluster_name
    cluster_endpoint       = data.aws_eks_cluster.target.endpoint
    cluster_ca             = data.aws_eks_cluster.target.certificate_authority[0].data
    nvidia_driver_version  = var.nvidia_driver_version
    enable_mps             = var.enable_gpu_mps
    gpu_time_slicing       = var.gpu_time_slicing_replicas
    kubelet_extra_args     = var.kubelet_extra_args
    region                 = data.aws_region.current.name
  }))

  tag_specifications {
    resource_type = "instance"

    tags = merge(var.common_tags, {
      Name                  = "${var.project_name}-${var.environment}-gpu-node-${var.region_short}"
      Component             = "gpu-inference"
      CostCenter            = "ml-inference"
      "nvidia.com/gpu.type" = "a100"
      GpuCount              = "8"
    })
  }

  tag_specifications {
    resource_type = "volume"

    tags = merge(var.common_tags, {
      Name       = "${var.project_name}-${var.environment}-gpu-volume-${var.region_short}"
      Component  = "gpu-inference"
      CostCenter = "ml-inference"
    })
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = merge(var.common_tags, {
    Name = "${var.project_name}-${var.environment}-gpu-launch-template"
  })
}

# -----------------------------------------------------------------------------
# EKS Managed Node Group for GPU Inference
# -----------------------------------------------------------------------------

resource "aws_eks_node_group" "gpu_inference" {
  cluster_name    = var.eks_cluster_name
  node_group_name = "${var.project_name}-${var.environment}-gpu-inference-${var.region_short}"
  node_role_arn   = var.node_role_arn
  subnet_ids      = var.subnet_ids

  instance_types = [var.gpu_instance_type]
  capacity_type  = var.use_spot_instances ? "SPOT" : "ON_DEMAND"
  ami_type       = "CUSTOM"

  launch_template {
    id      = aws_launch_template.gpu_nodes.id
    version = aws_launch_template.gpu_nodes.latest_version
  }

  scaling_config {
    desired_size = var.gpu_node_desired_count
    min_size     = var.gpu_node_min_count
    max_size     = var.gpu_node_max_count
  }

  update_config {
    max_unavailable_percentage = var.max_unavailable_percentage
  }

  labels = {
    "nvidia.com/gpu.present"  = "true"
    "nvidia.com/gpu.type"     = "a100"
    "node.kubernetes.io/role" = "gpu-inference"
    "workload-type"           = "llm-inference"
    "netflix.com/platform"    = "personalization"
  }

  taint {
    key    = "nvidia.com/gpu"
    value  = "true"
    effect = "NO_SCHEDULE"
  }

  taint {
    key    = "workload-type"
    value  = "llm-inference"
    effect = "PREFER_NO_SCHEDULE"
  }

  lifecycle {
    ignore_changes = [scaling_config[0].desired_size]
  }

  tags = merge(var.common_tags, {
    Name                                                      = "${var.project_name}-${var.environment}-gpu-nodegroup-${var.region_short}"
    Component                                                 = "gpu-inference"
    CostCenter                                                = "ml-inference"
    "k8s.io/cluster-autoscaler/enabled"                       = "true"
    "k8s.io/cluster-autoscaler/${var.eks_cluster_name}"       = "owned"
    "k8s.io/cluster-autoscaler/node-template/label/gpu-type"  = "a100"
  })
}

# -----------------------------------------------------------------------------
# Auto Scaling Group - Warm Pool for Fast Scaling
# -----------------------------------------------------------------------------

resource "aws_autoscaling_group_tag" "gpu_cost_tracking" {
  for_each = {
    "CostCenter"  = "ml-inference"
    "Platform"    = "netflix-personalization"
    "GpuType"     = "nvidia-a100"
    "Environment" = var.environment
  }

  autoscaling_group_name = aws_eks_node_group.gpu_inference.resources[0].autoscaling_groups[0].name

  tag {
    key                 = each.key
    value               = each.value
    propagate_at_launch = true
  }
}

resource "aws_autoscaling_policy" "gpu_scale_out" {
  name                   = "${var.project_name}-${var.environment}-gpu-scale-out"
  autoscaling_group_name = aws_eks_node_group.gpu_inference.resources[0].autoscaling_groups[0].name
  policy_type            = "TargetTrackingScaling"

  target_tracking_configuration {
    customized_metric_specification {
      metric_dimension {
        name  = "ClusterName"
        value = var.eks_cluster_name
      }

      metric_name = "gpu_utilization_percentage"
      namespace   = "Netflix/GPUInference"
      statistic   = "Average"
      unit        = "Percent"
    }

    target_value     = var.gpu_scale_out_threshold
    disable_scale_in = false
  }
}

resource "aws_autoscaling_policy" "gpu_scale_in" {
  name                   = "${var.project_name}-${var.environment}-gpu-scale-in"
  autoscaling_group_name = aws_eks_node_group.gpu_inference.resources[0].autoscaling_groups[0].name
  policy_type            = "SimpleScaling"
  adjustment_type        = "ChangeInCapacity"
  scaling_adjustment     = -1
  cooldown               = var.scale_in_cooldown
}

resource "aws_autoscaling_policy" "inference_latency_scaling" {
  name                   = "${var.project_name}-${var.environment}-latency-scaling"
  autoscaling_group_name = aws_eks_node_group.gpu_inference.resources[0].autoscaling_groups[0].name
  policy_type            = "TargetTrackingScaling"

  target_tracking_configuration {
    customized_metric_specification {
      metric_dimension {
        name  = "ClusterName"
        value = var.eks_cluster_name
      }

      metric_name = "inference_p99_latency_ms"
      namespace   = "Netflix/GPUInference"
      statistic   = "Average"
      unit        = "Milliseconds"
    }

    target_value     = var.latency_target_ms
    disable_scale_in = false
  }
}

# Warm pool keeps pre-initialized instances ready for rapid scaling
resource "aws_autoscaling_warm_pool" "gpu_warm_pool" {
  autoscaling_group_name = aws_eks_node_group.gpu_inference.resources[0].autoscaling_groups[0].name

  pool_state                  = "Stopped"
  min_size                    = var.warm_pool_min_size
  max_group_prepared_capacity = var.warm_pool_max_size

  instance_reuse_policy {
    reuse_on_scale_in = true
  }
}

# -----------------------------------------------------------------------------
# CloudWatch Alarms for GPU Monitoring
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "gpu_utilization_high" {
  alarm_name          = "${var.project_name}-${var.environment}-gpu-utilization-high-${var.region_short}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "gpu_utilization_percentage"
  namespace           = "Netflix/GPUInference"
  period              = 60
  statistic           = "Average"
  threshold           = var.gpu_alarm_high_threshold
  alarm_description   = "GPU utilization exceeds ${var.gpu_alarm_high_threshold}% for 3 consecutive minutes"
  alarm_actions       = var.alarm_sns_topic_arns

  dimensions = {
    ClusterName = var.eks_cluster_name
    NodeGroup   = aws_eks_node_group.gpu_inference.node_group_name
  }

  tags = merge(var.common_tags, {
    Component = "gpu-monitoring"
  })
}

resource "aws_cloudwatch_metric_alarm" "gpu_memory_high" {
  alarm_name          = "${var.project_name}-${var.environment}-gpu-memory-high-${var.region_short}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "gpu_memory_utilization_percentage"
  namespace           = "Netflix/GPUInference"
  period              = 60
  statistic           = "Average"
  threshold           = var.gpu_memory_alarm_threshold
  alarm_description   = "GPU memory utilization exceeds ${var.gpu_memory_alarm_threshold}% for 3 consecutive minutes"
  alarm_actions       = var.alarm_sns_topic_arns

  dimensions = {
    ClusterName = var.eks_cluster_name
    NodeGroup   = aws_eks_node_group.gpu_inference.node_group_name
  }

  tags = merge(var.common_tags, {
    Component = "gpu-monitoring"
  })
}

resource "aws_cloudwatch_metric_alarm" "gpu_node_count_low" {
  alarm_name          = "${var.project_name}-${var.environment}-gpu-nodes-low-${var.region_short}"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "GroupInServiceInstances"
  namespace           = "AWS/AutoScaling"
  period              = 60
  statistic           = "Minimum"
  threshold           = var.gpu_node_min_count
  alarm_description   = "GPU node count has fallen below minimum of ${var.gpu_node_min_count}"
  alarm_actions       = var.alarm_sns_topic_arns

  dimensions = {
    AutoScalingGroupName = aws_eks_node_group.gpu_inference.resources[0].autoscaling_groups[0].name
  }

  tags = merge(var.common_tags, {
    Component = "gpu-monitoring"
  })
}
