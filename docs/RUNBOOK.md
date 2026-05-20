# Runbook — LLP Backend Operations

Day-2 operations: what to do when things go wrong, common tasks, and on-call playbook.

---

## Quick reference

```bash
# Cluster name pattern: llp-{environment}-cluster
# Service names:        llp-{environment}-{web|worker|beat}
# Log groups:           /ecs/llp-{environment}/{web|worker|beat}

CLUSTER=llp-production-cluster
ENV=production

# Tail logs
aws logs tail /ecs/llp-$ENV/web --follow

# Open a shell in a running task
TASK=$(aws ecs list-tasks --cluster $CLUSTER --service-name llp-$ENV-web \
  --query 'taskArns[0]' --output text)
aws ecs execute-command --cluster $CLUSTER --task $TASK \
  --container web --interactive --command "/bin/bash"

# Force a redeploy
aws ecs update-service --cluster $CLUSTER --service llp-$ENV-web --force-new-deployment

# Scale up
aws ecs update-service --cluster $CLUSTER --service llp-$ENV-web --desired-count 4
```

---

## Incident response

### 1. Site is down (5xx errors)

**First check:** `https://api.laborlawpartner.com/api/health/deep/`

If 503 with `database` failing → see "Database is down" below.
If 503 with `cache` failing → see "Redis is down" below.
If timeouts or no response → see "All web tasks unhealthy" below.

### 2. Database is down

Check RDS:

```bash
aws rds describe-db-instances --db-instance-identifier llp-production-pg \
  --query 'DBInstances[0].DBInstanceStatus'
```

Status meanings:
- `available` → DB is fine, problem is connectivity (check security groups)
- `backing-up`, `modifying` → wait, will recover
- `failed`, `incompatible-network` → call AWS support; consider restoring from snapshot

**Failover** (if Multi-AZ enabled):

```bash
aws rds reboot-db-instance --db-instance-identifier llp-production-pg --force-failover
```

**Connection pool exhaustion**: when ECS tasks scale up faster than RDS can accept connections, you'll see "FATAL: too many connections" in logs. Reduce Gunicorn workers per task or upgrade RDS.

### 3. Redis is down

ElastiCache is generally rock-solid, but if it fails:

```bash
aws elasticache describe-replication-groups --replication-group-id llp-production-redis
```

The application **fails open** for rate limiting and quota when Redis is unreachable — no requests are blocked, but quota counters reset. Acceptable for short outages.

For longer outages, response cache and intent classification still work (they fall through to direct calls). Audit logs are written direct to Postgres regardless.

### 4. All web tasks unhealthy

```bash
aws ecs describe-services --cluster $CLUSTER --services llp-$ENV-web \
  --query 'services[0].events[:5]'
```

Common causes:

- **Image pull failed**: check ECR. Did your last push succeed? `aws ecr describe-images --repository-name llp-production-backend`
- **Task definition is bad**: check the latest revision in the AWS console for typos in env vars or secret ARNs.
- **Health check timing**: tasks need ~60s to migrate + start. If you reduced the grace period, increase it.
- **Out of memory**: CloudWatch metric `MemoryUtilization` for the service. Bump task `memory` in `infra/terraform/ecs.tf`.

### 5. Claude API is failing

Symptoms: chat returns errors, latency spikes, `tokens_in/tokens_out=0` in logs.

```bash
# Check Anthropic status
curl -I https://status.anthropic.com/api/v2/status.json
```

The pipeline retries 3 times with exponential backoff, then returns a structured error to the user. **The user is not charged quota for failed Claude calls.**

Mitigations:

- Set `ENABLE_VERIFIER_LOOP=False` to drop the verifier load in half
- Temporarily switch Max-tier model from Opus → Sonnet (env var `ANTHROPIC_MODEL_OPUS=claude-sonnet-4-6`)
- If extended outage: enable a static "service degraded" message at the API gateway level

### 6. Citation accuracy regression

Symptoms: users complaining about wrong sections; CitationAudit queue growing.

```bash
# Count pending audits in the last 24 hours
aws ecs execute-command ... --command "python manage.py shell -c 'from apps.documents.models import CitationAudit; print(CitationAudit.objects.filter(status=\"pending\").count())'"
```

If >50 in 24h, something has shifted. Investigate:

1. Was the corpus updated recently? Compare `Document.current_version_id` to the previous run.
2. Was the prompt changed? Look at `apps/chat/prompts.py` git history.
3. Was the model version changed? Check `ANTHROPIC_MODEL_*` env vars.

Roll back the change that caused it. The corpus loader is idempotent — if a re-import is bad, restore the prior version by setting an older `DocumentVersion.is_current=True`.

---

## Routine ops

### Deploying a code change

```bash
# Build, push, deploy
docker build -t llp-backend .
docker tag llp-backend:latest <ecr-url>:latest
docker push <ecr-url>:latest

aws ecs update-service --cluster $CLUSTER --service llp-$ENV-web --force-new-deployment
aws ecs update-service --cluster $CLUSTER --service llp-$ENV-worker --force-new-deployment

# Watch deploys
watch -n 5 'aws ecs describe-services --cluster $CLUSTER \
  --services llp-$ENV-web --query "services[0].deployments"'
```

### Updating tier configs

