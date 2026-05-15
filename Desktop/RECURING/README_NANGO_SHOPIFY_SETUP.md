# Nango Shopify Integration Setup

This setup uses the isolated Nango instance for this project only:

```text
Nango dashboard/API: http://localhost:3005
Nango Connect UI: http://localhost:3010
Docker project: retention-nango
Containers: retention-nango-server, retention-nango-db, retention-nango-redis
```

Do not use or modify REID's Nango instance on `3003/3009`.

## Start Isolated Nango

```powershell
docker compose -p retention-nango -f docker/nango/docker-compose.yml up -d
curl http://localhost:3005/health
```

Expected health response:

```json
{"result":"ok"}
```

## Required Env

Root `.env` should contain:

```dotenv
NANGO_BASE_URL=http://localhost:3005
NANGO_PUBLIC_KEY=
NANGO_SECRET_KEY=
NANGO_PROVIDER_CONFIG_KEY=shopify
SHOPIFY_CLIENT_ID=
SHOPIFY_CLIENT_SECRET=
SHOPIFY_SCOPES=read_products,read_customers,read_orders,read_inventory,read_script_tags,write_script_tags
```

Do not put real secrets in `.env.example`.

## Run Playwright Setup

```powershell
python scripts/setup_nango_shopify_integration_playwright.py
```

The script:

- verifies Docker and the isolated `retention-nango-*` containers
- verifies Nango health on `http://localhost:3005`
- logs into the local Nango dashboard
- creates or updates the `shopify` integration
- sets scopes to `read_products,read_customers,read_orders,read_inventory,read_script_tags,write_script_tags`
- writes `data/nango_shopify_setup.json`
- prints `NANGO_SHOPIFY_REDIRECT_URL=...`

## Shopify Redirect URL

After the script prints the redirect URL, paste the exact value into:

```text
Shopify Partner Dashboard
-> Apps
-> Your App
-> Configuration
-> Allowed redirection URL(s)
```

The expected Nango callback URL for this local setup is:

```text
http://localhost:3005/oauth/callback
```

Use the exact value printed by the script if it differs.

## Required Shopify Scopes

```text
read_products,read_customers,read_orders,read_inventory,read_script_tags,write_script_tags
```

## After Pasting Redirect URL

Do not start real merchant OAuth until the Shopify app is saved with the callback URL.

Next command to verify the setup after saving the redirect URL:

```powershell
python scripts/setup_nango_shopify_integration_playwright.py
```

Then you can start a real Nango Connect session from the FastAPI app.

## Troubleshooting Callback Errors

Do not open `http://localhost:3005/oauth/callback` directly in the browser as a test. It is only the OAuth callback endpoint and must be reached after Nango starts an OAuth flow with a generated `state` value.

If you open it directly, Nango may show an error such as `No state found in callback` or a fallback/callback error. That does not mean the Shopify redirect URL is wrong.

Paste the callback URL only in:

```text
Shopify Partner Dashboard
-> Apps
-> Your App
-> Configuration
-> Allowed redirection URL(s)
```

Do not use the Nango callback URL as the Shopify app URL or homepage URL.
