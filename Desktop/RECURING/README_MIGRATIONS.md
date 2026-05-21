# Database Migrations

This project uses Alembic for production schema changes.

Local SQLite development still works through `init_db()` and compatibility migrations, but production Postgres should not rely on `Base.metadata.create_all()`.

## Run Migrations

```powershell
alembic upgrade head
```

For a database different from `.env`, use:

```powershell
$env:ALEMBIC_DATABASE_URL="postgresql://user:password@host:5432/styleiq"
alembic upgrade head
Remove-Item Env:\ALEMBIC_DATABASE_URL
```

`ALEMBIC_DATABASE_URL` takes priority over `DATABASE_URL`. If it is not set, Alembic uses `DATABASE_URL` from the environment or `.env`.

## Current Head

```text
0003_current_schema
```

The baseline migration creates all current application tables, APScheduler job storage, foreign keys, and indexes used by:

- store/customer scoped lookups
- event timestamps
- send logs
- campaign states
- outfit image cache lookup

## Existing SQLite Dev DB

Your existing `app.db` can keep running locally. If you later want Alembic to track that existing SQLite DB without recreating tables, run:

```powershell
alembic stamp head
```
