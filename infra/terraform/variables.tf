variable "project" {
  type        = string
  default     = "llp"
  description = "Short project slug used to prefix resources."
}

variable "environment" {
  type        = string
  default     = "production"
  description = "Environment label (production/staging)."
}

variable "region" {
  type        = string
  default     = "ap-south-1"
  description = "AWS region."
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

# ── ECS / image ──────────────────────────────────────────────────────────────

variable "container_image" {
  type        = string
  description = "Full image URI (e.g. <acct>.dkr.ecr.<region>.amazonaws.com/llp-backend:latest)"
  default     = ""
}

variable "web_cpu" {
  type    = number
  default = 1024
}

variable "web_memory" {
  type    = number
  default = 2048
}

variable "web_desired_count" {
  type    = number
  default = 2
}

variable "worker_cpu" {
  type    = number
  default = 1024
}

variable "worker_memory" {
  type    = number
  default = 2048
}

variable "worker_desired_count" {
  type    = number
  default = 1
}

# ── RDS ──────────────────────────────────────────────────────────────────────

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_allocated_storage" {
  type    = number
  default = 50
}

variable "db_username" {
  type    = string
  default = "llp"
}

variable "db_password" {
  type      = string
  sensitive = true
  default   = ""  # leave blank → random_password generates one
}

# ── ElastiCache ──────────────────────────────────────────────────────────────

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.small"
}

# ── Domain + TLS ─────────────────────────────────────────────────────────────

variable "domain_name" {
  type        = string
  default     = ""
  description = "Optional: api.example.com — if set, ALB listener requires acm_certificate_arn"
}

variable "acm_certificate_arn" {
  type        = string
  default     = ""
  description = "Required when domain_name is set"
}

# ── Secrets / API keys ───────────────────────────────────────────────────────

variable "anthropic_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "gemini_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "django_secret_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "stripe_secret_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "stripe_webhook_secret" {
  type      = string
  sensitive = true
  default   = ""
}

variable "frontend_url" {
  type    = string
  default = "https://laborlawpartner.com"
}

variable "cors_origins" {
  type    = string
  default = "https://laborlawpartner.com,https://www.laborlawpartner.com"
}

variable "sentry_dsn" {
  type      = string
  sensitive = true
  default   = ""
}
