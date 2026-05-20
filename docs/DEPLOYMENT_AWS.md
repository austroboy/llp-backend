# AWS Deployment Guide

Step-by-step walkthrough to deploy the LLP backend on AWS using Terraform. Assumes you have AWS CLI configured and Docker installed locally.

**Estimated time:** 60–90 minutes for first deploy. ~5 minutes for subsequent deploys.

**Estimated AWS cost (small/medium production):**
- RDS db.t4g.medium: ~$60/mo
- ElastiCache cache.t4g.small: ~$20/mo
- 2× Fargate tasks (web): ~$50/mo
- 1× Fargate worker: ~$25/mo
- 1× Fargate beat: ~$8/mo
- ALB: ~$20/mo
- NAT gateway: ~$32/mo + data
- S3 + CloudWatch + ECR: ~$10/mo
- **Total: ~$225–280/month** before traffic data charges

For tighter budgets, see §11 (Cost reduction).

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [One-time AWS account setup](#2-one-time-aws-account-setup)
3. [Configure the secrets](#3-configure-the-secrets)
4. [Provision the infrastructure](#4-provision-the-infrastructure-with-terraform)
5. [Build and push the Docker image](#5-build-and-push-the-docker-image)
6. [First deployment](#6-first-deployment)
7. [Load the legal corpus](#7-load-the-legal-corpus)
8. [Create the first admin user](#8-create-the-first-admin-user)
9. [Connect a domain (Route 53 + ACM)](#9-connect-a-domain-route-53--acm)
10. [Connect the frontend](#10-connect-the-frontend)
11. [Cost reduction options](#11-cost-reduction-options)
12. [Common troubleshooting](#12-common-troubleshooting)
13. [Updates and rollouts](#13-updates-and-rollouts)
14. [Backup and disaster recovery](#14-backup-and-disaster-recovery)

---

## 1. Prerequisites

On your laptop:

- AWS CLI v2 (`aws --version` should report 2.x)
- Docker (`docker --version`)
- Terraform 1.6+ (`terraform version`)
- An AWS account with admin access (or at least: VPC, RDS, ElastiCache, ECS, ECR, IAM, S3, Secrets Manager, ELB, CloudWatch)

```bash
aws configure          # set default region to ap-south-1 (Mumbai) for Bangladesh
aws sts get-caller-identity   # confirm your credentials work
```

---

## 2. One-time AWS account setup

### Pick a region

`ap-south-1` (Mumbai) gives the lowest latency to Bangladesh users. `ap-southeast-1` (Singapore) is the second-best option.

### Enable Service Quotas (if needed)

New AWS accounts may have low default quotas. Check Service Quotas in the console for:

- ECS Fargate vCPU per region (default 6, you'll need ~10 for headroom)
- VPC EIPs (default 5)
- RDS instances (default 20)

If close to limits, request increases now — they take 1–24 hours.

### (Optional) Set up a Terraform state backend

For team work, store state in S3 with DynamoDB locking:

```bash
aws s3 mb s3://llp-terraform-state --region ap-south-1
aws s3api put-bucket-versioning --bucket llp-terraform-state \
  --versioning-configuration Status=Enabled
aws dynamodb create-table --table-name llp-terraform-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
```

Then uncomment the `backend "s3"` block at the top of `infra/terraform/main.tf`.

For solo deployments, skip this — local state is fine.

---

## 3. Configure the secrets

Get your API keys ready:

- **Anthropic** API key from <https://console.anthropic.com/>
- **Gemini** API key from <https://aistudio.google.com/app/apikey> (free tier covers initial usage)
- **Stripe** secret key + webhook secret (optional in v1; can defer)

Create a `terraform.tfvars` file in `infra/terraform/`:

```hcl
project           = "llp"
environment       = "production"
region            = "ap-south-1"

anthropic_api_key = "sk-ant-..."
gemini_api_key    = "..."
stripe_secret_key = ""           # leave blank if not yet integrated
stripe_webhook_secret = ""
sentry_dsn        = ""           # optional

frontend_url      = "https://laborlawpartner.com"
cors_origins      = "https://laborlawpartner.com,https://www.laborlawpartner.com"

# Domain config (skip for first deploy — we'll set it up in §9)
domain_name           = ""
acm_certificate_arn   = ""

# Sizing (defaults are fine for production)
db_instance_class    = "db.t4g.medium"
db_allocated_storage = 50
redis_node_type      = "cache.t4g.small"
web_desired_count    = 2
```

**Don't commit `terraform.tfvars` to git.** Add it to `.gitignore`.

---

## 4. Provision the infrastructure with Terraform

```bash
cd infra/terraform
terraform init
terraform plan      # review what will be created (read this carefully)
terraform apply     # type "yes" to confirm
```

This takes 15–25 minutes — RDS and ElastiCache are slow to provision. Expected output:

```
Apply complete! Resources: 47 added, 0 changed, 0 destroyed.

Outputs:
alb_dns_name       = "llp-production-alb-1234567890.ap-south-1.elb.amazonaws.com"
ecr_repository_url = "1234567890.dkr.ecr.ap-south-1.amazonaws.com/llp-production-backend"
ecs_cluster_name   = "llp-production-cluster"
secrets_arn        = "arn:aws:secretsmanager:ap-south-1:...:secret:llp-production/app-AbCdEf"
uploads_bucket     = "llp-production-uploads-1234567890"
```

Save these — you'll use them next.

If `apply` fails partway through, just rerun `terraform apply`. Terraform is idempotent.

---

## 5. Build and push the Docker image

Before ECS can run anything, you need an image in ECR:

```bash
# From repo root
ECR_URL=$(terraform -chdir=infra/terraform output -raw ecr_repository_url)

aws ecr get-login-password --region ap-south-1 | \
  docker login --username AWS --password-stdin "$ECR_URL"

docker build -t llp-backend .
docker tag llp-backend:latest "$ECR_URL:latest"
docker push "$ECR_URL:latest"
```

The first push uploads ~600 MB. Subsequent pushes only send the changed layers (typically <50 MB).

---

## 6. First deployment

Force ECS to pull the image and start tasks:

```bash
CLUSTER=$(terraform -chdir=infra/terraform output -raw ecs_cluster_name)

aws ecs update-service --cluster "$CLUSTER" \
  --service llp-production-web --force-new-deployment

aws ecs update-service --cluster "$CLUSTER" \
  --service llp-production-worker --force-new-deployment

aws ecs update-service --cluster "$CLUSTER" \
  --service llp-production-beat --force-new-deployment
```

Watch the deployment:

```bash
aws ecs describe-services --cluster "$CLUSTER" \
  --services llp-production-web \
  --query 'services[0].deployments'
```

Wait for `runningCount` to reach the desired count and `rolloutState` to be `COMPLETED`. ~3–5 minutes.

The entrypoint script runs migrations and seeds tier configs automatically on first start.

### Smoke test

```bash
ALB=$(terraform -chdir=infra/terraform output -raw alb_dns_name)

curl http://$ALB/api/health/         # → {"status":"ok","service":"llp-backend"}
curl http://$ALB/api/health/deep/    # → {"status":"ok","checks":{...}}
curl http://$ALB/api/v1/subscriptions/tiers/   # → list of 4 tier configs
```

If `/api/health/deep/` returns `degraded`, check `checks` in the response and the CloudWatch logs at `/ecs/llp-production/web`.

---

## 7. Load the legal corpus

Upload your `llp-chat-data6.zip` to S3, then run a one-shot ECS task to load it.

```bash
UPLOADS_BUCKET=$(terraform -chdir=infra/terraform output -raw uploads_bucket)

# Upload the zip to S3
aws s3 cp /path/to/llp-chat-data6.zip s3://$UPLOADS_BUCKET/corpus/llp-chat-data6.zip

# Get the subnet + security group IDs
SUBNET=$(aws ec2 describe-subnets --filters "Name=tag:Tier,Values=private" \
  --query 'Subnets[0].SubnetId' --output text)
SG=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=llp-production-ecs-tasks-sg" \
  --query 'SecurityGroups[0].GroupId' --output text)

# Run a one-shot task to download the corpus and load it
cat > /tmp/overrides.json <<EOF
{
  "containerOverrides": [{
    "name": "web",
    "command": [
      "sh", "-c",
      "aws s3 cp s3://$UPLOADS_BUCKET/corpus/llp-chat-data6.zip /tmp/corpus.zip && python manage.py load_corpus --zip /tmp/corpus.zip"
    ]
  }]
}
EOF

aws ecs run-task --cluster "$CLUSTER" \
  --task-definition llp-production-web \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET],securityGroups=[$SG]}" \
  --overrides file:///tmp/overrides.json
```

Watch its logs:

```bash
aws logs tail /ecs/llp-production/web --follow --since 5m
```

You should see lines like `ingested_json_document doc_code=DOC-002 nodes=...` and a final summary. Total runtime: 1–3 minutes for the standard corpus.

### Verify

```bash
curl http://$ALB/api/v1/documents/     # requires admin auth (next step)
```

Or via the Django admin (also next step) at `/admin/documents/document/`.

---

## 8. Create the first admin user

Use ECS Exec to open a shell into a running task:

```bash
# Enable execute-command on the service (one-time)
aws ecs update-service --cluster "$CLUSTER" \
  --service llp-production-web --enable-execute-command

# Find a running task
TASK=$(aws ecs list-tasks --cluster "$CLUSTER" --service-name llp-production-web \
  --query 'taskArns[0]' --output text)

# Exec in
aws ecs execute-command --cluster "$CLUSTER" --task "$TASK" \
  --container web --interactive --command "/bin/sh"

# Inside the container:
python manage.py createsuperuser
# Enter email, password
exit
```

Then log in at `http://$ALB/admin/`.

(If `execute-command` complains about session-manager-plugin, install it: <https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html>)

---

## 9. Connect a domain (Route 53 + ACM)

You'll want a real domain like `api.laborlawpartner.com` instead of the long ALB DNS name.

### a) Request a TLS certificate

In ACM (Certificate Manager) **in the same region as your ALB**:

```bash
aws acm request-certificate \
  --domain-name api.laborlawpartner.com \
  --validation-method DNS \
  --region ap-south-1
```

ACM will give you a CNAME record to add to your DNS provider. Add it. Validation takes 5–60 minutes.

Get the certificate ARN once issued:

```bash
aws acm list-certificates --region ap-south-1
```

### b) Add to Terraform and reapply

In `terraform.tfvars`:

```hcl
domain_name         = "api.laborlawpartner.com"
acm_certificate_arn = "arn:aws:acm:ap-south-1:<acct>:certificate/<id>"
```

Then:

```bash
terraform -chdir=infra/terraform apply
```

This adds an HTTPS listener (443) to the ALB and redirects HTTP → HTTPS.

### c) Point your DNS

Add a CNAME or alias record:

- If using Route 53: alias record from `api.laborlawpartner.com` → ALB DNS
- If using another DNS provider: CNAME from `api` → ALB DNS

Verify:

```bash
curl https://api.laborlawpartner.com/api/health/
```

---

## 10. Connect the frontend

In your Next.js project's `.env.production`:

```
NEXT_PUBLIC_API_BASE_URL=https://api.laborlawpartner.com
```

The chat client should hit `POST /api/v1/chat/conversations/{id}/messages/` and consume the SSE stream. The pricing page should hit `GET /api/v1/subscriptions/tiers/`. See `docs/API.md` for the full contract.

If you get CORS errors, double-check `cors_origins` in `terraform.tfvars` includes your frontend domain — and re-apply Terraform after changing it.

---

## 11. Cost reduction options

If the default ~$250/mo is too much:

### Single-AZ trade-offs (~$50/mo savings)

In `terraform.tfvars`:

```hcl
db_instance_class = "db.t4g.small"     # half the size
redis_node_type   = "cache.t4g.micro"  # half the size
web_desired_count = 1                  # not HA
```

This drops to ~$140/mo but loses redundancy. Acceptable for early-stage.

### Skip NAT Gateway (~$32/mo savings)

If you don't need outbound internet from private subnets (you do, for Claude API), you can use VPC endpoints for ECR/S3/CloudWatch and let the workload speak only to AWS services through them. Terraform changes are non-trivial; defer until needed.

### Use a single VPS instead of ECS (~$200/mo savings)

For very early traffic, run everything on a single t3.medium EC2 with Docker Compose. See `infra/nginx/llp.conf` for the reverse-proxy config. You lose: managed scaling, blue-green deploys, easy rollback. Trade-off depends on your stage.

---

## 12. Common troubleshooting

### "Tasks keep restarting"

Check CloudWatch logs at `/ecs/llp-production/web`. The most common cause is an environment variable missing in Secrets Manager. Verify the secret has every key the app needs:

```bash
aws secretsmanager get-secret-value --secret-id llp-production/app \
  --query SecretString --output text | jq 'keys'
```

### "Health check is failing"

Tasks take 60s+ to migrate + collectstatic on first start. The ALB health check has a 30s grace period — this can cause initial flapping. Wait 5 minutes; it should stabilize. If not, check `/api/health/deep/` directly via the task's private IP.

### "Database connection refused"

The RDS instance lives in private subnets. The ECS task SG allows port 5432 to the RDS SG. If you changed SGs, ensure that rule still exists. From a task shell:

```bash
python -c "import os; from urllib.parse import urlparse; u=urlparse(os.environ['DATABASE_URL']); print(u.hostname, u.port)"
nc -zv <hostname> 5432
```

### "pgvector extension not found"

The `pgvector/pgvector:pg16` Docker image and AWS RDS Postgres 16 both include pgvector, but the extension must be installed in the database. The `common` app's first migration runs `CREATE EXTENSION vector` — if it failed (RDS sometimes needs a restart first), connect via psql and run it manually:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;
```

### "SSE stream is buffered / arrives all at once"

This is almost always nginx. Make sure `proxy_buffering off` is set on the chat path, or that `X-Accel-Buffering: no` is reaching the proxy (Django sets it by default). On the AWS ALB, no extra config is needed — ALBs don't buffer SSE.

### "Slow first response (8+ seconds)"

The first hit after a cold start can be slow because:

1. The Gunicorn worker hasn't loaded the app yet (~1s)
2. pgvector loads its index lazily into memory (~500ms)
3. Claude streams the first token after ~800ms

Subsequent requests should be <2s to first token. If chronically slow, check CloudWatch ECS Service CPU — you may need more workers.

---

## 13. Updates and rollouts

```bash
# Build + push the new image
docker build -t llp-backend .
docker tag llp-backend:latest "$ECR_URL:latest"
docker push "$ECR_URL:latest"

# Trigger rolling deploy
aws ecs update-service --cluster "$CLUSTER" \
  --service llp-production-web --force-new-deployment

# Watch
aws ecs describe-services --cluster "$CLUSTER" --services llp-production-web \
  --query 'services[0].deployments'
```

ECS does a rolling deploy: starts new tasks, waits for healthy, drains old tasks. Zero downtime.

### Rolling back

If a deploy goes wrong:

```bash
# Find the previous task definition revision
aws ecs list-task-definitions --family-prefix llp-production-web --sort DESC

# Update the service to that revision
aws ecs update-service --cluster "$CLUSTER" \
  --service llp-production-web \
  --task-definition llp-production-web:42   # or whichever was good
```

### Schema migrations

Migrations run automatically on container start (via `entrypoint.sh`). For migrations that need long offline time, set `web_desired_count = 0`, run a one-shot task to migrate, then scale back up.

---

## 14. Backup and disaster recovery

### What's backed up automatically

- **RDS**: 14 daily automated snapshots + continuous WAL (point-in-time recovery to within 5 min)
- **S3 uploads bucket**: versioning enabled — deletes don't actually delete
- **Secrets Manager**: 7-day recovery window on deletion
- **ECR**: keeps the last 30 image tags

### Restoring

```bash
# Point-in-time RDS restore
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier llp-production-pg \
  --target-db-instance-identifier llp-production-pg-restored \
  --restore-time 2026-04-29T16:00:00Z
```

Then update Secrets Manager `DATABASE_URL` to point at the restored host, and force a new deployment.

### What's NOT in AWS backups

- **Your `.env.example` and `terraform.tfvars`** — store these in a password manager or secure note. Losing them means you can't redeploy.
- **The corpus zip** itself — keep a copy outside AWS. The processed nodes are in RDS, but if RDS is gone and you don't have the zip, you can't reload from scratch.

---

## Done

You now have:

- A production Django backend on AWS ECS
- Postgres + Redis + S3 + ALB
- HTTPS on a custom domain
- Auto-scaling on CPU
- Secrets in Secrets Manager
- Logs in CloudWatch
- Billing webhooks ready (after Stripe integration)

Next: see `docs/RUNBOOK.md` for day-2 operations.
