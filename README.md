# SupplyPilot

A working supply chain decision-support application for the Software Engineering lab project.
Built with **Django 5.2 LTS, PostgreSQL, HTMX, Bootstrap, Chart.js, pandas, NumPy and statsmodels**.
It runs entirely locally. No AI subscription, API key, paid service, or CDN access is required to use the app.

## Start on this Windows computer

Double-click **`Start SupplyPilot.cmd`**, or run:

```powershell
.\start.ps1
```

Open **http://127.0.0.1:8000**. Accounts and passwords are in the ignored local file **`.demo-credentials.txt`**.
Use **manager** to explore everything. **planner** can operate inventory, forecasts, orders and recommendations.
**admin** also has the Django administration interface at `/admin/` for account and role management.

The launcher installs locked dependencies if needed, starts the configured local PostgreSQL container,
runs migrations, creates demo data only when the database is empty, and starts the web server.
If SupplyPilot is already healthy on port 8000, the launcher reports its URL and exits. If another application
uses that port, stop that application first or use `-Port 8001`.

The alternative `./start.ps1 -LocalOnly` runs a **separate SQLite database** without Docker.
This fallback is suitable for a single-user demonstration; PostgreSQL is the supported database for concurrent writes.
The two databases do not synchronize. Credentials can differ between separately seeded databases; use the credential
file written during their initial seeding or set a password with `manage.py changepassword`.

The app includes **synthetic demonstration data**: 12 products, 3 warehouses, 4 suppliers, 24 inventory positions,
90 days of daily sales, historical deliveries, active shipments, low-stock situations and pending recommendations.
The UI labels this as a demo workspace. No supplier messages or purchases are sent externally.

## Demonstrate the complete workflow

1. Sign in as `manager` and review Overview.
2. Open **Inventory**, filter by warehouse or stock health, and inspect a stock ledger.
   Managers and Administrators can use **Add inventory → New product** to enter a name, SKU, category,
   supplier, cost, warehouse and opening balance together. **Existing product** adds a warehouse position;
   choosing an existing product/warehouse opens its edit screen without overwriting its balance.
   Each row has **Edit stock** for changing on-hand/reserved units with a reason, plus **Edit product details**
   on the detail screen. Django Admin's inventory Add/Change links open these same audited workflows.
3. Open **Demand forecasting**, select a product/location and a 7, 30 or 90-day horizon, and generate a forecast.
4. Open **Supplier intelligence** to inspect observed delivery and fulfilment metrics.
5. Click **Run planning**, then review a pending recommendation and its evidence.
6. Approve it, or choose an override quantity with a reason. This creates a **draft** purchase order.
7. Open the order, mark it Ordered, then In transit or Delayed. Record Delivered with the actual received quantity.
8. Return to inventory: received units are added exactly once; relevant alerts resolve.
9. Check **Alerts & activity** for the audit trail, then **Reports** for period filters and CSV export.
10. Use **Data workspace** to import validated inventory balances or sales history.

Planner and Manager can approve/reject recommendations. Only Manager and Administrator can override quantities,
manage the catalog, or access reports. A user without one of these groups is denied operational access.
The built-in password-change page is available from the profile link. Administrators manage users and groups in `/admin/`.

## Fresh setup

