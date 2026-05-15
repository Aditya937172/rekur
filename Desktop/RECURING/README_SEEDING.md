# Shopify Retention Seed Dataset

This project contains a deterministic Shopify seed utility for a D2C clothing brand test store. It generates realistic products, customers, orders, recommendation relationships, and a readiness/scoring report for AI retention workflows.

## What It Creates

- 150 clothing products across shirts, t-shirts, jeans, trousers, cargos, dresses, hoodies, jackets, co-ord sets, ethnic wear, and accessories
- 1,000 customers with realistic segments, consent states, contact gaps, duplicate-looking names, bad phone data, and incomplete addresses
- 2,500+ historical orders across the last 365 days
- Product-level recommendation map with complementary, similar, upsell, and seasonal products
- Seed report with scoring groups, channel readiness, revenue, AOV, top products, top cities, and skipped/error records

All seeded Shopify records are tagged:

```text
seeded_by_retention_app
```

Records also receive stable per-record tags such as `seed_product_<handle>`, `seed_customer_<key>`, and `seed_order_<key>` for rerun safety.

## Required Shopify Scopes

Configure a custom app on the Shopify development store with:

```text
read_products
write_products
read_customers
write_customers
read_orders
write_orders
```

For full idempotency and deletion of historical orders older than Shopify's normal order read window, request `read_all_orders` if your app is eligible. The script also writes `data/shopify_seed_state.json` in live mode so it can track created historical orders by ID even when older orders are not returned by list APIs.

## Setup

Install dependencies:

```powershell
python -m pip install requests python-dotenv faker pydantic
```

Create `.env` from `.env.example`:

```powershell
Copy-Item .env.example .env
```

Fill in your development store values:

```dotenv
SHOPIFY_STORE_DOMAIN=your-development-store.myshopify.com
SHOPIFY_API_KEY=replace_me
SHOPIFY_API_SECRET=replace_me
SHOPIFY_ADMIN_ACCESS_TOKEN=shpat_replace_me
SHOPIFY_API_VERSION=2026-01
DRY_RUN=true
DELETE_SEEDED_DATA=false
```

The API key and secret are not enough for Admin API calls. The seeder uses `SHOPIFY_ADMIN_ACCESS_TOKEN`.

Do not commit `.env`. If a real app secret or token has been pasted into chat, rotate it before using the store for anything beyond local testing.

## Dry Run

Dry run is the default. It generates the local JSON files and report without calling Shopify:

```powershell
python scripts/seed_shopify_store.py --dry-run
```

Generated files:

```text
data/product_seed.json
data/customer_seed.json
data/order_seed.json
data/recommendation_map.json
data/seed_report.json
```

## Live Seed

After reviewing the generated files and confirming the store target:

```powershell
$env:DRY_RUN="false"
python scripts/seed_shopify_store.py --live
```

The live seed flow:

1. Generates the local JSON files.
2. Fetches existing records tagged `seeded_by_retention_app` where Shopify allows it.
3. Skips records already known by remote tags or local seed state.
4. Creates missing products, customers, and orders.
5. Writes `data/seed_report.json` and `data/shopify_seed_state.json`.

## Delete Seeded Data

To delete records created by this seeder:

```powershell
$env:DRY_RUN="false"
$env:DELETE_SEEDED_DATA="true"
python scripts/seed_shopify_store.py --live --delete-seeded-data
```

Deletion runs in dependency order:

1. Orders
2. Customers
3. Products

Only records tagged `seeded_by_retention_app` or tracked in `data/shopify_seed_state.json` are targeted.

## Common Shopify Errors

`401 Unauthorized`

The Admin token is missing, expired, or for a different store. Confirm `SHOPIFY_ADMIN_ACCESS_TOKEN` and `SHOPIFY_STORE_DOMAIN`.

`403 Forbidden`

The custom app does not have the required scopes. Add the scopes, reinstall the app, and use the new Admin access token.

`404 Not Found`

The store domain or API version is wrong. Use the `*.myshopify.com` domain and confirm `SHOPIFY_API_VERSION`.

`422 Unprocessable Entity`

Shopify rejected one record. Common causes are duplicate customer emails, invalid phone formats, image URL fetch failures, or unsupported order fields. The seeder omits malformed phone numbers from Shopify customer payloads but keeps them in `customer_seed.json` and notes. Product image failures are retried without images.

`429 Too Many Requests`

The seeder retries with exponential backoff and honors Shopify rate-limit headers. Live seeding thousands of orders can take time.

Historical orders not found on rerun

Shopify may limit order reads without `read_all_orders`. Keep `data/shopify_seed_state.json`; it stores created IDs for idempotency and cleanup.

## Files

- `scripts/seed_shopify_store.py`: deterministic dataset generator and optional live Shopify seeder
- `app/services/shopify_client.py`: Shopify REST Admin API client with pagination, retry, backoff, and rate-limit handling
- `app/core/config.py`: `.env` backed settings
- `data/*.json`: generated seed data and report

## Notes

Shopify's REST Admin API is marked legacy by Shopify, but it remains practical for this seed utility. The client is isolated in `app/services/shopify_client.py` so the seeding script can be moved to GraphQL later without rewriting the dataset generator.
