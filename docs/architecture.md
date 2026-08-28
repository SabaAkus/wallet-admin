# Architecture

The application is a Flask modular monolith using direct SQLAlchemy sessions and
Alembic migrations. The primary tables are `users`, `operators`, `players`,
`wallets`, `transactions`, `balance_postings`, and `audit_events`.

`transactions` preserves business and historical source records.
`balance_postings` is reserved for authoritative effects performed by this system.
`wallets.current_balance_minor` is the cached available balance.

The importer deliberately does not replay historical rows. It initializes a wallet
from its latest unambiguous supplied balance and stores that transaction as the
provenance anchor. Because the source is an incomplete snapshot, no opening
transaction is inferred and no attempt is made to reconcile the displayed subset.
Once a wallet has a system posting, later historical-import retries leave its cached
balance and anchor unchanged and report a warning; snapshot data can never overwrite
live ledger effects.

A pending historical transaction may be the initialization anchor when it is the
latest source row. This is an explicit snapshot-import assumption supported by the
provided data: pending transactions show an unchanged balance-after value. It does
not mean pending transactions affect balance, and no posting is created for them.

The supplied table is identified by the assignment as "Sample Data." The stated
requirements are to store and display it and to allow an authorized administrator
to add or update records; they do not state that supplied pending rows must be
processed. Therefore every `HISTORICAL_IMPORT` row, including TX10012, is read-only.
Only newly created `SYSTEM` transactions participate in approval, failure,
cancellation, and reversal workflows. This avoids replaying a snapshot against a
wallet balance initialized from that same snapshot.

The `wallets.balance_initialized` flag is an integrity guard: an ambiguous or
otherwise unusable source balance cannot silently become an apparent zero balance.
Later transaction processing must refuse balance changes for an uninitialized wallet.

## Financial write transactions

The financial service owns a fresh, short database session for every write. SQLite
uses `BEGIN IMMEDIATE` before business reads. PostgreSQL uses `SELECT ... FOR UPDATE`
for transaction and original-reversal rows. Available-funds protection is an atomic
conditional wallet `UPDATE`, not an application read/check/write sequence.

The wallet update, unique posting, final status, and audit event commit together.
`PENDING` changes directly to `APPROVED` only after a posting is ready, or directly
to `FAILED` when the conditional balance update reports insufficient funds.

Reversal attempts are separate transactions. A partial unique database index allows
multiple pending/failed attempts for an original but at most one approved reversal.
The original row lock is the primary concurrency control and the index is the final
database backstop.

## Authentication and authorization

Flask-Login provides signed session authentication using Flask's cookie; passwords
are stored only as Werkzeug adaptive hashes. Every state-changing form is protected
by Flask-WTF CSRF validation. Local cookies are `HttpOnly` and `SameSite=Lax`;
production mode additionally requires HTTPS cookies and refuses the development secret.

Permission decorators enforce route access, and the financial and user services
repeat important role checks so direct service invocation cannot bypass policy.
Viewers have read access, Finance Operators can create/process ordinary system
transactions, and Administrators additionally control reversals, audit viewing, and
users. Inactive users are rejected by login and user loading. The final active
administrator cannot be disabled or demoted; PostgreSQL locks active administrator
rows while evaluating that invariant.

## Web layer

The UI is server-rendered Jinja with local CSS and no CDN or client framework. Routes
query through a request-scoped read session and delegate mutations to short-lived
service sessions. Transaction and wallet lists use bounded database pagination.
Filters are applied in SQL. UTC timestamps are labeled consistently and money stays
integer minor units until display formatting.
