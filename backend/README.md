# Pro-sight backend

FastAPI + SQLAlchemy (SQLite for now). Python 3.12, managed with `uv`.

## Run

```bash
cd backend
uv run uvicorn prosight.main:app --reload      # API on http://127.0.0.1:8000, docs at /docs
```

In another terminal, from the project root, run the frontend: `PORT=8080 pnpm run dev`.
Vite forwards `/api/*` to the backend.

The database (`prosight.db`) is created and filled with synthetic data on first start.
Reset it with `uv run python -m prosight.seed` (also needed after a model change: the server
refuses to start on an out-of-date database and says so). Run tests with `uv run pytest`.

LightGBM on macOS needs the OpenMP runtime: `brew install libomp`.

## Deploying

The frontend is static and can live on Cloudflare (or Figma Make). The API is Python with
LightGBM and SQLite, so it needs a Python host; the frontend then calls it by URL.

1. **Backend on Render** (free plan): Render → New → Blueprint → choose this repository. It uses
   [`render.yaml`](../render.yaml) and [`Dockerfile`](Dockerfile). When it's live, opening
   `https://<your-service>.onrender.com/api/health` shows `{"status":"ok"}`.
2. **Allow the frontend's site**: `PROSIGHT_CORS_ORIGINS` (set in `render.yaml`) must list the
   frontend's address, comma-separated if more than one, e.g.
   `https://procurement-plug-play.kalpataru7165.workers.dev`.
3. **Point the frontend at it**: in Cloudflare, add the build variable
   `VITE_API_URL=https://<your-service>.onrender.com` and redeploy. It's read at build time, so
   changing it always needs a rebuild.

Free-plan limits: the service sleeps after ~15 minutes idle (the next visit takes about a minute
to wake it), and its disk is wiped on restart, so the demo data is re-created and imported data
must be uploaded again. For data that persists, use a plan with a persistent disk and set
`PROSIGHT_DATABASE_URL=sqlite:////<disk mount>/prosight.db`.

Without a backend the site shows "Can't reach the Pro-sight API … returned a web page instead of
data": the static host answers `/api/...` with the app's own HTML page.

## Your own data (Excel / CSV)

The demo dataset is synthetic (`seed.py`). To use real data, open the Inventory or Financials tab →
**Upload CSV or Excel**, or call the API directly:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/import/template.xlsx` | Blank template with example rows and an Instructions sheet (`?sample=true` fills it with the current data) |
| `GET /api/import/template.zip` | The same as one CSV per sheet |
| `POST /api/import/validate` | Check an upload (one `.xlsx`, or `.csv` files named after the sheets); changes nothing |
| `POST /api/import` | Validate, then replace the dataset if there are no errors |
| `POST /api/import/reset-demo` | Restore the synthetic demo data |
| `GET /api/dataset` | Whether demo or imported data is loaded, its as-of date, currency and fiscal-year start |

Sheets: Vendors, SKUs, Inventory, Sales, Expenses (required); Purchase orders, Energy, Settings
(optional). Column definitions live in `importer.TABLES` and are written into the template's
Instructions sheet. Sales may be daily or weekly and are summed into Monday-start weeks; the
dataset's as-of date becomes the Monday after the last week. Forecast-based recommendations need
at least 25 weeks of sales. Importing replaces products, stock, sales, expenses, POs, energy,
decisions, the built-in ledger and FY targets.

## Layout

| Path | What it does |
| --- | --- |
| `connectors/base.py` | The plug-and-play connector contract (fetch financials/SKUs, create POs, post journal entries) |
| `connectors/demo.py` | Built-in ERP over the local synthetic data; also the built-in ledger |
| `connectors/external.py` | SAP S/4HANA, Salesforce Sales Cloud, Oracle NetSuite adapters (live calls not enabled yet) |
| `connectors/__init__.py` | Registry, connect/disconnect, routing of ABOR and PO postings |
| `analytics.py` | Financial periods (YTD/QTD/MTD, fiscal year from July), income statement, inventory health |
| `decisions.py` | Admin approval: executes a PO or write-off and posts it to the ABOR |
| `ledger.py` | Balanced double-entry journal posting and chart of accounts |
| `emissions.py` | Scope 1/2/3 accounting with EPA factors (`data/` holds the EPA v1.3 supply-chain factor CSV) |
| `planning.py` | FY targets: revenue → purchase budget; emission target → gap and ranked reduction levers |
| `forecasting.py` | LightGBM weekly demand forecast per SKU, backtested against a naive baseline |
| `recommendations.py` | Reorder timing, quantity and vendor choice per SKU; creates pending PO decisions |
| `writeoffs.py` | Aging stock: hold vs. markdown cash comparison; raises write-off and markdown decisions |
| `importer.py` | Excel/CSV templates, upload validation (row-level errors), and dataset replacement |
| `runtime.py` | As-of date, currency and fiscal-year start that an import overrides |
| `seed.py` | Deterministic synthetic dataset (2 years of weekly sales, stock lots, PO history) |

## Accounting treatment on approval

| Event | Entry | Ledger |
| --- | --- | --- |
| Write-off approved | Dr 5150 Inventory write-off expense / Cr 1300 Inventory (oldest stock first) | financial |
| Markdown approved, clearance price below cost | Dr 5160 Inventory write-down / Cr 1300 Inventory, down to net realizable value (clearance price less selling costs) | financial |
| PO approved | Dr 9100 Purchase commitments / Cr 9200 Reserve for commitments | memo |
| PO received | Dr 1300 Inventory / Cr 2100 Goods received not invoiced, and the commitment is released | financial |

If the target system rejects a posting, nothing is saved and the decision stays pending with
`last_error` set, so it can be retried.

Salesforce Sales Cloud can receive POs (into a `Purchase_Order__c` custom object) but cannot be
the ABOR: it has no general ledger.
