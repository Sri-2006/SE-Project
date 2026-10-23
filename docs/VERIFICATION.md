# Verification record

Verified locally on 23 October 2026. All records used were synthetic demonstration or isolated test data.
The original lab PDFs were used as requirements input; their sample execution results were not adopted as evidence.

## Automated checks

- PostgreSQL: **57 tests passed**, final Windows-host run in **15.710 seconds**, exit code 0.
- Final Linux web-container run: **57 tests passed in 5.902 seconds**, including successful test-database cleanup.
- Includes three real concurrent PostgreSQL tests: simultaneous approvals create one order; simultaneous delivery
  updates add stock once; replaying the same integration event concurrently creates one stock movement.
- Other coverage: role restrictions, CSRF, login throttling, invalid/gapped/stale forecast data, model evaluation,
  supplier scores, approval/rejection/override transitions, stale recommendations, transaction rollback, validation,
  atomic imports, export formula escaping, alerts and operational page rendering.
- A container run initially passed all assertions but failed to delete its test database because thread connections
  remained open. Worker cleanup now explicitly closes each thread's connections. The final run completed cleanup.
- Django system checks passed. `makemigrations --check --dry-run` reported no changes.
- Container build and startup succeeded. Both PostgreSQL and web health checks passed.
- `check --deploy` passed with DEBUG disabled in the container. This checks Django configuration; it does not certify hosting security.
- Windows launcher successfully recognizes an already-running healthy app.

## Browser workflows

Verified through the in-app browser using the local manager account:

1. Sign in and render dashboard, chart, metrics, alerts and navigation.
2. Approve recommendation REC-0005 for 285 Protective Wrap units at Bengaluru Hub.
3. Inspect generated draft order PO-71EEC9A9, move it to Ordered, then Delivered with 285 units.
4. Confirm inventory moved from 48 on hand / 32 available to **333 on hand / 317 available** and contains the receipt ledger entry.
5. Confirm the order, balances and audit records survived transfer to PostgreSQL and the later database restart.
6. Generate a fresh seven-day Cable Assembly / Delhi North forecast: 41 total units, 5.8 daily units and 2.0 MAE.
7. Filter inventory by Bengaluru Hub and Protective: exactly the expected received inventory position appears via HTMX.
8. Inspect desktop and 390px mobile layouts. No document-level horizontal overflow; mobile navigation expands and collapses.
9. Final browser console inspection returned no warning/error entries.

Screenshots: [desktop overview](screenshots/overview-desktop.png), [mobile overview](screenshots/overview-mobile.png),
[generated forecast](screenshots/forecast.png).

## Local HTTP measurements

Final deployment: Docker web container (Waitress, 8 threads) and PostgreSQL 17 container on this Windows computer.
One warm-up followed by one cohort of 50 authenticated parallel GET requests per page, using the seeded demo data.

| Page | Successes | Median | 95th percentile | Maximum |
|---|---:|---:|---:|---:|
| Overview | 50 / 50 | 1.000 s | 1.514 s | 1.543 s |
| Recommendation list | 50 / 50 | 0.590 s | 0.838 s | 0.858 s |

Raw results: [benchmark.json](benchmark.json). Reproduce with `python scripts/benchmark.py --clients 50` while the app is running.
These are local HTTP response measurements, not browser rendering timings, sustained-load results, planning-generation
latency, or a guarantee at production scale. They do not establish the original distributed-system performance requirements.

## Operational limits

- This is a functional local prototype with synthetic data, not an externally published production service.
- No real supplier purchasing, ERP/carrier credentials, emails, or payments are connected. Orders are internal records.
- Forecasts are statistical point estimates. Their assumptions and holdout errors are visible; forecast accuracy on real data is unproven.
- Sales history must extend through yesterday. Import fresh data when returning on a later date.
- SQLite is an optional separate single-user fallback; concurrency assurance applies to PostgreSQL.
- Docker must be available for the primary deployment. Services now use restart policies; database connections have bounded
  connection/query/lock waits and health checks. An actual stopped database was restored successfully during verification.
- Automated backup/restore drills, independent penetration testing, accessibility certification, distributed failover,
  real multi-tenant isolation, and external integrations are outside this delivered local scope.
- See ARCHITECTURE.md for the explicit reconciliation of the lab's microservices/scaling requirements with the simplified stack.
