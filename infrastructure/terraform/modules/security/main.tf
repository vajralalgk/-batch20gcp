###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# Security Module - IAM, KMS, Security Groups, VPC Endpoints, Secrets
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
data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# -----------------------------------------------------------------------------
# KMS Key for Model Encryption & Data at Rest
# -----------------------------------------------------------------------------

resource "aws_kms_key" "platform" {
  description             = "KMS key for Netflix LLM Platform - model encryption, secrets, and data at rest"
  deletion_window_in_days = var.kms_deletion_window_days
  enable_key_rotation     = true
  rotation_period_in_days = 365
  multi_region            = var.enable_multi_region_kms

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableRootAccountAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowEKSClusterEncryption"
        Effect = "Allow"
        Principal = {
          AWS = var.eks_cluster_role_arn
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
          "kms:CreateGrant"
        ]
        Resource = "*"
      },
      {
        Sid    = "AllowCloudWatchLogs"
        Effect = "Allow"
        Principal = {
          Service = "logs.${data.aws_region.current.name}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey"
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:${data.aws_partition.current.partition}:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:*"
          }
        }
      },
      {
        Sid    = "AllowSNSEncryption"
        Effect = "Allow"
        Principal = {
          Service = "sns.amazonaws.com"
        }
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey*"
        ]
        Resource = "*"
      }
    ]
  })

  tags = merge(var.common_tags, {
    Name       = "${var.project_name}-${var.environment}-platform-key-${var.region_short}"
    Component  = "encryption"
    CostCenter = "security"
  })
}

resource "aws_kms_alias" "platform" {
  name          = "alias/${var.project_name}-${var.environment}-${var.region_short}"
  target_key_id = aws_kms_key.platform.key_id
}

# -----------------------------------------------------------------------------
# IAM Role - EKS Cluster Control Plane
# -----------------------------------------------------------------------------

resource "aws_iam_role" "eks_cluster" {
  name = "${var.project_name}-${var.environment}-eks-cluster-${var.region_short}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "eks.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-eks-cluster-role-${var.region_short}"
    Component = "iam"
  })
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKSClusterPolicy"
  role       = aws_iam_role.eks_cluster.name
}

resource "aws_iam_role_policy_attachment" "eks_vpc_resource_controller" {
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKSVPCResourceController"
  role       = aws_iam_role.eks_cluster.name
}

# -----------------------------------------------------------------------------
# IAM Role - EKS Worker Nodes (GPU Inference)
# -----------------------------------------------------------------------------

resource "aws_iam_role" "eks_node" {
  name = "${var.project_name}-${var.environment}-eks-node-${var.region_short}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-eks-node-role-${var.region_short}"
    Component = "iam"
  })
}

resource "aws_iam_role_policy_attachment" "eks_worker_node_policy" {
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKSWorkerNodePolicy"
  role       = aws_iam_role.eks_node.name
}

resource "aws_iam_role_policy_attachment" "eks_cni_policy" {
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKS_CNI_Policy"
  role       = aws_iam_role.eks_node.name
}

resource "aws_iam_role_policy_attachment" "ecr_read_only" {
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
  role       = aws_iam_role.eks_node.name
}

resource "aws_iam_role_policy_attachment" "ssm_managed_instance" {
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
  role       = aws_iam_role.eks_node.name
}

# Custom policy for GPU inference nodes - S3 model access and CloudWatch metrics
resource "aws_iam_role_policy" "inference_node_policy" {
  name = "${var.project_name}-${var.environment}-inference-node-policy"
  role = aws_iam_role.eks_node.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowModelS3Access"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
          "s3:HeadObject"
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:s3:::${var.model_artifacts_bucket}",
          "arn:${data.aws_partition.current.partition}:s3:::${var.model_artifacts_bucket}/*"
        ]
      },
      {
        Sid    = "AllowCloudWatchMetrics"
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData",
          "cloudwatch:GetMetricData",
          "cloudwatch:ListMetrics"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "Netflix/GPUInference"
          }
        }
      },
      {
        Sid    = "AllowCloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams"
        ]
        Resource = "arn:${data.aws_partition.current.partition}:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/netflix/${var.project_name}/*"
      },
      {
        Sid    = "AllowKMSDecryptModels"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey",
          "kms:GenerateDataKey"
        ]
        Resource = [aws_kms_key.platform.arn]
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# IAM Role - EBS CSI Driver
# -----------------------------------------------------------------------------

