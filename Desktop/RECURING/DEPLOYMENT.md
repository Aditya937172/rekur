# RECURING Production Deployment Guide

## Pre-deployment Checklist

1. Verify all tests pass: `python scripts/test_pipelines.py`
2. Ensure `.env` is properly configured (see `.env.template`)
3. Set `DRY_RUN=false` for production
4. Configure Sentry DSN for error tracking

## Local Development with Docker

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f api
docker-compose logs -f worker

# Stop all services
docker-compose down
```

## Railway Deployment

### 1. Install Railway CLI
```bash
npm install -g @railway/cli
railway login
```

### 2. Initialize Project
```bash
railway init
# Select "New Project"
```

### 3. Add Services

In Railway Dashboard:
1. Add **PostgreSQL** service
2. Add **Redis** service

### 4. Set Environment Variables

```bash
# Copy from .env.template and set production values
railway variables set --file .env
```

Or set individually:
```bash
railway variables set DATABASE_URL=<postgres_url>
railway variables set REDIS_URL=<redis_url>
railway variables set SHOPIFY_ADMIN_ACCESS_TOKEN=<token>
# ... etc
```

### 5. Deploy API Service
```bash
railway up
```

### 6. Deploy Celery Worker

Create a second service in Railway:
1. Go to your project
2. Add Service → GitHub Repo (same repo)
3. Set start command: `celery -A app.worker worker --loglevel=info --concurrency=4`
4. Link same environment variables

### 7. Deploy Celery Beat (Scheduler)

Create third service:
1. Start command: `celery -A app.worker beat --loglevel=info`

## Shopify Webhook Registration

After deployment, register webhooks in Shopify Admin:

1. Go to Settings → Notifications → Webhooks
2. Add these webhooks:

| Topic | URL |
|-------|-----|
| Orders/Fulfillments | `https://your-app.railway.app/webhooks/shopify/{store_id}/orders-fulfilled` |
| Products/Create | `https://your-app.railway.app/webhooks/shopify/{store_id}/products-create` |
| Products/Update | `https://your-app.railway.app/webhooks/shopify/{store_id}/products-update` |
| Products/Delete | `https://your-app.railway.app/webhooks/shopify/{store_id}/products-delete` |
| Customers/Create | `https://your-app.railway.app/webhooks/shopify/{store_id}/customers-create` |
| Customers/Update | `https://your-app.railway.app/webhooks/shopify/{store_id}/customers-update` |

## SendGrid Webhook

Configure in SendGrid Dashboard:

1. Settings → Mail Settings → Event Webhook
2. URL: `https://your-app.railway.app/email-events/sendgrid`
3. Enable: Delivered, Opened, Clicked, Bounced, Unsubscribed, Spam Reports

## Monitoring

### Health Check
```
GET https://your-app.railway.app/health
```

### Flower (Celery Monitoring)
```bash
# Port forward to local
celery -A app.worker flower --port=5555
```

### Sentry Dashboard
Monitor at: https://sentry.io

## Database Migrations

```bash
# Generate migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

## Production Environment Variables

Critical variables for production:

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection for Celery |
| `SHOPIFY_ADMIN_ACCESS_TOKEN` | Shopify Admin API token |
| `SHOPIFY_WEBHOOK_SECRET` | Webhook HMAC verification |
| `GROQ_API_KEY` | LLM API key for message generation |
| `GPT_IMAGE_KEY` | Image generation API key |
| `SENTRY_DSN` | Error tracking |
| `PUBLIC_APP_URL` | Your production domain |

## Scaling

### Horizontal Scaling
- Add more Railway services pointing to same repo
- Increase `concurrency` for Celery workers
- Use Railway's autoscaling feature

### Database Scaling
- Upgrade Railway Postgres plan
- Add read replicas if needed

### Redis Scaling
- Upgrade Railway Redis plan
- Consider Redis Cluster for high throughput
