###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# Networking Module - Multi-Region VPC with GPU-Optimized Subnets
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

data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

# -----------------------------------------------------------------------------
# VPC
# -----------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  # IPv6 support for EFA-enabled GPU instances
  assign_generated_ipv6_cidr_block = var.enable_ipv6

  tags = merge(var.common_tags, {
    Name                                            = "${var.project_name}-${var.environment}-vpc-${var.region_short}"
    "kubernetes.io/cluster/${var.eks_cluster_name}"  = "shared"
  })
}

# -----------------------------------------------------------------------------
# Internet Gateway
# -----------------------------------------------------------------------------

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = merge(var.common_tags, {
    Name = "${var.project_name}-${var.environment}-igw-${var.region_short}"
  })
}

# -----------------------------------------------------------------------------
# NAT Gateways (one per AZ for high availability)
# -----------------------------------------------------------------------------

resource "aws_eip" "nat" {
  count  = var.az_count
  domain = "vpc"

  tags = merge(var.common_tags, {
    Name = "${var.project_name}-${var.environment}-nat-eip-${var.region_short}-${count.index}"
  })

  depends_on = [aws_internet_gateway.main]
}

resource "aws_nat_gateway" "main" {
  count         = var.az_count
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = merge(var.common_tags, {
    Name = "${var.project_name}-${var.environment}-nat-${var.region_short}-${count.index}"
  })

  depends_on = [aws_internet_gateway.main]
}

# -----------------------------------------------------------------------------
# Public Subnets (Load Balancers, NAT Gateways)
# -----------------------------------------------------------------------------

resource "aws_subnet" "public" {
  count = var.az_count

  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = merge(var.common_tags, {
    Name                                            = "${var.project_name}-${var.environment}-public-${var.region_short}-${data.aws_availability_zones.available.names[count.index]}"
    "kubernetes.io/role/elb"                         = "1"
    "kubernetes.io/cluster/${var.eks_cluster_name}"  = "shared"
    Tier                                             = "public"
  })
}

# -----------------------------------------------------------------------------
# Private Subnets - GPU Inference Nodes (EFA-enabled)
# -----------------------------------------------------------------------------

resource "aws_subnet" "gpu_inference" {
  count = var.az_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + var.az_count)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = merge(var.common_tags, {
    Name                                            = "${var.project_name}-${var.environment}-gpu-${var.region_short}-${data.aws_availability_zones.available.names[count.index]}"
    "kubernetes.io/role/internal-elb"                = "1"
    "kubernetes.io/cluster/${var.eks_cluster_name}"  = "shared"
    Tier                                             = "gpu-inference"
    EFAEnabled                                       = "true"
  })
}

# -----------------------------------------------------------------------------
# Private Subnets - Application / General Workloads
# -----------------------------------------------------------------------------

resource "aws_subnet" "private_app" {
  count = var.az_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + (var.az_count * 2))
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = merge(var.common_tags, {
    Name                                            = "${var.project_name}-${var.environment}-private-app-${var.region_short}-${data.aws_availability_zones.available.names[count.index]}"
    "kubernetes.io/role/internal-elb"                = "1"
    "kubernetes.io/cluster/${var.eks_cluster_name}"  = "shared"
    Tier                                             = "private-app"
  })
}

# -----------------------------------------------------------------------------
# Private Subnets - Data Layer (Cache, Databases)
# -----------------------------------------------------------------------------

resource "aws_subnet" "private_data" {
  count = var.az_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + (var.az_count * 3))
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = merge(var.common_tags, {
    Name = "${var.project_name}-${var.environment}-private-data-${var.region_short}-${data.aws_availability_zones.available.names[count.index]}"
    Tier = "private-data"
  })
}

# -----------------------------------------------------------------------------
# Route Tables - Public
# -----------------------------------------------------------------------------

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  # Routes to peered VPCs
  dynamic "route" {
    for_each = var.peer_vpc_routes
    content {
      cidr_block                = route.value.cidr_block
      vpc_peering_connection_id = route.value.peering_connection_id
    }
  }

  tags = merge(var.common_tags, {
    Name = "${var.project_name}-${var.environment}-public-rt-${var.region_short}"
  })
}

resource "aws_route_table_association" "public" {
  count = var.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# -----------------------------------------------------------------------------
# Route Tables - Private (per-AZ for NAT gateway isolation)
# -----------------------------------------------------------------------------

resource "aws_route_table" "private" {
  count = var.az_count

  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }

  # Routes to peered VPCs
  dynamic "route" {
    for_each = var.peer_vpc_routes
    content {
      cidr_block                = route.value.cidr_block
      vpc_peering_connection_id = route.value.peering_connection_id
    }
  }

  tags = merge(var.common_tags, {
    Name = "${var.project_name}-${var.environment}-private-rt-${var.region_short}-${count.index}"
  })
}

