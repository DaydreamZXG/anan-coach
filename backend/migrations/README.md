# Database migrations

Alembic is the schema version owner from the WeChat identity release onward.

The legacy production schema is represented by revision `20260712_00`. Existing environments must first be checked against `scripts/server_setup_learning_ai_tables.sh`, then stamped at that revision before applying later migrations:

```bash
alembic stamp 20260712_00
alembic upgrade head
```

New environments must provision the complete baseline schema before running Alembic. A release may not stamp an environment whose schema contract test does not pass.
