output "alb_dns_name" {
  description = "Public ALB hostname (point your domain CNAME here)"
  value       = aws_lb.main.dns_name
}

output "alb_zone_id" {
  description = "Hosted zone ID for the ALB (Route 53 alias target)"
  value       = aws_lb.main.zone_id
}

output "rds_endpoint" {
  description = "RDS Postgres endpoint (private)"
  value       = aws_db_instance.postgres.address
  sensitive   = true
}

output "redis_endpoint" {
  description = "ElastiCache Redis primary endpoint (private)"
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address
  sensitive   = true
}

output "ecr_repository_url" {
  description = "ECR repository URL for pushing images"
  value       = aws_ecr_repository.backend.repository_url
}

output "uploads_bucket" {
  description = "S3 bucket for user uploads"
  value       = aws_s3_bucket.uploads.bucket
}

output "secrets_arn" {
  description = "Secrets Manager ARN — edit values via AWS console"
  value       = aws_secretsmanager_secret.app.arn
}

output "ecs_cluster_name" {
  description = "ECS cluster name (for kubectl-style ops)"
  value       = aws_ecs_cluster.main.name
}

output "next_steps" {
  value = <<-EOT

    ===== LLP Infrastructure provisioned =====

    1. Push the Docker image to ECR:
         aws ecr get-login-password --region ${var.region} | \
           docker login --username AWS --password-stdin ${aws_ecr_repository.backend.repository_url}
         docker build -t llp-backend .
         docker tag llp-backend:latest ${aws_ecr_repository.backend.repository_url}:latest
         docker push ${aws_ecr_repository.backend.repository_url}:latest

    2. Force ECS to pull the new image:
         aws ecs update-service --cluster ${aws_ecs_cluster.main.name} \
           --service ${aws_ecs_service.web.name} --force-new-deployment

    3. Get the ALB:
         http://${aws_lb.main.dns_name}/api/health/

    4. (One-time) Load the corpus into the running Postgres:
         aws ecs run-task --cluster ${aws_ecs_cluster.main.name} \
           --task-definition ${aws_ecs_task_definition.web.family} \
           --launch-type FARGATE \
           --network-configuration 'awsvpcConfiguration={subnets=[${aws_subnet.private[0].id}],securityGroups=[${aws_security_group.ecs_tasks.id}]}' \
           --overrides '{"containerOverrides":[{"name":"web","command":["python","manage.py","load_corpus","--zip","/tmp/llp-chat-data6.zip"]}]}'

    5. Create the first admin user:
         aws ecs execute-command --cluster ${aws_ecs_cluster.main.name} \
           --task <task-arn> --container web --interactive \
           --command "python manage.py createsuperuser"

    6. Point your domain at: ${aws_lb.main.dns_name}

    ==========================================
  EOT
}
