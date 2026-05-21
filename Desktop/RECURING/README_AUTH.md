# Store Auth and Shopify Connect

This backend now treats `store_id` as private data. Dashboard/user-facing store APIs require:

```text
Authorization: Bearer <access_token>
```

## Local Dev Token

For local development only:

```powershell
curl -X POST http://127.0.0.1:8010/auth/dev-token `
  -H "Content-Type: application/json" `
  -d "{\"email\":\"owner@example.com\",\"name\":\"Store Owner\"}"
```

Use the returned `access_token` on store-scoped endpoints.

## Start Shopify Connect

```powershell
curl -X POST http://127.0.0.1:8010/connect/shopify/start `
  -H "Authorization: Bearer <access_token>" `
  -H "Content-Type: application/json" `
  -d "{}"
```

Open the returned `connect_url` or use the returned Nango Connect session token from the frontend.

## Complete Shopify Connect

After OAuth succeeds, call the callback with the Nango connection and shop domain:

```powershell
curl -X POST http://127.0.0.1:8010/connect/shopify/callback `
  -H "Authorization: Bearer <access_token>" `
  -H "Content-Type: application/json" `
  -d "{\"connection_id\":\"<nango_connection_id>\",\"shopify_store_domain\":\"brand.myshopify.com\"}"
```

The callback creates or updates the `Store`, saves the per-store Nango connection ID, and creates the owner mapping.

## Protected Store Endpoints

These now require store ownership:

```text
GET/POST /stores/{store_id}/...
GET /stores/{store_id}/events/summary
GET /stores/{store_id}/sessions
POST /messages/{message_id}/...
POST /outfits/{outfit_id}/send
```

Shopify webhooks and email provider callbacks stay callback-facing. They are not dashboard user APIs.
