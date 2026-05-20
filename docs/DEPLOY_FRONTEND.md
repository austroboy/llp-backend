# Deploying the Existing Next.js Frontend

The frontend (`laborlawpartner-dev-main`) is a Next.js 16 application. It can be deployed in several ways. This guide covers the three best options for your situation, in order of recommendation.

---

## Should you deploy the existing codebase as-is?

**Yes — with one caveat.** The frontend already has the chat UI, pricing page, sign-in/sign-up, and admin pages built. It's been engineered to consume an API that this Django backend now provides.

**The caveat:** the frontend is currently coupled to Convex (real-time backend) and Supabase (some data). When you point it at the Django backend, the chat will work, but a few real-time features (notifications, presence) will need either:

1. **Path A (recommended for v1)** — keep Convex/Supabase running alongside Django. Django handles chat + auth + subscriptions. Convex handles real-time UI state. They coexist.
2. **Path B (v2)** — fully migrate Convex/Supabase functionality into Django + Channels. More work; defer.

This guide assumes Path A.

---

## Option 1 — Vercel (recommended)

**Why:** Next.js is built by Vercel. Best support, fastest deployment, automatic SSL, edge caching, preview environments per pull request. Free tier covers most early-stage needs.

**Cost:** $0/mo on Hobby tier (with reasonable limits). $20/user/mo on Pro for production traffic.

### Steps

1. **Push the frontend to GitHub** (if not already there).

2. **Sign up at <https://vercel.com>** with your GitHub account.

3. **Import the project**: New Project → Import → select your repo.

4. **Configure environment variables** in Vercel's UI (Settings → Environment Variables). At minimum:

   ```
   NEXT_PUBLIC_API_BASE_URL=https://api.laborlawpartner.com
   NEXT_PUBLIC_FRONTEND_URL=https://laborlawpartner.com

   # Existing services (keep these from your current .env)
   NEXT_PUBLIC_CONVEX_URL=https://....convex.cloud
   CONVEX_DEPLOY_KEY=...
   NEXT_PUBLIC_SUPABASE_URL=...
   NEXT_PUBLIC_SUPABASE_ANON_KEY=...
   CLERK_SECRET_KEY=...
   NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=...
   ```

   Set them for **Production**, **Preview**, and **Development** as appropriate.

5. **Deploy**. Vercel runs `next build` and serves it. First deploy takes 5–10 min; subsequent ones ~2 min.

6. **Add your domain** (Settings → Domains): `laborlawpartner.com` and `www.laborlawpartner.com`. Vercel handles SSL automatically.

7. **Verify**: `https://laborlawpartner.com` loads. Open the chat, send "What is the maternity leave entitlement?" — it should call the Django API and stream back.

### Connecting to the Django backend

The frontend uses `NEXT_PUBLIC_API_BASE_URL` for backend calls. After setting this and redeploying, all `/api/v1/...` requests go to your Django backend. You may need to update some Convex/Supabase calls in the frontend code that should now hit Django; those changes are explicit and reviewable in PR.

### CORS

The Django backend's `CORS_ALLOWED_ORIGINS` must include your Vercel domain. From the AWS deployment guide §10, this is set in `terraform.tfvars`:

```hcl
cors_origins = "https://laborlawpartner.com,https://www.laborlawpartner.com,https://llp-frontend.vercel.app"
```

Re-apply Terraform after changing it.

---

## Option 2 — AWS Amplify

**Why:** If you're already on AWS for the backend, keeping the frontend on AWS gives you single-bill simplicity, IAM-based deployment, and tight S3/CloudFront integration.

**Cost:** Pay-per-use. ~$10–30/mo for moderate traffic.

### Steps

1. In AWS Amplify Console, click **New app → Host web app**.

2. **Connect your Git repo** (GitHub, GitLab, BitBucket).

3. **Choose the branch** (`main` for prod, optionally a `staging` branch for staging).

4. **Build settings** — Amplify auto-detects Next.js. Default settings are fine. Check the generated `amplify.yml`:

   ```yaml
   version: 1
   frontend:
     phases:
       preBuild:
         commands:
           - npm ci
       build:
         commands:
           - npm run build
     artifacts:
       baseDirectory: .next
       files:
         - '**/*'
     cache:
       paths:
         - node_modules/**/*
   ```

5. **Add environment variables** (App settings → Environment variables) — same list as Vercel above.

6. **Save and deploy**.

7. **Add your domain** (App settings → Domain management). Amplify handles ACM cert and Route 53 (or instructs you to add the CNAME if your DNS is elsewhere).

### Connecting to backend

Same as Vercel: set `NEXT_PUBLIC_API_BASE_URL` to your Django backend's URL.

---

## Option 3 — Self-hosted on the same AWS account

**Why:** Some teams want full control or to keep everything inside their VPC. Less convenient than Vercel/Amplify but possible.

This is enough work that I won't expand it here. The pattern is:

- Build the Next.js app: `npm run build`
- Containerize with `next/standalone` output (Dockerfile included in the Next.js project)
- Push to ECR
- Add a second ECS service alongside the Django one (or run both in the same cluster)
- Add an ALB rule routing `laborlawpartner.com` → Next.js service, `api.laborlawpartner.com` → Django service

If you go this route, ask and I'll add the Terraform for the Next.js service.

---

## After the frontend is live

1. **Create a test user** via your sign-up page (or Clerk if you're using it).
2. **Make a few chat requests** as that user to verify the full flow.
3. **Promote the user to admin** in the Django admin, then test the admin panel at `https://api.laborlawpartner.com/admin/`.
4. **Test a tier upgrade** (mock checkout — the actual payment integration is the next milestone).
5. **Check observability**: CloudWatch logs for the backend, Vercel/Amplify logs for the frontend, optionally Sentry for both.

---

## What about the Convex backend?

Your existing frontend uses Convex for some features. With the Django backend in place, you have three choices:

- **Keep Convex running** for what it's good at (real-time, presence). Django handles chat + auth + subscriptions. Lowest disruption.
- **Phase Convex out** over time as you migrate features. Some features (chat history, user state) move to Django; others (notifications) might too.
- **Drop Convex now** by porting its functionality into Django. Most code lift; cleanest end state.

I recommend **keep + phase out**. Ship the Django chat backend first, validate it in production, then migrate Convex pieces one feature at a time.
