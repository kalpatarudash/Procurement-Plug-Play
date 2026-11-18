import { useEffect, useState, type ReactNode } from 'react'
import { api, formatMoney, formatPct, formatSignedPct, periodQuery, useApi, type ConnectorInfo, type Emissions, type Financials, type Inventory, type Period, type PlanResponse } from './api'

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

/** Format a value expressed in thousands, e.g. 780 -> "$780K", 1200 -> "$1.2M". */
function formatK(k: number) {
  return k >= 1000 ? `$${(k / 1000).toFixed(2).replace(/\.?0+$/, '')}M` : `$${k}K`
}

/** Format an ISO yyyy-mm-dd string in local time (avoids the UTC off-by-one of `new Date(iso)`). */
function formatDate(iso: string) {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

const monthPoints = [38, 43, 40, 50, 53, 58, 55, 66, 72, 69, 79, 84]
const chartPath = monthPoints.map((point, index) => `${index === 0 ? 'M' : 'L'} ${index * 62 + 8} ${145 - point}`).join(' ')

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

function Overview({ setTab }: { setTab: (tab: Tab) => void }) {
  return (
    <>
      <section className="hero">
        <div>
          <span className="eyebrow">MONDAY, 16 JUNE</span>
          <h1>Good morning, Olivia.</h1>
          <p>Here’s what needs your attention across procurement and finance.</p>
        </div>
        <button className="primary-button" onClick={() => setTab('budget')}>Plan FY 2026 <Icon name="arrow" /></button>
      </section>

      <section className="metrics-grid">
        <Metric label="Revenue" value="$4.82M" detail="+12.4% vs last year" />
        <Metric label="Net income" value="$684K" detail="+8.2% vs last year" tone="blue" />
        <Metric label="Inventory value" value="$1.24M" detail="8.6% aging stock" tone="orange" />
        <Metric label="Open purchase orders" value="24" detail="$318K committed" tone="blue" />
      </section>

      <div className="content-grid">
        <section className="panel forecast-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">PERFORMANCE</span><h2>Revenue forecast</h2></div>
            <button className="text-button" onClick={() => setTab('financials')}>View financials <Icon name="arrow" size={15} /></button>
          </div>
          <div className="chart-summary"><strong>$5.46M</strong><span>Projected FY revenue</span><b>+13.3%</b></div>
          <div className="chart-wrap">
            <svg viewBox="0 0 700 160" preserveAspectRatio="none" aria-label="Revenue forecast chart">
              <defs><linearGradient id="area" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#40786d" stopOpacity=".24" /><stop offset="1" stopColor="#40786d" stopOpacity="0" /></linearGradient></defs>
              {[30, 70, 110, 150].map(y => <line key={y} x1="0" x2="700" y1={y} y2={y} className="grid-line" />)}
              <path d={`${chartPath} L 690 155 L 8 155 Z`} fill="url(#area)" />
              <path d={chartPath} className="chart-line" />
            </svg>
            <div className="chart-labels">{['Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'].map(m => <span key={m}>{m}</span>)}</div>
          </div>
        </section>

        <section className="panel ai-panel">
          <div className="ai-mark"><Icon name="spark" size={20} /></div>
          <span className="eyebrow">PROCURE AI</span>
          <h2>3 actions could protect $126K in margin</h2>
          <p>Based on your current inventory, supplier performance, and sales forecast.</p>
          <div className="mini-action">
            <span className="alert-dot"><Icon name="alert" size={16} /></span>
            <div><strong>Reduce Spring Outerwear PO</strong><small>Demand forecast is 18% below plan</small></div>
          </div>
          <button className="dark-button" onClick={() => setTab('recommendations')}>Review recommendations <Icon name="arrow" /></button>
        </section>
      </div>

      <section className="panel inventory-table">
        <div className="panel-heading">
          <div><span className="eyebrow">INVENTORY HEALTH</span><h2>Items that need attention</h2></div>
          <button className="text-button" onClick={() => setTab('inventory')}>View all inventory <Icon name="arrow" size={15} /></button>
        </div>
        <div className="table-head"><span>Category</span><span>Stock value</span><span>Sell-through</span><span>Status</span><span>Suggested action</span></div>
        {[
          ['Spring Outerwear', '$142,680', '42%', 'Overstocked', 'Reduce next PO by 28%'],
          ['Classic Footwear', '$88,240', '81%', 'Healthy', 'Reorder within 12 days'],
          ['Accessories', '$64,920', '36%', 'Aging', 'Bundle slow-moving SKUs'],
        ].map((row, index) => (
          <div className="table-row" key={row[0]}>
            <span><i className={`category-swatch s${index}`} />{row[0]}</span>
            <span>{row[1]}</span><span>{row[2]}</span>
            <span><b className={`status status-${index}`}>{row[3]}</b></span>
            <span>{row[4]}</span>
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
          <div><span className="eyebrow">EMISSIONS CONTROL PLAN</span><h2>Align carbon reduction with the operating budget</h2><p>Baseline: {em.baseline.total.toLocaleString()} tCO₂e over the last 12 months. The cheapest levers that close the gap are selected.</p></div>
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

function Recommendations() {
  const [approved, setApproved] = useState<string[]>([])
  const [editing, setEditing] = useState(false)
  const [requestOpen, setRequestOpen] = useState(false)
  const [requestPlaced, setRequestPlaced] = useState(false)
  const [firstTitle, setFirstTitle] = useState('Reduce Spring Outerwear order by 28%')
  const [firstImpact, setFirstImpact] = useState(74)
  const [amount, setAmount] = useState(50000)
  // Stable ids are used as React keys so editing the title doesn't remount the card (and drop input focus).
  const items = [
    { id: 'outerwear', title: firstTitle, area: 'Procurement', impact: firstImpact, confidence: 'High', reason: 'Sales velocity has fallen for 6 consecutive weeks while weeks-of-cover reached 19.', credits: '1,240 credits' },
    { id: 'accessories', title: 'Write off 34 aging accessory SKUs', area: 'Inventory', impact: 31, confidence: 'High', reason: 'These units are over 270 days old and carrying costs now exceed expected margin.', credits: null },
    { id: 'northstar', title: 'Renegotiate terms with Northstar Textiles', area: 'Supplier', impact: 21, confidence: 'Medium', reason: 'On-time delivery is 96%, but current unit pricing is 7% above category benchmark.', credits: '860 credits' },
  ]
  const totalImpact = items.reduce((sum, item) => sum + item.impact, 0)
  const approve = (id: string) => setApproved(prev => (prev.includes(id) ? prev : [...prev, id]))
  const closeRequest = () => setRequestOpen(false)

  useEffect(() => {
    if (!requestOpen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setRequestOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [requestOpen])

  return (
    <>
      <section className="hero compact"><div><span className="eyebrow">AGENTIC INSIGHTS</span><h1>Actions, not just answers.</h1><p>Recommendations ranked by financial impact and urgency.</p></div><div className="hero-actions"><div className="impact-pill"><Icon name="spark" /> {formatK(totalImpact)} potential impact</div><button className="primary-button" onClick={() => { setRequestOpen(true); setRequestPlaced(false) }}>+ Raise purchase request</button></div></section>
      <div className="recommendation-list">
        {items.map((item, i) => {
          const isApproved = approved.includes(item.id)
          return (
            <article className={`panel recommendation ${isApproved ? 'approved' : ''}`} key={item.id}>
              <div className="rec-rank">{String(i + 1).padStart(2, '0')}</div>
              <div className="rec-copy">
                <div><span className="eyebrow">{item.area}</span><span className={`confidence c${i}`}>{item.confidence} confidence</span>{item.credits && <span className="carbon-credit">− CO₂ · {item.credits}</span>}</div>
                {i === 0 && editing
                  ? <><input className="edit-title" aria-label="Recommendation title" value={firstTitle} onChange={e => setFirstTitle(e.target.value)} /><label className="edit-impact">Estimated impact ($K)<input type="number" min="0" value={firstImpact} onChange={e => setFirstImpact(toNumber(e.target.value))} /></label></>
                  : <h2>{item.title}</h2>}
                <p>{item.reason}</p>
              </div>
              <div className="rec-impact">
                <small>EST. IMPACT</small><strong>{formatK(item.impact)}</strong>
                {i === 0
                  ? <div className="rec-actions"><button className="edit-button" disabled={isApproved || (editing && !firstTitle.trim())} onClick={() => { if (editing) setFirstTitle(firstTitle.trim()); setEditing(!editing) }}>{editing ? 'Save changes' : 'Edit'}</button><button disabled={isApproved || editing} onClick={() => approve(item.id)}>{isApproved ? 'Order placed' : 'Place order'} <Icon name={isApproved ? 'check' : 'arrow'} size={16} /></button></div>
                  : <button disabled={isApproved} onClick={() => approve(item.id)}>{isApproved ? 'Approved' : 'Approve action'} <Icon name={isApproved ? 'check' : 'arrow'} size={16} /></button>}
              </div>
            </article>
          )
        })}
      </div>
      {requestOpen && (
        <div className="modal-backdrop" onMouseDown={closeRequest}>
          <section className="request-modal" role="dialog" aria-modal="true" aria-labelledby="request-title" onMouseDown={e => e.stopPropagation()}>
            {requestPlaced ? (
              <div className="request-success"><span><Icon name="check" size={26} /></span><h2 id="request-title">Purchase request raised</h2><p>PR-2025-048 for ${amount.toLocaleString()} has been sent to Finance for approval.</p><button className="primary-button" onClick={closeRequest}>Done</button></div>
            ) : (
              <>
                <div className="request-heading"><div><span className="eyebrow">NEW PURCHASE REQUEST</span><h2 id="request-title">Raise a custom PR</h2></div><button onClick={closeRequest} aria-label="Close">×</button></div>
                <label className="form-field">Purchase category<select defaultValue="Spring Outerwear"><option>Spring Outerwear</option><option>Classic Footwear</option><option>Accessories</option></select></label>
                <label className="form-field">Requested amount<span className="amount-field"><span>$</span><input type="number" min="0" step="100" value={amount} onChange={e => setAmount(toNumber(e.target.value))} /></span></label>
                <label className="form-field">Preferred supplier<select defaultValue="Northstar Textiles"><option>Northstar Textiles</option><option>Evergreen Manufacturing</option><option>Atlas Apparel Co.</option></select></label>
                <div className="pr-insight"><Icon name="spark" /><div><strong>Pro-sight recommendation</strong><p>Northstar Textiles is the best fit with a 96% on-time rating. Based on forecast demand, keep this PR between <b>$42,000–$58,000</b>{amount > 58000 ? ' — your current amount may create 4.5 weeks of excess stock.' : amount < 42000 ? ' — your current amount may lead to stockouts.' : ' to maintain healthy stock cover.'}</p><p className="credit-note"><b>Lower-emission option:</b> Evergreen Manufacturing emits 18% less CO₂ and could earn approximately <b>{Math.round(amount / 120).toLocaleString()} carbon credits</b> on this order.</p></div></div>
                <div className="modal-actions"><button className="secondary-button" onClick={closeRequest}>Cancel</button><button className="primary-button" disabled={amount <= 0} onClick={() => setRequestPlaced(true)}>Raise PR <Icon name="arrow" /></button></div>
              </>
            )}
          </section>
        </div>
      )}
    </>
  )
}

function LoadState({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  return (
    <section className="panel load-state">
      {error
        ? <><Icon name="alert" /><div><strong>{error}</strong><p>Start it with <code>cd backend && uv run uvicorn prosight.main:app --reload</code></p></div><button className="secondary-button" onClick={onRetry}>Retry</button></>
        : <p>Loading…</p>}
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
      <Connectors />
      {data && (
        <section className="panel statement-panel">
          <div className="panel-heading"><div><span className="eyebrow">CATEGORY PERFORMANCE</span><h2>Capital by category</h2></div><span className="period-note">As of {formatDate(data.as_of)}</span></div>
          {data.categories.map(category => (
            <div className="statement-row" key={category.name}>
              <span>{category.name} · <em className="category-status">{category.status}</em></span>
              <strong>${Math.round(category.stock_value).toLocaleString()}</strong>
            </div>
          ))}
          <div className="statement-row total"><span>Total</span><strong>${Math.round(data.total_value).toLocaleString()}</strong></div>
        </section>
      )}
    </>
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
    </>
  )
}

export default function App() {
  const [tab, setTab] = useState<Tab>('overview')
  const [dateView, setDateView] = useState('YTD')
  const [customDates, setCustomDates] = useState(false)
  const [appliedRange, setAppliedRange] = useState<{ from: string; to: string } | null>(null)
  const [draftRange, setDraftRange] = useState({ from: '2025-01-01', to: '2025-06-16' })
  const rangeInvalid = !draftRange.from || !draftRange.to || draftRange.from > draftRange.to
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span>P</span><strong>Pro-sight</strong></div>
        <nav>
          <small>WORKSPACE</small>
          {nav.map(item => <button key={item.id} className={tab === item.id ? 'active' : ''} onClick={() => setTab(item.id)}><Icon name={item.icon} /><span>{item.label}</span>{item.id === 'recommendations' && <b>3</b>}</button>)}
        </nav>
        <div className="sidebar-footer"><div className="support"><Icon name="spark" /><div><strong>Need support?</strong><span>Ask your AI copilot</span></div></div><div className="profile"><span>OH</span><div><strong>Olivia Hayes</strong><small>Finance Lead</small></div><button aria-label="Profile options">•••</button></div></div>
      </aside>
      <main>
        <header className="topbar"><div className="search"><Icon name="search" /><input placeholder="Search anything..." aria-label="Search" /></div><div><button className="icon-button" aria-label="Notifications"><Icon name="bell" /><i /></button><select className="period" defaultValue="FY 2025 · Q4" aria-label="Dashboard reporting period"><option>FY 2025 · Q4</option><option>FY 2024 · Q4</option><option>FY 2023 · Q4</option></select></div></header>
        <div className="date-toolbar">
          <div className="date-context"><span>Viewing</span><strong>{customDates ? (appliedRange ? `${formatDate(appliedRange.from)} – ${formatDate(appliedRange.to)}` : 'Custom date range') : dateView}</strong></div>
          <div className="date-options">
            {['YTD', 'QTD', 'MTD'].map(view => <button className={!customDates && dateView === view ? 'active' : ''} onClick={() => { setDateView(view); setCustomDates(false) }} key={view} title={view === 'YTD' ? 'Year to date' : view === 'QTD' ? 'Quarter to date' : 'Month to date'}>{view}<span>{view === 'YTD' ? 'Year to date' : view === 'QTD' ? 'Quarter to date' : 'Month to date'}</span></button>)}
            <button className={customDates ? 'active' : ''} onClick={() => setCustomDates(true)}>Custom range</button>
          </div>
          {customDates && <div className="custom-dates"><label>From<input type="date" value={draftRange.from} max={draftRange.to || undefined} aria-invalid={rangeInvalid} onChange={e => setDraftRange(r => ({ ...r, from: e.target.value }))} /></label><span>→</span><label>To<input type="date" value={draftRange.to} min={draftRange.from || undefined} aria-invalid={rangeInvalid} onChange={e => setDraftRange(r => ({ ...r, to: e.target.value }))} /></label><button disabled={rangeInvalid} onClick={() => setAppliedRange(draftRange)}>Apply</button></div>}
        </div>
        <div className="page">
          {tab === 'overview' && <Overview setTab={setTab} />}
          {tab === 'budget' && <Budget />}
          {tab === 'recommendations' && <Recommendations />}
          {(tab === 'inventory' || tab === 'financials') && <DetailPage type={tab} period={customDates && appliedRange ? { start: appliedRange.from, end: appliedRange.to } : { view: dateView as 'YTD' | 'QTD' | 'MTD' }} />}
        </div>
      </main>
    </div>
  )
}
