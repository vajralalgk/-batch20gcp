###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# Monitoring Module - Prometheus, Grafana, DCGM Exporter, CloudWatch
###############################################################################

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.12"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.25"
    }
  }
}

# -----------------------------------------------------------------------------
# Data Sources
# -----------------------------------------------------------------------------

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# Kubernetes Namespace for Monitoring
# -----------------------------------------------------------------------------

resource "kubernetes_namespace" "monitoring" {
  metadata {
    name = var.monitoring_namespace

    labels = {
      "app.kubernetes.io/managed-by" = "terraform"
      "netflix.com/platform"         = "llm-personalization"
      "purpose"                      = "monitoring"
    }
  }
}

# -----------------------------------------------------------------------------
# Prometheus Server - Helm Deployment
# -----------------------------------------------------------------------------

resource "helm_release" "prometheus" {
  name       = "prometheus"
  namespace  = kubernetes_namespace.monitoring.metadata[0].name
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "kube-prometheus-stack"
  version    = var.prometheus_chart_version

  values = [
    yamlencode({
      prometheus = {
        prometheusSpec = {
          replicas = var.prometheus_replicas
          retention = var.prometheus_retention

          resources = {
            requests = {
              cpu    = var.prometheus_cpu_request
              memory = var.prometheus_memory_request
            }
            limits = {
              cpu    = var.prometheus_cpu_limit
              memory = var.prometheus_memory_limit
            }
          }

          storageSpec = {
            volumeClaimTemplate = {
              spec = {
                storageClassName = "gp3"
                resources = {
                  requests = {
                    storage = var.prometheus_storage_size
                  }
                }
              }
            }
          }

          # Additional scrape configs for GPU and inference metrics
          additionalScrapeConfigs = [
            {
              job_name        = "dcgm-exporter"
              scrape_interval = "10s"
              kubernetes_sd_configs = [{
                role = "pod"
              }]
              relabel_configs = [
                {
                  source_labels = ["__meta_kubernetes_pod_label_app"]
                  regex         = "dcgm-exporter"
                  action        = "keep"
                },
                {
                  source_labels = ["__meta_kubernetes_namespace"]
                  target_label  = "namespace"
                },
                {
                  source_labels = ["__meta_kubernetes_pod_name"]
                  target_label  = "pod"
                }
              ]
            },
            {
              job_name        = "triton-inference-server"
              scrape_interval = "10s"
              kubernetes_sd_configs = [{
                role = "pod"
              }]
              relabel_configs = [
                {
                  source_labels = ["__meta_kubernetes_pod_label_app"]
                  regex         = "triton-inference-server"
                  action        = "keep"
                },
                {
                  source_labels = ["__meta_kubernetes_pod_annotation_prometheus_io_port"]
                  target_label  = "__address__"
                  regex         = "(\\d+)"
                  replacement   = "$${1}:8002"
                }
              ]
            }
          ]

          # Node affinity: prefer non-GPU nodes for monitoring
          affinity = {
            nodeAffinity = {
              preferredDuringSchedulingIgnoredDuringExecution = [{
                weight = 100
                preference = {
                  matchExpressions = [{
                    key      = "nvidia.com/gpu.present"
                    operator = "DoesNotExist"
                  }]
                }
              }]
            }
          }
        }
      }

      alertmanager = {
        alertmanagerSpec = {
          replicas = 2
          resources = {
            requests = {
              cpu    = "100m"
              memory = "256Mi"
            }
            limits = {
              cpu    = "500m"
              memory = "512Mi"
            }
          }
        }

        config = {
          route = {
            group_by        = ["alertname", "cluster", "service"]
            group_wait      = "10s"
            group_interval  = "5m"
            repeat_interval = "3h"
            receiver        = "sns-notifications"

            routes = [
              {
                match = {
                  severity = "critical"
                }
                receiver        = "sns-critical"
                repeat_interval = "15m"
              },
              {
                match = {
                  severity = "warning"
                }
                receiver        = "sns-notifications"
                repeat_interval = "1h"
              }
            ]
          }

          receivers = [
            {
              name = "sns-notifications"
              sns_configs = [{
                sigv4 = {
                  region = data.aws_region.current.name
                }
                topic_arn = var.alert_sns_topic_arn
                subject   = "Netflix LLM Platform Alert - {{ .GroupLabels.alertname }}"
              }]
            },
            {
              name = "sns-critical"
              sns_configs = [{
                sigv4 = {
                  region = data.aws_region.current.name
                }
                topic_arn = var.critical_sns_topic_arn
                subject   = "CRITICAL: Netflix LLM Platform - {{ .GroupLabels.alertname }}"
              }]
            }
          ]
        }
      }
    })
  ]

  set {
    name  = "grafana.enabled"
    value = "false"
  }

  timeout = 600

  depends_on = [kubernetes_namespace.monitoring]
}

# -----------------------------------------------------------------------------
# Grafana Deployment - Helm
# -----------------------------------------------------------------------------