resource "aws_iam_role" "ebs_csi" {
  name = "${var.project_name}-${var.environment}-ebs-csi-${var.region_short}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = var.oidc_provider_arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${var.oidc_provider_url}:sub" = "system:serviceaccount:kube-system:ebs-csi-controller-sa"
          "${var.oidc_provider_url}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-ebs-csi-role-${var.region_short}"
    Component = "iam"
  })
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
  role       = aws_iam_role.ebs_csi.name
}

# -----------------------------------------------------------------------------
# IAM Role - Monitoring (Prometheus, Grafana CloudWatch access)
# -----------------------------------------------------------------------------

resource "aws_iam_role" "monitoring" {
  name = "${var.project_name}-${var.environment}-monitoring-${var.region_short}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = var.oidc_provider_arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${var.oidc_provider_url}:sub" = "system:serviceaccount:monitoring:grafana"
          "${var.oidc_provider_url}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-monitoring-role-${var.region_short}"
    Component = "iam"
  })
}

resource "aws_iam_role_policy" "monitoring_cloudwatch" {
  name = "${var.project_name}-${var.environment}-monitoring-cw-policy"
  role = aws_iam_role.monitoring.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCloudWatchRead"
        Effect = "Allow"
        Action = [
          "cloudwatch:DescribeAlarms",
          "cloudwatch:GetDashboard",
          "cloudwatch:GetMetricData",
          "cloudwatch:ListDashboards",
          "cloudwatch:ListMetrics",
          "cloudwatch:GetMetricStatistics"
        ]
        Resource = "*"
      },
      {
        Sid    = "AllowCloudWatchLogsRead"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogGroups",
          "logs:GetLogEvents",
          "logs:GetLogRecord",
          "logs:GetQueryResults",
          "logs:StartQuery",
          "logs:StopQuery"
        ]
        Resource = "arn:${data.aws_partition.current.partition}:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/netflix/${var.project_name}/*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# IAM Role - VPC Flow Logs
# -----------------------------------------------------------------------------

resource "aws_iam_role" "flow_logs" {
  name = "${var.project_name}-${var.environment}-flow-logs-${var.region_short}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "vpc-flow-logs.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-flow-logs-role-${var.region_short}"
    Component = "iam"
  })
}

resource "aws_iam_role_policy" "flow_logs" {
  name = "${var.project_name}-${var.environment}-flow-logs-policy"
  role = aws_iam_role.flow_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams"
      ]
      Resource = "*"
    }]
  })
}

# -----------------------------------------------------------------------------
# Security Group - EKS Cluster Control Plane
# -----------------------------------------------------------------------------

resource "aws_security_group" "eks_cluster" {
  name_prefix = "${var.project_name}-${var.environment}-eks-cluster-"
  description = "Security group for EKS cluster control plane"
  vpc_id      = var.vpc_id

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-eks-cluster-sg-${var.region_short}"
    Component = "inference-security"
  })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group_rule" "eks_cluster_ingress_nodes" {
  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = aws_security_group.eks_cluster.id
  source_security_group_id = aws_security_group.inference_nodes.id
  description              = "Allow HTTPS from worker nodes to EKS API server"
}

resource "aws_security_group_rule" "eks_cluster_egress_nodes" {
  type                     = "egress"
  from_port                = 1025
  to_port                  = 65535
  protocol                 = "tcp"
  security_group_id        = aws_security_group.eks_cluster.id
  source_security_group_id = aws_security_group.inference_nodes.id
  description              = "Allow control plane to communicate with worker nodes"
}

