# Database

- Schema changes are made through **migrations** — checked-in, versioned,
  applied in order. Never alter a schema by running an ad-hoc `ALTER` (or
  `CREATE`/`DROP`) by hand; the migration history is the single source of truth
  for the shape of the database, and a change that isn't in it doesn't exist.
- **Migrations are forward-only. Never write or run a down/downgrade
  migration.** To undo a bad migration, write a *new* forward migration that
  corrects it — leave the original in place as history.
- Rationale: in production a live schema is never downgraded. A down migration
  risks dropping columns and destroying data, and can't be applied safely once
  new code and rows already depend on the new shape. Because the production path
  is forward-only, every environment stays forward-only — dev and CI exercise
  the same one-way path prod does, so a downgrade is never relied on or tested
  into existence.
- With Alembic specifically: never fill in the reverse operations Alembic
  scaffolds in a revision's `downgrade()`, and never run `alembic downgrade`.
  Make `downgrade()` fail loudly instead of leaving it a silent no-op —
  `raise NotImplementedError("Downgrades not supported")` — so an accidental
  downgrade errors out rather than appearing to succeed while doing nothing.
  Correct a bad revision with a new `alembic revision` that moves the schema
  forward.

## Approved stack

- **PostgreSQL** (database) + **SQLAlchemy** (ORM/query layer) + **Alembic**
  (migrations) is the sanctioned, preferred stack — reach for it by default.
  It isn't the only allowed option, but any alternative should be a deliberate,
  justified choice for that project (and, if it's a direction others will build
  on, worth an ADR — see `decisions.md`), not an ad-hoc substitution.