resource "aws_route_table_association" "gpu_inference" {
  count = var.az_count

  subnet_id      = aws_subnet.gpu_inference[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

resource "aws_route_table_association" "private_app" {
  count = var.az_count

  subnet_id      = aws_subnet.private_app[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

resource "aws_route_table_association" "private_data" {
  count = var.az_count

  subnet_id      = aws_subnet.private_data[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# -----------------------------------------------------------------------------
# VPC Flow Logs
# -----------------------------------------------------------------------------

resource "aws_flow_log" "vpc" {
  vpc_id               = aws_vpc.main.id
  traffic_type         = "ALL"
  log_destination_type = "cloud-watch-logs"
  log_destination      = aws_cloudwatch_log_group.vpc_flow_logs.arn
  iam_role_arn         = var.flow_log_role_arn

  tags = merge(var.common_tags, {
    Name = "${var.project_name}-${var.environment}-flow-logs-${var.region_short}"
  })
}

resource "aws_cloudwatch_log_group" "vpc_flow_logs" {
  name              = "/netflix/${var.project_name}/${var.environment}/vpc-flow-logs/${var.region_short}"
  retention_in_days = var.flow_log_retention_days
  kms_key_id        = var.kms_key_arn

  tags = merge(var.common_tags, {
    Name = "${var.project_name}-${var.environment}-flow-logs-${var.region_short}"
  })
}

# -----------------------------------------------------------------------------
# VPC Peering Connections (cross-region)
# -----------------------------------------------------------------------------

resource "aws_vpc_peering_connection" "cross_region" {
  for_each = var.vpc_peering_configs

  vpc_id      = aws_vpc.main.id
  peer_vpc_id = each.value.peer_vpc_id
  peer_region = each.value.peer_region
  auto_accept = false

  tags = merge(var.common_tags, {
    Name     = "${var.project_name}-${var.environment}-peer-${var.region_short}-to-${each.key}"
    PeerFrom = data.aws_region.current.name
    PeerTo   = each.value.peer_region
  })
}

# -----------------------------------------------------------------------------
# VPC Peering Accepter (for peerings initiated from other regions)
# -----------------------------------------------------------------------------

resource "aws_vpc_peering_connection_accepter" "cross_region" {
  for_each = var.vpc_peering_accepter_ids

  vpc_peering_connection_id = each.value
  auto_accept               = true

  tags = merge(var.common_tags, {
    Name = "${var.project_name}-${var.environment}-peer-accepted-${each.key}-to-${var.region_short}"
  })
}

# -----------------------------------------------------------------------------
# Network ACLs - GPU Inference Subnets
# -----------------------------------------------------------------------------

resource "aws_network_acl" "gpu_inference" {
  vpc_id     = aws_vpc.main.id
  subnet_ids = aws_subnet.gpu_inference[*].id

  # Allow inbound from VPC CIDR (inter-node GPU communication)
  ingress {
    protocol   = "-1"
    rule_no    = 100
    action     = "allow"
    cidr_block = var.vpc_cidr
    from_port  = 0
    to_port    = 0
  }

  # Allow inbound from peered VPCs
  dynamic "ingress" {
    for_each = var.peer_vpc_cidrs
    content {
      protocol   = "-1"
      rule_no    = 200 + ingress.key
      action     = "allow"
      cidr_block = ingress.value
      from_port  = 0
      to_port    = 0
    }
  }

  # Allow inbound return traffic from internet (via NAT)
  ingress {
    protocol   = "tcp"
    rule_no    = 900
    action     = "allow"
    cidr_block = "0.0.0.0/0"
    from_port  = 1024
    to_port    = 65535
  }

  # Allow all outbound
  egress {
    protocol   = "-1"
    rule_no    = 100
    action     = "allow"
    cidr_block = "0.0.0.0/0"
    from_port  = 0
    to_port    = 0
  }

  tags = merge(var.common_tags, {
    Name = "${var.project_name}-${var.environment}-gpu-nacl-${var.region_short}"
  })
}

# -----------------------------------------------------------------------------
# EFA Security Group (Elastic Fabric Adapter for GPU communication)
# -----------------------------------------------------------------------------

resource "aws_security_group" "efa" {
  name_prefix = "${var.project_name}-${var.environment}-efa-"
  description = "Security group for EFA-enabled GPU instances - allows all traffic between EFA peers"
  vpc_id      = aws_vpc.main.id

  # EFA requires all traffic between members of the group
  ingress {
    description = "All traffic from EFA peers"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    description = "All traffic to EFA peers"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    description = "HTTPS outbound for AWS API access"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "HTTP outbound"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-efa-sg-${var.region_short}"
    Component = "gpu-networking"
  })

  lifecycle {
    create_before_destroy = true
  }
}
