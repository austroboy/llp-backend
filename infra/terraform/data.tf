# =============================================================================
# Data layer: RDS Postgres 16 (with pgvector) + ElastiCache Redis
# =============================================================================

# ── RDS subnet group ───────────────────────────────────────────────────────
resource "aws_db_subnet_group" "main" {
  name       = "${local.name_prefix}-db-subnets"
  subnet_ids = aws_subnet.private[*].id

  tags = { Name = "${local.name_prefix}-db-subnets" }
}

# ── DB password ───────────────────────────────────────────────────────────
resource "random_password" "db" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>?"
}

locals {
  effective_db_password = var.db_password != "" ? var.db_password : random_password.db.result
}

# ── RDS Postgres ──────────────────────────────────────────────────────────
resource "aws_db_parameter_group" "postgres16" {
  name   = "${local.name_prefix}-pg16-params"
  family = "postgres16"

  # Allow pgvector + pg_trgm at the cluster level. pgvector ships with RDS PG16+.
  parameter {
    name  = "shared_preload_libraries"
    value = "pg_stat_statements"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }
}

resource "aws_db_instance" "postgres" {
  identifier             = "${local.name_prefix}-pg"
  engine                 = "postgres"
  engine_version         = "16.4"
  instance_class         = var.db_instance_class
  allocated_storage      = var.db_allocated_storage
  max_allocated_storage  = var.db_allocated_storage * 4
  storage_type           = "gp3"
  storage_encrypted      = true

  db_name  = "llp"
  username = var.db_username
  password = local.effective_db_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  parameter_group_name   = aws_db_parameter_group.postgres16.name
  publicly_accessible    = false
  multi_az               = false  # set true in production for HA
  backup_retention_period = 14
  backup_window           = "16:00-17:00"  # UTC
  maintenance_window      = "sun:18:00-sun:19:00"

  copy_tags_to_snapshot      = true
  delete_automated_backups   = false
  deletion_protection        = true
  skip_final_snapshot        = false
  final_snapshot_identifier  = "${local.name_prefix}-pg-final-${formatdate("YYYYMMDD-HHmmss", timestamp())}"
  performance_insights_enabled = true

  enabled_cloudwatch_logs_exports = ["postgresql"]

  tags = { Name = "${local.name_prefix}-pg" }

  lifecycle {
    ignore_changes = [final_snapshot_identifier]
  }
}

# ── ElastiCache subnet group ──────────────────────────────────────────────
resource "aws_elasticache_subnet_group" "main" {
  name       = "${local.name_prefix}-redis-subnets"
  subnet_ids = aws_subnet.private[*].id
}

# ── ElastiCache Redis ─────────────────────────────────────────────────────
resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "${local.name_prefix}-redis"
  description          = "LLP Redis (cache + Celery + ratelimit)"

  engine               = "redis"
  engine_version       = "7.1"
  node_type            = var.redis_node_type
  port                 = 6379

  num_cache_clusters         = 1  # increase for HA
  parameter_group_name       = "default.redis7"
  automatic_failover_enabled = false  # requires num_cache_clusters >= 2
  multi_az_enabled           = false

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = false  # set true + auth_token for prod-grade

  snapshot_retention_limit = 5

  tags = { Name = "${local.name_prefix}-redis" }
}
