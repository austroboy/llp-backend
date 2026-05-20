# API Reference

REST + Server-Sent Events. Base URL: `https://api.laborlawpartner.com/api/v1/`

For an interactive version, visit `/api/docs/` (Swagger UI).

---

## Authentication

Three auth modes:

| Mode | Header | Used by |
|---|---|---|
| JWT (preferred) | `Authorization: Bearer <access_token>` | Logged-in users |
| Guest token | `X-Guest-Token: <token>` | Anonymous visitors (free_guest tier) |
| Public | none | `/auth/*`, `/subscriptions/tiers/`, `/health/*` |

JWT lifetimes: access 15 minutes, refresh 7 days. Refresh tokens are rotated and the old one blacklisted on use.

---

## Errors

All errors are RFC 7807 `application/problem+json`:

```json
{
  "type": "/errors/quota_exceeded",
  "title": "Daily quota exceeded.",
  "status": 403,
  "detail": "...",
  "request_id": "f3a9b2c1d4e5f6a7",
  "upgrade_cta": {
    "text": "Need drafting, memory & more? → Mini ১৪৯৳/mo",
    "text_bn": "...",
    "target_tier": "mini"
  }
}
```

Common error types: `validation_error`, `not_authenticated`, `permission_denied`, `quota_exceeded`, `intent_blocked`, `rate_limited`, `not_found`, `internal`.

---

## Endpoint catalog

### Auth

| Method | Path | Auth | Body | Returns |
|---|---|---|---|---|
| POST | `/auth/register/` | none | `{email, password, full_name?, preferred_language?}` | `{user, tokens}` |
| POST | `/auth/login/` | none | `{email, password}` | `{user, tokens}` |
| POST | `/auth/refresh/` | none | `{refresh}` | `{access}` |
| POST | `/auth/logout/` | JWT | `{refresh}` | `{detail}` |
| GET | `/auth/me/` | JWT | — | `User` |
| PATCH | `/auth/me/` | JWT | partial `User` | `User` |
| POST | `/auth/guest/` | none | `{language?}` | `{guest_token, language}` |
| POST | `/auth/password/reset/` | none | `{email}` | `{detail}` |
| POST | `/auth/password/reset/confirm/` | none | `{token, new_password}` | `{detail}` |
| POST | `/auth/verify/` | none | `{token}` | `{detail}` |

### Subscriptions

| Method | Path | Auth | Returns |
|---|---|---|---|
| GET | `/subscriptions/tiers/` | none | `{tiers: [TierConfig]}` |
| GET | `/subscriptions/me/` | JWT | `{active_subscription, effective_tier, tier_features}` |
| GET | `/subscriptions/quota/` | any | `{tier, used, limit, remaining, resets_in_seconds}` |
| POST | `/subscriptions/upgrade/` | JWT | `{invoice_id, checkout_url, provider, amount_bdt, target_tier}` |
| POST | `/subscriptions/cancel/` | JWT | `{detail}` |

Body for `upgrade/`: `{target_tier: "mini"|"max", payment_provider: "stripe"|"sslcommerz"|"manual"}`.

### Chat

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/chat/conversations/` | any | Creates a conversation. Returns `Conversation` |
| GET | `/chat/conversations/` | any | Lists user's conversations |
| GET | `/chat/conversations/{id}/` | any | Conversation + messages |
| DELETE | `/chat/conversations/{id}/` | any | Soft-archive |
| POST | `/chat/conversations/{id}/messages/` | any | **Sends message; streams SSE** |
| POST | `/chat/conversations/{id}/files/` | JWT (Max) | Multipart file upload (.docx/.pdf/.txt) |
| POST | `/chat/quota/check/` | any | Pre-flight quota check |

#### Send message — SSE contract

```http
POST /api/v1/chat/conversations/123/messages/
Content-Type: application/json
Authorization: Bearer <jwt>

{
  "user_message": "Is PF mandatory for a company to establish?",
  "language": "en",
  "attachments": []
}
```

Response is `text/event-stream`. Sequence of events:

```
event: meta
data: {"conversation_id":123,"intent":"FACTUAL","mode":"direct","tier":"free_subscribed","language":"english","remaining_quota":12}

event: text
data: {"delta":"Provident Fund is not "}

event: text
data: {"delta":"automatically mandatory…"}

event: legal_basis
data: {"rows":[{"issue":"General PF rule","reference_label":"Section 264, Labour Act 2006","node_id":"DOC-010-0264","verdict":"verified"}]}

event: cta
data: {"text":"Need advisory…","target_tier":"max"}    // only if intent was downgraded

event: done
data: {"message_id":456,"tokens_in":1840,"tokens_out":420,"cached":false,"verdict":"high","latency_ms":4200}
```

Errors mid-stream:

```
event: error
data: {"code":"quota_exceeded","message":"Daily quota reached","upgrade_cta":{...}}
```

#### Clarification mode

When the input is ambiguous, the stream emits a single `clarification` event in place of `text` + `legal_basis`:

```
event: meta
data: {...,"mode":"clarification"}

event: clarification
data: {"opening":"I want to make sure I guide you in the right direction…","options":["I want to know if this was done legally","I want to understand what compensation…",...]}

