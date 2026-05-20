# =============================================================================
# LLP backend — AWS infrastructure (root)
# =============================================================================
# Spins up: VPC + 2 public + 2 private subnets, NAT, ALB, ECS Fargate cluster
# (web + worker + beat services), RDS Postgres 16 with pgvector, ElastiCache
# Redis, S3 (uploads), Secrets Manager, IAM roles, CloudWatch logs.
#
# Quick start:
#   terraform init
#   terraform apply -var "project=llp" -var "region=ap-south-1" \
#                   -var "anthropic_api_key=sk-ant-..." \
#                   -var "gemini_api_key=..."
#
# Region note: ap-south-1 (Mumbai) is closest to Bangladesh. ap-southeast-1
# (Singapore) is the second-best for latency.

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Enable for shared state (pick ONE)
  # backend "s3" {
  #   bucket         = "llp-terraform-state"
  #   key            = "infra/terraform.tfstate"
  #   region         = "ap-south-1"
  #   encrypt        = true
  #   dynamodb_table = "llp-terraform-locks"
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
