# =============================================================================
# ECS Fargate: cluster + web service (behind ALB) + worker + beat
# =============================================================================

resource "aws_ecs_cluster" "main" {
  name = "${local.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# ── CloudWatch log groups ─────────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "web" {
  name              = "/ecs/${local.name_prefix}/web"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${local.name_prefix}/worker"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "beat" {
  name              = "/ecs/${local.name_prefix}/beat"
  retention_in_days = 30
}

# ── Common environment + secrets injected into all task defs ──────────────
locals {
  image = var.container_image != "" ? var.container_image : "${aws_ecr_repository.backend.repository_url}:latest"

  common_secrets = [
    { name = "SECRET_KEY",            valueFrom = "${aws_secretsmanager_secret.app.arn}:SECRET_KEY::" },
    { name = "DATABASE_URL",          valueFrom = "${aws_secretsmanager_secret.app.arn}:DATABASE_URL::" },
    { name = "REDIS_URL",             valueFrom = "${aws_secretsmanager_secret.app.arn}:REDIS_URL::" },
    { name = "CELERY_BROKER_URL",     valueFrom = "${aws_secretsmanager_secret.app.arn}:CELERY_BROKER_URL::" },
    { name = "CELERY_RESULT_BACKEND", valueFrom = "${aws_secretsmanager_secret.app.arn}:CELERY_RESULT_BACKEND::" },
    { name = "ANTHROPIC_API_KEY",     valueFrom = "${aws_secretsmanager_secret.app.arn}:ANTHROPIC_API_KEY::" },
    { name = "GEMINI_API_KEY",        valueFrom = "${aws_secretsmanager_secret.app.arn}:GEMINI_API_KEY::" },
    { name = "STRIPE_SECRET_KEY",     valueFrom = "${aws_secretsmanager_secret.app.arn}:STRIPE_SECRET_KEY::" },
    { name = "STRIPE_WEBHOOK_SECRET", valueFrom = "${aws_secretsmanager_secret.app.arn}:STRIPE_WEBHOOK_SECRET::" },
    { name = "SENTRY_DSN",            valueFrom = "${aws_secretsmanager_secret.app.arn}:SENTRY_DSN::" },
  ]

  common_env = [
    { name = "DJANGO_SETTINGS_MODULE", value = "config.settings.prod" },
    { name = "ALLOWED_HOSTS",          value = var.domain_name != "" ? var.domain_name : aws_lb.main.dns_name },
    { name = "CORS_ALLOWED_ORIGINS",   value = var.cors_origins },
    { name = "CSRF_TRUSTED_ORIGINS",   value = var.cors_origins },
    { name = "FRONTEND_URL",           value = var.frontend_url },
    { name = "EMBEDDING_DIM",          value = "768" },
    { name = "USE_S3_STORAGE",         value = "True" },
    { name = "AWS_REGION",             value = var.region },
    { name = "AWS_S3_BUCKET",          value = aws_s3_bucket.uploads.bucket },
  ]
}

# ── Web task ─────────────────────────────────────────────────────────────
resource "aws_ecs_task_definition" "web" {
  family                   = "${local.name_prefix}-web"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.web_cpu
  memory                   = var.web_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "web"
    image     = local.image
    essential = true
    command   = [
      "sh", "-c",
      "infra/scripts/entrypoint.sh gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --worker-class uvicorn.workers.UvicornWorker --timeout 300"
    ]
    portMappings = [{
      containerPort = 8000
      hostPort      = 8000
      protocol      = "tcp"
    }]
    environment = local.common_env
    secrets     = local.common_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.web.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "web"
      }
    }
    healthCheck = {
      command     = ["CMD-SHELL", "curl -f http://localhost:8000/api/health/ || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }
  }])
}

resource "aws_ecs_service" "web" {
  name            = "${local.name_prefix}-web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = var.web_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 8000
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  depends_on = [aws_lb_listener.http]

  lifecycle {
    ignore_changes = [task_definition]  # CI/CD updates the task def
  }
}

# ── Worker task (Celery worker) ──────────────────────────────────────────
resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name_prefix}-worker"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "worker"
    image     = local.image
    essential = true
    command   = ["celery", "-A", "config.celery", "worker", "--loglevel=info", "--concurrency=2"]
    environment = local.common_env
    secrets     = local.common_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.worker.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "worker"
      }
    }
  }])
}

resource "aws_ecs_service" "worker" {
  name            = "${local.name_prefix}-worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  lifecycle {
    ignore_changes = [task_definition]
  }
}

# ── Beat task (Celery scheduler — singleton) ─────────────────────────────
resource "aws_ecs_task_definition" "beat" {
  family                   = "${local.name_prefix}-beat"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "beat"
    image     = local.image
    essential = true
    command   = ["celery", "-A", "config.celery", "beat", "--loglevel=info"]
    environment = local.common_env
    secrets     = local.common_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.beat.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "beat"
      }
    }
  }])
}

resource "aws_ecs_service" "beat" {
  name            = "${local.name_prefix}-beat"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.beat.arn
  desired_count   = 1  # exactly one — it's a scheduler
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  lifecycle {
    ignore_changes = [task_definition]
  }
}

# ── Auto-scaling for the web service ──────────────────────────────────────
resource "aws_appautoscaling_target" "web" {
  max_capacity       = 8
  min_capacity       = var.web_desired_count
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.web.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "web_cpu" {
  name               = "${local.name_prefix}-web-cpu-tracking"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.web.resource_id
  scalable_dimension = aws_appautoscaling_target.web.scalable_dimension
  service_namespace  = aws_appautoscaling_target.web.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value = 70.0
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}
