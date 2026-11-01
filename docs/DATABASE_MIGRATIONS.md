# ANCOS database migrations

The ORM `create_all()` path is limited to development and test environments. Production startup only verifies that the database has the single Alembic head revision; it does not modify the schema.

## New database

Back up and review the target, then run the approved migration explicitly:

```powershell
$env:DATABASE_URL = 'postgresql+asyncpg://...'
uv run alembic upgrade head
```

SQLite development installations use the configured `DATABASE_URL` in `.env` in the same way.

## Existing unversioned database

Take and verify a restorable backup first. Compare the existing schema to the initial baseline on a copy. Only when every table, column, index, and constraint matches that baseline, record the baseline without applying DDL:

```powershell
uv run alembic stamp 12f5e817193f
```

If the schema differs, do not stamp it. Create a data-preserving migration from the inspected schema and validate it against a restored copy before using it.

## Deployment gate

`APP_ENV=production` fails startup if no migration revision is recorded or if the database is not at the repository head. Apply migrations as a separately reviewed operation, then restart the API. The initial baseline deliberately refuses downgrade because dropping the baseline tables would destroy data; restore a verified backup to recover from a failed first deployment. Later revisions must define and validate their own reversible or forward-repair strategy.