resource "aws_security_group_rule" "eks_cluster_egress_nodes_443" {
  type                     = "egress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = aws_security_group.eks_cluster.id
  source_security_group_id = aws_security_group.inference_nodes.id
  description              = "Allow control plane to communicate with webhook pods on nodes"
}

# -----------------------------------------------------------------------------
# Security Group - Inference Nodes
# -----------------------------------------------------------------------------

resource "aws_security_group" "inference_nodes" {
  name_prefix = "${var.project_name}-${var.environment}-inference-"
  description = "Security group for GPU inference worker nodes"
  vpc_id      = var.vpc_id

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-inference-sg-${var.region_short}"
    Component = "inference-security"
  })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group_rule" "inference_nodes_self" {
  type              = "ingress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.inference_nodes.id
  self              = true
  description       = "Allow all traffic between inference nodes (GPU communication)"
}

resource "aws_security_group_rule" "inference_nodes_cluster_api" {
  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = aws_security_group.inference_nodes.id
  source_security_group_id = aws_security_group.eks_cluster.id
  description              = "Allow EKS control plane to communicate with nodes"
}

resource "aws_security_group_rule" "inference_nodes_cluster_kubelet" {
  type                     = "ingress"
  from_port                = 1025
  to_port                  = 65535
  protocol                 = "tcp"
  security_group_id        = aws_security_group.inference_nodes.id
  source_security_group_id = aws_security_group.eks_cluster.id
  description              = "Allow EKS control plane to reach kubelet and node ports"
}

resource "aws_security_group_rule" "inference_nodes_alb" {
  type                     = "ingress"
  from_port                = 8000
  to_port                  = 8002
  protocol                 = "tcp"
  security_group_id        = aws_security_group.inference_nodes.id
  source_security_group_id = aws_security_group.alb.id
  description              = "Allow ALB to reach Triton ports (HTTP 8000, gRPC 8001, Metrics 8002)"
}

resource "aws_security_group_rule" "inference_nodes_egress" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.inference_nodes.id
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "Allow all outbound traffic"
}

# -----------------------------------------------------------------------------
# Security Group - Application Load Balancer
# -----------------------------------------------------------------------------

resource "aws_security_group" "alb" {
  name_prefix = "${var.project_name}-${var.environment}-alb-"
  description = "Security group for inference Application Load Balancer"
  vpc_id      = var.vpc_id

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-alb-sg-${var.region_short}"
    Component = "inference-security"
  })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group_rule" "alb_ingress_https" {
  type              = "ingress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  security_group_id = aws_security_group.alb.id
  cidr_blocks       = var.alb_allowed_cidrs
  description       = "Allow HTTPS inbound to inference ALB"
}

resource "aws_security_group_rule" "alb_ingress_grpc" {
  type              = "ingress"
  from_port         = 9000
  to_port           = 9000
  protocol          = "tcp"
  security_group_id = aws_security_group.alb.id
  cidr_blocks       = var.alb_allowed_cidrs
  description       = "Allow gRPC inbound to inference ALB"
}

resource "aws_security_group_rule" "alb_ingress_http_redirect" {
  type              = "ingress"
  from_port         = 80
  to_port           = 80
  protocol          = "tcp"
  security_group_id = aws_security_group.alb.id
  cidr_blocks       = var.alb_allowed_cidrs
  description       = "Allow HTTP inbound (redirects to HTTPS)"
}

resource "aws_security_group_rule" "alb_egress_inference" {
  type                     = "egress"
  from_port                = 8000
  to_port                  = 8002
  protocol                 = "tcp"
  security_group_id        = aws_security_group.alb.id
  source_security_group_id = aws_security_group.inference_nodes.id
  description              = "Allow ALB to forward to Triton inference ports"
}

# -----------------------------------------------------------------------------
# Security Group - Monitoring
# -----------------------------------------------------------------------------

