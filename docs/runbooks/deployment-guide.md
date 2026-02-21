# Enterprise Cloud Transformation Platform (ECTP)
# Comprehensive Deployment Guide

**Document ID:** ECTP-RUNBOOK-001
**Author:** Gopi Krishna Vajrala
**Version:** 2.0.0
**Date:** 2026-02-16
**Classification:** Internal - Confidential
**Status:** Approved

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 2.0.0 | 2026-02-16 | Gopi Krishna Vajrala | Comprehensive rewrite with full deployment procedures |
| 1.0.0 | 2026-02-16 | Gopi Krishna Vajrala | Initial release |

---

## Table of Contents

1. [Overview](#1-overview)
2. [Prerequisites](#2-prerequisites)
3. [Environment Setup](#3-environment-setup)
4. [Terraform Deployment](#4-terraform-deployment)
5. [Docker Deployment](#5-docker-deployment)
6. [Kubernetes Deployment](#6-kubernetes-deployment)
7. [Rollback Procedures](#7-rollback-procedures)
8. [Post-Deployment Validation Checklist](#8-post-deployment-validation-checklist)
9. [Troubleshooting](#9-troubleshooting)
10. [Appendix](#10-appendix)

---

## 1. Overview

This document provides comprehensive, step-by-step instructions for deploying the Enterprise Cloud Transformation Platform (ECTP) across all environments. It covers Terraform-based infrastructure provisioning, Docker-based containerized deployments, and Kubernetes orchestration for production-grade environments.

### 1.1 Deployment Architecture

```
Developer Workstation
       |
       v
  Git Repository (feature branch)
       |
       v
  CI/CD Pipeline (GitHub Actions)
       |
       +---> Terraform Plan/Apply (Infrastructure)
       |
       +---> Docker Build/Push (Application Images)
       |
       +---> Kubernetes / ECS Deploy (Orchestration)
       |
       v
  Target Environment (dev / qa / uat / prod)
       |
       v
  Post-Deployment Validation
       |
       v
  Monitoring & Observability
```

### 1.2 Deployment Environments

| Environment | Purpose | Approval Required | Auto-Deploy | Terraform Workspace |
|-------------|---------|-------------------|-------------|---------------------|
| Development | Feature development, unit testing | No | Yes (on merge to `develop`) | `ectp-dev` |
| QA | Integration testing, regression | No | Yes (on merge to `release/*`) | `ectp-qa` |
| UAT | User acceptance testing, stakeholder review | Tech Lead | No (manual trigger) | `ectp-uat` |
| Production | Live traffic, end users | Change Advisory Board (CAB) | No (manual trigger) | `ectp-prod` |

### 1.3 Deployment Cadence

| Environment | Frequency | Window | Notification |
|-------------|-----------|--------|--------------|
| Development | Continuous | Anytime | None required |
| QA | Daily | Overnight (2:00 AM - 4:00 AM ET) | Slack #ectp-qa |
| UAT | Weekly | Tuesday 6:00 AM - 8:00 AM ET | Email to stakeholders |
| Production | Bi-weekly | Saturday 2:00 AM - 6:00 AM ET | ServiceNow change ticket |

### 1.4 Deployment Flow Across Environments

```
  DEV             QA              UAT             PROD
  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌──────────┐
  │  Auto   │───>│  Auto   │───>│ Manual  │───>│  Manual  │
  │ on push │    │on merge │    │ trigger │    │ trigger  │
  └─────────┘    └─────────┘    └─────────┘    └──────────┘
  No Approval   Team Lead      Product Owner   Change Board
                Approval        Approval        Approval
```

---

## 2. Prerequisites

### 2.1 Required Tools

| Tool | Minimum Version | Purpose | Install Command |
|------|----------------|---------|-----------------|
| Python | 3.11+ | Application runtime | `pyenv install 3.11.7` |
| Terraform | 1.6+ | Infrastructure as Code | `tfenv install 1.6.6` |
| Docker | 24.0+ | Containerization | See Docker docs |
| Docker Compose | 2.23+ | Local multi-container | Included with Docker Desktop |
| kubectl | 1.28+ | Kubernetes CLI | `brew install kubectl` |
| Helm | 3.13+ | Kubernetes package manager | `brew install helm` |
| AWS CLI | 2.15+ | AWS interaction | `brew install awscli` |
| Git | 2.40+ | Version control | `brew install git` |
| jq | 1.7+ | JSON processing | `brew install jq` |
| psql | 15+ | Database client | `brew install postgresql@15` |
| redis-cli | 7.0+ | Redis client | `brew install redis` |

**Version Check Commands:**

```bash
python3 --version      # >= 3.11
terraform --version    # >= 1.6
aws --version          # >= 2.x
docker --version       # >= 24.x
kubectl version        # >= 1.28
helm version           # >= 3.13
```

### 2.2 AWS Account Access

Ensure the following AWS credentials and permissions are configured:

```bash
# Configure AWS CLI profile for the target environment
aws configure --profile ectp-dev
aws configure --profile ectp-qa
aws configure --profile ectp-uat
aws configure --profile ectp-prod

# Verify credentials
aws sts get-caller-identity --profile ectp-dev
```

**Required IAM Permissions (minimum):**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecs:*",
        "ecr:*",
        "ec2:Describe*",
        "ec2:CreateSecurityGroup",
        "ec2:AuthorizeSecurityGroupIngress",
        "rds:*",
        "elasticache:*",
        "s3:*",
        "sqs:*",
        "sns:*",
        "cloudwatch:*",
        "logs:*",
        "iam:PassRole",
        "iam:GetRole",
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret",
        "ssm:GetParameter",
        "ssm:PutParameter",
        "kms:Decrypt",
        "kms:GenerateDataKey",
        "eks:*",
        "elasticloadbalancing:*"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestedRegion": "us-east-1"
        }
      }
    }
  ]
}
```

### 2.3 Repository Access

```bash
# Clone the repository
git clone git@github.com:organization/ectp-platform.git
cd ectp-platform

# Install Python dependencies
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Verify installation
python -c "from src.api.main import app; print('Import successful')"
```

### 2.4 Required Secrets

Ensure the following secrets are available in AWS Secrets Manager or as environment variables:

| Secret | Environment Variable | Description | Rotation |
|--------|---------------------|-------------|----------|
| Database Password | `ECTP_DB_PASSWORD` | PostgreSQL master password | 90-day rotation |
| Redis Password | `ECTP_REDIS_PASSWORD` | ElastiCache AUTH token | 90-day rotation |
| JWT Secret Key | `ECTP_JWT_SECRET_KEY` | JWT signing secret (256-bit random) | Annual rotation |
| ServiceNow Client Secret | `ECTP_SERVICENOW_CLIENT_SECRET` | OAuth2 client secret | 90-day rotation |
| Ellucian API Key | `ECTP_ELLUCIAN_API_KEY` | Ethos platform API key | Quarterly rotation |

### 2.5 Network Prerequisites

- VPN connection to on-premises network established and tested
- DNS entries configured in Route53 for all environments
- SSL/TLS certificates provisioned via ACM (auto-renewal enabled)
- Security groups and NACLs reviewed and approved by Security team
- Transit Gateway or VPC peering configured for cross-account communication

---

## 3. Environment Setup

### 3.1 Development Environment

```bash
# ------------------------------------------------------------------
# Development Environment Setup
# Purpose: Local development and feature testing
# Author: Gopi Krishna Vajrala
# ------------------------------------------------------------------

# 1. Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 3. Copy environment template
cp .env.example .env

# 4. Edit .env with development values
cat > .env << 'EOF'
ECTP_APP_NAME=ECTP
ECTP_APP_VERSION=1.0.0
ECTP_ENV=development
ECTP_DEBUG=true
ECTP_LOG_LEVEL=DEBUG
ECTP_HOST=0.0.0.0
ECTP_PORT=8000

# Database (local Docker)
ECTP_DB_HOST=localhost
ECTP_DB_PORT=5432
ECTP_DB_NAME=ectp_dev
ECTP_DB_USER=ectp_user
ECTP_DB_PASSWORD=dev_password_change_me
ECTP_DB_POOL_SIZE=5
ECTP_DB_MAX_OVERFLOW=10

# Redis (local Docker)
ECTP_REDIS_HOST=localhost
ECTP_REDIS_PORT=6379
ECTP_REDIS_DB=0
ECTP_REDIS_PASSWORD=

# AWS
ECTP_AWS_REGION=us-east-1
ECTP_AWS_ACCOUNT_ID=123456789012

# JWT
ECTP_JWT_SECRET_KEY=dev-secret-key-not-for-production
ECTP_JWT_ALGORITHM=HS256
ECTP_JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60

# CORS
ECTP_CORS_ORIGINS=http://localhost:3000,http://localhost:8080

# ServiceNow (dev instance)
ECTP_SERVICENOW_INSTANCE_URL=https://dev-instance.service-now.com
ECTP_SERVICENOW_CLIENT_ID=dev-client-id
ECTP_SERVICENOW_CLIENT_SECRET=dev-client-secret

# Ellucian (sandbox)
ECTP_ELLUCIAN_ETHOS_API_URL=https://integrate.elluciancloud.com
ECTP_ELLUCIAN_API_KEY=dev-api-key
EOF

# 5. Start local infrastructure dependencies
docker compose -f docker-compose.dev.yml up -d postgres redis

# 6. Wait for services to be ready
echo "Waiting for PostgreSQL..."
until docker compose -f docker-compose.dev.yml exec postgres pg_isready -U ectp_user; do
  sleep 2
done
echo "PostgreSQL is ready."

echo "Waiting for Redis..."
until docker compose -f docker-compose.dev.yml exec redis redis-cli ping; do
  sleep 2
done
echo "Redis is ready."

# 7. Run database migrations
alembic upgrade head

# 8. Start the development server with hot-reload
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

# 9. Verify the application is running
curl -s http://localhost:8000/health | jq .
```

### 3.2 QA Environment

```bash
# ------------------------------------------------------------------
# QA Environment Configuration
# Purpose: Integration testing and automated regression
# Author: Gopi Krishna Vajrala
# ------------------------------------------------------------------

export AWS_PROFILE=ectp-qa
export TF_VAR_environment=qa
export TF_VAR_aws_region=us-east-1
export TF_VAR_instance_type=t3.medium
export TF_VAR_db_instance_class=db.t3.medium
export TF_VAR_redis_node_type=cache.t3.medium
export TF_VAR_ecs_desired_count=2
export TF_VAR_ecs_min_count=1
export TF_VAR_ecs_max_count=4
export TF_VAR_multi_az=false
export TF_VAR_deletion_protection=false
export TF_VAR_backup_retention_period=7

# QA-specific application settings
export ECTP_ENV=qa
export ECTP_DEBUG=false
export ECTP_LOG_LEVEL=INFO
```

### 3.3 UAT Environment

```bash
# ------------------------------------------------------------------
# UAT Environment Configuration
# Purpose: Stakeholder acceptance testing, mirrors production
# Author: Gopi Krishna Vajrala
# ------------------------------------------------------------------

export AWS_PROFILE=ectp-uat
export TF_VAR_environment=uat
export TF_VAR_aws_region=us-east-1
export TF_VAR_instance_type=t3.large
export TF_VAR_db_instance_class=db.r6g.large
export TF_VAR_redis_node_type=cache.r6g.large
export TF_VAR_ecs_desired_count=2
export TF_VAR_ecs_min_count=2
export TF_VAR_ecs_max_count=6
export TF_VAR_multi_az=true
export TF_VAR_deletion_protection=false
export TF_VAR_backup_retention_period=14

# UAT-specific application settings
export ECTP_ENV=uat
export ECTP_DEBUG=false
export ECTP_LOG_LEVEL=INFO
```

### 3.4 Production Environment

```bash
# ------------------------------------------------------------------
# Production Environment Configuration
# Purpose: Live traffic, end-user facing
# IMPORTANT: All production deployments require CAB approval
# Author: Gopi Krishna Vajrala
# ------------------------------------------------------------------

export AWS_PROFILE=ectp-prod
export TF_VAR_environment=production
export TF_VAR_aws_region=us-east-1
export TF_VAR_instance_type=t3.xlarge
export TF_VAR_db_instance_class=db.r6g.xlarge
export TF_VAR_redis_node_type=cache.r6g.xlarge
export TF_VAR_ecs_desired_count=3
export TF_VAR_ecs_min_count=3
export TF_VAR_ecs_max_count=12
export TF_VAR_multi_az=true
export TF_VAR_deletion_protection=true
export TF_VAR_backup_retention_period=35
export TF_VAR_performance_insights_enabled=true

# Production-specific application settings
export ECTP_ENV=production
export ECTP_DEBUG=false
export ECTP_LOG_LEVEL=WARNING
```

---

## 4. Terraform Deployment

### 4.1 Terraform Project Structure

```
infrastructure/
  terraform/
    modules/
      vpc/              # VPC, subnets, NAT gateways, transit gateway
      ecs/              # ECS cluster, services, task definitions
      rds/              # RDS PostgreSQL instances, replicas
      elasticache/      # Redis cluster, replication groups
      s3/               # S3 buckets with encryption, lifecycle
      iam/              # IAM roles, policies, service accounts
      monitoring/       # CloudWatch dashboards, alarms, log groups
      security/         # WAF, Security Groups, KMS keys
      dns/              # Route53 records, health checks
    environments/
      dev/
        main.tf
        variables.tf
        terraform.tfvars
        backend.tf
        outputs.tf
      qa/
      uat/
      prod/
    global/
      route53/          # Hosted zones
      acm/              # SSL certificates
      ecr/              # Container registries
      organizations/    # AWS Organizations config
```

### 4.2 Backend Configuration

```hcl
# backend.tf - S3 backend for remote state management
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "ectp-terraform-state-ACCOUNT_ID"
    key            = "ENVIRONMENT/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "ectp-terraform-locks"
    encrypt        = true
    kms_key_id     = "alias/ectp-terraform-state-key"
  }
}
```

### 4.3 Step-by-Step Terraform Deployment

```bash
# ------------------------------------------------------------------
# Terraform Deployment Steps
# Author: Gopi Krishna Vajrala
# ------------------------------------------------------------------

# Step 1: Navigate to the target environment directory
cd infrastructure/terraform/environments/${TF_VAR_environment}

# Step 2: Initialize Terraform (downloads providers, configures backend)
terraform init \
  -backend-config="bucket=ectp-terraform-state-${AWS_ACCOUNT_ID}" \
  -backend-config="key=${TF_VAR_environment}/terraform.tfstate" \
  -backend-config="region=us-east-1"

# Step 3: Validate Terraform configuration syntax
terraform validate

# Step 4: Format check (CI/CD enforces this)
terraform fmt -check -recursive

# Step 5: Generate and review the execution plan
terraform plan \
  -var-file="terraform.tfvars" \
  -out="${TF_VAR_environment}-plan.tfplan" \
  2>&1 | tee plan-output.txt

# Step 6: Review plan output carefully
echo "============================================"
echo "REVIEW THE PLAN OUTPUT ABOVE CAREFULLY"
echo "Ensure no unexpected resource destruction"
echo "Verify all resource changes are intentional"
echo "============================================"

# Step 7: Apply the plan (requires explicit approval for UAT/Prod)
terraform apply "${TF_VAR_environment}-plan.tfplan"

# Step 8: Capture outputs for downstream deployment steps
terraform output -json > terraform-outputs.json

# Step 9: Store critical outputs as SSM parameters
aws ssm put-parameter \
  --name "/ectp/${TF_VAR_environment}/rds-endpoint" \
  --value "$(terraform output -raw rds_endpoint)" \
  --type SecureString --overwrite

aws ssm put-parameter \
  --name "/ectp/${TF_VAR_environment}/redis-endpoint" \
  --value "$(terraform output -raw redis_endpoint)" \
  --type SecureString --overwrite

aws ssm put-parameter \
  --name "/ectp/${TF_VAR_environment}/ecs-cluster-name" \
  --value "$(terraform output -raw ecs_cluster_name)" \
  --type String --overwrite

aws ssm put-parameter \
  --name "/ectp/${TF_VAR_environment}/alb-dns-name" \
  --value "$(terraform output -raw alb_dns_name)" \
  --type String --overwrite

# Step 10: Verify infrastructure health
echo "Verifying infrastructure..."
aws ecs describe-clusters --clusters $(terraform output -raw ecs_cluster_name)
aws rds describe-db-instances --db-instance-identifier $(terraform output -raw rds_instance_id)
```

### 4.4 Terraform Module Deployment Order

Infrastructure modules must be deployed in a specific order due to dependencies:

```
1. VPC & Networking     (no dependencies)
2. Security Groups      (depends on: VPC)
3. IAM Roles            (no dependencies)
4. KMS Keys             (depends on: IAM)
5. RDS Database         (depends on: VPC, Security Groups, KMS)
6. ElastiCache          (depends on: VPC, Security Groups)
7. S3 Buckets           (depends on: KMS)
8. ECR Repository       (no dependencies)
9. ECS Cluster          (depends on: VPC, IAM, ECR)
10. ECS Services        (depends on: ECS Cluster, RDS, ElastiCache)
11. CloudWatch Alarms   (depends on: ECS Services)
12. WAF & Shield        (depends on: ALB from ECS)
13. DNS Records         (depends on: ALB, ACM)
```

### 4.5 Terraform State Management

```bash
# List all resources in current state
terraform state list

# Show specific resource details
terraform state show aws_ecs_service.ectp_api

# Import existing resources into state (for migration scenarios)
terraform import aws_rds_instance.ectp_db ectp-prod-db

# Move resources between state files (for refactoring)
terraform state mv aws_s3_bucket.old aws_s3_bucket.new

# CAUTION: Remove from state without destroying resource
terraform state rm aws_s3_bucket.do_not_destroy

# Refresh state to detect drift
terraform plan -refresh-only
```

---

## 5. Docker Deployment

### 5.1 Dockerfile (Multi-Stage Production Build)

```dockerfile
# ------------------------------------------------------------------
# ECTP Platform - Production Dockerfile
# Multi-stage build for security and minimal image size
# Author: Gopi Krishna Vajrala
# ------------------------------------------------------------------

# Stage 1: Build stage - Install dependencies
FROM python:3.11-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Production stage - Minimal runtime
FROM python:3.11-slim AS production
RUN groupadd -r ectp && useradd -r -g ectp -d /app -s /sbin/nologin ectp
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /install /usr/local
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini .
RUN chown -R ectp:ectp /app
USER ectp
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1
EXPOSE 8000
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

### 5.2 Docker Build and Push to ECR

```bash
# ------------------------------------------------------------------
# Docker Build and Push to ECR
# Author: Gopi Krishna Vajrala
# ------------------------------------------------------------------

# Variables
ENVIRONMENT=${TF_VAR_environment:-dev}
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION=${TF_VAR_aws_region:-us-east-1}
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE_NAME="ectp-platform"
IMAGE_TAG=$(git rev-parse --short HEAD)
FULL_TAG="${ECR_REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
LATEST_TAG="${ECR_REGISTRY}/${IMAGE_NAME}:${ENVIRONMENT}-latest"

# Step 1: Authenticate Docker with ECR
aws ecr get-login-password --region ${AWS_REGION} | \
  docker login --username AWS --password-stdin ${ECR_REGISTRY}

# Step 2: Build the Docker image
echo "Building Docker image: ${FULL_TAG}"
docker build \
  --target production \
  --build-arg BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --build-arg VCS_REF=${IMAGE_TAG} \
  --build-arg VERSION=$(cat VERSION 2>/dev/null || echo "1.0.0") \
  -t ${FULL_TAG} \
  -t ${LATEST_TAG} \
  -f Dockerfile .

# Step 3: Run security scan on the image
echo "Running container security scan..."
docker scout cves ${FULL_TAG} --exit-code --only-severity critical,high

# Step 4: Run smoke test
echo "Running smoke test..."
docker run --rm -d --name ectp-smoke-test \
  -e ECTP_ENV=development -e ECTP_DB_HOST=localhost \
  -p 8000:8000 ${FULL_TAG}
sleep 10
HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health)
docker stop ectp-smoke-test
if [ "${HEALTH_STATUS}" != "200" ]; then
  echo "ERROR: Smoke test failed. Health check returned ${HEALTH_STATUS}"
  exit 1
fi
echo "Smoke test passed."

# Step 5: Push to ECR
echo "Pushing image to ECR..."
docker push ${FULL_TAG}
docker push ${LATEST_TAG}
echo "Successfully pushed: ${FULL_TAG}"
```

### 5.3 Docker Compose (Local Development)

```yaml
# docker-compose.dev.yml
version: "3.9"
services:
  ectp-api:
    build:
      context: .
      dockerfile: Dockerfile
      target: production
    ports:
      - "8000:8000"
    environment:
      - ECTP_ENV=development
      - ECTP_DEBUG=true
      - ECTP_LOG_LEVEL=DEBUG
      - ECTP_DB_HOST=postgres
      - ECTP_DB_PORT=5432
      - ECTP_DB_NAME=ectp_dev
      - ECTP_DB_USER=ectp_user
      - ECTP_DB_PASSWORD=dev_password
      - ECTP_REDIS_HOST=redis
      - ECTP_REDIS_PORT=6379
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  postgres:
    image: postgres:15-alpine
    ports:
      - "5432:5432"
    environment:
      - POSTGRES_DB=ectp_dev
      - POSTGRES_USER=ectp_user
      - POSTGRES_PASSWORD=dev_password
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ectp_user -d ectp_dev"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    volumes:
      - redisdata:/data

volumes:
  pgdata:
  redisdata:
```

### 5.4 ECS Deployment

```bash
# ------------------------------------------------------------------
# Deploy to ECS (Fargate)
# Author: Gopi Krishna Vajrala
# ------------------------------------------------------------------

CLUSTER_NAME=$(aws ssm get-parameter \
  --name "/ectp/${ENVIRONMENT}/ecs-cluster-name" \
  --query "Parameter.Value" --output text)
SERVICE_NAME="ectp-api-${ENVIRONMENT}"

# Step 1: Create pre-deployment RDS snapshot (production only)
if [ "${ENVIRONMENT}" = "production" ]; then
  echo "Creating pre-deployment database snapshot..."
  aws rds create-db-snapshot \
    --db-instance-identifier ectp-${ENVIRONMENT}-db \
    --db-snapshot-identifier ectp-pre-deploy-$(date +%Y%m%d-%H%M%S)
fi

# Step 2: Register new task definition with updated image
TASK_DEF=$(aws ecs describe-task-definition \
  --task-definition ${SERVICE_NAME} --query 'taskDefinition' --output json)

NEW_TASK_DEF=$(echo ${TASK_DEF} | jq \
  --arg IMAGE "${FULL_TAG}" \
  '.containerDefinitions[0].image = $IMAGE |
   del(.taskDefinitionArn, .revision, .status, .requiresAttributes,
       .compatibilities, .registeredAt, .registeredBy)')

NEW_TASK_ARN=$(aws ecs register-task-definition \
  --cli-input-json "${NEW_TASK_DEF}" \
  --query 'taskDefinition.taskDefinitionArn' --output text)
echo "New task definition: ${NEW_TASK_ARN}"

# Step 3: Update ECS service with new task definition
aws ecs update-service \
  --cluster ${CLUSTER_NAME} --service ${SERVICE_NAME} \
  --task-definition ${NEW_TASK_ARN} --force-new-deployment

# Step 4: Wait for deployment to stabilize
echo "Waiting for deployment to stabilize..."
aws ecs wait services-stable --cluster ${CLUSTER_NAME} --services ${SERVICE_NAME}
echo "Deployment completed successfully."

# Step 5: Verify new tasks are running
aws ecs describe-services \
  --cluster ${CLUSTER_NAME} --services ${SERVICE_NAME} \
  --query 'services[0].{desiredCount:desiredCount,runningCount:runningCount}' \
  --output table
```

---

## 6. Kubernetes Deployment

### 6.1 Kubernetes Manifests

#### Namespace and Deployment

```yaml
# k8s/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: ectp
  labels:
    app.kubernetes.io/name: ectp
    app.kubernetes.io/managed-by: kubectl

---
# k8s/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ectp-api
  namespace: ectp
  labels:
    app.kubernetes.io/name: ectp-api
    app.kubernetes.io/version: "1.0.0"
    app.kubernetes.io/component: api
    app.kubernetes.io/part-of: ectp
spec:
  replicas: 3
  revisionHistoryLimit: 5
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  selector:
    matchLabels:
      app.kubernetes.io/name: ectp-api
  template:
    metadata:
      labels:
        app.kubernetes.io/name: ectp-api
    spec:
      serviceAccountName: ectp-api
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        fsGroup: 1000
      terminationGracePeriodSeconds: 60
      containers:
        - name: ectp-api
          image: ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/ectp-platform:latest
          ports:
            - containerPort: 8000
              protocol: TCP
              name: http
          envFrom:
            - configMapRef:
                name: ectp-config
            - secretRef:
                name: ectp-secrets
          resources:
            requests:
              cpu: "250m"
              memory: "512Mi"
            limits:
              cpu: "1000m"
              memory: "1Gi"
          livenessProbe:
            httpGet:
              path: /health
              port: http
            initialDelaySeconds: 30
            periodSeconds: 15
            timeoutSeconds: 5
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health/ready
              port: http
            initialDelaySeconds: 15
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
          startupProbe:
            httpGet:
              path: /health
              port: http
            initialDelaySeconds: 10
            periodSeconds: 5
            failureThreshold: 30
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels:
              app.kubernetes.io/name: ectp-api
```

#### Service, Ingress, and HPA

```yaml
# k8s/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: ectp-api
  namespace: ectp
spec:
  type: ClusterIP
  ports:
    - port: 80
      targetPort: http
      protocol: TCP
      name: http
  selector:
    app.kubernetes.io/name: ectp-api

---
# k8s/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ectp-api
  namespace: ectp
  annotations:
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/ssl-policy: ELBSecurityPolicy-TLS13-1-2-2021-06
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTPS":443}]'
    alb.ingress.kubernetes.io/healthcheck-path: /health
spec:
  rules:
    - host: api.ectp.example.edu
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: ectp-api
                port:
                  number: 80

---
# k8s/hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: ectp-api
  namespace: ectp
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: ectp-api
  minReplicas: 3
  maxReplicas: 12
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
        - type: Pods
          value: 2
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
        - type: Pods
          value: 1
          periodSeconds: 120
```

### 6.2 Kubernetes Deployment Steps

```bash
# ------------------------------------------------------------------
# Kubernetes Deployment Steps
# Author: Gopi Krishna Vajrala
# ------------------------------------------------------------------

# Step 1: Set the kubectl context
aws eks update-kubeconfig \
  --name ectp-${ENVIRONMENT}-cluster \
  --region us-east-1 --profile ectp-${ENVIRONMENT}

# Step 2: Verify cluster connectivity
kubectl cluster-info
kubectl get nodes

# Step 3: Apply namespace and configuration
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml

# Step 4: Create/update secrets
kubectl create secret generic ectp-secrets \
  --namespace ectp \
  --from-literal=ECTP_DB_PASSWORD="${DB_PASSWORD}" \
  --from-literal=ECTP_REDIS_PASSWORD="${REDIS_PASSWORD}" \
  --from-literal=ECTP_JWT_SECRET_KEY="${JWT_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Step 5: Update deployment image and wait for rollout
IMAGE_TAG=$(git rev-parse --short HEAD)
kubectl set image deployment/ectp-api \
  ectp-api=${ECR_REGISTRY}/ectp-platform:${IMAGE_TAG} --namespace ectp

kubectl rollout status deployment/ectp-api --namespace ectp --timeout=300s

# Step 6: Apply remaining resources
kubectl apply -f k8s/hpa.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/ingress.yaml

# Step 7: Verify deployment
kubectl get pods -n ectp -l app.kubernetes.io/name=ectp-api
```

---

## 7. Rollback Procedures

### 7.1 ECS Rollback

```bash
# ------------------------------------------------------------------
# ECS Rollback Procedure
# WHEN TO USE: New container image causing application errors
# Author: Gopi Krishna Vajrala
# ------------------------------------------------------------------

# Step 1: List recent task definition revisions
aws ecs list-task-definitions \
  --family-prefix ectp-api-${ENVIRONMENT} --sort DESC --max-items 5

# Step 2: Update service to use previous task definition
ROLLBACK_TASK_DEF="arn:aws:ecs:us-east-1:${AWS_ACCOUNT_ID}:task-definition/ectp-api-${ENVIRONMENT}:PREVIOUS_REVISION"
aws ecs update-service \
  --cluster ${CLUSTER_NAME} --service ectp-api-${ENVIRONMENT} \
  --task-definition ${ROLLBACK_TASK_DEF} --force-new-deployment

# Step 3: Wait and verify
aws ecs wait services-stable --cluster ${CLUSTER_NAME} --services ectp-api-${ENVIRONMENT}
echo "Rollback complete."
curl -s https://api.ectp.example.edu/health | jq .
```

### 7.2 Kubernetes Rollback

```bash
# ------------------------------------------------------------------
# Kubernetes Rollback Procedure
# Author: Gopi Krishna Vajrala
# ------------------------------------------------------------------

# Check rollout history
kubectl rollout history deployment/ectp-api -n ectp

# Rollback to previous revision
kubectl rollout undo deployment/ectp-api -n ectp

# OR rollback to a specific revision
kubectl rollout undo deployment/ectp-api -n ectp --to-revision=REVISION_NUMBER

# Wait for rollback
kubectl rollout status deployment/ectp-api -n ectp --timeout=300s

# Helm rollback (if using Helm)
helm rollback ectp REVISION_NUMBER --namespace ectp --wait --timeout 600s
```

### 7.3 Terraform Rollback

```bash
# ------------------------------------------------------------------
# Terraform Rollback Procedure
# Author: Gopi Krishna Vajrala
# ------------------------------------------------------------------

# Option A: Revert to previous Git commit
git log --oneline -10 -- infrastructure/terraform/
git checkout <PREVIOUS_COMMIT_HASH> -- infrastructure/terraform/environments/${ENVIRONMENT}/
terraform plan -var-file="terraform.tfvars" -out="rollback-plan.tfplan"
terraform apply "rollback-plan.tfplan"

# Option B: Targeted resource rollback
terraform plan -target=<resource> -out=rollback.tfplan
terraform apply rollback.tfplan
```

### 7.4 Database Migration Rollback

```bash
# ------------------------------------------------------------------
# Database Migration Rollback
# CAUTION: Verify rollback will not cause data loss
# Author: Gopi Krishna Vajrala
# ------------------------------------------------------------------

# Check current and downgrade
alembic current
alembic history --verbose
alembic downgrade -1

# EMERGENCY: Restore from RDS snapshot
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier ectp-${ENVIRONMENT}-db-restore \
  --db-snapshot-identifier ectp-${ENVIRONMENT}-pre-deploy-snapshot
```

### 7.5 Rollback Decision Matrix

| Scenario | Rollback Method | Estimated Time | Risk Level |
|----------|----------------|----------------|------------|
| Bad application code | ECS/K8s quick rollback | 2-5 minutes | Low |
| Infrastructure change | Terraform rollback | 5-15 minutes | Medium |
| Database migration issue | Alembic downgrade | 5-30 minutes | Medium-High |
| Database corruption | RDS snapshot restore | 15-60 minutes | High |
| Full environment rebuild | Terraform destroy + apply | 30-90 minutes | Critical |

---

## 8. Post-Deployment Validation Checklist

### 8.1 Infrastructure Validation

- [ ] VPC is active and subnets are correctly configured
- [ ] NAT Gateways are active with correct route tables
- [ ] Security groups are applied to all resources
- [ ] ECS/EKS cluster is active with desired task/pod count
- [ ] No tasks/pods in PENDING, STOPPED, or CrashLoopBackOff state
- [ ] Auto-scaling policies are configured and active
- [ ] RDS instance is in 'available' state with Multi-AZ (production)
- [ ] Automated backups are configured with correct retention
- [ ] ElastiCache Redis cluster is in 'available' state
- [ ] ALB is active with healthy targets
- [ ] SSL/TLS certificate is valid and not expiring within 30 days
- [ ] WAF rules are active and blocking test malicious requests

### 8.2 Application Validation Script

```bash
# ------------------------------------------------------------------
# Post-Deployment Application Validation Script
# Author: Gopi Krishna Vajrala
# ------------------------------------------------------------------

BASE_URL="https://api.ectp.example.edu"

echo "========================================="
echo "ECTP Post-Deployment Validation"
echo "Environment: ${ENVIRONMENT}"
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "========================================="

PASS=0; FAIL=0

run_check() {
  local name="$1"; local result="$2"
  if [ "$result" = "PASS" ]; then PASS=$((PASS+1)); echo "  [PASS] $name"
  else FAIL=$((FAIL+1)); echo "  [FAIL] $name - $result"; fi
}

# Test 1: Liveness
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" ${BASE_URL}/health)
[ "${HTTP_CODE}" = "200" ] && run_check "Liveness /health" "PASS" || run_check "Liveness /health" "HTTP ${HTTP_CODE}"

# Test 2: Readiness
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" ${BASE_URL}/health/ready)
[ "${HTTP_CODE}" = "200" ] && run_check "Readiness /health/ready" "PASS" || run_check "Readiness /health/ready" "HTTP ${HTTP_CODE}"

# Test 3: Detailed Health
STATUS=$(curl -s ${BASE_URL}/health/detailed | jq -r '.status')
[ "${STATUS}" = "healthy" ] && run_check "Detailed health status" "PASS" || run_check "Detailed health status" "status=${STATUS}"

# Test 4: Version check
VERSION=$(curl -s ${BASE_URL}/health | jq -r '.version')
run_check "Version: ${VERSION}" "PASS"

# Test 5: Response time
TIME=$(curl -s -o /dev/null -w "%{time_total}" ${BASE_URL}/health)
[ "$(echo "${TIME} < 1.0" | bc -l)" = "1" ] && run_check "Response time: ${TIME}s" "PASS" || run_check "Response time" "${TIME}s exceeds 1s"

# Test 6: CORS headers
CORS=$(curl -s -I -H "Origin: https://portal.ectp.example.edu" ${BASE_URL}/health | grep -i access-control)
[ -n "${CORS}" ] && run_check "CORS headers present" "PASS" || run_check "CORS headers" "Missing"

echo "========================================="
echo "Results: ${PASS} passed, ${FAIL} failed"
echo "========================================="
[ ${FAIL} -eq 0 ] && echo "DEPLOYMENT VALIDATED SUCCESSFULLY" || echo "DEPLOYMENT VALIDATION FAILED"
```

### 8.3 Monitoring Validation

- [ ] CloudWatch alarms are in OK state
- [ ] Log groups are receiving structured JSON logs
- [ ] Custom metrics (request count, latency, errors) are being published
- [ ] Dashboards are displaying real-time data
- [ ] Alert notifications are configured (SNS topics, PagerDuty, Slack)
- [ ] X-Ray tracing is capturing distributed traces

### 8.4 Security Validation

- [ ] WAF is blocking known malicious request patterns
- [ ] CORS headers are correctly restricting origins
- [ ] JWT authentication is validating tokens properly
- [ ] Rate limiting is active and enforcing thresholds
- [ ] No sensitive data (passwords, keys) exposed in health check responses
- [ ] CloudTrail logging is enabled for all API calls
- [ ] GuardDuty findings dashboard shows no new Critical/High findings

---

## 9. Troubleshooting

### 9.1 Common Issues and Resolutions

| Symptom | Possible Cause | Resolution |
|---------|---------------|------------|
| ECS tasks entering STOPPED state | Image pull failure, OOM, missing env vars | Check stopped task reason: `aws ecs describe-tasks` |
| Database connection refused | Security group misconfigured, RDS unavailable | Verify SG rules, check RDS status |
| Terraform state lock | Concurrent Terraform run, crashed process | `terraform force-unlock LOCK_ID` (verify no other runs) |
| K8s pods CrashLoopBackOff | Application crash, resource limits, missing config | `kubectl logs --previous`, check events |
| High API latency (>500ms p99) | Slow queries, pool exhaustion, cold starts | Check RDS Performance Insights, increase pool size |
| 502 errors from ALB | All targets unhealthy, container crash | Check ECS task logs, verify health check path |
| ServiceNow 401 Unauthorized | OAuth token expired | Rotate credentials in Secrets Manager |
| Terraform plan drift | Manual console changes | Run `terraform plan -refresh-only`, import or reconcile |

### 9.2 Diagnostic Commands

```bash
# ECS diagnostics
aws ecs describe-tasks --cluster ${CLUSTER_NAME} \
  --tasks $(aws ecs list-tasks --cluster ${CLUSTER_NAME} \
    --service-name ${SERVICE_NAME} --desired-status STOPPED \
    --query 'taskArns[0]' --output text) \
  --query 'tasks[0].{stoppedReason:stoppedReason,exitCode:containers[0].exitCode}'

# CloudWatch log search
aws logs filter-log-events \
  --log-group-name "/ectp/${ENVIRONMENT}/api" \
  --start-time $(date -u -d '1 hour ago' +%s)000 \
  --filter-pattern "ERROR" --limit 50

# Kubernetes diagnostics
kubectl logs -n ectp deployment/ectp-api --previous --tail=100
kubectl describe pod -n ectp -l app.kubernetes.io/name=ectp-api
kubectl top pods -n ectp

# Database connectivity test
aws ecs execute-command --cluster ${CLUSTER_NAME} --task ${TASK_ID} \
  --container ectp-api --interactive \
  --command "/bin/sh -c 'pg_isready -h ${DB_HOST} -p 5432'"
```

### 9.3 Emergency Procedures

```bash
# Full Application Outage Response
# 1. Check AWS service health: https://health.aws.amazon.com/health/status
# 2. Check application health endpoints
# 3. If code issue: IMMEDIATE ROLLBACK (Section 7)
# 4. If infrastructure issue: Check Terraform state, failover to DR
# 5. Notify: Cloud Operations team, then IT Director
# 6. Create ServiceNow P1 incident
```

---

## 10. Appendix

### 10.1 Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ECTP_APP_NAME` | No | ECTP | Application name |
| `ECTP_APP_VERSION` | No | 1.0.0 | Semantic version |
| `ECTP_ENV` | Yes | development | Environment name |
| `ECTP_DEBUG` | No | false | Debug mode |
| `ECTP_LOG_LEVEL` | No | INFO | Logging level |
| `ECTP_HOST` | No | 0.0.0.0 | Server bind host |
| `ECTP_PORT` | No | 8000 | Server bind port |
| `ECTP_DB_HOST` | Yes | localhost | PostgreSQL host |
| `ECTP_DB_PORT` | No | 5432 | PostgreSQL port |
| `ECTP_DB_NAME` | No | ectp | Database name |
| `ECTP_DB_USER` | Yes | ectp_user | Database user |
| `ECTP_DB_PASSWORD` | Yes | - | Database password |
| `ECTP_REDIS_HOST` | No | localhost | Redis host |
| `ECTP_REDIS_PORT` | No | 6379 | Redis port |
| `ECTP_AWS_REGION` | No | us-east-1 | AWS region |
| `ECTP_JWT_SECRET_KEY` | Yes | - | JWT signing secret |
| `ECTP_CORS_ORIGINS` | No | http://localhost:3000 | Comma-separated CORS origins |

### 10.2 Port Reference

| Service | Port | Protocol |
|---------|------|----------|
| ECTP API | 8000 | HTTP |
| PostgreSQL | 5432 | TCP |
| Redis | 6379 | TCP |
| ALB HTTPS | 443 | HTTPS |

---

**Document Author:** Gopi Krishna Vajrala
**Review Status:** Approved
**Next Review Date:** 2026-08-16
