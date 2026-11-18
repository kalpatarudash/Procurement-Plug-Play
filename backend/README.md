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
| `seed.py` | Deterministic synthetic dataset (2 years of weekly sales, stock lots, PO history) |

## Accounting treatment on approval

| Event | Entry | Ledger |
| --- | --- | --- |
| Write-off approved | Dr 5150 Inventory write-off expense / Cr 1300 Inventory (oldest stock first) | financial |
| PO approved | Dr 9100 Purchase commitments / Cr 9200 Reserve for commitments | memo |
| PO received | Dr 1300 Inventory / Cr 2100 Goods received not invoiced, and the commitment is released | financial |

If the target system rejects a posting, nothing is saved and the decision stays pending with
`last_error` set, so it can be retried.

Salesforce Sales Cloud can receive POs (into a `Purchase_Order__c` custom object) but cannot be
the ABOR: it has no general ledger.
