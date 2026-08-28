# Wallet Admin

A small server-rendered administration panel for a real-money gaming wallet. It is
a Flask modular monolith with an append-oriented transaction ledger, atomic cached
wallet balances, role-based access, CSRF protection, and audit logging.

## Local setup (PowerShell)

Python 3.13 was used for the take-home. Create an isolated environment and install
only the application and test dependencies:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

If the Windows `py` launcher is unavailable, use the installed interpreter directly,
for example:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" -m venv .venv
```

The local configuration uses SQLite at `instance/wallet_admin.db`, an HTTP-compatible
session cookie, and a development-only secret. Do not use those defaults in production.

## Database and sample data

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m flask --app run.py import-historical data\sample_transactions.csv
```

The importer prints a JSON report with imported, rejected, duplicate, wallet-anchor,
warning, and ambiguity details. It is safe to retry: identical IDs/payloads are
reported as duplicates, while a reused ID with different data is rejected and audited.

The assignment PDF's 12 rows are preserved as read-only `HISTORICAL_IMPORT` records.
They initialize wallet snapshot balances but create no postings and are never replayed.
This includes pending source row TX10012. See [sample data review](docs/sample-data-review.md).

Financial records are not generically editable. Updates use controlled status
transitions and linked reversals to preserve auditability and balance integrity.

## Create users

For a real initial local administrator, enter a password interactively:

```powershell
.\.venv\Scripts\python.exe -m flask --app run.py create-admin --username admin
```

For a disposable demo, generate one user per role with random passwords:

```powershell
.\.venv\Scripts\python.exe -m flask --app run.py setup-demo-users --prefix demo
```

The generated passwords are printed once and are not hardcoded in source. Choose a
new prefix if rerunning the command.

## Run and demo

```powershell
.\.venv\Scripts\python.exe -m flask --app run.py run
```

Open `http://127.0.0.1:5000` and use this short demonstration:

1. Sign in as Viewer; inspect dashboard totals, transaction filters, wallet balances,
   and the read-only label on a historical transaction.
2. Sign in as Finance Operator; create an ordinary transaction. It begins `PENDING`.
   Approve it and show its posting plus updated wallet balance. Create another and
   cancel it with a confirmation and reason.
3. Sign in as Administrator; reverse the approved system transaction with a reason,
   inspect the linked result, review audit events, and manage a user's role/status.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pytest -vv
.\.venv\Scripts\python.exe -m alembic check
.\.venv\Scripts\python.exe -m pip check
```

Migration downgrade/upgrade can be checked against a disposable database by setting
`DATABASE_URL`, then running `alembic upgrade head`, `alembic downgrade base`, and
`alembic upgrade head`.

## Production configuration

Set `APP_ENV=production`, a high-entropy `SECRET_KEY`, and a PostgreSQL `DATABASE_URL`.
Production mode forces `SESSION_COOKIE_SECURE`; startup rejects the development secret.
Terminate TLS at the application edge. Additional production work is listed in
[production readiness](docs/production-readiness.md).

Design details are in [architecture](docs/architecture.md), with explicit
[assumptions, tradeoffs, and risks](docs/assumptions-and-risks.md). The
[requirement traceability table](docs/traceability.md) maps every assignment item to
implementation and verification evidence.

## AI usage

AI was used as the coding and review partner to implement the approved schema,
historical importer, financial services, authentication, server-rendered UI, tests,
and documentation. The candidate retained responsibility for product and architecture
decisions, including wallet identity, pending-withdrawal behavior, historical snapshot
semantics, transaction immutability, reversal policy, roles, and the two-day scope.

AI proposals that were changed or rejected include operator-scoped wallets, generic
correction transactions, temporarily exposing `APPROVED` before posting, a reversal
constraint that blocked later attempts after failure, processing historical pending
rows, microservices, and additional frontend/infrastructure complexity. Correctness
was checked through source review, database constraints, rollback and independent-
connection concurrency tests, role/CSRF integration tests, migration/drift checks,
clean setup rehearsal, and manual desktop/mobile HTTP UI verification.