Edit them via Django admin at `/admin/subscriptions/tierconfig/`. Changes take effect on the next request (cache invalidates automatically via signal).

For bulk changes, use a management command:

```python
# apps/subscriptions/management/commands/update_limits.py (custom)
from apps.subscriptions.models import TierConfig
TierConfig.objects.filter(tier="mini").update(daily_request_limit=200)
```

### Granting a manual subscription override

Via Django admin: create a new `UserSubscription` with `status=overridden`, `granted_by=<your user>`. The audit trail is automatic.

### Loading a new document

Via the admin upload UI at `/admin/documents/document/` (the upload form), or via management command:

```bash
aws ecs run-task --cluster $CLUSTER \
  --task-definition llp-$ENV-web \
  --launch-type FARGATE \
  --network-configuration "..." \
  --overrides '{"containerOverrides":[{"name":"web","command":["python","manage.py","load_corpus","--zip","/tmp/new-corpus.zip"]}]}'
```

The new document gets a fresh `DocumentVersion` with `is_current=True`. The old version stays queryable.

### Rebuilding embeddings

```bash
aws ecs execute-command ... \
  --command "python manage.py shell -c 'from apps.documents.tasks import embed_version_leaves; embed_version_leaves.delay(<version_id>)'"
```

The Celery worker picks up the task. Watch progress in worker logs.

### Clearing the response cache

```bash
aws ecs execute-command ... \
  --command "python manage.py shell -c 'from django.core.cache import cache; cache.clear(); from apps.chat.models import ResponseCache; ResponseCache.objects.all().delete()'"
```

### Checking quota usage for a user

```python
# In a Django shell
from apps.subscriptions.services import subject_id_for, check_daily_quota, get_tier_config, resolve_tier
from apps.accounts.models import User

u = User.objects.get(email="user@example.com")
res = resolve_tier(user=u)
print(check_daily_quota(subject_id_for(user=u), res.config["daily_request_limit"], res.tier))
```

### Auditing an admin action

```bash
aws ecs execute-command ... \
  --command "python manage.py shell -c 'from apps.audit.services import verify_chain; print(verify_chain())'"
```

Returns `{"ok": True, "checked": N}` if the audit chain is intact.

---

## Monitoring & alerting

### Recommended CloudWatch alarms

In the AWS console (or add to Terraform):

- `ecs-service-llp-production-web` CPU > 80% for 5 min → page
- `ecs-service-llp-production-web` task count < 1 for 2 min → page
- RDS `DatabaseConnections` > 80% of max → warn
- RDS `FreeStorageSpace` < 10 GB → warn (RDS auto-scales, but warn anyway)
- ALB `HTTPCode_Target_5XX_Count` > 10 in 5 min → page
- ALB `TargetResponseTime` p95 > 8s → warn

### Sentry (recommended)

Set `SENTRY_DSN` in Secrets Manager. The app already integrates Sentry on Django + Celery. Errors show up in Sentry with request IDs and user context.

### Cost alarm

Set up a Cost Anomaly Detection monitor in AWS Billing. Anything >50% above the rolling baseline → email.

---

## Disaster recovery

### Recovery objectives

- **RPO** (max data loss): 5 minutes (RDS continuous WAL backup)
- **RTO** (max downtime): 1 hour (ALB swap to a restored RDS instance)

### Steps

1. **Restore RDS from a point-in-time snapshot** (see Deployment guide §14)
2. **Update the `DATABASE_URL` in Secrets Manager** to point at the restored instance
3. **Force-redeploy ECS** to pick up the new connection string
4. **Verify** `/api/health/deep/` is green
5. **Reload the corpus zip** if necessary

### What to do if AWS region is fully down

If `ap-south-1` is unreachable for >2 hours:

1. Restore the latest RDS snapshot to `ap-southeast-1` (Singapore)
2. Apply Terraform with `region=ap-southeast-1` (a new VPC, ALB, etc.)
3. Update DNS: `api.laborlawpartner.com` → new ALB
4. Push the existing image to a new ECR repo

This is rare. Most of the time, single-AZ failures are masked by Multi-AZ RDS / autoscaling.

---

## Periodic maintenance

### Weekly

- Review CloudWatch ECS service CPU/memory — adjust task sizing if consistently >70%
- Skim CitationAudit pending queue; batch-resolve obvious cases
- Review Sentry top issues

### Monthly

- Update Python dependencies (Dependabot / `pip list --outdated`); rebuild image
- Vacuum-analyze RDS Postgres if not auto-vacuum'd: `VACUUM ANALYZE documents_node;`
- Review IAM access keys, rotate if any are >90 days old
- Test the disaster recovery procedure (restore to a staging instance)

### Quarterly

- Rotate `SECRET_KEY` (forces re-login of all users — schedule for low traffic)
- Review tier configs for staleness; update limits based on real usage
- Re-verify the audit chain integrity (`verify_chain` should return `ok=True`)

---

## Escalation

If a problem isn't covered above:

1. **Check logs** in CloudWatch first
2. **Check Sentry** for a stack trace
3. **Reproduce in staging** if you have one
4. **Roll back** to the last known good deployment if production is degraded
5. **Page the on-call** with: timestamp, what's wrong, what you've tried, what logs show
