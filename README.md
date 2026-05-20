# Labor Law Partner — Django Backend

Bangladesh labour-law AI chatbot. Django 5 + DRF + PostgreSQL (pgvector) + Claude API.

This is the **backend only**. The frontend lives in a separate Next.js repo and consumes this API at `/api/v1/...`.

---

## What's inside

```
.
├── config/                  # Django settings (base, dev, prod, test), URLs, Celery
├── apps/
│   ├── common/              # middleware, exceptions, health checks, JSON logging
│   ├── accounts/            # User model, JWT auth, guest tokens, email verification
│   ├── subscriptions/       # tier configs, quotas, rate limits, intent gating
│   ├── documents/           # legal corpus: Document, Node (pgvector), ingestion, retrieval
│   ├── chat/                # the RAG pipeline + SSE streaming + citation extraction
│   ├── billing/             # invoices, Stripe + SSLCommerz webhooks
│   └── audit/               # hash-chained event log + admin endpoints
├── infra/
│   ├── docker/              # (see Dockerfile + docker-compose.yml at root)
│   ├── nginx/               # llp.conf for VPS deployments
│   ├── terraform/           # AWS production infra (ECS + RDS + ElastiCache + ALB)
│   └── scripts/             # entrypoint.sh
├── docs/                    # deployment guide, runbook, frontend-deploy guide
├── tests/                   # smoke + critical-path tests
├── Dockerfile
├── docker-compose.yml
├── manage.py
├── pyproject.toml
└── requirements.txt
```

---

## Quick start (local)

**Prerequisites:** Docker Desktop or Docker Engine. That's it.

```bash
git clone <this-repo>
cd llp-backend
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY and GEMINI_API_KEY
docker compose up --build
```

In another terminal, load the corpus and create an admin:

```bash
docker compose exec web python manage.py load_corpus --zip /path/to/llp-chat-data6.zip
docker compose exec web python manage.py createsuperuser
```

Visit:

- API root: <http://localhost:8000/api/v1/>
- Django admin: <http://localhost:8000/admin/>
- Swagger UI: <http://localhost:8000/api/docs/>
- Health check: <http://localhost:8000/api/health/>

---

## Quick start (production — AWS)

See `docs/DEPLOYMENT_AWS.md` for the full step-by-step. TL;DR:

```bash
cd infra/terraform
terraform init
terraform apply -var "anthropic_api_key=sk-ant-..." \
                -var "gemini_api_key=..."

# Push the Docker image
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin <ecr-url>
docker build -t llp-backend .
docker tag llp-backend:latest <ecr-url>:latest
docker push <ecr-url>:latest

# Force ECS to pull the new image
aws ecs update-service --cluster llp-production-cluster --service llp-production-web --force-new-deployment

# Load the corpus (one-time)
# See docs/DEPLOYMENT_AWS.md §6 for the exact run-task command
```

---

## Architecture at a glance

```
Next.js frontend (existing)  ─HTTPS+SSE─▶  Django API
                                            │
                ┌───────────────────────────┴──────────────────────────┐
                │                                                       │
                ▼                                                       ▼
         RAG pipeline                                       Quota / tier middleware
         (chat app)                                         (subscriptions app)
                │
        ┌───────┴────────┐
        ▼                ▼
   pgvector cosine    Claude API
   + Postgres FTS     (streaming)
        │                │
        ▼                ▼
   Document corpus   Intent classifier (Haiku)
   (documents app)   Generation (Sonnet/Opus)
                     Verifier (Haiku)
```

Key points:

- **Two-zone answers**: model writes prose (Zone 1); the system deterministically builds the legal-basis table (Zone 2) from retrieved `node_id`s. The model never produces citation tables, so it can't fabricate them.
- **Hybrid retrieval**: pgvector cosine + Postgres FTS, weighted 0.7 / 0.3, with a +0.1 boost for cross-referenced nodes and a 0.5× demotion for superseded ones.
- **Tier enforcement** lives in middleware. The prompt only adapts depth.
- **Anti-hallucination**: every cited section must resolve to a real `node_id`; unmatched citations create rows in the citation audit queue.

---

## Common commands

```bash
# Run tests
docker compose exec web pytest

# Generate migrations (after model changes)
docker compose exec web python manage.py makemigrations

# Open a Django shell
docker compose exec web python manage.py shell

# Tail Celery worker logs
docker compose logs -f worker

# Re-seed tier configs (idempotent)
docker compose exec web python manage.py seed_tiers
```

---

## Configuration

All config flows through environment variables. See `.env.example` for the complete list. The most important ones:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API for generation, intent classification, verifier |
| `GEMINI_API_KEY` | Embedding generation (Gemini `text-embedding-004`, 768 dim) |
| `EMBEDDING_DIM` | Must match your existing corpus. **768** for the default Gemini model |
| `DATABASE_URL` | Postgres + pgvector connection string |
| `REDIS_URL` | Redis for cache + rate limit + Channels |
| `ENABLE_VERIFIER_LOOP` | Max-tier verifier pass for unverified citations |

---

## Health checks

- `/api/health/` — process liveness (always 200 if running)
- `/api/health/deep/` — readiness: DB + Redis + AI keys

ALB and ECS use the cheap one. Use `/deep/` for monitoring dashboards.

---

## Documentation

- [`docs/DEPLOYMENT_AWS.md`](docs/DEPLOYMENT_AWS.md) — full AWS production deployment
- [`docs/DEPLOY_FRONTEND.md`](docs/DEPLOY_FRONTEND.md) — deploying the existing Next.js frontend
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — incident response, common ops tasks
- [`docs/API.md`](docs/API.md) — REST + SSE contract reference

---

## License

Proprietary. © Labor Law Partner.
