# Wallet Admin

This repository currently contains the approved Stage 1–6 backend foundation:

- Flask application factory and SQLAlchemy database integration
- seven-table schema and Alembic migration
- historical CSV snapshot importer
- pending system transaction creation and idempotency
- atomic approval, cancellation, and reversal services
- schema, import, lifecycle, rollback, and concurrency tests

Authentication, authorization routes, and UI pages are intentionally deferred until
the Stage 6 checkpoint is reviewed.

## Local setup (PowerShell)

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

## Database migration

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

To verify that ORM metadata and migrations agree:

```powershell
.\.venv\Scripts\python.exe -m alembic check
```

## Historical import

The importer accepts a CSV extracted from the assignment source:

```powershell
.\.venv\Scripts\python.exe -m flask --app run.py import-historical data\sample.csv
```

It prints a JSON report containing imported counts, idempotent and conflicting
duplicates, rejected rows, warnings, ambiguous wallets, and chosen wallet anchors.

Historical transactions preserve `source_balance_after_minor` and never create
`balance_postings`. The latest unambiguous historical balance initializes the
player/currency wallet. An ambiguous wallet remains explicitly uninitialized.

All `HISTORICAL_IMPORT` transactions, including supplied pending rows such as
TX10012, are read-only sample/seed records. The assignment requires storing and
displaying the sample and permits authorized record updates, but does not require
the supplied pending rows to enter the operational workflow. Approval, failure,
cancellation, and reversal actions apply only to `SYSTEM` transactions.

The assignment PDF/sample file is not currently present in this workspace, so the
real source rows have not been imported. Files under `tests/fixtures` are synthetic
test cases and are not presented as assignment data.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -vv
```

## Current import assumptions

- Player IDs are global across operators.
- Wallet identity is player plus currency; operator is transaction metadata.
- Input monetary values are major units with exactly two supported decimal places.
- Input timestamps without a timezone are interpreted as UTC and reported as a warning.
- Country and operator labels are preserved as source text after trimming whitespace.
- Transaction types determine direction, except historical reversals require an explicit direction and original transaction reference.
- Equal latest timestamps with different balances are ambiguous and do not initialize the wallet.
- Historical rows are stored but never replayed as new wallet changes.
- A pending historical transaction's balance-after value may initialize a wallet. This is an explicit import assumption because the supplied data is an incomplete historical snapshot and the pending rows show the unchanged current balance; pending transactions still create no posting.
