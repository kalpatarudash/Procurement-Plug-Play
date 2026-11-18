import { useCallback, useEffect, useState } from 'react'

/** Base URL of the Pro-sight backend. In dev, Vite proxies /api to the FastAPI server. */
const API_BASE = import.meta.env.VITE_API_URL ?? ''

export type Period = { view: 'YTD' | 'QTD' | 'MTD' } | { start: string; end: string }

export type PeriodFigures = {
  start: string
  end: string
  revenue: number
  cogs: number
  gross_profit: number
  gross_margin: number
  operating_expenses: number
  taxes_interest: number
  net_income: number
  net_margin: number
}

export type Financials = {
  currency: string
  label: string
  current: PeriodFigures
  prior_year: PeriodFigures
  revenue_growth: number | null
  net_income_growth: number | null
  monthly_revenue: { month: string; revenue: number }[]
}

export type Inventory = {
  as_of: string
  currency: string
  total_value: number
  sku_count: number
  turns: number
  aged_value: number
  aged_share: number
  stockout_risk_skus: number
  categories: {
    name: string
    stock_value: number
    sell_through: number
    weeks_of_cover: number | null
    aged_share: number
    status: string
    suggested_action: string
  }[]
}

export type ConnectorInfo = {
  id: string
  name: string
  kind: string
  description: string
  capabilities: string[]
  configured: boolean
  setup_hint: string | null
  connected: boolean
  is_abor: boolean
  is_po_target: boolean
}

export type EmissionsPeriod = {
  start: string
  end: string
  scope1: number
  scope2: number
  scope3: number
  total: number
  purchases_usd: number
  kg_per_revenue_usd: number | null
}

export type Emissions = {
  label: string
  current: EmissionsPeriod
  prior_year: EmissionsPeriod
  total_change: number | null
  scope3_by_vendor: Record<string, number>
  sources: Record<string, string>
}

type ScopeFigures = { scope1: number; scope2: number; scope3: number; total: number }

export type Lever = {
  id: string
  scope: 1 | 2 | 3
  title: string
  detail: string
  reduction_t: number
  annual_cost: number
  cost_per_t: number | null
  basis: 'data' | 'assumption'
  selected: boolean
}

export type PlanResponse = {
  saved: { revenue_target: number; emission_reduction_pct: number; updated_at: string; updated_by: string } | null
  analysis: {
    fiscal_year: number
    start: string
    end: string
    baseline_start: string
    baseline_end: string
    currency: string
    revenue: {
      baseline: number
      prior_baseline: number
      trend_growth: number
      projected_at_trend: number
      target: number
      required_growth: number
      gap_to_trend: number
    }
    margins: { gross_margin: number; target_gross_profit: number; target_cogs: number }
    procurement: {
      current_inventory: number
      inventory_turns: number
      target_ending_inventory: number
      purchase_budget: number
      baseline_purchases: number
      change_vs_baseline: number | null
    }
    monthly: { month: string; revenue_target: number; purchase_budget: number }[]
    emissions: {
      reduction_pct: number
      baseline: ScopeFigures
      projected: ScopeFigures
      target_total: number
      gap: number
      levers: Lever[]
      selected_reduction: number
      investment: number
      on_track: boolean
      emission_pressure: number
      sources: { scope3: string; scope1_2: string }
    }
    assumptions: Record<string, number>
  }
}

export type VendorOption = {
  vendor_id: number
  name: string
  unit_cost: number
  lead_weeks: number
  on_time_rate: number
  kg_co2e_per_unit: number
  arrives_before_stockout: boolean
  score: number
  components: Record<string, number>
}

export type JournalEntry = {
  id: number
  number: string
  entry_date: string
  memo: string
  ledger: 'financial' | 'memo'
  posted_to: string
  external_ref: string | null
  lines: { account_code: string; account_name: string; debit: number; credit: number }[]
}

type PoDetails = {
  order_by: string | null
  stockout_date: string | null
  on_order: number
  kg_co2e: number
  vendor_options: VendorOption[]
  weights: Record<string, number>
}

type AgingDetails = {
  aged_units: number
  aged_value: number
  oldest_days: number
  unit_price: number
  hold: AgingScenario
  markdown: AgingScenario
}

export type Decision = {
  id: number
  kind: 'purchase_order' | 'write_off' | 'markdown'
  status: 'pending' | 'approved' | 'rejected'
  title: string
  reason: string
  impact_usd: number
  source: 'manual' | 'model'
  payload: { sku_id: number; quantity: number; vendor_id?: number; unit_cost?: number; clearance_price?: number }
  // Model evidence; which fields exist depends on `kind`.
  details: ({ sku_code: string; category: string; confidence: 'High' | 'Medium' | 'Low'; on_hand: number; amount: number }
    & Partial<PoDetails> & Partial<AgingDetails>) | null
  created_at: string
  decided_at: string | null
  decided_by: string | null
  note: string | null
  last_error: string | null
  purchase_order: { id: number; number: string; vendor: string; quantity: number; amount: number; external_system: string | null; expected_date: string } | null
  journal_entry: JournalEntry | null
}

