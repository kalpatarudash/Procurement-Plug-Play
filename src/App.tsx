import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { api, apiUrl, formatMoney, formatPct, formatSignedPct, periodQuery, useApi, type ConnectorInfo, type Decision, type Emissions, type Financials, type Inventory, type Period, type PlanResponse, type RecommendationRun, type SkuInfo, type VendorInfo, type WriteOffRun, type ImportReport, type DatasetInfo } from './api'

type IconName =
  | 'grid'
  | 'box'
  | 'chart'
  | 'target'
  | 'spark'
  | 'search'
  | 'bell'
  | 'arrow'
  | 'trend'
  | 'wallet'
  | 'check'
  | 'alert'

function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, ReactNode> = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="2" /><rect x="14" y="3" width="7" height="7" rx="2" /><rect x="3" y="14" width="7" height="7" rx="2" /><rect x="14" y="14" width="7" height="7" rx="2" /></>,
    box: <><path d="m4 7 8-4 8 4-8 4-8-4Z" /><path d="m4 7 8 4 8-4v10l-8 4-8-4V7Z" /><path d="M12 11v10" /></>,
    chart: <><path d="M4 19V9" /><path d="M10 19V5" /><path d="M16 19v-7" /><path d="M22 19H2" /></>,
    target: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="4" /><path d="m15 9 5-5" /></>,
    spark: <><path d="m12 3 1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5L12 3Z" /><path d="m19 15 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z" /></>,
    search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
    bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" /><path d="M10 21h4" /></>,
    arrow: <><path d="M5 12h14" /><path d="m15 8 4 4-4 4" /></>,
    trend: <><path d="m3 17 6-6 4 4 8-9" /><path d="M15 6h6v6" /></>,
    wallet: <><rect x="3" y="5" width="18" height="15" rx="3" /><path d="M3 9h18" /><path d="M16 14h2" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    alert: <><path d="M12 4 3 20h18L12 4Z" /><path d="M12 9v5" /><path d="M12 17h.01" /></>,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

const nav = [
  { id: 'overview', label: 'Overview', icon: 'grid' },
  { id: 'inventory', label: 'Inventory', icon: 'box' },
  { id: 'financials', label: 'Financials', icon: 'chart' },
  { id: 'budget', label: 'Budget planning', icon: 'target' },
  { id: 'recommendations', label: 'Decisions', icon: 'spark' },
] as const

type Tab = (typeof nav)[number]['id']

/** Parse a numeric input value, falling back to `min` for empty/invalid input and clamping to [min, max]. */
function toNumber(value: string, min = 0, max = Number.MAX_SAFE_INTEGER) {
  const n = Number(value)
  return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : min
}

