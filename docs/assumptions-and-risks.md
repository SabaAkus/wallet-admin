# Assumptions, tradeoffs, and risks

## Explicit assumptions

- Wallet identity is `(player, currency)`; operator is transaction metadata because
  the assignment does not establish operator-scoped wallets.
- The supplied table is an incomplete historical snapshot, not a complete ledger.
  Its latest unambiguous balance-after value initializes each wallet without replay.
- A pending historical row may be a snapshot anchor. This preserves the supplied
  current balance but does not create a posting or make that row operational.
- Historical timestamps without offsets are interpreted as UTC with an import warning.
- The MVP supports currencies displayed with two decimal minor units.
- Pending withdrawals do not reserve funds. Only approval changes available balance.

## Main risks

- Money movement is high impact. A defect in idempotency, posting atomicity, or access
  control can create financial loss or an incorrect ledger.
- Snapshot balances cannot be independently reconciled without opening entries or a
  complete source ledger. They must not be represented as reconstructed balances.
- No reserved balance means a pending withdrawal's funds may be spent before approval.
- A reversal can be rejected after credited funds have been spent because this MVP
  never permits a negative available balance.
- SQLite has one writer and differs from PostgreSQL in locking and operational behavior.
- Browser sessions are cookie-backed and have no central forced-revocation store.
- The assignment's country data is transaction metadata, not identity/KYC evidence.
- Database access is expected to go through the importer and domain services. A user
  or script with unrestricted database/ORM write access could bypass lifecycle and
  immutable-field rules; production database roles must deny such direct writes.

## Deliberate MVP tradeoffs

- A modular monolith and synchronous requests keep the two-day system understandable.
- Cached wallet balance plus immutable postings makes reads fast while retaining an
  authoritative system-created trail; reconciliation tooling is deferred.
- Role granularity is intentionally limited to three roles.
- Reversal creation and approval happen in one administrator interaction, while the
  service still models the reversal as its own pending transaction before processing.
- UI amount formatting assumes two decimals; production needs currency metadata for
  currencies with zero or three minor digits.
