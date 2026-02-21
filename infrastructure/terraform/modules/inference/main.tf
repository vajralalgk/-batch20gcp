###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# Inference Module - EKS Cluster with Triton Inference Server & ALB
###############################################################################

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

# -----------------------------------------------------------------------------
# Data Sources
# -----------------------------------------------------------------------------

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

# -----------------------------------------------------------------------------
# EKS Cluster for Inference Workloads
# -----------------------------------------------------------------------------

resource "aws_eks_cluster" "inference" {
  name     = "${var.project_name}-${var.environment}-inference-${var.region_short}"
  role_arn = var.cluster_role_arn
  version  = var.eks_version

  vpc_config {
    subnet_ids              = concat(var.private_subnet_ids, var.gpu_subnet_ids)
    security_group_ids      = [var.cluster_security_group_id]
    endpoint_private_access = true
    endpoint_public_access  = var.enable_public_endpoint
    public_access_cidrs     = var.enable_public_endpoint ? var.public_access_cidrs : null
  }

  encryption_config {
    provider {
      key_arn = var.kms_key_arn
    }
    resources = ["secrets"]
  }

  kubernetes_network_config {
    service_ipv4_cidr = var.service_cidr
    ip_family         = "ipv4"
  }

  enabled_cluster_log_types = [
    "api",
    "audit",
    "authenticator",
    "controllerManager",
    "scheduler"
  ]

  tags = merge(var.common_tags, {
    Name       = "${var.project_name}-${var.environment}-inference-${var.region_short}"
    Component  = "inference-cluster"
    CostCenter = "ml-inference"
  })

  depends_on = [
    aws_cloudwatch_log_group.eks_cluster,
  ]
}

resource "aws_cloudwatch_log_group" "eks_cluster" {
  name              = "/aws/eks/${var.project_name}-${var.environment}-inference-${var.region_short}/cluster"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn

  tags = merge(var.common_tags, {
    Component = "inference-logging"
  })
}

# -----------------------------------------------------------------------------
# EKS Addons - Required for GPU Inference
# -----------------------------------------------------------------------------

resource "aws_eks_addon" "vpc_cni" {
  cluster_name  = aws_eks_cluster.inference.name
  addon_name    = "vpc-cni"
  addon_version = var.vpc_cni_version

  configuration_values = jsonencode({
    enableNetworkPolicy = "true"
    env = {
      ENABLE_PREFIX_DELEGATION = "true"
      WARM_PREFIX_TARGET       = "1"
    }
  })

  resolve_conflicts_on_update = "OVERWRITE"

  tags = merge(var.common_tags, {
    Component = "inference-networking"
  })
}

resource "aws_eks_addon" "coredns" {
  cluster_name  = aws_eks_cluster.inference.name
  addon_name    = "coredns"
  addon_version = var.coredns_version

  resolve_conflicts_on_update = "OVERWRITE"

  tags = merge(var.common_tags, {
    Component = "inference-dns"
  })
}

resource "aws_eks_addon" "kube_proxy" {
  cluster_name  = aws_eks_cluster.inference.name
  addon_name    = "kube-proxy"
  addon_version = var.kube_proxy_version

  resolve_conflicts_on_update = "OVERWRITE"

  tags = merge(var.common_tags, {
    Component = "inference-networking"
  })
}

resource "aws_eks_addon" "ebs_csi" {
  cluster_name             = aws_eks_cluster.inference.name
  addon_name               = "aws-ebs-csi-driver"
  addon_version            = var.ebs_csi_version
  service_account_role_arn = var.ebs_csi_role_arn

  resolve_conflicts_on_update = "OVERWRITE"

  tags = merge(var.common_tags, {
    Component = "inference-storage"
  })
}

# -----------------------------------------------------------------------------
# OIDC Provider for Service Account IAM Roles
# -----------------------------------------------------------------------------

data "tls_certificate" "eks" {
  url = aws_eks_cluster.inference.identity[0].oidc[0].issuer
}

resource "aws_iam_openid_connect_provider" "eks" {
  url             = aws_eks_cluster.inference.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.eks.certificates[0].sha1_fingerprint]

  tags = merge(var.common_tags, {
    Component = "inference-iam"
  })
}

# -----------------------------------------------------------------------------
# Application Load Balancer - gRPC/HTTP2 Support for Triton
# -----------------------------------------------------------------------------

resource "aws_lb" "inference" {
  name               = "${var.project_name}-${var.environment}-inf-${var.region_short}"
  internal           = var.alb_internal
  load_balancer_type = "application"
  security_groups    = [var.alb_security_group_id]
  subnets            = var.alb_internal ? var.private_subnet_ids : var.public_subnet_ids

  enable_deletion_protection = var.environment == "production" ? true : false
  enable_http2               = true
  idle_timeout               = 120
  drop_invalid_header_fields = true

  access_logs {
    bucket  = var.alb_access_logs_bucket
    prefix  = "inference-alb/${var.region_short}"
    enabled = var.enable_alb_access_logs
  }

  tags = merge(var.common_tags, {
    Name       = "${var.project_name}-${var.environment}-inference-alb-${var.region_short}"
    Component  = "inference-loadbalancer"
    CostCenter = "ml-inference"
  })
}

# -----------------------------------------------------------------------------
# Target Groups
# -----------------------------------------------------------------------------

