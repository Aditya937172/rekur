# RECURING Manual Testing Checklist

## Pre-Launch Validation

Run these tests manually before onboarding any paying brand.

---

## 1. Webhook Pipeline

### 1.1 Create Test Order
```
1. Open Shopify dev store admin
2. Create order with test product
3. Mark order as fulfilled
4. Watch for webhook in your logs (should arrive within 10s)
```

**Verify:**
- [ ] Webhook received in API logs
- [ ] Celery task created (check Flower at :5555)
- [ ] Outfit image generated (check `generated_outfit_images` table)
- [ ] Email delivered to customer inbox
- [ ] `RetentionSendLog` record created
- [ ] Open email → `EmailEngagement` record with event_type="open"

---

## 2. Pre-Churn Detection

### 2.1 Seed Test Data
```sql
-- Add engagement records for test customer
INSERT INTO email_engagement (store_id, customer_id, event_type, timestamp)
VALUES 
  (1, 1, 'sent', datetime('now', '-30 days')),
  (1, 1, 'sent', datetime('now', '-20 days')),
  (1, 1, 'sent', datetime('now', '-10 days')),
  (1, 1, 'open', datetime('now', '-9 days'));
```

### 2.2 Calculate Churn Risk
```bash
curl http://localhost:8000/stores/1/customers/1/churn-risk
```

**Verify:**
- [ ] Response includes churn_score
- [ ] Signals include purchase_drop, email_engagement_drop
- [ ] Personal baseline used (not hardcoded values)

### 2.3 Run Pre-Churn Campaign
```bash
curl -X POST http://localhost:8000/stores/1/campaigns/pre-churn \
  -H "Content-Type: application/json" \
  -d '{"limit": 10, "send_email": true}'
```

**Verify:**
- [ ] Customer with score > 65 received email
- [ ] Campaign response shows `sent` count

---

## 3. Anniversary Campaign

### 3.1 Set Test Anniversary Date
```sql
-- Set first order exactly 365 days ago
UPDATE buyer_memory 
SET first_order_at = datetime('now', '-365 days')
WHERE store_id = 1 AND customer_id = 1;
```

### 3.2 Run Anniversary Campaign
```bash
curl -X POST http://localhost:8000/stores/1/anniversary \
  -H "Content-Type: application/json" \
  -d '{"days_window": 365, "send_email": true}'
```

**Verify:**
- [ ] Email references actual purchase (not generic text)
- [ ] Shows first product purchased
- [ ] Recommends similar products
- [ ] Email delivered to customer inbox

---

## 4. Vector Cache

### 4.1 First Generation (Cache Miss)
```bash
curl -X POST http://localhost:8000/stores/1/outfits/generate \
  -H "Content-Type: application/json" \
  -d '{"customer_id": 1, "trigger_reason": "test"}'
```

**Verify:**
- [ ] New record in `outfit_image_cache` table
- [ ] `hit_count = 0` initially

### 4.2 Second Generation (Cache Hit)
```bash
# Same customer, same products
curl -X POST http://localhost:8000/stores/1/outfits/generate \
  -H "Content-Type: application/json" \
  -d '{"customer_id": 1, "trigger_reason": "test"}'
```

**Verify:**
- [ ] Logs show "cache_hit"
- [ ] Response time < 1 second (not 60-90s)
- [ ] `hit_count` incremented in database

---

## 5. Multi-Store

### 5.1 Create Second Store
```bash
curl -X POST http://localhost:8000/stores \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Store 2", "shopify_store_domain": "test-store-2.myshopify.com", "nango_connection_id": "conn_2"}'
```

### 5.2 Add Customers to Store 2
```sql
INSERT INTO customer (store_id, email, first_name, shopify_customer_id)
VALUES (2, 'test2@example.com', 'Test2', 'cust2');
```

### 5.3 Run Seasonal Campaign
```bash
curl -X POST http://localhost:8000/stores/campaigns/seasonal \
  -H "Content-Type: application/json" \
  -d '{"season": "spring", "limit": 10}'
```

**Verify:**
- [ ] Logs show processing for both stores
- [ ] Customers from store 1 receive email
- [ ] Customers from store 2 receive email
- [ ] No hard-coded `store_id=1` in scheduler logs

---

## 6. Reply Handling

### 6.1 Send Test Email
```bash
curl -X POST http://localhost:8000/stores/1/outfits/generate \
  -H "Content-Type: application/json" \
  -d '{"customer_id": 1, "trigger_reason": "test", "send_email": true, "recipient_email": "your-test-email@gmail.com"}'
```

### 6.2 Reply to Email
```
1. Check your test inbox
2. Reply to the StyleIQ email with style preferences
3. Example: "I love minimalist clothes, mostly black and white. Need more casual weekend outfits."
```

### 6.3 Verify Reply Processing
```
Wait up to 5 minutes for polling cycle.
```

**Verify:**
- [ ] `CustomerReply` record created in database
- [ ] `extracted_preferences_json` contains signals
- [ ] `BuyerMemory.style_tags` updated
- [ ] Acknowledgment email sent to customer

---

## 7. Shopify Sync

### 7.1 Test Product Webhook
```
1. Add new product in Shopify admin
2. Watch for webhook at /webhooks/shopify/{store_id}/products-create
```

**Verify:**
- [ ] Product appears in `products` table within 10s
- [ ] Product has correct title, image_url, tags

### 7.2 Test Customer Webhook
```
1. Create new customer in Shopify admin
2. Watch for webhook at /webhooks/shopify/{store_id}/customers-create
```

**Verify:**
- [ ] Customer appears in `customers` table
- [ ] Email, name fields populated

### 7.3 Test Nightly Sync
```bash
curl -X POST http://localhost:8000/admin/trigger-sync
```

**Verify:**
- [ ] All products reconciled
- [ ] All customers reconciled
- [ ] Sync stats returned (created/updated counts)

---

## 8. Email Engagement Tracking

### 8.1 Configure SendGrid Webhook
```
1. Open SendGrid dashboard
2. Settings > Mail Settings > Event Webhook
3. Add: https://your-app.railway.app/email-events/sendgrid
4. Enable: Delivered, Opened, Clicked, Bounced
```

### 8.2 Trigger Test Events
```
1. Send email via API
2. Open the email in test inbox
3. Click link in email (if any)
```

**Verify:**
- [ ] `EmailEngagement` record with event_type="open"
- [ ] `EmailEngagement` record with event_type="click"
- [ ] Timestamp matches email open time

---

## Final Checklist

Before onboarding first brand:

- [ ] All webhook tests pass
- [ ] Image generation works (30-90s)
- [ ] Email delivery confirmed
- [ ] Churn detection returns scores
- [ ] Anniversary emails reference purchase history
- [ ] Vector cache shows hits on repeat requests
- [ ] Multi-store processes all active stores
- [ ] Reply handling extracts style signals
- [ ] Shopify sync updates database
- [ ] Email tracking populates engagement table

---

## Quick Commands

```bash
# Run automated tests
python scripts/test_pre_launch.py

# Start all services
docker-compose up -d

# Watch API logs
docker-compose logs -f api

# Watch Celery worker
docker-compose logs -f worker

# Monitor Celery tasks
open http://localhost:5555

# Check scheduler jobs
curl http://localhost:8000/scheduler/jobs

# Health check
curl http://localhost:8000/health
```
