# Production readiness

Before production, the following are required:

1. Run PostgreSQL in all integration/concurrency environments and validate lock,
   isolation, timeout, deadlock-retry, and partial-index behavior under load.
2. Add a complete source-ledger reconciliation process, opening-balance policy,
   posting-to-wallet consistency checks, and operational alerts.
3. Model held/reserved funds for pending withdrawals and agree a policy for reversals
   when credited funds were already spent.
4. Use a managed secret store, TLS, secure proxy/header configuration, session
   revocation, password reset, MFA/SSO, rate limiting, and login abuse monitoring.
5. Define segregation of duties and approval limits; high-value or sensitive actions
   should use maker-checker approval rather than a single Finance Operator.
6. Make audit logs tamper-evident and centrally retained, with request/correlation IDs,
   access monitoring, retention policy, and controlled export.
7. Add data privacy classification, encryption/backup/restore testing, disaster
   recovery targets, retention/deletion rules, and jurisdiction-specific compliance.
8. Add observability, health checks, structured logs, metrics, tracing, and alerts.
9. Add background jobs or partitioning/read models when transaction volume justifies
   them; paginate all history views and archive/partition large ledger tables.
10. Add PostgreSQL-backed end-to-end, security, accessibility, browser, load, and
    failure-injection testing in CI before release.