Requirements: Python 3.13, and Docker Desktop for PostgreSQL. SQLite fallback needs only Python.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
Copy-Item .env.example .env
# Set unique POSTGRES_PASSWORD and DJANGO_SECRET_KEY values in .env.
docker compose up -d --wait db
# Add DATABASE_URL=postgresql://supplypilot:YOUR_URL_SAFE_PASSWORD@127.0.0.1:5433/supplypilot to .env.
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
.\.venv\Scripts\python.exe -m waitress --threads=8 --listen=127.0.0.1:8000 supplypilot.wsgi:application
```

If Windows `venv` creation fails during `ensurepip`, bootstrap pip into the already-created environment:

```powershell
python -m pip --python .venv\Scripts\python.exe install pip
```

Linux/macOS can use `sh start.sh` after Python is installed and any configured PostgreSQL database is available.

## Run both services in Docker

```powershell
docker compose up --build -d
docker compose exec web python manage.py seed_demo
docker compose exec web cat .demo-credentials.txt
```

The web container runs as a non-root user. PostgreSQL persists in a named volume. Host bindings are loopback-only.
Stop services with `docker compose down`; this preserves the database volume. Do not add `-v` unless you intend to delete it.
Demo seeding is explicitly disabled with `DJANGO_DEBUG=0`. Create real accounts with `createsuperuser` for a clean deployment.

## Configuration

`.env` supports simple `KEY=value` entries; exported environment variables take precedence. Never commit secrets.

| Setting | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection URL; omit or set `sqlite` for the local fallback |
| `DJANGO_SECRET_KEY` | Session/CSRF signing secret; mandatory with DEBUG off |
| `DJANGO_DEBUG` | `1` for local development, `0` for HTTPS deployment |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hostnames |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated HTTPS origins for a hosted deployment |
| `DB_SSLMODE` | PostgreSQL SSL mode; use `require` with managed remote databases |

For internet hosting, run behind an HTTPS reverse proxy, set DEBUG off, use a unique secret and proper hostnames,
provide database backups and monitoring, and create non-demo accounts. HTTPS cookies, redirects, and HSTS are enabled
with DEBUG off. Reverse-proxy trust headers are deliberately not enabled automatically. This repository is a tested
local prototype, not an externally deployed service or a claim of production readiness.

## Data imports and integration API

**Inventory CSV:** `sku,warehouse,quantity`. Quantity is an absolute on-hand balance, not a delta.
**Sales CSV:** `sku,warehouse,date,quantity`, with ISO dates before today and explicit zero-sales rows.
Create the product, warehouse and inventory position first. Headers are required. The whole file is validated and
committed atomically. Maximum: 2 MB / 10,000 rows. Duplicate rows are rejected; replayed inventory files are skipped.
Sales imports upsert daily records and supersede pending recommendations for affected positions.

Examples live in `static/samples/`. Refresh their dates with `python scripts/make_sample_data.py`.
Seeded sales are fixed at creation time; forecasts intentionally reject stale data. Import current history when
returning to the demo on another day.

`POST /api/events/` accepts an authenticated Django session **and CSRF token**, with `Content-Type: application/json`:

```json
{"event_id":"erp-stock-001","type":"inventory","sku":"SKU-1001","warehouse":"Mumbai Central","quantity":35,"reserved":0}
```

```json
{"event_id":"shipment-001","type":"shipment","order_reference":"PO-2026-101","status":"delayed","carrier":"Demo carrier","tracking_reference":"TRACK-001"}
```

Shipment delivery additionally needs `received_quantity`. Replays return `200 {"status":"duplicate"}`; new events
return `201 {"status":"accepted"}`. Invalid payloads return 400 and are logged. A reused event ID with different data
is rejected. Correct rejected events with a new ID. The API does not disable CSRF or invent an external ERP connection.

## Forecasting and planning

- At least 28 consecutive daily sales records through yesterday are required. Missing days are not silently treated as zero.
- Compare 7-day moving average, weekly seasonal baseline and exponential smoothing against up to 14 held-out days using MAE.
- Refit the best model to the full history; produce nonnegative 7/30/90-day point forecasts.
- Safety stock = `ceil(1.65 × recent daily demand standard deviation × sqrt(lead time))`.
- Reorder point = `ceil(mean forecast demand × lead time + safety stock)`.
- Inventory position = on hand − reserved + open purchase-order quantities (including drafts).
- Recommend enough to cover supplier lead time plus a 14-day review period when position is at/below the reorder point.
- Priority increases for likely stockouts or high supplier risk. Risk is `50% late delivery rate + 30% unfulfilled-unit rate + 20% disruption rate`.
- Human approval remains necessary. Fixed lead time and normal-demand assumptions are documented in the decision screen.
- A rerun supersedes previous pending recommendations, preserving history. Stale stock, open-order, supplier, and cost snapshots block approval.

`python manage.py run_planning` can run after an import or from an OS scheduler. No scheduler is silently installed.
Inventory lists refresh every 30 seconds. Alerts update on operational writes and planning runs.

## Tests and verification

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe scripts\benchmark.py --clients 50
```

Tests use a temporary test database. The PostgreSQL account needs permission to create it (the Docker demo account does).
The suite covers access control, CSRF, login throttling, forecast data checks, approval/override state transitions,
atomic rollback, duplicate events, atomic CSV imports, reporting, shipment receipts, and concurrent PostgreSQL writes.
The benchmark uses demo credentials against localhost only. See `docs/VERIFICATION.md` for measured results and limits.

## Repository map

```text
supplypilot/                 Django configuration and WSGI entry point
operations/models.py        Relational models and database constraints
operations/services/        Analytics, transactions, and import processing
operations/views.py         Role-protected web screens and integration endpoint
operations/tests.py         Functional, security, integrity and concurrency tests
operations/management/      Seed data and planning commands
templates/                  Server-rendered screens and HTMX partials
static/                     Local CSS, JS, vendored libraries and example CSVs
docs/                       Architecture, requirement mapping, verification
scripts/                    Startup helpers, assets and benchmark
```

The original lab documents remain untouched. `docs/ARCHITECTURE.md` records the proposed changes from microservices
requirements to this simpler implementation. Your evaluator can review those explicitly.