event: done
data: {...}
```

### Documents (admin-only)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/documents/` | admin | List corpus |
| POST | `/documents/upload/` | admin | Multipart: `doc_code`, `file`, `title?`, `language?` |
| GET | `/documents/jobs/{job_id}/` | admin | Ingestion progress |
| GET | `/documents/{doc_code}/` | admin | Document metadata |
| GET | `/documents/{doc_code}/nodes/` | admin | Node list (paginated) |
| GET | `/documents/nodes/{node_id}/` | admin | Single node |
| PATCH | `/documents/nodes/{node_id}/` | admin | Edit summary/content (re-embeds async) |
| GET | `/documents/citation-audits/` | admin | Pending citation audits |
| POST | `/documents/citation-audits/{id}/resolve/` | admin | `{decision, notes?}` |
| GET | `/documents/sidebar/{node_id}/` | public | Sidebar payload (9 fields per the LLP guideline) |

### Billing

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/billing/invoices/` | JWT | User's invoice history |
| POST | `/billing/invoices/{id}/confirm/` | admin | Manual confirm (testing/ops) |
| POST | `/billing/webhooks/stripe/` | none + signature | Stripe webhook receiver |
| POST | `/billing/webhooks/sslcommerz/` | none | SSLCommerz IPN receiver |

### Admin / Audit

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/admin/audit/` | admin | Filterable event log |
| GET | `/admin/audit/integrity/` | admin | Hash-chain check |
| GET | `/admin/cost/` | admin | Rolling cost dashboard |
| GET | `/admin/summary/` | admin | Top-level numbers |

### Health

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/health/` | none | Liveness — always 200 if alive |
| GET | `/health/deep/` | none | DB + Redis + AI keys |

---

## Schemas

### `User`

```json
{
  "id": 12,
  "email": "user@example.com",
  "full_name": "Aisha Rahman",
  "phone": "",
  "role": "premium_user",
  "is_email_verified": true,
  "preferred_language": "en",
  "is_premium": true,
  "created_at": "2026-04-29T16:32:11Z"
}
```

### `TierConfig`

```json
{
  "tier": "mini",
  "label": "Mini — ১৪৯৳/mo",
  "label_bn": "মিনি — ১৪৯৳/মাস",
  "daily_request_limit": 100,
  "rate_limit_per_min": 20,
  "allowed_intents": ["FACTUAL","ADVISORY","DRAFTING","CALCULATION","PROCEDURAL","CROSS_DOMAIN","PRODUCT_INQUIRY"],
  "file_upload_allowed": false,
  "cross_domain_allowed": true,
  "advisory_allowed": false,
  "memory_window_days": 7,
  "zone2_max_rows": 4,
  "price_bdt": 149
}
```

### `Conversation`

```json
{
  "id": 123,
  "title": "Termination question",
  "language": "en",
  "tier_at_start": "mini",
  "archived": false,
  "created_at": "2026-04-29T15:00:00Z",
  "updated_at": "2026-04-29T15:30:00Z"
}
```

### `ChatMessage` (assistant, completed)

```json
{
  "id": 456,
  "role": "assistant",
  "content": "Provident Fund is not automatically mandatory…",
  "intent": "FACTUAL",
  "mode": "direct",
  "retrieved_node_ids": ["DOC-010-0264","DOC-010-0264-0010"],
  "legal_basis": [
    {"issue":"General PF rule","reference_label":"Section 264, Labour Act 2006","node_id":"DOC-010-0264","verdict":"verified"}
  ],
  "citations": [
    {"section":"264","rule":"","raw":"Section 264, Labour Act 2006"}
  ],
  "clarification_options": [],
  "cta": {},
  "next_step": "",
  "tokens_in": 1840,
  "tokens_out": 420,
  "model_name": "sonnet",
  "latency_ms": 4200,
  "cached": false,
  "verdict": "high",
  "created_at": "2026-04-29T15:30:00Z"
}
```

---

## Rate limits

| Tier | Per minute | Per day |
|---|---|---|
| free_guest | 5 | 5 |
| free_subscribed | 10 | 15 |
| mini | 20 | 100 |
| max | 30 | 500 |

Rate limit headers on every response:

```
X-RateLimit-Remaining: 12
X-RateLimit-Reset: 27
```

---

## SDKs

None official. The frontend uses native `fetch()` + `EventSource` for SSE. Examples:

```typescript
// Send a message and stream the response
const res = await fetch(`${API}/api/v1/chat/conversations/${convId}/messages/`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${jwt}`,
  },
  body: JSON.stringify({user_message: input, language: "en"}),
});

const reader = res.body!.getReader();
const decoder = new TextDecoder();
let buf = "";
while (true) {
  const {done, value} = await reader.read();
  if (done) break;
  buf += decoder.decode(value, {stream: true});
  const events = buf.split("\n\n");
  buf = events.pop() ?? "";
  for (const block of events) {
    const [eventLine, dataLine] = block.split("\n");
    const event = eventLine.replace(/^event:\s*/, "");
    const data = JSON.parse(dataLine.replace(/^data:\s*/, ""));
    handleEvent(event, data);
  }
}
```