# Triton HTTP endpoint (model inference REST API)
resource "aws_lb_target_group" "triton_http" {
  name                 = "${var.project_name}-triton-http-${var.region_short}"
  port                 = 8000
  protocol             = "HTTP"
  vpc_id               = var.vpc_id
  target_type          = "ip"
  deregistration_delay = 30

  health_check {
    enabled             = true
    path                = "/v2/health/ready"
    port                = "8000"
    protocol            = "HTTP"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 10
    interval            = 15
    matcher             = "200"
  }

  stickiness {
    type            = "lb_cookie"
    cookie_duration = 86400
    enabled         = true
  }

  tags = merge(var.common_tags, {
    Name       = "${var.project_name}-${var.environment}-triton-http-${var.region_short}"
    Component  = "triton-inference"
    Protocol   = "http"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# Triton gRPC endpoint (high-performance inference)
resource "aws_lb_target_group" "triton_grpc" {
  name                 = "${var.project_name}-triton-grpc-${var.region_short}"
  port                 = 8001
  protocol             = "HTTP"
  protocol_version     = "gRPC"
  vpc_id               = var.vpc_id
  target_type          = "ip"
  deregistration_delay = 30

  health_check {
    enabled             = true
    path                = "/nvidia.inferenceserver.GRPCInferenceService/ServerReady"
    port                = "8001"
    protocol            = "HTTP"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 10
    interval            = 15
    matcher             = "0-99"
  }

  tags = merge(var.common_tags, {
    Name       = "${var.project_name}-${var.environment}-triton-grpc-${var.region_short}"
    Component  = "triton-inference"
    Protocol   = "grpc"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# Triton Metrics endpoint (Prometheus scraping)
resource "aws_lb_target_group" "triton_metrics" {
  name                 = "${var.project_name}-triton-met-${var.region_short}"
  port                 = 8002
  protocol             = "HTTP"
  vpc_id               = var.vpc_id
  target_type          = "ip"
  deregistration_delay = 30

  health_check {
    enabled             = true
    path                = "/metrics"
    port                = "8002"
    protocol            = "HTTP"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }

  tags = merge(var.common_tags, {
    Name       = "${var.project_name}-${var.environment}-triton-metrics-${var.region_short}"
    Component  = "triton-monitoring"
    Protocol   = "http"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# -----------------------------------------------------------------------------
# ALB Listeners
# -----------------------------------------------------------------------------

# HTTPS Listener - default action routes to Triton HTTP
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.inference.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.triton_http.arn
  }

  tags = merge(var.common_tags, {
    Component = "inference-loadbalancer"
  })
}

# gRPC Listener on port 9000
resource "aws_lb_listener" "grpc" {
  load_balancer_arn = aws_lb.inference.arn
  port              = 9000
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.triton_grpc.arn
  }

  tags = merge(var.common_tags, {
    Component = "inference-loadbalancer"
  })
}

# HTTP to HTTPS redirect
resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.inference.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  tags = merge(var.common_tags, {
    Component = "inference-loadbalancer"
  })
}

# -----------------------------------------------------------------------------
# Listener Rules - Path-based routing
# -----------------------------------------------------------------------------

resource "aws_lb_listener_rule" "triton_health" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.triton_http.arn
  }

  condition {
    path_pattern {
      values = ["/v2/health/*", "/v2/models/*"]
    }
  }

  tags = merge(var.common_tags, {
    Component = "inference-routing"
  })
}

resource "aws_lb_listener_rule" "triton_metrics" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 20

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.triton_metrics.arn
  }

  condition {
    path_pattern {
      values = ["/metrics"]
    }
  }

  condition {
    source_ip {
      values = var.monitoring_cidr_blocks
    }
  }

  tags = merge(var.common_tags, {
    Component = "inference-monitoring"
  })
}

# -----------------------------------------------------------------------------
# WAF Association (optional)
# -----------------------------------------------------------------------------

resource "aws_wafv2_web_acl_association" "inference_alb" {
  count = var.waf_acl_arn != "" ? 1 : 0

  resource_arn = aws_lb.inference.arn
  web_acl_arn  = var.waf_acl_arn
}

# -----------------------------------------------------------------------------
# CloudWatch Alarms - Inference ALB
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "alb_5xx_errors" {
  alarm_name          = "${var.project_name}-${var.environment}-inference-5xx-${var.region_short}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = var.alb_5xx_threshold
  alarm_description   = "Inference ALB 5xx errors exceed threshold"
  alarm_actions       = var.alarm_sns_topic_arns
  ok_actions          = var.alarm_sns_topic_arns

  dimensions = {
    LoadBalancer = aws_lb.inference.arn_suffix
  }

  tags = merge(var.common_tags, {
    Component = "inference-monitoring"
  })
}

resource "aws_cloudwatch_metric_alarm" "alb_target_response_time" {
  alarm_name          = "${var.project_name}-${var.environment}-inference-latency-${var.region_short}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  extended_statistic  = "p99"
  threshold           = var.alb_latency_threshold_seconds
  alarm_description   = "Inference ALB p99 latency exceeds ${var.alb_latency_threshold_seconds}s"
  alarm_actions       = var.alarm_sns_topic_arns
  ok_actions          = var.alarm_sns_topic_arns

  dimensions = {
    LoadBalancer = aws_lb.inference.arn_suffix
  }

  tags = merge(var.common_tags, {
    Component = "inference-monitoring"
  })
}

resource "aws_cloudwatch_metric_alarm" "healthy_host_count" {
  alarm_name          = "${var.project_name}-${var.environment}-inference-healthy-hosts-${var.region_short}"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Minimum"
  threshold           = var.min_healthy_hosts
  alarm_description   = "Healthy inference hosts below minimum threshold of ${var.min_healthy_hosts}"
  alarm_actions       = var.alarm_sns_topic_arns

  dimensions = {
    LoadBalancer = aws_lb.inference.arn_suffix
    TargetGroup  = aws_lb_target_group.triton_http.arn_suffix
  }

  tags = merge(var.common_tags, {
    Component = "inference-monitoring"
  })
}