resource "aws_security_group" "monitoring" {
  name_prefix = "${var.project_name}-${var.environment}-monitoring-"
  description = "Security group for monitoring components (Prometheus, Grafana)"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Prometheus from monitoring nodes"
    from_port       = 9090
    to_port         = 9090
    protocol        = "tcp"
    security_groups = [aws_security_group.inference_nodes.id]
  }

  ingress {
    description     = "Grafana UI"
    from_port       = 3000
    to_port         = 3000
    protocol        = "tcp"
    cidr_blocks     = var.monitoring_access_cidrs
  }

  ingress {
    description     = "DCGM exporter metrics"
    from_port       = 9400
    to_port         = 9400
    protocol        = "tcp"
    self            = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound"
  }

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-monitoring-sg-${var.region_short}"
    Component = "monitoring-security"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# -----------------------------------------------------------------------------
# Security Group - Management / Bastion
# -----------------------------------------------------------------------------

resource "aws_security_group" "management" {
  name_prefix = "${var.project_name}-${var.environment}-mgmt-"
  description = "Security group for management and bastion hosts"
  vpc_id      = var.vpc_id

  ingress {
    description = "SSH from corporate network"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.management_access_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound"
  }

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-management-sg-${var.region_short}"
    Component = "management-security"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# -----------------------------------------------------------------------------
# VPC Endpoints - Private connectivity to AWS services
# -----------------------------------------------------------------------------

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = var.private_route_table_ids

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-s3-endpoint-${var.region_short}"
    Component = "vpc-endpoints"
  })
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = var.private_route_table_ids

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-dynamodb-endpoint-${var.region_short}"
    Component = "vpc-endpoints"
  })
}

resource "aws_security_group" "vpc_endpoints" {
  name_prefix = "${var.project_name}-${var.environment}-vpce-"
  description = "Security group for VPC interface endpoints"
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTPS from VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-vpce-sg-${var.region_short}"
    Component = "vpc-endpoints"
  })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_endpoint" "ecr_api" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.ecr.api"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-ecr-api-endpoint-${var.region_short}"
    Component = "vpc-endpoints"
  })
}

resource "aws_vpc_endpoint" "ecr_dkr" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.ecr.dkr"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-ecr-dkr-endpoint-${var.region_short}"
    Component = "vpc-endpoints"
  })
}

resource "aws_vpc_endpoint" "sts" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.sts"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-sts-endpoint-${var.region_short}"
    Component = "vpc-endpoints"
  })
}

resource "aws_vpc_endpoint" "logs" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.logs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-logs-endpoint-${var.region_short}"
    Component = "vpc-endpoints"
  })
}

# -----------------------------------------------------------------------------
# Secrets Manager - Credentials Storage
# -----------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "redis_auth" {
  name        = "${var.project_name}/${var.environment}/${var.region_short}/redis-auth-token"
  description = "Redis AUTH token for ElastiCache clusters"
  kms_key_id  = aws_kms_key.platform.arn

  recovery_window_in_days = var.environment == "production" ? 30 : 7

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-redis-auth-${var.region_short}"
    Component = "secrets"
  })
}

resource "aws_secretsmanager_secret" "grafana_admin" {
  name        = "${var.project_name}/${var.environment}/${var.region_short}/grafana-admin"
  description = "Grafana admin credentials for monitoring dashboard"
  kms_key_id  = aws_kms_key.platform.arn

  recovery_window_in_days = var.environment == "production" ? 30 : 7

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-grafana-admin-${var.region_short}"
    Component = "secrets"
  })
}

resource "aws_secretsmanager_secret" "model_api_keys" {
  name        = "${var.project_name}/${var.environment}/${var.region_short}/model-api-keys"
  description = "API keys for model inference endpoints"
  kms_key_id  = aws_kms_key.platform.arn

  recovery_window_in_days = var.environment == "production" ? 30 : 7

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-model-api-keys-${var.region_short}"
    Component = "secrets"
  })
}
