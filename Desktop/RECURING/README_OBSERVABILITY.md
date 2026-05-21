# Production Observability and Safety

Sentry stays active through `SENTRY_DSN`.

## Structured Pipeline Logs

Pipeline logs are emitted as JSON strings through `app.pipeline` and service loggers. Important event names:

```text
trigger_received
customer_resolved
order_resolved
image_cache_hit
image_cache_miss
image_generated
email_sent
engagement_received
pipeline_failed
external_api_retry
celery_task_dead_lettered
```

These include safe fields such as `store_id`, `customer_id`, `order_id`, `provider`, `task_id`, and `campaign_type`. Secrets/tokens are redacted.

## Retry Policy

Shared retry/backoff is configured by:

```dotenv
EXTERNAL_RETRY_MAX_ATTEMPTS=3
EXTERNAL_RETRY_BASE_DELAY_SECONDS=0.75
```

Applied to:

- EvoLink/GPT image generation
- RunPod Seedream adapter
- Gmail API
- SendGrid adapter
- Nango API/proxy
- Shopify client retry logs
- Groq LLM retry logs

Retryable statuses: `408, 409, 425, 429, 500, 502, 503, 504`.

## Public Endpoint Rate Limits

Public callback/tracking endpoints use an in-memory per-process limiter:

```dotenv
PUBLIC_RATE_LIMIT_ENABLED=true
PUBLIC_RATE_LIMIT_PER_MINUTE=120
```

Covered paths include `/events`, `/webhooks/*`, `/email-events/sendgrid`, `/replies/inbound`, Gmail OAuth callback, and Shopify connect callback.

For multi-instance production, replace this with Redis/API-gateway rate limiting.

## Celery Dead Letters

Final failed Celery tasks are written to:

```dotenv
DEAD_LETTER_DIR=data/dead_letters
```

Operator endpoints:

```text
GET  /celery/dead-letters
POST /celery/dead-letters/{dead_letter_id}/requeue
```

Both require app bearer auth.
