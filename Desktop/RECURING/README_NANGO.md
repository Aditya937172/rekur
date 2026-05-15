# Isolated Nango Setup

This app uses a separate self-hosted Nango stack. It must not reuse REID's Nango containers, database, Redis, volumes, ports, or env files.

## Isolation

This project uses:

```text
retention-nango-server
retention-nango-db
retention-nango-redis
retention-nango-network
retention-nango-data
```

Ports:

```text
Nango server: http://localhost:3005
Nango Connect UI: http://localhost:3010
Postgres host port: 5544
Redis host port: 6381
```

It intentionally avoids the common REID/local ports `3003`, `3009`, `5432`, and `6379`.

## Start Nango

From the project root:

```powershell
docker compose -p retention-nango -f docker/nango/docker-compose.yml up -d
```

Open:

```text
http://localhost:3005
http://localhost:3010
```

The dashboard credentials are in `docker/nango/.env`. That file is local-only and ignored by git.

## Stop Only This App's Nango

```powershell
docker compose -p retention-nango -f docker/nango/docker-compose.yml down
```

To remove only this app's Nango data volume:

```powershell
docker compose -p retention-nango -f docker/nango/docker-compose.yml down -v
```

Do not run broad Docker cleanup commands such as `docker system prune` if REID containers exist.

## Confirm REID Was Not Used

List running containers:

```powershell
docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Ports}}"
```

Expected names for this project all start with `retention-nango-`. No `reid-*` container should be stopped, renamed, reused, or connected to `retention-nango-network`.

Check volumes:

```powershell
docker volume ls
```

This project should use `retention-nango-data`, not any REID volume.

## FastAPI Environment

Add these to the root `.env`:

```dotenv
NANGO_BASE_URL=http://localhost:3005
NANGO_SECRET_KEY=
NANGO_PUBLIC_KEY=
NANGO_ENVIRONMENT=dev
```

After Nango starts, create or open the `dev` environment in the Nango dashboard and copy the secret/public keys from the dashboard environment or API keys/settings area. Self-hosted Nango versions can label this area slightly differently; use the key intended for server-side API calls as `NANGO_SECRET_KEY`, and the browser/Connect SDK key as `NANGO_PUBLIC_KEY`.

## Shopify Provider Config

Do not create the Shopify OAuth integration until you are ready with the real Shopify app credentials.

When ready, in the Nango dashboard:

1. Create an integration/provider config for Shopify.
2. Use a stable provider config key such as `shopify`.
3. Add the Shopify app client ID and client secret.
4. Configure OAuth redirect URLs exactly as Nango shows them.
5. Add the required Shopify scopes for the retention app.

Credentials needed later:

```text
Shopify app client ID
Shopify app client secret
Required Shopify Admin API scopes
Store domain during connection
```

## FastAPI Usage

The service wrapper is in `app/services/nango_service.py`.

Example:

```python
from app.services.nango_service import NangoService

nango = NangoService.from_settings()
health = nango.health_check()
integrations = nango.list_integrations()
connection = nango.get_connection("connection-id", provider_config_key="shopify")
products = nango.proxy_get(
    "connection-id",
    "shopify",
    "/admin/api/2026-01/products.json",
)
```

The Shopify Connect helper prepares a Nango Connect session:

```python
session = nango.start_shopify_connection(
    end_user_id="merchant_123",
    provider_config_key="shopify",
)
```

## Troubleshooting

Docker daemon not running:

```text
open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified
```

Start Docker Desktop, then retry the compose command.

Port already in use:

```powershell
netstat -ano | Select-String -Pattern ":3005|:3010|:5544|:6381"
```

Change only this project's `docker/nango/.env` port values if needed. Do not change REID's compose files or containers.

Nango dashboard does not load:

```powershell
docker compose -p retention-nango -f docker/nango/docker-compose.yml logs retention-nango-server
```

Database not healthy:

```powershell
docker compose -p retention-nango -f docker/nango/docker-compose.yml logs retention-nango-db
```

FastAPI cannot call Nango:

1. Confirm `NANGO_BASE_URL=http://localhost:3005`.
2. Confirm `NANGO_SECRET_KEY` is copied from the Nango dashboard.
3. Run a health check against `http://localhost:3005`.
