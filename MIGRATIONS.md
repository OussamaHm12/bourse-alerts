# Migrations & database operations

Everything the platform knows lives in one database: three years of séances, every
generated report, every prediction, the whole learning history. The upstream history
endpoint only re-serves a **~3-year rolling window**, so anything older that is lost
is lost **permanently** — no re-collection rebuilds it. That is why every procedure
here starts with a backup and ends with a verification.

---

## The rules

1. **Always back up first.** `cli backup` exits non-zero when the snapshot cannot be
   verified, so it works as a gate: `cli backup && cli migrate`.
2. **Never migrate from a laptop.** `DATABASE_URL` is `sqlite:///data/market.db` — a
   **relative** path. Inside the container it means the Railway volume; on your
   machine it means `./data/market.db`. `railway run` executes **locally** with
   Railway's variables injected, so it would report a perfectly plausible success
   against the wrong database. Use `railway ssh`.
3. **Migrations never run on boot.** `init_db` creates missing tables and stamps the
   Alembic revision, but applies nothing. Auto-migrating on deploy would mean a bad
   migration takes the app down with no chance to back up first.

---

## Where the schema stands

The schema was created by `create_all` and had no migration history until 2026-07-16.
`create_all` creates what is missing and ignores everything else — it can never
**alter** or **drop**. Alembic now owns changes.

| Revision | What |
| --- | --- |
| `50c59b463e1e` | baseline — the 15 tables as deployed |
| `1b2587ed6aab` | drop the orphan `signals` table |

`init_db` reconciles the two automatically:

* **fresh database** → `create_all` just built the current schema, so it is stamped
  **head**: nothing older to run.
* **pre-Alembic database** (the deployed one) → stamped at the **baseline**, so
  pending migrations still run. Stamping head would silently skip them and the
  orphan table would live forever.
* **already stamped** → untouched; migrations own it.

```bash
python -m moroccan_stock_intelligence.cli migrate-status   # where am I, what is pending
python -m moroccan_stock_intelligence.cli migrate          # apply everything pending
python -m moroccan_stock_intelligence.cli migrate --to -1  # roll back one revision
python -m moroccan_stock_intelligence.cli migrate --sql    # print the SQL, run nothing
```

### Applying the pending migration to production

```bash
railway ssh
python -m moroccan_stock_intelligence.cli backup            # verified + shipped off-host
python -m moroccan_stock_intelligence.cli migrate-status    # expect: 50c59b463e1e
python -m moroccan_stock_intelligence.cli migrate           # drops the orphan signals table
python -m moroccan_stock_intelligence.cli migrate-status    # expect: 1b2587ed6aab (head)
curl -s localhost:8000/api/health
```

**Rollback:** `cli migrate --to -1` recreates `signals` — the shape, not the rows. A
drop is a drop. That is acceptable **here and nowhere else**: nothing ever read that
table, so nothing can miss its contents. Any future migration touching a table with
real data must stage the rows elsewhere first; a downgrade that only rebuilds the
shape is not a rollback for data.

If a migration fails mid-way, restore the backup rather than improvising:

```bash
gunzip -c /app/data/backups/market-<stamp>.db.gz > /app/data/market.db
```

**Verified** on a copy of the real database (2026-07-16): stamp → upgrade → the
orphan table is gone and all rows survive (400 prices, 11 news, 80 stocks);
downgrade → the table is back; re-upgrade → gone again. The cycle is repeatable, and
the same cycle passes on PostgreSQL 16.

---

## SQLite → PostgreSQL

Production runs **SQLite on a Railway volume**. PostgreSQL is supported by the config
and the driver ships, but has never been used. The procedure below is **proven, not
proposed** — the numbers are from a real run, not an estimate.

### Why not `.dump` / `pg_restore`

SQLite's dump is SQLite SQL: its types, its `AUTOINCREMENT` and its date handling do
not survive contact with Postgres. `cli copy-database` goes through the ORM metadata
instead, so every column lands with the type `models.py` declares — on either backend,
and in either direction, which is what makes the rollback real.

### What it guarantees

* **Read-only on the source.** If anything fails, production is untouched and the
  answer is "do nothing".
* **Verified, not assumed.** Row counts are compared per table *after* the copy; a
  mismatch is an error, not a warning.
* **Refuses a non-empty target.** Primary keys are copied as-is, so a populated
  target does not duplicate rows — it raises halfway and leaves a partial database.
  There is no override flag: it was written, and removed, because its only reachable
  outcome was that crash.
* **Resets Postgres sequences.** Copying explicit ids leaves each sequence at 1, so
  the next INSERT collides with an existing row. SQLite has no sequences, which is
  exactly why this is easy to forget — it fails on the first write *after* the
  switch, not during the migration.

### The procedure

```bash
# 1. Back up. Non-negotiable.
railway ssh
python -m moroccan_stock_intelligence.cli backup

# 2. Provision Postgres and copy. Read-only on the source.
python -m moroccan_stock_intelligence.cli copy-database \
    --to "postgresql+psycopg://user:pass@host:5432/market"

# 3. Bring the target's migration state up.
DATABASE_URL="postgresql+psycopg://..." \
    python -m moroccan_stock_intelligence.cli migrate-status

# 4. Switch: set DATABASE_URL on the service, redeploy.
# 5. Validate.
curl -s https://<app>/api/health
curl -s https://<app>/api/stocks | head -c 400
```

**Rollback:** put the old `DATABASE_URL` back and redeploy. The SQLite volume was
never written to, so it is exactly as it was. This is the reason step 2 is read-only.

### Proof (2026-07-16, PostgreSQL 16.13)

A demo database at production volume — 15 tables, **60 034 rows**, 15.3 MB, including
59 040 price rows over 738 séances × 80 symbols:

| | |
| --- | --- |
| Copy | **60 034 rows in 5.2 s** (11 546 rows/s), 15/15 tables count-verified |
| App on Postgres | **13/13 endpoints HTTP 200** |
| Writes | `POST /api/favorites` 200; a new notification got `id=51` after a max of 50 — the sequence reset works. Without it, the first INSERT violates the primary key |
| Alembic on Postgres | baseline → head, downgrade, re-upgrade — all clean |
| Second copy | correctly **refused** (target not empty) |

### Should you switch?

**Not yet, on this evidence.** The migration is proven; the *need* is not. SQLite on
a volume currently serves the whole market in 4-34 ms per endpoint behind the
fingerprint cache, and the write load is one scheduler thread. Postgres would buy
concurrent writers and managed backups, and cost a second service plus a network hop
on every query — the same first request measured 1 045 ms on Postgres vs ~990 ms on
SQLite, i.e. no gain at this size.

The honest trigger is one of: a second writer (a worker, a second instance), a volume
that outgrows the plan, or wanting managed PITR backups rather than the Telegram
copy. Until one of those is true, this document exists so the switch is a decision
rather than a project.