export type AgingScenario = {
  aged_sold: number
  aged_revenue: number
  newer_revenue: number
  weeks_to_clear: number | null
  holding_cost: number
  write_off_units: number
  write_off_cost: number
  cash: number
}

export type AgingPlan = {
  sku_id: number
  sku_code: string
  sku_name: string
  category: string
  aged_units: number
  aged_value: number
  oldest_days: number
  unit_price: number
  clearance_price: number
  hold: AgingScenario
  markdown: AgingScenario
  recommendation: 'hold' | 'write_off' | 'markdown'
  cash_gain_vs_hold: number
  reason: string
  actions: { kind: 'write_off' | 'markdown'; quantity: number; value: number; clearance_price?: number }[]
}

export type WriteOffRun = {
  as_of: string
  assumptions: Record<string, number>
  plans: AgingPlan[]
  totals: { aged_units: number; aged_value: number; write_off_cost: number; markdown_revenue: number; cash_gain_vs_hold: number }
}

export type SkuPlan = {
  sku_id: number
  sku_code: string
  sku_name: string
  category: string
  status: 'order_now' | 'upcoming' | 'healthy' | 'overstocked'
  on_hand: number
  on_order: number
  stockout_date: string | null
  order_by: string | null
  quantity: number
  amount: number
  vendor: VendorOption | null
  confidence: string
  reason: string
}

export type RecommendationRun = {
  as_of: string
  weights: Record<string, number>
  emission_pressure: number
  forecast: { model: string; backtest_wape: number; naive_wape: number; backtest_weeks: number; horizon_weeks: number }
  plans: SkuPlan[]
  budget: { month: string; recommended: number; plan_budget: number | null; within_budget: boolean }[]
  plan_alignment: { forecast_revenue: number; plan_revenue: number; gap_pct: number | null; weeks_compared: number }
}

export type SkuInfo = { id: number; code: string; name: string; category: string; vendor: string; unit_cost: number; on_hand: number }
export type VendorInfo = { id: number; name: string; price_index: number; lead_time_days: number; on_time_rate: number; kg_co2e_per_usd: number }

export type ImportReport = {
  ok: boolean
  imported: boolean
  errors: { sheet: string; row: number | null; column: string | null; message: string }[]
  error_count: number
  warnings: string[]
  summary: {
    vendors: number
    skus: number
    categories: number
    inventory_lots: number
    inventory_units: number
    inventory_value: number
    sales_rows: number
    sales_weeks: number
    sales_from: string | null
    sales_to: string | null
    revenue: number
    expense_months: number
    purchase_orders: number
    open_purchase_orders: number
    energy_months: number
    as_of: string | null
    currency: string
    fiscal_year_start_month: number
  }
}

export type DatasetInfo = { source: 'demo' | 'import'; as_of: string; currency: string; fiscal_year_start_month: number }

export class ApiError extends Error {}

/** Absolute URL of an API path, for links such as file downloads. */
export function apiUrl(path: string) {
  return `${API_BASE}/api${path}`
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(apiUrl(path), {
      ...init,
      // JSON bodies are strings; FormData (file uploads) must set its own multipart header.
      headers: typeof init?.body === 'string' ? { 'Content-Type': 'application/json', ...init?.headers } : init?.headers,
    })
  } catch {
    throw new ApiError('Can’t reach the Pro-sight API. Is the backend running?')
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = typeof body?.detail === 'string' ? body.detail : `Request failed (${res.status})`
    throw new ApiError(detail)
  }
  return res.json() as Promise<T>
}

export function periodQuery(period: Period) {
  return new URLSearchParams('view' in period ? { view: period.view } : { start: period.start, end: period.end }).toString()
}

/** Fetch `path` whenever it changes. Stale responses from a previous path are ignored. */
export function useApi<T>(path: string) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  useEffect(() => {
    let cancelled = false
    setError(null)
    api<T>(path)
      .then(result => { if (!cancelled) setData(result) })
      .catch((e: Error) => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [path, version])
  const reload = useCallback(() => setVersion(v => v + 1), [])
  return { data, error, reload, setData }
}

/** $4.82M, $684K, $950 */
export function formatMoney(value: number) {
  const abs = Math.abs(value)
  const sign = value < 0 ? '−' : ''
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return `${sign}$${Math.round(abs / 1e3)}K`
  return `${sign}$${Math.round(abs)}`
}

export function formatPct(value: number, digits = 1) {
  return `${(value * 100).toFixed(digits)}%`
}

export function formatSignedPct(value: number | null) {
  if (value === null) return 'n/a'
  return `${value >= 0 ? '+' : '−'}${Math.abs(value * 100).toFixed(1)}%`
}
