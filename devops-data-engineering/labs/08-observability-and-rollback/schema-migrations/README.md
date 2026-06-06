# Schema migrations

Schema changes are forward-only by default — `ALTER TABLE` doesn't roll
back when you `terraform apply` an older SHA. This folder is the pattern
for handling them safely without making rollback impossible.

## The pattern

Every migration is two artifacts in the same PR:

```
schema-migrations/
├── 0007_add_currency_to_orders.up.sql
└── 0007_add_currency_to_orders.down.sql
```

- `*.up.sql` is what runs forward (new ALTER on apply).
- `*.down.sql` is the manual recipe for rolling back. It's not always
  trivial — e.g., dropping a column you've started writing to means data
  loss — so the down script's job is to **document the steps** and
  **make the trade-offs explicit**, not always to be runnable.

A migration runner reads both files. A reviewer signs off on the down
plan as much as on the up plan. The lab includes a runner skeleton in
`run_migrations.py` that:

1. Tracks applied migrations in a Glue catalog table.
2. Refuses to run an `up.sql` if the `down.sql` is missing or
   contains `# IRREVERSIBLE` without a reviewer's exception flag.
3. Logs the migration ID with the deploy ID so an incident can correlate
   "deploy v1.4.2 ran migration 0007".

## When the down isn't reversible

Some migrations are intentionally one-way (drop a column, narrow a
type, repartition a large table). For these, the rollback path isn't
"run the down", it's:

- **Restore from Iceberg time-travel** to a snapshot before the up
  ran (the `snapshot-on-deploy.py` from this lab is what makes this
  findable).
- **Replay the pipeline** from the original raw data with the old code.

Both are documented in the down.sql as commented steps, so an oncall
following the migration's own breadcrumbs gets the right answer at
2 AM.

## What's NOT here

A full migration framework (Alembic, Flyway-equivalent for Iceberg /
Athena). This is the scaffolding pattern. For a real platform, pick
one of the established tools and adapt the up/down convention to it —
the discipline is "every migration ships with a reviewed down plan,"
which is what most ad-hoc migration scripts skip.
