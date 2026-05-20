# =============================================================================
# Secrets Manager + S3 (uploads) + ECR repository
# =============================================================================

# ── Django secret key ─────────────────────────────────────────────────────
resource "random_password" "django_secret" {
  length  = 64
  special = true
}

locals {
  effective_django_secret = var.django_secret_key != "" ? var.django_secret_key : random_password.django_secret.result
}

# ── Secrets Manager ───────────────────────────────────────────────────────
resource "aws_secretsmanager_secret" "app" {
  name = "${local.name_prefix}/app"
  description = "Runtime secrets for LLP Django backend"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    SECRET_KEY            = local.effective_django_secret
    DATABASE_URL          = "postgres://${aws_db_instance.postgres.username}:${urlencode(local.effective_db_password)}@${aws_db_instance.postgres.address}:5432/${aws_db_instance.postgres.db_name}"
    REDIS_URL             = "redis://${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379/0"
    CELERY_BROKER_URL     = "redis://${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379/1"
    CELERY_RESULT_BACKEND = "redis://${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379/2"
    ANTHROPIC_API_KEY     = var.anthropic_api_key
    GEMINI_API_KEY        = var.gemini_api_key
    STRIPE_SECRET_KEY     = var.stripe_secret_key
    STRIPE_WEBHOOK_SECRET = var.stripe_webhook_secret
    SENTRY_DSN            = var.sentry_dsn
  })

  lifecycle {
    ignore_changes = [secret_string]  # let manual secret edits stick
  }
}

# ── S3 (uploads) ──────────────────────────────────────────────────────────
resource "aws_s3_bucket" "uploads" {
  bucket = "${local.name_prefix}-uploads-${data.aws_caller_identity.current.account_id}"

  tags = { Name = "${local.name_prefix}-uploads" }
}

resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    id     = "delete-old-noncurrent"
    status = "Enabled"
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

# ── ECR repository ────────────────────────────────────────────────────────
resource "aws_ecr_repository" "backend" {
  name                 = "${local.name_prefix}-backend"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 30 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 30
      }
      action = { type = "expire" }
    }]
  })
}

data "aws_caller_identity" "current" {}