/** Format an ISO yyyy-mm-dd string in local time (avoids the UTC off-by-one of `new Date(iso)`). */
function formatDate(iso: string) {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function Metric({ label, value, detail, tone = 'green' }: { label: string; value: string; detail: string; tone?: 'green' | 'orange' | 'blue' }) {
  return (
    <div className="metric-card">
      <div className={`metric-icon ${tone}`}><Icon name={tone === 'blue' ? 'wallet' : 'trend'} /></div>
      <p>{label}</p>
      <strong>{value}</strong>
      <span className={detail.startsWith('+') ? 'positive' : ''}>{detail}</span>
    </div>
  )
}

const STATUS_CLASS: Record<string, string> = { Overstocked: 'status-0', 'Stockout risk': 'status-0', Healthy: 'status-1', Aging: 'status-2' }
const DECISION_KIND: Record<Decision['kind'], string> = { purchase_order: 'Purchase order', write_off: 'Write-off', markdown: 'Markdown' }

/** Monthly revenue line for the fiscal year so far; a partial current month is left off. */
function RevenueChart({ months, asOf }: { months: { month: string; revenue: number }[]; asOf: string }) {
  const complete = months.filter(m => m.month.slice(0, 7) !== asOf.slice(0, 7))
  if (complete.length < 2) return <p className="chart-empty">Not enough complete months to chart yet.</p>
  const max = Math.max(...complete.map(m => m.revenue))
  const min = Math.min(...complete.map(m => m.revenue))
  const span = max - min || 1
  const step = 684 / (complete.length - 1)
  const path = complete.map((m, i) => `${i === 0 ? 'M' : 'L'} ${8 + i * step} ${140 - ((m.revenue - min) / span) * 110}`).join(' ')
  return (
    <div className="chart-wrap">
      <svg viewBox="0 0 700 160" preserveAspectRatio="none" aria-label="Monthly revenue chart">
        <defs><linearGradient id="area" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#40786d" stopOpacity=".24" /><stop offset="1" stopColor="#40786d" stopOpacity="0" /></linearGradient></defs>
        {[30, 70, 110, 150].map(y => <line key={y} x1="0" x2="700" y1={y} y2={y} className="grid-line" />)}
        <path d={`${path} L ${8 + (complete.length - 1) * step} 155 L 8 155 Z`} fill="url(#area)" />
        <path d={path} className="chart-line" />
      </svg>
      <div className="chart-labels">{complete.map(m => <span key={m.month} title={formatMoney(m.revenue)}>{formatDate(m.month).split(' ')[0]}</span>)}</div>
    </div>
  )
}

/** Fiscal years are named by the calendar year they end in (FY 2026 = Jul 2025–Jun 2026 when they start in July). */
function fiscalLabels(asOf: string, startMonth: number) {
  const [y, m] = asOf.split('-').map(Number)
  const startYear = m >= startMonth ? y : y - 1
  const currentFy = startMonth === 1 ? startYear : startYear + 1
  const monthsIn = (y - startYear) * 12 + m - startMonth
  return { currentFy, planFy: currentFy + 1, quarter: Math.floor(monthsIn / 3) + 1 }
}

type Fiscal = ReturnType<typeof fiscalLabels>

function Overview({ setTab, period, pending, fiscal }: { setTab: (tab: Tab) => void; period: Period; pending: Decision[] | null; fiscal: Fiscal | null }) {
  const fin = useApi<Financials>(`/financials?${periodQuery(period)}`)
  const ytd = useApi<Financials>('/financials?view=YTD')
  const inv = useApi<Inventory>('/inventory')
  const openPos = useApi<{ amount: number }[]>('/purchase-orders?status=open&limit=1000')
  const failed = fin.error || ytd.error || inv.error || openPos.error
  if (!fin.data || !ytd.data || !inv.data || !openPos.data) {
    return <LoadState error={failed} onRetry={() => { fin.reload(); ytd.reload(); inv.reload(); openPos.reload() }} />
  }

  const c = fin.data.current
  const [y, m, d] = inv.data.as_of.split('-').map(Number)
  const today = new Date(y, m - 1, d)
  const committed = openPos.data.reduce((sum, po) => sum + po.amount, 0)
  const actions = [...(pending ?? [])].sort((a, b) => b.impact_usd - a.impact_usd)
  const impact = actions.reduce((sum, a) => sum + a.impact_usd, 0)
  const top = actions[0]
  const attention = [...inv.data.categories].sort((a, b) => Number(a.status === 'Healthy') - Number(b.status === 'Healthy') || b.stock_value - a.stock_value).slice(0, 3)
  const ytdData = ytd.data

  return (
    <>
      <section className="hero">
        <div>
          <span className="eyebrow">{today.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' }).toUpperCase()}</span>
          <h1>Good morning, {CURRENT_USER.split(' ')[0]}.</h1>
          <p>Here’s what needs your attention across procurement and finance.</p>
        </div>
        {fiscal && <button className="primary-button" onClick={() => setTab('budget')}>Plan FY {fiscal.planFy} <Icon name="arrow" /></button>}
      </section>

      <section className="metrics-grid">
        <Metric label={`Revenue · ${fin.data.label}`} value={formatMoney(c.revenue)} detail={`${formatSignedPct(fin.data.revenue_growth)} vs last year`} />
        <Metric label={`Net income · ${fin.data.label}`} value={formatMoney(c.net_income)} detail={`${formatSignedPct(fin.data.net_income_growth)} vs last year`} tone="blue" />
        <Metric label="Inventory value" value={formatMoney(inv.data.total_value)} detail={`${formatPct(inv.data.aged_share)} aging stock`} tone="orange" />
        <Metric label="Open purchase orders" value={openPos.data.length.toLocaleString()} detail={`${formatMoney(committed)} committed`} tone="blue" />
      </section>

      <div className="content-grid">
        <section className="panel forecast-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">PERFORMANCE</span><h2>Revenue by month</h2></div>
            <button className="text-button" onClick={() => setTab('financials')}>View financials <Icon name="arrow" size={15} /></button>
          </div>
          <div className="chart-summary"><strong>{formatMoney(ytdData.current.revenue)}</strong><span>Fiscal year to date</span><b>{formatSignedPct(ytdData.revenue_growth)}</b></div>
          <RevenueChart months={ytdData.monthly_revenue} asOf={inv.data.as_of} />
        </section>

        <section className="panel ai-panel">
          <div className="ai-mark"><Icon name="spark" size={20} /></div>
          <span className="eyebrow">PROCURE AI</span>
          {pending === null ? <h2>Checking the latest forecast…</h2>
            : actions.length === 0 ? <h2>No actions waiting for approval</h2>
            : <h2>{actions.length} action{actions.length === 1 ? '' : 's'} worth {formatMoney(impact)} waiting for approval</h2>}
          <p>From the demand forecast, your FY plan, and aging stock.</p>
          {top && (
            <div className="mini-action">
              <span className="alert-dot"><Icon name="alert" size={16} /></span>
              <div><strong>{top.title}</strong><small>{DECISION_KIND[top.kind]} · {formatMoney(top.impact_usd)} impact</small></div>
            </div>
          )}
          <button className="dark-button" onClick={() => setTab('recommendations')}>Review recommendations <Icon name="arrow" /></button>
        </section>
      </div>

      <section className="panel inventory-table">
        <div className="panel-heading">
          <div><span className="eyebrow">INVENTORY HEALTH</span><h2>Items that need attention</h2></div>
          <button className="text-button" onClick={() => setTab('inventory')}>View all inventory <Icon name="arrow" size={15} /></button>
        </div>
        <div className="table-head"><span>Category</span><span>Stock value</span><span>Sell-through</span><span>Status</span><span>Suggested action</span></div>
        {attention.map((category, index) => (
          <div className="table-row" key={category.name}>
            <span><i className={`category-swatch s${index}`} />{category.name}</span>
            <span>${Math.round(category.stock_value).toLocaleString()}</span>
            <span>{formatPct(category.sell_through, 0)}</span>
            <span><b className={`status ${STATUS_CLASS[category.status] ?? 'status-1'}`}>{category.status}</b></span>
            <span>{category.suggested_action}</span>
          </div>
        ))}
      </section>
    </>
  )
}

const SCOPE_LABELS: Record<number, [string, string]> = { 1: ['scope-one', 'OPERATIONS'], 2: ['scope-two', 'ENERGY'], 3: ['scope-three', 'SUPPLY CHAIN'] }
const CURRENT_USER = 'Olivia Hayes' // until sign-in exists

function Budget() {
  const [revenueK, setRevenueK] = useState<number | null>(null)
  const [reduction, setReduction] = useState<number | null>(null)
  const [plan, setPlan] = useState<PlanResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [loadKey, setLoadKey] = useState(0)

  // Saved targets (or defaults) on first load.
  useEffect(() => {
    let cancelled = false
    setError(null)
    api<PlanResponse>('/plan')
      .then(p => {
        if (cancelled) return
        setPlan(p)
        setRevenueK(Math.round(p.analysis.revenue.target / 1000))
        setReduction(p.analysis.emissions.reduction_pct)
      })
      .catch((e: Error) => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [loadKey])

  // Live what-if preview as the sliders move (debounced; stale responses ignored).
  useEffect(() => {
    if (revenueK === null || reduction === null || revenueK <= 0) return
    let cancelled = false
    const timer = setTimeout(() => {
      api<PlanResponse>(`/plan?revenue_target=${revenueK * 1000}&emission_reduction_pct=${reduction}`)
        .then(p => { if (!cancelled) { setPlan(p); setError(null) } })
        .catch((e: Error) => { if (!cancelled) setError(e.message) })
    }, 250)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [revenueK, reduction])

  if (!plan || revenueK === null || reduction === null) return <LoadState error={error} onRetry={() => setLoadKey(k => k + 1)} />

  const a = plan.analysis
  const { revenue: rev, margins, procurement: proc, emissions: em } = a
  const isSaved = plan.saved !== null && plan.saved.revenue_target === revenueK * 1000 && plan.saved.emission_reduction_pct === reduction
  const baselineK = Math.round(rev.baseline / 1000)
  const sliderMin = Math.round((baselineK * 0.9) / 10) * 10
  const sliderMax = Math.round((baselineK * 1.5) / 10) * 10
  const peak = a.monthly.reduce((best, m) => (m.purchase_budget > best.purchase_budget ? m : best), a.monthly[0])
  const monthName = (iso: string) => formatDate(iso).split(' ')[0]
  const save = async () => {
    setSaving(true)
    try {
      setPlan(await api<PlanResponse>('/plan', { method: 'PUT', body: JSON.stringify({ revenue_target: revenueK * 1000, emission_reduction_pct: reduction, updated_by: CURRENT_USER }) }))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <section className="hero compact">
        <div><span className="eyebrow">FY {a.fiscal_year} PLANNING</span><h1>Set the year’s ambition.</h1><p>Set your revenue and emissions targets and we’ll align the procurement budget around them.</p></div>
        <button className="primary-button" onClick={save} disabled={isSaved || saving || revenueK <= 0}><Icon name="check" /> {isSaved ? 'Plan saved' : saving ? 'Saving…' : 'Save plan'}</button>
      </section>
      {error && <p className="inline-error" role="alert">{error}</p>}
      <div className="budget-grid">
        <section className="panel target-card">
          <span className="step">01</span><h2>Revenue target</h2><p>What gross revenue should the business deliver in FY {a.fiscal_year}?</p>
          <label htmlFor="revenue-target">Target gross revenue</label>
          <div className="money-input"><span>$</span><input id="revenue-target" type="number" min="1" value={revenueK} onChange={e => setRevenueK(toNumber(e.target.value, 0, baselineK * 10))} /><b>K</b></div>
          <input className="range" type="range" min={sliderMin} max={sliderMax} step="10" value={Math.min(sliderMax, Math.max(sliderMin, revenueK))} onChange={e => setRevenueK(toNumber(e.target.value, sliderMin, sliderMax))} aria-label="Target gross revenue slider" />
          <div className="range-labels"><span>Last 12 months · {formatMoney(rev.baseline)}</span><span>Ambitious · {formatMoney(sliderMax * 1000)}</span></div>
          {plan.saved && <p className="saved-note">Saved {formatMoney(plan.saved.revenue_target)} · −{plan.saved.emission_reduction_pct}% by {plan.saved.updated_by}</p>}
        </section>
        <section className="panel alignment-card">
          <span className="eyebrow">REQUIRED ALIGNMENT</span><h2>Your path to {formatMoney(rev.target)}</h2>
          <div className="alignment-row"><span>Required growth</span><strong>{formatSignedPct(rev.required_growth)}</strong><b className="neutral">trend {formatSignedPct(rev.trend_growth)}</b></div>
          <div className="alignment-row"><span>Gross profit</span><strong>{formatMoney(margins.target_gross_profit)}</strong><b className="neutral">{formatPct(margins.gross_margin)} GM</b></div>
          <div className="alignment-row"><span>Purchase budget</span><strong>{formatMoney(proc.purchase_budget)}</strong><b className={proc.change_vs_baseline !== null && proc.change_vs_baseline < 0 ? 'neutral' : ''}>{formatSignedPct(proc.change_vs_baseline)} vs LY</b></div>
          <div className="alignment-row"><span>Ending inventory</span><strong>{formatMoney(proc.target_ending_inventory)}</strong><b className="neutral">{proc.inventory_turns.toFixed(1)}× turns</b></div>
          <div className="insight"><Icon name="spark" /><p><strong>Planning insight</strong>
            {rev.gap_to_trend > 0
              ? `Your target is ${formatMoney(rev.gap_to_trend)} above the current trajectory (${formatMoney(rev.projected_at_trend)}), so growth must beat the trend.`
              : `Your target is within the current trajectory, with ${formatMoney(-rev.gap_to_trend)} of headroom.`}
            {` Purchases peak in ${monthName(peak.month)} at ${formatMoney(peak.purchase_budget)}.`}</p></div>
        </section>
      </div>
      <section className="panel emission-plan">
        <div className="emission-plan-heading">
          <div><span className="eyebrow">EMISSIONS CONTROL PLAN</span><h2>Align carbon reduction with the operating budget</h2><p>Baseline: {Math.round(em.baseline.total).toLocaleString()} tCO₂e over the last 12 months. The cheapest levers that close the gap are selected.</p></div>
          <div className="emission-target"><small>FY {a.fiscal_year} TARGET</small><strong>−{reduction}%</strong><span>{Math.round(em.target_total).toLocaleString()} tCO₂e target · {Math.round(em.projected.total).toLocaleString()} projected</span></div>
        </div>
        <div className="emission-control">
          <div className="emission-slider">
            <label htmlFor="emissions">Target emissions reduction</label>
            <input id="emissions" type="range" min="0" max="50" step="1" value={reduction} onChange={e => setReduction(toNumber(e.target.value, 0, 50))} />
            <div><span>0%</span><span>50% accelerated</span></div>
          </div>
          <div className="carbon-budget-stat"><small>REQUIRED INVESTMENT</small><strong>{formatMoney(em.investment)}</strong><span>per year for selected levers</span></div>
          <div className="carbon-budget-stat"><small>GAP TO CLOSE</small><strong>{Math.round(em.gap).toLocaleString()} tCO₂e</strong><span className={em.on_track ? '' : 'off-track'}>{em.gap === 0 ? 'Already on track' : em.on_track ? `Levers cover ${Math.round(em.selected_reduction).toLocaleString()} t` : `${Math.round(em.gap - em.selected_reduction).toLocaleString()} t short with all levers`}</span></div>
        </div>
        <div className="scope-actions">
          {em.levers.map(lever => {
            const [dot, area] = SCOPE_LABELS[lever.scope]
            return (
              <div key={lever.id} title={lever.detail}>
                <span className={`scope-dot ${dot}`} />
                <p><small>SCOPE {lever.scope} · {area}{lever.basis === 'assumption' && ' *'}</small><strong>{lever.title}</strong>
                  <span>−{Math.round(lever.reduction_t).toLocaleString()} tCO₂e · {lever.annual_cost < 0 ? `saves ${formatMoney(-lever.annual_cost)}` : `${formatMoney(lever.annual_cost)}/yr`}</span></p>
                <b className={lever.selected ? '' : 'recommended'}>{lever.selected ? 'Selected' : 'Optional'}</b>
              </div>
            )
          })}
        </div>
        <p className="source-note">Scope 3: {em.sources.scope3}. Scope 1–2: {em.sources.scope1_2}. * Uses a stated cost assumption.</p>
      </section>
    </>
  )
}

const CONFIDENCE_CLASS: Record<string, string> = { High: 'c0', Medium: 'c1', Low: 'c2' }
const SYSTEM_NAMES: Record<string, string> = { demo: 'Demo ERP', sap_s4hana: 'SAP S/4HANA', salesforce: 'Salesforce', oracle_netsuite: 'Oracle NetSuite' }
const shortDate = (iso: string) => formatDate(iso).replace(/, \d{4}$/, '')

function DecisionCard({ decision, rank, onChanged }: { decision: Decision; rank: number; onChanged: (d: Decision) => void }) {
  const d = decision.details
  const isPO = decision.kind === 'purchase_order'
  const options = d?.vendor_options ?? []
  const [editing, setEditing] = useState(false)
  const [quantity, setQuantity] = useState(decision.payload.quantity)
  const [vendorId, setVendorId] = useState(decision.payload.vendor_id)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(decision.last_error)
  const vendor = options.find(o => o.vendor_id === vendorId)
  const unitCost = vendor?.unit_cost ?? decision.payload.unit_cost ?? 0
  const amount = quantity * unitCost
  const kg = vendor ? quantity * vendor.kg_co2e_per_unit : d?.kg_co2e
  const kindLabel = isPO ? 'PROCUREMENT' : decision.kind === 'markdown' ? 'INVENTORY · MARKDOWN' : 'INVENTORY · WRITE-OFF'
  // Value shown on the card, scaled if the admin edits the quantity.
  const value = isPO ? amount
    : decision.kind === 'markdown' ? quantity * (decision.payload.clearance_price ?? 0)
    : d ? (d.amount * quantity) / decision.payload.quantity : decision.impact_usd
  const valueLabel = isPO ? 'ORDER VALUE' : decision.kind === 'markdown' ? 'CLEARANCE REVENUE' : 'WRITE-OFF AT COST'
  const meta = !d ? [] : isPO
    ? [d.order_by && `Order by ${shortDate(d.order_by)}`, d.stockout_date && `stockout ~${shortDate(d.stockout_date)}`, `${d.on_hand.toLocaleString()} on hand`, `${(d.on_order ?? 0).toLocaleString()} on order`]
    : [`${(d.aged_units ?? 0).toLocaleString()} aged of ${d.on_hand.toLocaleString()} on hand`, d.oldest_days && `oldest ${d.oldest_days} days`,
       decision.kind === 'markdown' && d.unit_price && `$${decision.payload.clearance_price?.toFixed(2)} (was $${d.unit_price.toFixed(2)})`]
  const edited = quantity !== decision.payload.quantity || vendorId !== decision.payload.vendor_id

  const act = async (action: 'approve' | 'reject') => {
    setBusy(true)
    setError(null)
    try {
      const body = action === 'approve'
        ? { approved_by: CURRENT_USER, ...(edited ? { quantity, vendor_id: isPO ? vendorId : undefined } : {}) }
        : { rejected_by: CURRENT_USER }
      onChanged(await api<Decision>(`/decisions/${decision.id}/${action}`, { method: 'POST', body: JSON.stringify(body) }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <article className="panel recommendation">
      <div className="rec-rank">{String(rank).padStart(2, '0')}</div>
      <div className="rec-copy">
        <div>
          <span className="eyebrow">{kindLabel}{decision.source === 'manual' && ' · MANUAL'}</span>
          {d && <span className={`confidence ${CONFIDENCE_CLASS[d.confidence]}`}>{d.confidence} confidence</span>}
          {isPO && kg !== undefined && <span className="carbon-credit">{Math.round(kg).toLocaleString()} kg CO₂e</span>}
        </div>
        <h2>{decision.title}</h2>
        <p>{decision.reason}</p>
        {meta.length > 0 && <p className="rec-meta">{meta.filter(Boolean).join(' · ')}</p>}
        {editing && (
          <div className="rec-edit">
            <label>Quantity<input type="number" min="1" value={quantity} onChange={e => setQuantity(toNumber(e.target.value, 1))} /></label>
            {isPO && options.length > 0 && (
              <label>Vendor<select value={vendorId} onChange={e => setVendorId(Number(e.target.value))}>
                {options.map((o, i) => <option key={o.vendor_id} value={o.vendor_id}>{o.name} · ${o.unit_cost.toFixed(2)} · {o.lead_weeks} wk · {o.kg_co2e_per_unit.toFixed(2)} kg/unit{i === 0 ? ' (recommended)' : ''}{o.arrives_before_stockout ? '' : ' · late'}</option>)}
              </select></label>
            )}
          </div>
        )}
        {error && <p className="inline-error" role="alert">{error}</p>}
      </div>
      <div className="rec-impact">
        <small>{valueLabel}</small><strong>{formatMoney(value)}</strong>
        {decision.impact_usd > 0 && <small className="rec-protects">{isPO ? `protects ${formatMoney(decision.impact_usd)} margin` : decision.kind === 'markdown' ? `+${formatMoney(decision.impact_usd)} cash vs holding` : `saves ${formatMoney(decision.impact_usd)} storage`}</small>}
        <div className="rec-actions">
          <button className="edit-button" disabled={busy} onClick={() => setEditing(!editing)}>{editing ? 'Done' : 'Edit'}</button>
          <button className="edit-button" disabled={busy} onClick={() => act('reject')}>Reject</button>
          <button disabled={busy || quantity <= 0} onClick={() => act('approve')}>{busy ? 'Posting…' : isPO ? 'Place order' : decision.kind === 'markdown' ? 'Approve markdown' : 'Approve write-off'} <Icon name="arrow" size={16} /></button>
        </div>
      </div>
    </article>
  )
}

function RaiseRequestModal({ run, onClose, onCreated }: { run: RecommendationRun | null; onClose: () => void; onCreated: () => void }) {
  const { data: skus } = useApi<SkuInfo[]>('/inventory/skus')
  const { data: vendors } = useApi<VendorInfo[]>('/vendors')
  const [skuId, setSkuId] = useState<number | null>(null)
  const [vendorId, setVendorId] = useState<number | null>(null)
  const [quantity, setQuantity] = useState(100)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [placed, setPlaced] = useState<Decision | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const sku = skus?.find(s => s.id === (skuId ?? skus[0]?.id))
  const plan = run?.plans.find(p => p.sku_id === sku?.id)
  const vendor = vendors?.find(v => v.id === (vendorId ?? vendors.find(x => x.name === (plan?.vendor?.name ?? sku?.vendor))?.id))
  const unitCost = sku && vendor ? Math.round(sku.unit_cost * vendor.price_index * 100) / 100 : 0
  const submit = async () => {
    if (!sku || !vendor) return
    setBusy(true)
    setError(null)
    try {
      setPlaced(await api<Decision>('/decisions', {
        method: 'POST',
        body: JSON.stringify({
          kind: 'purchase_order',
          title: `Order ${quantity.toLocaleString()} × ${sku.name} from ${vendor.name}`,
          reason: `Raised manually by ${CURRENT_USER}.`,
          payload: { sku_id: sku.id, vendor_id: vendor.id, quantity, unit_cost: unitCost },
        }),
      }))
      onCreated()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section className="request-modal" role="dialog" aria-modal="true" aria-labelledby="request-title" onMouseDown={e => e.stopPropagation()}>
        {placed ? (
          <div className="request-success"><span><Icon name="check" size={26} /></span><h2 id="request-title">Purchase request raised</h2><p>{placed.title} ({formatMoney(quantity * unitCost)}) is waiting for approval in Decisions.</p><button className="primary-button" onClick={onClose}>Done</button></div>
        ) : (
          <>
            <div className="request-heading"><div><span className="eyebrow">NEW PURCHASE REQUEST</span><h2 id="request-title">Raise a custom PR</h2></div><button onClick={onClose} aria-label="Close">×</button></div>
            {!skus || !vendors ? <p>Loading…</p> : (
              <>
                <label className="form-field">SKU<select value={sku?.id} onChange={e => { setSkuId(Number(e.target.value)); setVendorId(null) }}>
                  {skus.map(s => <option key={s.id} value={s.id}>{s.category} · {s.name} ({s.code})</option>)}
                </select></label>
                <label className="form-field">Quantity<span className="amount-field"><input type="number" min="1" value={quantity} onChange={e => setQuantity(toNumber(e.target.value, 1))} /><span>units</span></span></label>
                <label className="form-field">Supplier<select value={vendor?.id} onChange={e => setVendorId(Number(e.target.value))}>
                  {vendors.map(v => <option key={v.id} value={v.id}>{v.name} · {Math.round(v.on_time_rate * 100)}% on time · {v.kg_co2e_per_usd.toFixed(3)} kg CO₂e/$</option>)}
                </select></label>
                <div className="pr-insight"><Icon name="spark" /><div>
                  <strong>Pro-sight recommendation</strong>
                  <p>{!plan ? 'No forecast for this SKU.'
                    : plan.quantity > 0 ? <>The demand model suggests <b>{plan.quantity.toLocaleString()} units from {plan.vendor?.name}</b>{plan.order_by ? ` by ${shortDate(plan.order_by)}` : ''}. {plan.reason}</>
                    : <>{plan.reason}</>}</p>
                  {vendor && <p className="credit-note">This order: <b>{formatMoney(quantity * unitCost)}</b> at ${unitCost.toFixed(2)}/unit, about <b>{Math.round(quantity * unitCost * vendor.kg_co2e_per_usd).toLocaleString()} kg CO₂e</b>.</p>}
                </div></div>
                {error && <p className="inline-error" role="alert">{error}</p>}
                <div className="modal-actions"><button className="secondary-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || quantity <= 0} onClick={submit}>Raise PR <Icon name="arrow" /></button></div>
              </>
            )}
          </>
        )}
      </section>
    </div>
  )
}

type ModelRefresh = { orders: { created: number; run: RecommendationRun }; aging: { created: number; run: WriteOffRun } }

function Recommendations({ pending, reloadPending, refreshModels }: {
  pending: Decision[] | null
  reloadPending: () => Promise<void>
  refreshModels: () => Promise<ModelRefresh>
}) {
  const approved = useApi<Decision[]>('/decisions?status=approved')
  const [run, setRun] = useState<RecommendationRun | null>(null)
  const [agingRun, setAgingRun] = useState<WriteOffRun | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [requestOpen, setRequestOpen] = useState(false)
  const [raising, setRaising] = useState<number | null>(null)
  const reloadApproved = approved.reload

  // Read-only view of the latest model output (decisions are raised by refreshModels).
  const loadRuns = useCallback(async () => {
    setRunError(null)
    try {
      const [orders, aging] = await Promise.all([api<RecommendationRun>('/recommendations'), api<WriteOffRun>('/writeoffs')])
      setRun(orders)
      setAgingRun(aging)
    } catch (e) {
      setRunError((e as Error).message)
    }
  }, [])

  const refresh = async () => {
    setRefreshing(true)
    setRunError(null)
    try {
      const { orders, aging } = await refreshModels()
      setRun(orders.run)
      setAgingRun(aging.run)
      setNotice(`Model re-run: ${orders.created} order${orders.created === 1 ? '' : 's'} and ${aging.created} aging-stock action${aging.created === 1 ? '' : 's'} recommended.`)
    } catch (e) {
      setRunError((e as Error).message)
    } finally {
      setRefreshing(false)
    }
  }

  useEffect(() => { loadRuns() }, [loadRuns])

  const onChanged = (updated: Decision) => {
    if (updated.status === 'approved' && updated.purchase_order) {
      const po = updated.purchase_order
      setNotice(`${po.number} sent to ${SYSTEM_NAMES[po.external_system ?? ''] ?? po.external_system}; ${updated.journal_entry?.number} posted to the book of record.`)
    } else if (updated.status === 'approved' && updated.kind === 'markdown') {
      setNotice(updated.journal_entry
        ? `Markdown approved; ${updated.journal_entry.number} wrote the stock down to its clearance value in the book of record.`
        : 'Markdown approved; the clearance price is above cost, so no write-down was needed.')
    } else if (updated.status === 'approved') {
      setNotice(`Write-off approved; ${updated.journal_entry?.number} posted to the book of record.`)
    } else {
      setNotice(`Rejected: ${updated.title}`)
    }
    reloadPending()
    reloadApproved()
    loadRuns()
  }

  const raiseEarly = async (skuId: number) => {
    setRaising(skuId)
    try {
      await api<Decision>(`/recommendations/${skuId}/raise`, { method: 'POST' })
      reloadPending()
    } catch (e) {
      setNotice((e as Error).message)
    } finally {
      setRaising(null)
    }
  }

  const items = [...(pending ?? [])].sort((a, b) => b.impact_usd - a.impact_usd)
  const totalImpact = items.reduce((sum, d) => sum + d.impact_usd, 0)
  const pendingSkus = new Set(items.map(d => d.payload.sku_id))
  const upcoming = run?.plans.filter(p => p.status === 'upcoming') ?? []
  const fc = run?.forecast
  const gap = run?.plan_alignment.gap_pct

  return (
    <>
      <section className="hero compact">
        <div><span className="eyebrow">AGENTIC INSIGHTS</span><h1>Actions, not just answers.</h1><p>Purchase orders, markdowns and write-offs from the demand forecast. Approved decisions post to your book of record.</p></div>
        <div className="hero-actions">
          <div className="impact-pill"><Icon name="spark" /> {formatMoney(totalImpact)} potential impact</div>
          <button className="secondary-button" disabled={refreshing} onClick={refresh}>{refreshing ? 'Running model…' : 'Re-run model'}</button>
          <button className="primary-button" onClick={() => setRequestOpen(true)}>+ Raise purchase request</button>
        </div>
      </section>
      {notice && <p className="decision-notice" role="status"><Icon name="check" size={14} /> {notice}</p>}
      {runError && <LoadState error={runError} onRetry={loadRuns} />}
      {!pending ? <LoadState error={null} onRetry={reloadPending} /> : (
        <div className="recommendation-list">
          {items.length === 0 && <section className="panel empty-state">No decisions waiting. The model will raise orders as stock approaches its reorder point.</section>}
          {items.map((d, i) => <DecisionCard key={d.id} decision={d} rank={i + 1} onChanged={onChanged} />)}
        </div>
      )}
      {upcoming.length > 0 && (
        <section className="panel upcoming-panel">
          <div className="panel-heading"><div><span className="eyebrow">COMING UP</span><h2>Reorders due in the next 4 weeks</h2></div></div>
          <div className="upcoming-head"><span>SKU</span><span>Order by</span><span>Stockout</span><span>If ordered today</span><span /></div>
          {upcoming.map(p => (
            <div className="upcoming-row" key={p.sku_id}>
              <span><strong>{p.sku_name}</strong><small>{p.category} · {p.on_hand.toLocaleString()} on hand</small></span>
              <span>{p.order_by ? shortDate(p.order_by) : '—'}</span>
              <span>{p.stockout_date ? shortDate(p.stockout_date) : '—'}</span>
              <span>{p.quantity > 0 ? `${p.quantity.toLocaleString()} from ${p.vendor?.name} · ${formatMoney(p.amount)}` : 'Covered by open POs'}</span>
              <span>{pendingSkus.has(p.sku_id) ? <em>Pending</em> : p.quantity > 0 && <button className="text-button" disabled={raising === p.sku_id} onClick={() => raiseEarly(p.sku_id)}>Raise now <Icon name="arrow" size={13} /></button>}</span>
            </div>
          ))}
        </section>
      )}
      {agingRun && agingRun.plans.length > 0 && (
        <section className="panel upcoming-panel">
          <div className="panel-heading"><div><span className="eyebrow">AGING STOCK</span><h2>Hold, mark down, or write off</h2></div></div>
          <p className="aging-summary">
            {formatMoney(agingRun.totals.aged_value)} of stock is over {agingRun.assumptions.aging_days} days old.
            {' '}Recommended write-offs: {formatMoney(agingRun.totals.write_off_cost)} at cost. Markdowns bring in {formatMoney(agingRun.totals.markdown_revenue)} of clearance revenue,
            {' '}{formatMoney(agingRun.totals.cash_gain_vs_hold)} more cash than holding.
          </p>
          <div className="aging-head"><span>SKU</span><span>Aged</span><span>Oldest</span><span>Hold: cash · write-off</span><span>Markdown: cash · write-off</span><span>Plan</span></div>
          {agingRun.plans.map(p => (
            <div className="aging-row" key={p.sku_id} title={p.reason}>
              <span><strong>{p.sku_name}</strong><small>{p.category}</small></span>
              <span>{p.aged_units.toLocaleString()} · {formatMoney(p.aged_value)}</span>
              <span>{p.oldest_days} days</span>
              <span className={p.recommendation !== 'markdown' ? 'best' : ''}>{formatMoney(p.hold.cash)} · {p.hold.write_off_units.toLocaleString()} units</span>
              <span className={p.recommendation === 'markdown' ? 'best' : ''}>{formatMoney(p.markdown.cash)} · {p.markdown.write_off_units.toLocaleString()} units</span>
              <span><b className={`aging-tag ${p.recommendation}`}>{p.recommendation === 'markdown' ? 'Mark down' : p.recommendation === 'write_off' ? (p.hold.write_off_units === p.aged_units ? 'Write off all' : 'Write off excess') : 'Hold'}</b></span>
            </div>
          ))}
          <p className="source-note">
            Cash over {agingRun.assumptions.sell_window_weeks} weeks from the demand forecast. Assumptions: stock older than {agingRun.assumptions.max_age_days} days can't be sold;
            {' '}markdown {formatPct(agingRun.assumptions.clearance_discount, 0)} off with price elasticity {agingRun.assumptions.price_elasticity};
            {' '}holding cost {formatPct(agingRun.assumptions.holding_cost_rate_annual, 0)} of cost a year; selling costs {formatPct(agingRun.assumptions.selling_cost_rate, 0)}. Hover a row for the reasoning.
          </p>
        </section>
      )}
      {(approved.data?.length ?? 0) > 0 && (
        <section className="panel upcoming-panel">
          <div className="panel-heading"><div><span className="eyebrow">POSTED</span><h2>Recently approved</h2></div></div>
          {approved.data!.slice(0, 6).map(d => (
            <div className="posted-row" key={d.id}>
              <span><strong>{d.title}</strong><small>Approved by {d.decided_by}</small></span>
              <span>{d.purchase_order && <>{d.purchase_order.number} · {SYSTEM_NAMES[d.purchase_order.external_system ?? ''] ?? d.purchase_order.external_system}</>}</span>
              <span>{d.journal_entry && <>{d.journal_entry.number} · {d.journal_entry.ledger === 'memo' ? 'commitment' : 'GL'} · {SYSTEM_NAMES[d.journal_entry.posted_to] ?? d.journal_entry.posted_to}</>}</span>
            </div>
          ))}
        </section>
      )}
      {run && fc && (
        <p className="source-note">
          {fc.model}: {formatPct(fc.backtest_wape)} weekly error vs {formatPct(fc.naive_wape)} for a naive forecast over a {fc.backtest_weeks}-week backtest.
          {' '}Vendor scoring weights: cost {formatPct(run.weights.cost, 0)}, reliability {formatPct(run.weights.reliability, 0)}, lead time {formatPct(run.weights.lead_time, 0)}, emissions {formatPct(run.weights.emissions, 0)}
          {run.emission_pressure > 0 ? ` (raised because the FY plan is ${formatPct(run.emission_pressure, 0)} off its emissions target)` : ''}.
          {gap !== null && gap !== undefined && ` Forecast demand is ${formatPct(Math.abs(gap), 0)} ${gap < 0 ? 'below' : 'above'} the revenue plan for the ${run.plan_alignment.weeks_compared} overlapping weeks.`}
        </p>
      )}
      {requestOpen && <RaiseRequestModal run={run} onClose={() => setRequestOpen(false)} onCreated={() => reloadPending()} />}
    </>
  )
}

function LoadState({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  return (
    <section className="panel load-state">
      {error
        ? <><Icon name="alert" /><div><strong>{error}</strong>{error.includes('reach the Pro-sight API') && (['localhost', '127.0.0.1'].includes(window.location.hostname)
          ? <p>Start it with <code>cd backend && uv run uvicorn prosight.main:app --reload</code></p>
          : <p>This site only hosts the frontend. Deploy the backend (see <code>backend/README.md</code>, “Deploying”) and rebuild the site with <code>VITE_API_URL</code> set to its address.</p>)}</div><button className="secondary-button" onClick={onRetry}>Retry</button></>
        : <p>Loading…</p>}
    </section>
  )
}

const SUMMARY_FIELDS: [keyof ImportReport['summary'], string, (v: never) => string][] = [
  ['skus', 'SKUs', (v: number) => v.toLocaleString()],
  ['vendors', 'Vendors', (v: number) => v.toLocaleString()],
  ['inventory_value', 'Inventory value', (v: number) => formatMoney(v)],
  ['sales_weeks', 'Weeks of sales', (v: number) => v.toLocaleString()],
  ['revenue', 'Revenue in file', (v: number) => formatMoney(v)],
  ['purchase_orders', 'Purchase orders', (v: number) => v.toLocaleString()],
  ['expense_months', 'Expense months', (v: number) => v.toLocaleString()],
  ['as_of', 'Data as of', (v: string | null) => (v ? formatDate(v) : '—')],
]

function ImportPanel() {
  const { data: dataset } = useApi<DatasetInfo>('/dataset')
  const [files, setFiles] = useState<File[]>([])
  const [report, setReport] = useState<ImportReport | null>(null)
  const [busy, setBusy] = useState<'check' | 'import' | 'reset' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  const send = async (path: string) => {
    const form = new FormData()
    files.forEach(f => form.append('files', f))
    return api<ImportReport>(path, { method: 'POST', body: form })
  }
  const run = async (kind: 'check' | 'import' | 'reset') => {
    if (kind === 'import' && !window.confirm('Importing replaces all current inventory, sales, financials, purchase orders, decisions and ledger entries. Continue?')) return
    if (kind === 'reset' && !window.confirm('Restore the demo data? Your imported data, decisions and ledger entries will be removed.')) return
    setBusy(kind)
    setError(null)
    try {
      if (kind === 'check') setReport(await send('/import/validate'))
      else {
        if (kind === 'import') setReport(await send('/import'))
        else await api<DatasetInfo>('/import/reset-demo', { method: 'POST' })
        setDone(kind === 'import' ? 'Imported. Reloading with your data…' : 'Demo data restored. Reloading…')
        setTimeout(() => window.location.reload(), 900)
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="panel import-panel">
      <div className="connector-heading">
        <div><span className="eyebrow">BRING YOUR OWN DATA</span><h2>Upload CSV or Excel</h2><p>Load your own inventory, sales and financials from a spreadsheet instead of an ERP connection.</p></div>
        {dataset && <span className={`dataset-badge ${dataset.source}`}>{dataset.source === 'demo' ? 'Using demo data' : `Using your data · as of ${formatDate(dataset.as_of)}`}</span>}
      </div>
      <div className="import-steps">
        <div>
          <b>1</b><strong>Download the template</strong>
          <p>One sheet per table, with an Instructions sheet explaining every column.</p>
          <div className="import-links">
            <a className="secondary-button" href={apiUrl('/import/template.xlsx')} download>Excel template</a>
            <a className="text-button" href={apiUrl('/import/template.xlsx?sample=true')} download>Excel with current data</a>
            <a className="text-button" href={apiUrl('/import/template.zip')} download>CSV templates (.zip)</a>
          </div>
        </div>
        <div>
          <b>2</b><strong>Choose your file</strong>
          <p>One .xlsx workbook, or several .csv files named after the sheets (sales.csv, skus.csv…).</p>
          <label className="file-pick">
            <input type="file" multiple accept=".xlsx,.csv" onChange={e => { setFiles(Array.from(e.target.files ?? [])); setReport(null); setDone(null) }} />
            <span>{files.length ? files.map(f => f.name).join(', ') : 'Choose .xlsx or .csv files'}</span>
          </label>
          <button className="secondary-button" disabled={!files.length || busy !== null} onClick={() => run('check')}>{busy === 'check' ? 'Checking…' : 'Check file'}</button>
        </div>
        <div>
          <b>3</b><strong>Import</strong>
          <p>Only possible once the check finds no errors. Replaces the current data.</p>
          <button className="primary-button" disabled={!report?.ok || report.imported || busy !== null} onClick={() => run('import')}>{busy === 'import' ? 'Importing…' : 'Import data'}</button>
        </div>
      </div>
      {error && <p className="inline-error" role="alert">{error}</p>}
      {done && <p className="decision-notice" role="status"><Icon name="check" size={14} /> {done}</p>}
      {report && (
        <div className="import-report">
          <p className={report.ok ? 'report-ok' : 'report-bad'}>
            {report.ok ? (report.imported ? 'Imported successfully.' : 'No errors found. Ready to import.') : `${report.error_count.toLocaleString()} problem${report.error_count === 1 ? '' : 's'} to fix before importing:`}
          </p>
          <div className="import-summary">
            {SUMMARY_FIELDS.map(([key, label, fmt]) => <div key={key}><small>{label}</small><strong>{(fmt as (v: unknown) => string)(report.summary[key])}</strong></div>)}
          </div>
          {report.warnings.map(w => <p className="import-warning" key={w}><Icon name="alert" size={13} /> {w}</p>)}
          {report.errors.length > 0 && (
            <div className="import-errors">
              <div className="import-error-head"><span>Sheet</span><span>Row</span><span>Column</span><span>Problem</span></div>
              {report.errors.map((e, i) => <div className="import-error-row" key={i}><span>{e.sheet}</span><span>{e.row ?? '—'}</span><span>{e.column ?? '—'}</span><span>{e.message}</span></div>)}
              {report.error_count > report.errors.length && <p className="import-warning">…and {(report.error_count - report.errors.length).toLocaleString()} more.</p>}
            </div>
          )}
        </div>
      )}
      {dataset?.source === 'import' && <button className="text-button restore-demo" disabled={busy !== null} onClick={() => run('reset')}>Restore demo data</button>}
    </section>
  )
}

function Connectors() {
  const { data: connectors, error, reload, setData } = useApi<ConnectorInfo[]>('/connectors')
  const [busy, setBusy] = useState<string | null>(null)
  const [messages, setMessages] = useState<Record<string, string>>({})
  const toggle = async (connector: ConnectorInfo) => {
    setBusy(connector.id)
    setMessages(m => ({ ...m, [connector.id]: '' }))
    try {
      setData(await api<ConnectorInfo[]>(`/connectors/${connector.id}/${connector.connected ? 'disconnect' : 'connect'}`, { method: 'POST' }))
    } catch (e) {
      setMessages(m => ({ ...m, [connector.id]: (e as Error).message }))
    } finally {
      setBusy(null)
    }
  }
  return (
    <section className="panel connector-panel">
      <div className="connector-heading"><div><span className="eyebrow">PLUG-AND-PLAY CONNECTORS</span><h2>Connect your ERP or CRM</h2><p>Pull financials and SKU data in, and post approved POs and write-offs back to your book of record.</p></div><span className="sync-status"><i /> Live sync ready</span></div>
      {!connectors ? <LoadState error={error} onRetry={reload} /> : (
        <div className="connector-grid">
          {connectors.map((connector, i) => (
            <article className={`connector-card ${connector.connected ? 'connected' : ''}`} key={connector.id}>
              <div className={`connector-logo logo-${i % 4}`}>{connector.name.split(' ').map(word => word[0]).join('').slice(0, 2)}</div>
              <div>
                <span>{connector.kind.toUpperCase()}{connector.is_abor && ' · BOOK OF RECORD'}{connector.is_po_target && ' · PO TARGET'}</span>
                <strong>{connector.name}</strong>
                <p>{connector.description}</p>
                {messages[connector.id] && <p className="connector-error" role="alert">{messages[connector.id]}</p>}
              </div>
              <button disabled={busy === connector.id} onClick={() => toggle(connector)}>{connector.connected ? <><Icon name="check" size={14} /> Connected</> : 'Connect'}</button>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}

function FinancialsPage({ period }: { period: Period }) {
  const { data, error, reload } = useApi<Financials>(`/financials?${periodQuery(period)}`)
  if (!data) return <LoadState error={error} onRetry={reload} />
  const c = data.current
  return (
    <>
      <section className="metrics-grid">
        <Metric label="Revenue" value={formatMoney(c.revenue)} detail={`${formatSignedPct(data.revenue_growth)} YoY`} />
        <Metric label="COGS" value={formatMoney(c.cogs)} detail={`${formatPct(c.revenue ? c.cogs / c.revenue : 0)} of revenue`} tone="blue" />
        <Metric label="Gross profit" value={formatMoney(c.gross_profit)} detail={`${formatPct(c.gross_margin)} margin`} />
        <Metric label="Net income" value={formatMoney(c.net_income)} detail={`${formatPct(c.net_margin)} margin`} tone="blue" />
      </section>
      {error && <p className="inline-error" role="alert">{error}</p>}
      <section className="panel statement-panel">
        <div className="panel-heading"><div><span className="eyebrow">INCOME STATEMENT</span><h2>{data.label === 'Custom range' ? 'Custom range' : `FY ${c.end.slice(0, 4)} ${data.label}`}</h2></div><span className="period-note">{formatDate(c.start)} – {formatDate(c.end)}</span></div>
        {([
          ['Revenue', c.revenue], ['Cost of goods sold', -c.cogs], ['Gross profit', c.gross_profit],
          ['Operating expenses', -c.operating_expenses], ['Taxes & interest', -c.taxes_interest], ['Net income', c.net_income],
        ] as const).map(([label, value], i, all) => (
          <div className={`statement-row ${i === all.length - 1 ? 'total' : ''}`} key={label}>
            <span>{label}</span>
            <strong>{value < 0 ? `($${Math.round(-value).toLocaleString()})` : `$${Math.round(value).toLocaleString()}`}</strong>
          </div>
        ))}
      </section>
    </>
  )
}

function InventoryPage() {
  const { data, error, reload } = useApi<Inventory>('/inventory')
  return (
    <>
      {!data ? <LoadState error={error} onRetry={reload} /> : (
        <section className="metrics-grid">
          <Metric label="Inventory value" value={formatMoney(data.total_value)} detail={`Across ${data.sku_count.toLocaleString()} SKUs`} />
          <Metric label="Inventory turns" value={`${data.turns.toFixed(1)}×`} detail="Trailing 52 weeks" tone="blue" />
          <Metric label="Aging stock" value={formatMoney(data.aged_value)} detail={`${formatPct(data.aged_share)} of inventory`} />
          <Metric label="Stockout risk" value={`${data.stockout_risk_skus} SKUs`} detail="Next 30 days" tone="blue" />
        </section>
      )}
      {data && <InventoryTables inventory={data} />}
      <Connectors />
      <ImportPanel />
    </>
  )
}

type InventoryView = 'items' | 'vendors' | 'categories'

function InventoryTables({ inventory }: { inventory: Inventory }) {
  const [view, setView] = useState<InventoryView>('items')
  const [vendorFilter, setVendorFilter] = useState('')
  const [search, setSearch] = useState('')
  const [highlight, setHighlight] = useState<string | null>(null)
  const items = useApi<SkuInfo[]>('/inventory/skus')
  const vendors = useApi<VendorInfo[]>('/vendors')

  const showVendor = (name: string) => { setHighlight(name); setView('vendors') }
  const showItemsFor = (name: string) => { setVendorFilter(name); setSearch(''); setView('items') }
  const q = search.trim().toLowerCase()
  const shownItems = (items.data ?? []).filter(s =>
    (!vendorFilter || s.vendor === vendorFilter) && (!q || `${s.name} ${s.code} ${s.category}`.toLowerCase().includes(q)))

  const tabs: [InventoryView, string][] = [
    ['items', `Items${items.data ? ` (${items.data.length})` : ''}`],
    ['vendors', `Vendors${vendors.data ? ` (${vendors.data.length})` : ''}`],
    ['categories', 'By category'],
  ]
  return (
    <section className="panel inventory-tables">
      <div className="panel-heading">
        <div><span className="eyebrow">STOCK POSITION</span><h2>{view === 'items' ? 'Items and their vendors' : view === 'vendors' ? 'All vendors' : 'Capital by category'}</h2></div>
        <span className="period-note">As of {formatDate(inventory.as_of)}</span>
      </div>
      <div className="view-tabs" role="tablist">
        {tabs.map(([key, label]) => <button key={key} role="tab" aria-selected={view === key} className={view === key ? 'active' : ''} onClick={() => setView(key)}>{label}</button>)}
      </div>

      {view === 'items' && (!items.data ? <LoadState error={items.error} onRetry={items.reload} /> : (
        <>
          <div className="table-tools">
            <input type="search" placeholder="Search items, codes or categories" value={search} onChange={e => setSearch(e.target.value)} aria-label="Search items" />
            <select value={vendorFilter} onChange={e => setVendorFilter(e.target.value)} aria-label="Filter by vendor">
              <option value="">All vendors</option>
              {[...new Set(items.data.map(s => s.vendor))].sort().map(v => <option key={v}>{v}</option>)}
            </select>
            <span>{shownItems.length} of {items.data.length} items</span>
          </div>
          <div className="data-table items-table">
            <div className="data-head"><span>Item</span><span>Category</span><span>Vendor</span><span>On hand</span><span>Stock value</span><span>Weeks of cover</span><span>Status</span></div>
            {shownItems.map(s => (
              <div className="data-row" key={s.id}>
                <span><strong>{s.name}</strong><small>{s.code}</small></span>
                <span>{s.category}</span>
                <span>
                  <button className="link-button" onClick={() => showVendor(s.vendor)}>{s.vendor}</button>
                  {s.alternative_vendors.length > 0 && <small title={`Also supplied by: ${s.alternative_vendors.join(', ')}`}>+{s.alternative_vendors.length} alternative{s.alternative_vendors.length === 1 ? '' : 's'}: {s.alternative_vendors.join(', ')}</small>}
                </span>
                <span>{s.on_hand.toLocaleString()}</span>
                <span>${Math.round(s.stock_value).toLocaleString()}</span>
                <span>{s.weeks_of_cover === null ? '—' : s.weeks_of_cover.toFixed(1)}</span>
                <span><b className={`status ${STATUS_CLASS[s.status] ?? 'status-1'}`}>{s.status}</b></span>
              </div>
            ))}
            {shownItems.length === 0 && <p className="empty-state">No items match.</p>}
          </div>
        </>
      ))}

      {view === 'vendors' && (!vendors.data ? <LoadState error={vendors.error} onRetry={vendors.reload} /> : (
        <div className="data-table vendors-table">
          <div className="data-head"><span>Vendor</span><span>Supplies</span><span>Items</span><span>Lead time</span><span>On time</span><span>Price vs benchmark</span><span>CO₂e per $</span><span>Open POs</span><span>Spend, last 12 mo</span></div>
          {vendors.data.map(v => (
            <div className={`data-row ${highlight === v.name ? 'highlight' : ''}`} key={v.id}>
              <span><strong>{v.name}</strong><small title={`NAICS ${v.naics_code}`}>{v.industry}</small></span>
              <span>{v.categories.join(', ') || '—'}</span>
              <span>{v.preferred_sku_count > 0 ? <button className="link-button" onClick={() => showItemsFor(v.name)}>{v.preferred_sku_count} item{v.preferred_sku_count === 1 ? '' : 's'}</button> : '0'}</span>
              <span>{v.lead_time_days} days</span>
              <span>{formatPct(v.on_time_rate, 0)}</span>
              <span>{v.price_index === 1 ? 'At benchmark' : `${v.price_index > 1 ? '+' : '−'}${Math.round(Math.abs(v.price_index - 1) * 100)}%`}</span>
              <span>{v.kg_co2e_per_usd.toFixed(3)} kg</span>
              <span>{v.open_po_count ? `${v.open_po_count} · ${formatMoney(v.open_po_value)}` : '—'}</span>
              <span>{formatMoney(v.spend_12m)}</span>
            </div>
          ))}
        </div>
      ))}

      {view === 'categories' && (
        <div className="statement-panel">
          {inventory.categories.map(category => (
            <div className="statement-row" key={category.name}>
              <span>{category.name} · <em className="category-status">{category.status}</em></span>
              <strong>${Math.round(category.stock_value).toLocaleString()}</strong>
            </div>
          ))}
          <div className="statement-row total"><span>Total</span><strong>${Math.round(inventory.total_value).toLocaleString()}</strong></div>
        </div>
      )}
    </section>
  )
}

function CarbonPanel({ period }: { period: Period }) {
  const { data, error, reload } = useApi<Emissions>(`/emissions?${periodQuery(period)}`)
  if (!data) return <LoadState error={error} onRetry={reload} />
  const c = data.current
  const share = (v: number) => (c.total ? formatPct(v / c.total) : '0%')
  const change = data.total_change
  return (
    <section className="panel carbon-panel">
      <div className="panel-heading"><div><span className="eyebrow">CARBON ACCOUNTING</span><h2>Total emissions</h2></div><div className="carbon-total"><strong>{Math.round(c.total).toLocaleString()}</strong><span>tCO₂e · {formatDate(c.start)} – {formatDate(c.end)}</span></div></div>
      <div className="scope-grid">
        <div><span className="scope-dot scope-one" /><small>SCOPE 1</small><strong>{Math.round(c.scope1).toLocaleString()} tCO₂e</strong><p>Fuel: fleet and heating</p><b>{share(c.scope1)}</b></div>
        <div><span className="scope-dot scope-two" /><small>SCOPE 2</small><strong>{Math.round(c.scope2).toLocaleString()} tCO₂e</strong><p>Purchased electricity</p><b>{share(c.scope2)}</b></div>
        <div><span className="scope-dot scope-three" /><small>SCOPE 3</small><strong>{Math.round(c.scope3).toLocaleString()} tCO₂e</strong><p>Purchased goods (PO spend)</p><b>{share(c.scope3)}</b></div>
      </div>
      <div className="carbon-footer">
        <span><Icon name="trend" size={16} /> {change === null ? 'No prior-year data' : <>Total emissions are <strong>{formatPct(Math.abs(change))} {change <= 0 ? 'lower' : 'higher'}</strong> year over year</>}</span>
        <b>{c.kg_per_revenue_usd === null ? '' : `${(c.kg_per_revenue_usd * 1000).toFixed(0)} kg CO₂e per $1K revenue`}</b>
      </div>
    </section>
  )
}

function DetailPage({ type, period }: { type: 'inventory' | 'financials'; period: Period }) {
  const financial = type === 'financials'
  return (
    <>
      <section className="hero compact"><div><span className="eyebrow">{financial ? 'FINANCIAL STATEMENTS' : 'CONNECTED INVENTORY'}</span><h1>{financial ? 'Financial performance.' : 'Inventory at a glance.'}</h1><p>{financial ? 'A clear view of profitability, cost, and growth.' : 'Stock position, movement, and capital efficiency.'}</p></div><button className="secondary-button">Export report</button></section>
      {financial ? <FinancialsPage period={period} /> : <InventoryPage />}
      {financial && <CarbonPanel period={period} />}
      {financial && <ImportPanel />}
    </>
  )
}

export default function App() {
  const [tab, setTab] = useState<Tab>('overview')
  // Pending decisions are shared by the Overview, the sidebar badge and the Decisions tab.
  const [pending, setPending] = useState<Decision[] | null>(null)
  const reloadPending = useCallback(async () => {
    try {
      setPending(await api<Decision[]>('/decisions?status=pending'))
    } catch {
      setPending(null)
    }
  }, [])
  const refreshModels = useCallback(async (): Promise<ModelRefresh> => {
    const [orders, aging] = await Promise.all([
      api<ModelRefresh['orders']>('/recommendations/refresh', { method: 'POST' }),
      api<ModelRefresh['aging']>('/writeoffs/refresh', { method: 'POST' }),
    ])
    await reloadPending()
    return { orders, aging }
  }, [reloadPending])
  // Run the models once on load so the latest recommendations are waiting for approval.
  useEffect(() => { refreshModels().catch(() => reloadPending()) }, [refreshModels, reloadPending])
  const pendingCount = pending?.length
  const { data: dataset } = useApi<DatasetInfo>('/dataset')
  const fiscal = dataset ? fiscalLabels(dataset.as_of, dataset.fiscal_year_start_month) : null
  const [dateView, setDateView] = useState('YTD')
  const [customDates, setCustomDates] = useState(false)
  const [appliedRange, setAppliedRange] = useState<{ from: string; to: string } | null>(null)
  const [draftRange, setDraftRange] = useState({ from: '2025-01-01', to: '2025-06-16' })
  const rangeInvalid = !draftRange.from || !draftRange.to || draftRange.from > draftRange.to
  const period: Period = customDates && appliedRange ? { start: appliedRange.from, end: appliedRange.to } : { view: dateView as 'YTD' | 'QTD' | 'MTD' }
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span>P</span><strong>Pro-sight</strong></div>
        <nav>
          <small>WORKSPACE</small>
          {nav.map(item => <button key={item.id} className={tab === item.id ? 'active' : ''} onClick={() => setTab(item.id)}><Icon name={item.icon} /><span>{item.label}</span>{item.id === 'recommendations' && pendingCount ? <b>{pendingCount}</b> : null}</button>)}
        </nav>
        <div className="sidebar-footer"><div className="support"><Icon name="spark" /><div><strong>Need support?</strong><span>Ask your AI copilot</span></div></div><div className="profile"><span>OH</span><div><strong>Olivia Hayes</strong><small>Finance Lead</small></div><button aria-label="Profile options">•••</button></div></div>
      </aside>
      <main>
        <header className="topbar"><div className="search"><Icon name="search" /><input placeholder="Search anything..." aria-label="Search" /></div><div><button className="icon-button" aria-label="Notifications"><Icon name="bell" /><i /></button>{fiscal && <span className="period" title={dataset ? `${dataset.source === 'import' ? 'Your data' : 'Demo data'}, as of ${formatDate(dataset.as_of)}` : undefined}>FY {fiscal.currentFy} · Q{fiscal.quarter}{dataset?.source === 'demo' ? ' · Demo' : ''}</span>}</div></header>
        <div className="date-toolbar">
          <div className="date-context"><span>Viewing</span><strong>{customDates ? (appliedRange ? `${formatDate(appliedRange.from)} – ${formatDate(appliedRange.to)}` : 'Custom date range') : dateView}</strong></div>
          <div className="date-options">
            {['YTD', 'QTD', 'MTD'].map(view => <button className={!customDates && dateView === view ? 'active' : ''} onClick={() => { setDateView(view); setCustomDates(false) }} key={view} title={view === 'YTD' ? 'Year to date' : view === 'QTD' ? 'Quarter to date' : 'Month to date'}>{view}<span>{view === 'YTD' ? 'Year to date' : view === 'QTD' ? 'Quarter to date' : 'Month to date'}</span></button>)}
            <button className={customDates ? 'active' : ''} onClick={() => setCustomDates(true)}>Custom range</button>
          </div>
          {customDates && <div className="custom-dates"><label>From<input type="date" value={draftRange.from} max={draftRange.to || undefined} aria-invalid={rangeInvalid} onChange={e => setDraftRange(r => ({ ...r, from: e.target.value }))} /></label><span>→</span><label>To<input type="date" value={draftRange.to} min={draftRange.from || undefined} aria-invalid={rangeInvalid} onChange={e => setDraftRange(r => ({ ...r, to: e.target.value }))} /></label><button disabled={rangeInvalid} onClick={() => setAppliedRange(draftRange)}>Apply</button></div>}
        </div>
        <div className="page">
          {tab === 'overview' && <Overview setTab={setTab} period={period} pending={pending} fiscal={fiscal} />}
          {tab === 'budget' && <Budget />}
          {tab === 'recommendations' && <Recommendations pending={pending} reloadPending={reloadPending} refreshModels={refreshModels} />}
          {(tab === 'inventory' || tab === 'financials') && <DetailPage type={tab} period={period} />}
        </div>
      </main>
    </div>
  )
}