resource "helm_release" "grafana" {
  name       = "grafana"
  namespace  = kubernetes_namespace.monitoring.metadata[0].name
  repository = "https://grafana.github.io/helm-charts"
  chart      = "grafana"
  version    = var.grafana_chart_version

  values = [
    yamlencode({
      replicas = var.grafana_replicas

      resources = {
        requests = {
          cpu    = "250m"
          memory = "512Mi"
        }
        limits = {
          cpu    = "1000m"
          memory = "1Gi"
        }
      }

      persistence = {
        enabled          = true
        storageClassName = "gp3"
        size             = var.grafana_storage_size
      }

      datasources = {
        "datasources.yaml" = {
          apiVersion = 1
          datasources = [
            {
              name      = "Prometheus"
              type      = "prometheus"
              url       = "http://prometheus-kube-prometheus-prometheus:9090"
              access    = "proxy"
              isDefault = true
            },
            {
              name   = "CloudWatch"
              type   = "cloudwatch"
              access = "proxy"
              jsonData = {
                authType      = "default"
                defaultRegion = data.aws_region.current.name
              }
            }
          ]
        }
      }

      dashboardProviders = {
        "dashboardproviders.yaml" = {
          apiVersion = 1
          providers = [{
            name            = "default"
            orgId           = 1
            folder          = "Netflix LLM Platform"
            type            = "file"
            disableDeletion = true
            editable        = false
            options = {
              path = "/var/lib/grafana/dashboards/default"
            }
          }]
        }
      }

      adminPassword = var.grafana_admin_password

      ingress = {
        enabled    = var.enable_grafana_ingress
        ingressClassName = "alb"
        annotations = {
          "alb.ingress.kubernetes.io/scheme"          = "internal"
          "alb.ingress.kubernetes.io/target-type"     = "ip"
          "alb.ingress.kubernetes.io/certificate-arn"  = var.acm_certificate_arn
          "alb.ingress.kubernetes.io/listen-ports"     = "[{\"HTTPS\":443}]"
        }
        hosts = var.grafana_hosts
      }

      # Node affinity: prefer non-GPU nodes
      affinity = {
        nodeAffinity = {
          preferredDuringSchedulingIgnoredDuringExecution = [{
            weight = 100
            preference = {
              matchExpressions = [{
                key      = "nvidia.com/gpu.present"
                operator = "DoesNotExist"
              }]
            }
          }]
        }
      }
    })
  ]

  timeout = 600

  depends_on = [kubernetes_namespace.monitoring, helm_release.prometheus]
}

# -----------------------------------------------------------------------------
# DCGM Exporter DaemonSet - GPU Metrics Collection
# -----------------------------------------------------------------------------

resource "kubernetes_daemon_set" "dcgm_exporter" {
  metadata {
    name      = "dcgm-exporter"
    namespace = kubernetes_namespace.monitoring.metadata[0].name

    labels = {
      app                    = "dcgm-exporter"
      "app.kubernetes.io/name"    = "dcgm-exporter"
      "app.kubernetes.io/part-of" = "nvidia-gpu-monitoring"
    }
  }

  spec {
    selector {
      match_labels = {
        app = "dcgm-exporter"
      }
    }

    template {
      metadata {
        labels = {
          app                    = "dcgm-exporter"
          "app.kubernetes.io/name"    = "dcgm-exporter"
          "app.kubernetes.io/part-of" = "nvidia-gpu-monitoring"
        }

        annotations = {
          "prometheus.io/scrape" = "true"
          "prometheus.io/port"   = "9400"
          "prometheus.io/path"   = "/metrics"
        }
      }

      spec {
        # Only run on GPU nodes
        node_selector = {
          "nvidia.com/gpu.present" = "true"
        }

        toleration {
          key      = "nvidia.com/gpu"
          operator = "Exists"
          effect   = "NoSchedule"
        }

        container {
          name  = "dcgm-exporter"
          image = var.dcgm_exporter_image

          port {
            name           = "metrics"
            container_port = 9400
            protocol       = "TCP"
          }

          env {
            name  = "DCGM_EXPORTER_KUBERNETES"
            value = "true"
          }

          env {
            name  = "DCGM_EXPORTER_LISTEN"
            value = ":9400"
          }

          # Collect comprehensive GPU metrics
          env {
            name  = "DCGM_EXPORTER_COLLECTORS"
            value = "/etc/dcgm-exporter/dcp-metrics-included.csv"
          }

          resources {
            requests = {
              cpu    = "100m"
              memory = "128Mi"
            }
            limits = {
              cpu    = "500m"
              memory = "256Mi"
            }
          }

          security_context {
            run_as_non_root = false
            privileged       = true
          }

          volume_mount {
            name       = "nvidia-install-dir"
            mount_path = "/usr/local/nvidia"
            read_only  = true
          }

          liveness_probe {
            http_get {
              path = "/health"
              port = 9400
            }
            initial_delay_seconds = 30
            period_seconds        = 15
          }

          readiness_probe {
            http_get {
              path = "/health"
              port = 9400
            }
            initial_delay_seconds = 15
            period_seconds        = 10
          }
        }

        volume {
          name = "nvidia-install-dir"
          host_path {
            path = "/usr/local/nvidia"
            type = "Directory"
          }
        }

        host_network = false
        dns_policy   = "ClusterFirst"

        restart_policy = "Always"
      }
    }
  }
}

# -----------------------------------------------------------------------------
# CloudWatch Integration - Custom Metrics
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "inference_metrics" {
  name              = "/netflix/${var.project_name}/${var.environment}/inference-metrics/${var.region_short}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-inference-metrics-${var.region_short}"
    Component = "monitoring"
  })
}

resource "aws_cloudwatch_log_group" "gpu_metrics" {
  name              = "/netflix/${var.project_name}/${var.environment}/gpu-metrics/${var.region_short}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-gpu-metrics-${var.region_short}"
    Component = "monitoring"
  })
}

resource "aws_cloudwatch_log_group" "application_logs" {
  name              = "/netflix/${var.project_name}/${var.environment}/application/${var.region_short}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-application-logs-${var.region_short}"
    Component = "monitoring"
  })
}

# -----------------------------------------------------------------------------
# SNS Topics for Alerts
# -----------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name              = "${var.project_name}-${var.environment}-alerts-${var.region_short}"
  kms_master_key_id = var.kms_key_id
  display_name      = "Netflix LLM Platform Alerts - ${var.environment} ${var.region_short}"

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-alerts-${var.region_short}"
    Component = "alerting"
  })
}

resource "aws_sns_topic" "critical_alerts" {
  name              = "${var.project_name}-${var.environment}-critical-alerts-${var.region_short}"
  kms_master_key_id = var.kms_key_id
  display_name      = "Netflix LLM Platform CRITICAL Alerts - ${var.environment} ${var.region_short}"

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-critical-alerts-${var.region_short}"
    Component = "alerting"
  })
}

resource "aws_sns_topic_subscription" "alert_email" {
  for_each = toset(var.alert_email_addresses)

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

resource "aws_sns_topic_subscription" "critical_alert_email" {
  for_each = toset(var.critical_email_addresses)

  topic_arn = aws_sns_topic.critical_alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

# SNS topic policy allowing CloudWatch to publish
resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowCloudWatchAlarms"
        Effect    = "Allow"
        Principal = { Service = "cloudwatch.amazonaws.com" }
        Action    = "SNS:Publish"
        Resource  = aws_sns_topic.alerts.arn
        Condition = {
          ArnLike = {
            "aws:SourceArn" = "arn:aws:cloudwatch:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:alarm:*"
          }
        }
      }
    ]
  })
}

resource "aws_sns_topic_policy" "critical_alerts" {
  arn = aws_sns_topic.critical_alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowCloudWatchAlarms"
        Effect    = "Allow"
        Principal = { Service = "cloudwatch.amazonaws.com" }
        Action    = "SNS:Publish"
        Resource  = aws_sns_topic.critical_alerts.arn
        Condition = {
          ArnLike = {
            "aws:SourceArn" = "arn:aws:cloudwatch:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:alarm:*"
          }
        }
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# CloudWatch Dashboard
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_dashboard" "inference_platform" {
  dashboard_name = "${var.project_name}-${var.environment}-inference-${var.region_short}"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "GPU Utilization (%)"
          metrics = [
            ["Netflix/GPUInference", "gpu_utilization_percentage", "ClusterName", var.eks_cluster_name]
          ]
          period = 60
          stat   = "Average"
          region = data.aws_region.current.name
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "GPU Memory Utilization (%)"
          metrics = [
            ["Netflix/GPUInference", "gpu_memory_utilization_percentage", "ClusterName", var.eks_cluster_name]
          ]
          period = 60
          stat   = "Average"
          region = data.aws_region.current.name
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title   = "Inference Latency (p99 ms)"
          metrics = [
            ["Netflix/GPUInference", "inference_p99_latency_ms", "ClusterName", var.eks_cluster_name]
          ]
          period = 60
          stat   = "Average"
          region = data.aws_region.current.name
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title   = "Inference Requests Per Second"
          metrics = [
            ["Netflix/GPUInference", "inference_requests_per_second", "ClusterName", var.eks_cluster_name]
          ]
          period = 60
          stat   = "Sum"
          region = data.aws_region.current.name
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 8
        height = 6
        properties = {
          title   = "Redis KV Cache - Hit Rate"
          metrics = [
            ["AWS/ElastiCache", "CacheHitRate", "ReplicationGroupId", "${var.project_name}-kv-${var.region_short}"]
          ]
          period = 60
          stat   = "Average"
          region = data.aws_region.current.name
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 12
        width  = 8
        height = 6
        properties = {
          title   = "Redis Session Memory - CPU"
          metrics = [
            ["AWS/ElastiCache", "EngineCPUUtilization", "ReplicationGroupId", "${var.project_name}-sess-${var.region_short}"]
          ]
          period = 60
          stat   = "Average"
          region = data.aws_region.current.name
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 12
        width  = 8
        height = 6
        properties = {
          title   = "ALB Request Count"
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", var.alb_arn_suffix]
          ]
          period = 60
          stat   = "Sum"
          region = data.aws_region.current.name
          view   = "timeSeries"
        }
      }
    ]
  })
}
