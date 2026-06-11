import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Search, RefreshCw, CheckCircle, Zap, Loader2,
  ExternalLink, ChevronDown, ChevronUp, Briefcase,
  AlertCircle, Target, ArrowLeft, ArrowRight, Database, Users,
  ThumbsUp, ThumbsDown, List, Bookmark, X,
} from 'lucide-react'
import type { ICP, Candidate, PhaseEntry, CacheInfo, CareerSnapshotItem } from './types'

// ── Constants ──────────────────────────────────────────────────────────────────

const PHASE_LABELS: Record<string, string> = {
  fetch_employees:  'Employee profiles',
  build_icp:        'Building ICP',
  research_role:    'Role requirements',
  generate_queries: 'Search queries',
  search_one_query: 'LinkedIn search',
  score_candidates: 'Scoring candidates',
}

const RATINGS_KEY  = 'nimble-candidate-ratings'
const SAVED_KEY    = 'nimble-saved-searches'
const LAST_RUN_KEY = 'nimble-last-run'   // auto-save — restored on any page reload
const MAX_SAVED    = 20
const LAST_RUN_TTL = 24 * 60 * 60 * 1000 // 24 hours

// ── Saved-search types ─────────────────────────────────────────────────────────

interface SavedSearch {
  id: string
  name: string
  job_title: string
  additional_notes: string
  candidates: Candidate[]
  icp: ICP | null
  queries: string[]
  strong_count: number
  total_count: number
  saved_at: number
}

function readSaved(): SavedSearch[] {
  try { return JSON.parse(localStorage.getItem(SAVED_KEY) || '[]') } catch { return [] }
}
function writeSaved(list: SavedSearch[]) {
  localStorage.setItem(SAVED_KEY, JSON.stringify(list))
}

// ── Score helpers ──────────────────────────────────────────────────────────────

function scoreBadgeClass(s: number) {
  if (s >= 7) return 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200'
  if (s >= 5) return 'bg-amber-50  text-amber-700  ring-1 ring-amber-200'
  return             'bg-red-50    text-red-600    ring-1 ring-red-200'
}

function scoreRingClass(s: number) {
  if (s >= 7) return 'text-emerald-600 border-emerald-300 bg-emerald-50'
  if (s >= 5) return 'text-amber-600  border-amber-300  bg-amber-50'
  return             'text-red-500    border-red-200    bg-red-50'
}

function extractSubtitle(name: string, headline: string): string {
  if (!name || !headline) return headline || ''
  if (headline.toLowerCase().startsWith(name.toLowerCase()))
    return headline.slice(name.length).replace(/^\s*[-–—|]\s*/, '').trim()
  return headline
}

function daysAgo(ts: number): number {
  return Math.floor((Date.now() - ts) / 86_400_000)
}

// Inline chevron-right icon (avoids adding lucide import for one icon)
function ChevronRight({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <polyline points="9 18 15 12 9 6" />
    </svg>
  )
}

// ── Header ─────────────────────────────────────────────────────────────────────

function Header({ onNewSearch, showNew }: { onNewSearch: () => void; showNew: boolean }) {
  return (
    <header style={{ background: '#0A0A0A', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
      <div className="max-w-5xl mx-auto px-6 h-16 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center font-bold text-base shrink-0"
            style={{ background: 'linear-gradient(135deg,#F5D06B 0%,#E8B84B 55%,#C49520 100%)', color: '#0A0A0A' }}>
            N
          </div>
          <div>
            <div className="flex items-baseline gap-2">
              <span className="text-white font-semibold text-sm tracking-tight">Talent Agent</span>
              <span className="text-white/25 text-xs">by Nimble</span>
            </div>
            <p className="text-white/35 text-[10px] leading-tight">Find candidates who fit — and would actually join</p>
          </div>
        </div>
        {showNew && (
          <button onClick={onNewSearch}
            className="flex items-center gap-1.5 text-xs text-white/40 hover:text-white/70 transition-colors duration-150 px-3 py-1.5 rounded-lg hover:bg-white/5">
            <ArrowLeft size={12} /> New search
          </button>
        )}
      </div>
    </header>
  )
}

// ── Cache bar ──────────────────────────────────────────────────────────────────

function CacheBar({ info, onRefresh }: { info: CacheInfo; onRefresh: () => void }) {
  if (!info.has_employees) {
    return (
      <div className="flex items-center gap-2.5 text-xs text-amber-700 bg-amber-50 border border-amber-200/70 rounded-xl px-4 py-2.5">
        <Database size={13} className="shrink-0" />
        No employee cache — first run fetches live Nimble profiles (~30s extra)
      </div>
    )
  }
  const days  = info.employees_days_ago ?? 0
  const stale = !info.employees_fresh
  return (
    <div className={`flex items-center justify-between text-xs rounded-xl px-4 py-2.5 border ${stale ? 'bg-amber-50 border-amber-200/70 text-amber-700' : 'bg-slate-50 border-slate-200 text-slate-500'}`}>
      <span className="flex items-center gap-2 min-w-0">
        <Database size={13} style={{ color: 'var(--nimble-gold)' }} className="shrink-0" />
        <span className="truncate">
          <strong className="text-slate-700">{info.employee_count}</strong> employees cached
          {info.has_icp && <span className="text-slate-400"> · ICP ready</span>}
          {' · '}{days === 0 ? 'today' : `${days}d ago`}
          {stale && <span className="text-amber-600 font-semibold"> · refresh recommended</span>}
        </span>
      </span>
      <button onClick={onRefresh} className="flex items-center gap-1 ml-3 shrink-0 text-slate-400 hover:text-slate-700 transition-colors duration-150">
        <RefreshCw size={12} /> Refresh
      </button>
    </div>
  )
}

// ── Idle hero ──────────────────────────────────────────────────────────────────

function IdleHero() {
  const cards = [
    { icon: <Briefcase size={18} />, title: 'Researches the role',        body: "Builds a job description from Nimble's careers page and web context — no manual input needed." },
    { icon: <Search size={18} />,    title: 'Searches LinkedIn at scale', body: "8 queries across 8 distinct search strategies using Nimble's real-time data API." },
    { icon: <Target size={18} />,    title: 'Scores bidirectionally',     body: 'Rates role fit, culture fit, and likelihood to join — so you only contact people worth reaching out to.' },
  ]
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      {cards.map((c, i) => (
        <div key={i} className="card p-5 animate-fade-in-up" style={{ animationDelay: `${80 + i * 55}ms` }}>
          <div className="w-9 h-9 rounded-lg flex items-center justify-center mb-4"
            style={{ background: 'linear-gradient(135deg,#F5D06B 0%,#E8B84B 55%,#C49520 100%)', color: '#0A0A0A' }}>
            {c.icon}
          </div>
          <h3 className="font-semibold text-slate-900 text-sm mb-1.5">{c.title}</h3>
          <p className="text-xs text-slate-400 leading-relaxed">{c.body}</p>
        </div>
      ))}
    </div>
  )
}

// ── Saved searches list ────────────────────────────────────────────────────────

function SavedSearchesList({ searches, onLoad, onDelete, currentId }: {
  searches: SavedSearch[]
  onLoad: (s: SavedSearch) => void
  onDelete: (id: string) => void
  currentId: string | null
}) {
  const [open, setOpen] = useState(true)
  if (!searches.length) return null

  return (
    <div className="card overflow-hidden animate-fade-in">
      <button onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-50 transition-colors duration-150">
        <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest flex items-center gap-2">
          <Bookmark size={12} /> Saved searches · {searches.length}
        </span>
        {open ? <ChevronUp size={13} className="text-slate-300" /> : <ChevronDown size={13} className="text-slate-300" />}
      </button>
      {open && (
        <div className="border-t border-slate-100 divide-y divide-slate-50 animate-slide-down">
          {searches.map(s => {
            const age      = daysAgo(s.saved_at)
            const outdated = age >= 7
            const active   = s.id === currentId
            return (
              <div key={s.id} className={`flex items-center gap-2 px-4 py-3 transition-colors duration-100 ${active ? 'bg-amber-50/60' : 'hover:bg-slate-50'}`}>
                <button onClick={() => onLoad(s)} className="flex-1 text-left min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-slate-800 truncate">{s.name}</span>
                    {active   && <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded" style={{ background: 'rgba(232,184,75,0.2)', color: '#92610a' }}>active</span>}
                    {outdated && <span className="text-[10px] font-semibold bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">outdated</span>}
                  </div>
                  <p className="text-[11px] text-slate-400 mt-0.5">
                    {s.strong_count} strong · {s.total_count} total · {age === 0 ? 'today' : `${age}d ago`}
                  </p>
                </button>
                <button onClick={() => onDelete(s.id)}
                  className="p-1.5 text-slate-300 hover:text-red-400 hover:bg-red-50 rounded-lg transition-colors duration-150 shrink-0">
                  <X size={13} />
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Progress panel ─────────────────────────────────────────────────────────────

function ProgressPanel({ phases, displayFraction, liveElapsed, remaining, status, usage }: {
  phases: PhaseEntry[]
  displayFraction: number
  liveElapsed: number
  remaining: number
  status: string
  usage?: Record<string, number> | null
}) {
  const pct = Math.round(displayFraction * 100)
  return (
    <div className="card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-widest">Progress</h3>
        <span className="text-xs font-semibold tabular-nums" style={{ color: 'var(--nimble-gold)' }}>{pct}%</span>
      </div>
      <div className="h-1.5 bg-slate-100 rounded-full mb-1 overflow-hidden">
        <div className="h-full rounded-full"
          style={{
            width: `${pct}%`,
            background: 'linear-gradient(90deg,#F5D06B,#E8B84B,#C49520)',
            transition: 'width 1500ms cubic-bezier(0.4,0,0.2,1)',
          }} />
      </div>
      <div className="flex justify-between text-[11px] text-slate-400 mb-5 tabular-nums">
        <span>{liveElapsed}s elapsed</span>
        <span>{status === 'done' ? `Complete · ${liveElapsed}s` : `~${remaining}s left`}</span>
      </div>
      <div className="space-y-3">
        {phases.map((p, i) => (
          <div key={i} className="flex items-start gap-3 animate-fade-in" style={{ animationDelay: `${i * 30}ms` }}>
            <div className="mt-0.5 shrink-0">
              {p.from_cache
                ? <Zap size={14} style={{ color: 'var(--nimble-gold)' }} />
                : <CheckCircle size={14} className="text-emerald-500" />}
            </div>
            <div className="min-w-0">
              <span className="text-sm font-medium text-slate-700">{PHASE_LABELS[p.phase] ?? p.phase}</span>
              <span className="text-xs text-slate-400 ml-2">{p.message}</span>
            </div>
          </div>
        ))}
        {status === 'running' && (
          <div className="flex items-center gap-3 animate-fade-in">
            <Loader2 size={14} className="animate-spin shrink-0" style={{ color: 'var(--nimble-gold)' }} />
            <span className="text-xs text-slate-400">Working…</span>
          </div>
        )}
      </div>
      {status === 'done' && usage && (usage.haiku_calls || usage.sonnet_calls) ? (
        <p className="text-[11px] text-slate-400 mt-3 pt-3 border-t border-slate-100 tabular-nums">
          ~{usage.candidates_scored ?? 0} scored · ~{usage.haiku_calls ?? 0} Haiku · ~{usage.sonnet_calls ?? 0} Sonnet call{(usage.sonnet_calls ?? 0) !== 1 ? 's' : ''}
          {(usage.filtered_out ?? 0) > 0 && ` · ${usage.filtered_out} pre-filtered`}
          {(usage.est_cost_usd ?? 0) > 0 && ` · est. $${(usage.est_cost_usd as number).toFixed(3)}`}
        </p>
      ) : null}
    </div>
  )
}

// ── Search strategy panel ──────────────────────────────────────────────────────

function SearchStrategyPanel({ queries }: { queries: string[] }) {
  const [open, setOpen] = useState(false)
  if (!queries.length) return null
  const labels = ['A · Role + skills', 'B · Lookalike companies', 'C · Career trajectory', 'D · Availability signals', 'E · Deep skill search']
  return (
    <div className="card overflow-hidden animate-fade-in">
      <button onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-50 transition-colors duration-150">
        <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest flex items-center gap-2">
          <List size={12} /> Search strategy · {queries.length} quer{queries.length === 1 ? 'y' : 'ies'}
        </span>
        {open ? <ChevronUp size={13} className="text-slate-300" /> : <ChevronDown size={13} className="text-slate-300" />}
      </button>
      {open && (
        <div className="border-t border-slate-100 divide-y divide-slate-50 animate-slide-down">
          {queries.map((q, i) => (
            <div key={i} className="px-4 py-2.5 flex gap-3 min-w-0">
              <span className="text-[10px] font-semibold text-slate-300 shrink-0 w-28 leading-tight mt-0.5">{labels[i % labels.length]}</span>
              <code className="text-[11px] text-slate-500 font-mono leading-relaxed break-all min-w-0">{q}</code>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── ICP skeleton ───────────────────────────────────────────────────────────────

function ICPSkeleton() {
  return (
    <div className="card p-5 space-y-4 overflow-hidden">
      <div className="skeleton h-4 w-36 rounded" />
      <div className="skeleton h-3 w-full rounded" />
      <div className="skeleton h-3 w-4/5 rounded" />
      <div className="skeleton h-3 w-3/5 rounded" />
      <div className="flex gap-2 mt-4 flex-wrap">
        {[72, 90, 56, 80, 64, 96].map((w, i) => (
          <div key={i} className="skeleton h-6 rounded-full" style={{ width: w }} />
        ))}
      </div>
    </div>
  )
}

// ── ICP card ───────────────────────────────────────────────────────────────────

function ICPCard({ icp }: { icp: ICP }) {
  return (
    <div className="card p-5 animate-scale-in overflow-hidden max-w-full space-y-5">
      <div className="flex items-start gap-3 flex-wrap">
        <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-widest flex items-center gap-2 shrink-0">
          <Users size={13} style={{ color: 'var(--nimble-gold)' }} /> Ideal Candidate Profile
        </h3>
        {icp.seniority_range && (
          <span className="text-[11px] bg-slate-100 text-slate-500 px-2.5 py-0.5 rounded-full">
            {icp.seniority_range}
          </span>
        )}
      </div>

      {icp.company_summary && (
        <p className="text-xs text-slate-500 italic leading-relaxed pl-3 border-l-2"
          style={{ borderColor: 'var(--nimble-gold)', wordBreak: 'break-word' }}>
          {icp.company_summary}
        </p>
      )}

      {/* Section 1: What we're looking for (BASE_CANDIDATE_ICP fields) */}
      <div>
        <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest mb-3 flex items-center gap-2">
          <span style={{ color: 'var(--nimble-gold)' }}>▸</span> What we're looking for
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
          <div className="space-y-3 min-w-0">
            {icp.key_skills?.length > 0 && (
              <div>
                <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest mb-2">Key Skills</div>
                <div className="flex flex-wrap gap-1.5">
                  {icp.key_skills.slice(0, 10).map(s => (
                    <span key={s} className="px-2.5 py-1 rounded-full text-[11px] font-medium"
                      style={{ background: 'rgba(232,184,75,0.12)', color: '#92610a', border: '1px solid rgba(232,184,75,0.3)' }}>
                      {s}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
          <div className="space-y-3 min-w-0">
            {icp.green_flags?.length > 0 && (
              <div>
                <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest mb-2">Strong Signals</div>
                <ul className="space-y-1.5">
                  {icp.green_flags.slice(0, 5).map(f => (
                    <li key={f} className="flex gap-2 text-slate-600 leading-tight">
                      <CheckCircle size={12} className="text-emerald-500 shrink-0 mt-0.5" />
                      <span style={{ wordBreak: 'break-word' }}>{f}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {icp.red_flags?.length > 0 && (
              <div>
                <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest mb-2">Disqualifiers</div>
                <ul className="space-y-1.5">
                  {icp.red_flags.slice(0, 4).map(f => (
                    <li key={f} className="flex gap-2 text-slate-600 leading-tight">
                      <AlertCircle size={12} className="text-red-400 shrink-0 mt-0.5" />
                      <span style={{ wordBreak: 'break-word' }}>{f}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Section 2: What current employees look like (employee analysis) */}
      {(icp.career_trajectory_patterns?.length > 0 || icp.typical_roles?.length > 0) && (
        <div className="pt-4 border-t border-slate-100">
          <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest mb-3 flex items-center gap-2">
            <span className="text-slate-300">▸</span> What current employees look like
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
            <div className="space-y-3 min-w-0">
              {icp.career_trajectory_patterns?.length > 0 && (
                <div>
                  <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest mb-2">Career Paths to Nimble</div>
                  <ul className="space-y-1">
                    {icp.career_trajectory_patterns.slice(0, 4).map(p => (
                      <li key={p} className="text-slate-500 leading-tight" style={{ wordBreak: 'break-word' }}>→ {p}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            <div className="space-y-3 min-w-0">
              {icp.typical_roles?.length > 0 && (
                <div>
                  <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest mb-2">Typical Roles</div>
                  <div className="flex flex-wrap gap-1">
                    {icp.typical_roles.slice(0, 6).map(r => (
                      <span key={r} className="text-[11px] bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full">{r}</span>
                    ))}
                  </div>
                </div>
              )}
              {icp.location_context && (
                <div className="text-slate-500" style={{ wordBreak: 'break-word' }}>
                  <span className="font-semibold text-slate-600">Location: </span>{icp.location_context}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Candidate detail drawer ────────────────────────────────────────────────────

function CandidateDrawer({ candidate, onClose }: {
  candidate: Candidate | null
  onClose: () => void
}) {
  useEffect(() => {
    if (!candidate) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [candidate, onClose])

  const c        = candidate
  const subtitle = c ? extractSubtitle(c.name, c.headline) : ''
  const isOpen   = c !== null

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 z-40 transition-opacity duration-300 ${isOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'}`}
        style={{ background: 'rgba(10,10,10,0.35)', backdropFilter: 'blur(2px)' }}
        onClick={onClose}
      />

      {/* Panel — slides from right on desktop, rises from bottom on mobile */}
      <div className={`
        fixed z-50 bg-white overflow-y-auto
        inset-x-0 bottom-0 max-h-[92vh] rounded-t-2xl shadow-2xl
        lg:inset-x-auto lg:inset-y-0 lg:right-0 lg:w-[480px] lg:max-h-none lg:rounded-none
        transform transition-transform duration-300 ease-out
        ${isOpen ? 'translate-y-0 lg:translate-x-0' : 'translate-y-full lg:translate-y-0 lg:translate-x-full'}
      `}>
        {c && (
          <>
            {/* Sticky header */}
            <div className="sticky top-0 bg-white border-b border-slate-100 px-5 py-4 z-10">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h2 className="font-bold text-slate-900 text-base leading-tight">{c.name || 'Unknown'}</h2>
                    <a href={c.url} target="_blank" rel="noreferrer" title="View on LinkedIn"
                      className="p-0.5 text-slate-300 hover:text-blue-500 transition-colors">
                      <ExternalLink size={14} />
                    </a>
                  </div>
                  {subtitle && <p className="text-xs text-slate-500 mt-0.5 line-clamp-2">{subtitle}</p>}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <div className={`score-ring ${scoreRingClass(c.overall_score)}`}>
                    <span className="text-base font-bold leading-none">{c.overall_score}</span>
                    <span className="text-[9px] opacity-50 leading-none mt-0.5">/10</span>
                  </div>
                  <button onClick={onClose}
                    className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors">
                    <X size={16} />
                  </button>
                </div>
              </div>
            </div>

            <div className="px-5 pb-8 pt-4 space-y-6">

              {/* Score breakdown */}
              <div>
                <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest mb-3">Score Breakdown</div>
                <div className="divide-y divide-slate-50 rounded-xl border border-slate-100 overflow-hidden">
                  {[
                    { label: 'Role fit',    score: c.role_fit_score,    explanation: c.role_fit_explanation    },
                    { label: 'Culture fit', score: c.culture_fit_score, explanation: c.culture_fit_explanation },
                    { label: 'Interest',   score: c.interest_score,    explanation: c.interest_explanation    },
                  ].map(({ label, score, explanation }) => (
                    <div key={label} className="flex gap-3 px-4 py-3">
                      <div className="flex items-center gap-2 w-32 shrink-0">
                        <span className="text-xs font-semibold text-slate-600">{label}</span>
                        <span className={`text-[11px] font-bold px-1.5 py-0.5 rounded ${scoreBadgeClass(score)}`}>{score}/10</span>
                      </div>
                      <p className="text-xs text-slate-600 leading-relaxed">
                        {explanation ?? <span className="text-slate-300 italic">No explanation available</span>}
                      </p>
                    </div>
                  ))}
                </div>
              </div>

              {/* Nimble fit + Candidate fit */}
              {(c.nimble_fit || c.candidate_fit) && (
                <div className="space-y-4">
                  {c.nimble_fit && (
                    <div>
                      <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest mb-1.5">Why Nimble would want them</div>
                      <p className="text-xs text-slate-600 leading-relaxed">{c.nimble_fit}</p>
                    </div>
                  )}
                  {c.candidate_fit && (
                    <div>
                      <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest mb-1.5">Why they might want Nimble</div>
                      <p className="text-xs text-slate-600 leading-relaxed">{c.candidate_fit}</p>
                    </div>
                  )}
                </div>
              )}

              {/* Friction */}
              {c.friction && c.friction !== 'None identified.' && (
                <div className="bg-amber-50 border border-amber-200/70 rounded-xl px-4 py-3">
                  <div className="text-[10px] font-semibold text-amber-700 uppercase tracking-widest mb-1.5">Friction</div>
                  <p className="text-xs text-amber-800 leading-relaxed">{c.friction}</p>
                </div>
              )}

              {/* Career arc */}
              {c.career_narrative && (
                <div>
                  <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest mb-1.5">Career Arc</div>
                  <p className="text-xs text-slate-500 leading-relaxed italic">{c.career_narrative}</p>
                </div>
              )}

              {/* ICP signal map */}
              {c.icp_match && (
                <div className="bg-slate-50 border border-slate-100 rounded-xl px-4 py-2.5">
                  <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest mb-1">ICP Signal Map</div>
                  <p className="text-xs text-slate-600 leading-relaxed">{c.icp_match}</p>
                </div>
              )}

              {/* Why prioritize / Potential friction */}
              {((c.why_yes?.length ?? 0) > 0 || (c.why_no?.length ?? 0) > 0) && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  {(c.why_yes?.length ?? 0) > 0 && (
                    <div>
                      <div className="text-[10px] font-semibold text-emerald-600 uppercase tracking-widest mb-2">Why prioritize</div>
                      <ul className="space-y-2">
                        {c.why_yes!.map((r, i) => (
                          <li key={i} className="flex gap-2 text-xs text-slate-700 leading-relaxed">
                            <CheckCircle size={12} className="text-emerald-500 shrink-0 mt-0.5" />
                            {r}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {(c.why_no?.length ?? 0) > 0 && (
                    <div>
                      <div className="text-[10px] font-semibold text-amber-600 uppercase tracking-widest mb-2">Potential friction</div>
                      <ul className="space-y-2">
                        {c.why_no!.map((r, i) => (
                          <li key={i} className="flex gap-2 text-xs text-slate-700 leading-relaxed">
                            <AlertCircle size={12} className="text-amber-500 shrink-0 mt-0.5" />
                            {r}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              {/* Career snapshot */}
              {c.career_snapshot && c.career_snapshot.length > 0 && (
                <div>
                  <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest mb-3">Career Snapshot</div>
                  <div className="space-y-2">
                    {c.career_snapshot.map((item: CareerSnapshotItem, i: number) => (
                      <div key={i} className="flex items-start gap-3 text-xs">
                        <div className="w-1.5 h-1.5 rounded-full bg-slate-300 mt-1.5 shrink-0" />
                        <div className="min-w-0">
                          <span className="font-medium text-slate-700">{item.role}</span>
                          {item.company && (
                            <><span className="text-slate-400"> at </span><span className="text-slate-600">{item.company}</span></>
                          )}
                          {item.duration && <span className="text-slate-400"> · {item.duration}</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Availability signals */}
              {c.availability_signals?.length > 0 && (
                <div>
                  <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest mb-2">Availability Signals</div>
                  <div className="flex flex-wrap gap-1.5">
                    {c.availability_signals.map(s => (
                      <span key={s} className="text-xs bg-slate-50 border border-slate-200 text-slate-600 px-2.5 py-1 rounded-full">{s}</span>
                    ))}
                  </div>
                </div>
              )}

              {/* Full reasoning + snippet */}
              {c.reasoning && (
                <div>
                  <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest mb-2">Recruiter Notes</div>
                  <p className="text-xs text-slate-600 leading-relaxed">{c.reasoning}</p>
                  {c.snippet && (
                    <p className="mt-3 text-[11px] text-slate-400 italic pl-3 border-l-2 border-slate-200 leading-relaxed">
                      {c.snippet.slice(0, 400)}
                    </p>
                  )}
                </div>
              )}

            </div>
          </>
        )}
      </div>
    </>
  )
}

// ── Candidate card ─────────────────────────────────────────────────────────────

function CandidateCard({ c, index, rating, onRate, onSelect, isSelected }: {
  c: Candidate
  index: number
  rating?: 'up' | 'down'
  onRate: (r: 'up' | 'down') => void
  onSelect: () => void
  isSelected: boolean
}) {
  const subtitle = extractSubtitle(c.name, c.headline)

  return (
    <div
      onClick={onSelect}
      className="card hover:shadow-card-md hover:-translate-y-0.5 transition-all duration-200 overflow-hidden animate-fade-in-up cursor-pointer"
      style={{
        animationDelay: `${Math.min(index, 7) * 55}ms`,
        ...(isSelected ? { outline: '2px solid rgba(232,184,75,0.55)', outlineOffset: '2px' } : {}),
      }}
    >
      <div className="p-5">
        {rating && (
          <div className={`inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full mb-2 ${rating === 'up' ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-600'}`}>
            {rating === 'up' ? <ThumbsUp size={10} /> : <ThumbsDown size={10} />}
            {rating === 'up' ? 'Good fit' : 'Not a fit'}
          </div>
        )}

        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 mb-0.5 flex-wrap">
              <span className="font-semibold text-slate-900 text-sm">{c.name || 'Unknown'}</span>
              <a href={c.url} target="_blank" rel="noreferrer" title="View on LinkedIn"
                onClick={e => e.stopPropagation()}
                className="p-0.5 text-slate-300 hover:text-blue-500 transition-colors duration-150">
                <ExternalLink size={12} />
              </a>
            </div>
            {subtitle && <p className="text-xs text-slate-400 leading-snug line-clamp-2">{subtitle}</p>}
          </div>
          <div className={`score-ring shrink-0 ${scoreRingClass(c.overall_score)}`}>
            <span className="text-base font-bold leading-none">{c.overall_score}</span>
            <span className="text-[9px] opacity-50 leading-none mt-0.5">/10</span>
          </div>
        </div>

        <div className="flex gap-1.5 flex-wrap mb-2">
          {[{ label: 'Role', val: c.role_fit_score }, { label: 'Culture', val: c.culture_fit_score }, { label: 'Interest', val: c.interest_score }].map(({ label, val }) => (
            <span key={label} className={`text-[11px] font-medium px-2 py-0.5 rounded-full ${scoreBadgeClass(val)}`}>
              {label} {val}/10
            </span>
          ))}
        </div>

        {c.icp_match && (
          <p className="text-[11px] text-slate-400 leading-snug mb-2 line-clamp-1">{c.icp_match}</p>
        )}

        {c.availability_signals?.length > 0 && (
          <div className="flex gap-1 flex-wrap mb-2.5">
            {c.availability_signals.slice(0, 3).map(s => (
              <span key={s} className="text-[10px] bg-slate-50 border border-slate-200 text-slate-500 px-2 py-0.5 rounded-full">{s}</span>
            ))}
          </div>
        )}

        <div className="flex items-center justify-between mt-2">
          <span className="text-[11px] text-slate-400 flex items-center gap-1">
            <ChevronRight size={11} /> View details
          </span>
          <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
            <button onClick={() => onRate('up')} title="Good fit"
              className={`p-1.5 rounded-lg transition-colors duration-150 ${rating === 'up' ? 'bg-emerald-100 text-emerald-600' : 'text-slate-300 hover:text-emerald-500 hover:bg-emerald-50'}`}>
              <ThumbsUp size={13} />
            </button>
            <button onClick={() => onRate('down')} title="Not a fit"
              className={`p-1.5 rounded-lg transition-colors duration-150 ${rating === 'down' ? 'bg-red-100 text-red-500' : 'text-slate-300 hover:text-red-400 hover:bg-red-50'}`}>
              <ThumbsDown size={13} />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Error card ─────────────────────────────────────────────────────────────────

function ErrorCard({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="bg-red-50 border border-red-200 rounded-2xl p-6 flex items-start gap-4 animate-fade-in-up">
      <div className="w-10 h-10 bg-red-100 rounded-full flex items-center justify-center shrink-0">
        <AlertCircle size={20} className="text-red-500" />
      </div>
      <div>
        <h3 className="font-semibold text-red-800 text-sm mb-1">Something went wrong</h3>
        <p className="text-xs text-red-600 mb-3 leading-relaxed">The agent hit a network error or API timeout. This is usually temporary — try again.</p>
        <button onClick={onRetry} className="flex items-center gap-1.5 text-xs font-semibold text-red-700 bg-red-100 hover:bg-red-200 px-3 py-1.5 rounded-lg transition-colors duration-150">
          <RefreshCw size={12} /> Retry
        </button>
      </div>
    </div>
  )
}

// ── Tier divider ───────────────────────────────────────────────────────────────

function TierHeader({ label, count, color }: { label: string; count: number; color: string }) {
  return (
    <div className="flex items-center gap-3 my-2">
      <span className={`text-xs font-semibold ${color}`}>{label}</span>
      <span className="text-xs text-slate-400">{count} candidate{count !== 1 ? 's' : ''}</span>
      <div className="flex-1 h-px bg-slate-100" />
    </div>
  )
}

// ── Job description accordion ──────────────────────────────────────────────────

function JobDescAccordion({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="card overflow-hidden">
      <button onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-50 transition-colors duration-150">
        <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest flex items-center gap-2">
          <Briefcase size={12} /> Job description
        </span>
        {open ? <ChevronUp size={13} className="text-slate-300" /> : <ChevronDown size={13} className="text-slate-300" />}
      </button>
      {open && (
        <div className="px-4 pb-4 pt-0 border-t border-slate-100 animate-slide-down">
          <p className="text-xs text-slate-600 leading-relaxed whitespace-pre-wrap font-sans pt-3">{text}</p>
        </div>
      )}
    </div>
  )
}

// ── Main App ───────────────────────────────────────────────────────────────────

export default function App() {
  const [jobTitle,          setJobTitle]          = useState('')
  const [notes,             setNotes]             = useState('')
  const [status,            setStatus]            = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [phases,            setPhases]            = useState<PhaseEntry[]>([])
  const [fraction,          setFraction]          = useState(0)
  const [displayFraction,   setDisplayFraction]   = useState(0)
  const [liveElapsed,       setLiveElapsed]       = useState(0)
  const [remaining,         setRemaining]         = useState(54)
  const [icp,               setIcp]               = useState<ICP | null>(null)
  const [jobDesc,           setJobDesc]           = useState('')
  const [candidates,        setCandidates]        = useState<Candidate[]>([])
  const [queriesLog,        setQueriesLog]        = useState<string[]>([])
  const [minScore,          setMinScore]          = useState(5)
  const [cacheInfo,         setCacheInfo]         = useState<CacheInfo | null>(null)
  const [totalElapsed,      setTotalElapsed]      = useState(0)
  const [weakOpen,          setWeakOpen]          = useState(false)
  const [ratings,           setRatings]           = useState<Record<string, 'up' | 'down'>>({})
  const [loadedFromSave,    setLoadedFromSave]    = useState(false)
  const [currentSavedId,    setCurrentSavedId]    = useState<string | null>(null)
  const [savedSearches,     setSavedSearches]     = useState<SavedSearch[]>(() => readSaved())
  const [savePromptOpen,    setSavePromptOpen]    = useState(false)
  const [saveName,          setSaveName]          = useState('')
  const [selectedCandidate, setSelectedCandidate] = useState<Candidate | null>(null)
  const [usageStats,        setUsageStats]        = useState<Record<string, number> | null>(null)

  const esRef              = useRef<EventSource | null>(null)
  const startTimeRef       = useRef<number>(0)
  const pendingCandidates  = useRef<Candidate[]>([])

  useEffect(() => {
    try {
      const stored = localStorage.getItem(RATINGS_KEY)
      if (stored) setRatings(JSON.parse(stored))
    } catch {}
  }, [])

  // Restore last completed run on mount (survives HMR, page refreshes, closed tabs)
  useEffect(() => {
    try {
      const stored = localStorage.getItem(LAST_RUN_KEY)
      if (!stored) return
      const last = JSON.parse(stored)
      if (!last.candidates?.length) return
      if (Date.now() - last.saved_at > LAST_RUN_TTL) return
      setJobTitle(last.job_title || '')
      setNotes(last.additional_notes || '')
      setCandidates(last.candidates)
      setIcp(last.icp ?? null)
      setQueriesLog(last.queries || [])
      setTotalElapsed(last.total_elapsed || 0)
      setFraction(1)
      setDisplayFraction(1)
      setLoadedFromSave(true)
      setStatus('done')
    } catch {}
  }, [])

  useEffect(() => {
    fetch('/api/cache').then(r => r.json()).then(setCacheInfo).catch(() => {})
  }, [])

  // Live elapsed counter — ticks every second while running
  useEffect(() => {
    if (status !== 'running') return
    const id = setInterval(() => {
      if (startTimeRef.current)
        setLiveElapsed(Math.round((performance.now() - startTimeRef.current) / 1000))
    }, 1000)
    return () => clearInterval(id)
  }, [status])

  // Slowly inch displayFraction forward between SSE events (max 6% ahead of server)
  useEffect(() => {
    if (status === 'done')    { setDisplayFraction(1); return }
    if (status !== 'running') { setDisplayFraction(0); return }
    setDisplayFraction(fraction)
    const id = setInterval(() => {
      setDisplayFraction(prev => {
        const cap = Math.min(fraction + 0.06, 0.97)
        return prev < cap ? parseFloat((prev + 0.002).toFixed(3)) : prev
      })
    }, 400)
    return () => clearInterval(id)
  }, [status, fraction])

  // Auto-save every completed run to localStorage so a reload/HMR never loses results
  useEffect(() => {
    if (status !== 'done' || !candidates.length || loadedFromSave) return
    try {
      localStorage.setItem(LAST_RUN_KEY, JSON.stringify({
        job_title: jobTitle,
        additional_notes: notes,
        candidates,
        icp,
        queries: queriesLog,
        total_elapsed: totalElapsed,
        saved_at: Date.now(),
      }))
    } catch {}
  }, [status, candidates])

  const closeDrawer = useCallback(() => setSelectedCandidate(null), [])

  // ── Ratings ───────────────────────────────────────────────────────────────────

  const rate = (url: string, r: 'up' | 'down') => {
    setRatings(prev => {
      const next = { ...prev }
      if (prev[url] === r) delete next[url]; else next[url] = r
      try { localStorage.setItem(RATINGS_KEY, JSON.stringify(next)) } catch {}
      return next
    })
  }

  // ── Saved searches ────────────────────────────────────────────────────────────

  const defaultSaveName = () => {
    const d = new Date()
    return `${jobTitle} · ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`
  }

  const doSave = () => {
    const name  = saveName.trim() || defaultSaveName()
    const entry: SavedSearch = {
      id: crypto.randomUUID(),
      name,
      job_title: jobTitle,
      additional_notes: notes,
      candidates,
      icp,
      queries: queriesLog,
      strong_count: candidates.filter(c => c.overall_score >= 7).length,
      total_count: candidates.length,
      saved_at: Date.now(),
    }
    const next = [entry, ...savedSearches].slice(0, MAX_SAVED)
    try { writeSaved(next) } catch {}   // never let a storage error block the UI
    setSavedSearches(next)
    setCurrentSavedId(entry.id)
    setSavePromptOpen(false)
  }

  const deleteSavedSearch = (id: string) => {
    setSavedSearches(prev => {
      const next = prev.filter(s => s.id !== id)
      writeSaved(next)
      return next
    })
    if (currentSavedId === id) setCurrentSavedId(null)
  }

  const loadSavedSearch = (s: SavedSearch) => {
    esRef.current?.close()
    closeDrawer()
    setJobTitle(s.job_title)
    setNotes(s.additional_notes)
    setPhases([])
    setFraction(1)
    setDisplayFraction(1)
    setLiveElapsed(0)
    setRemaining(0)
    setIcp(s.icp)
    setJobDesc('')
    setCandidates(s.candidates)
    setQueriesLog(s.queries)
    setTotalElapsed(0)
    setWeakOpen(false)
    setSavePromptOpen(false)
    setCurrentSavedId(s.id)
    setLoadedFromSave(true)
    setStatus('done')
  }

  // ── Run control ───────────────────────────────────────────────────────────────

  const reset = () => {
    esRef.current?.close()
    pendingCandidates.current = []
    closeDrawer()
    try { localStorage.removeItem(LAST_RUN_KEY) } catch {}
    setPhases([])
    setFraction(0)
    setDisplayFraction(0)
    setLiveElapsed(0)
    setRemaining(54)
    setIcp(null)
    setJobDesc('')
    setCandidates([])
    setQueriesLog([])
    setTotalElapsed(0)
    setWeakOpen(false)
    setSavePromptOpen(false)
    setCurrentSavedId(null)
    setLoadedFromSave(false)
    setUsageStats(null)
    setStatus('idle')
  }

  const startRun = async (forceRefresh = false) => {
    if (!jobTitle.trim()) return
    reset()
    startTimeRef.current = performance.now()
    setStatus('running')

    let runId: string
    try {
      const res = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_title: jobTitle, additional_notes: notes, force_refresh: forceRefresh }),
      })
      runId = (await res.json()).run_id
    } catch {
      setStatus('error')
      return
    }

    const es = new EventSource(`/api/stream/${runId}`)
    esRef.current = es

    es.onmessage = (e) => {
      const ev = JSON.parse(e.data)
      if (ev.type === 'phase') {
        setPhases(p => [...p, ev as PhaseEntry])
        setFraction(ev.fraction ?? 0)
        setRemaining(ev.remaining ?? 0)
        if (ev.elapsed && Math.abs(ev.elapsed - Math.round((performance.now() - startTimeRef.current) / 1000)) > 5)
          startTimeRef.current = performance.now() - ev.elapsed * 1000
      } else if (ev.type === 'icp') {
        setIcp(ev.data)
      } else if (ev.type === 'job_description') {
        setJobDesc(ev.text)
      } else if (ev.type === 'candidates') {
        pendingCandidates.current = ev.data   // buffer until 'done'
      } else if (ev.type === 'queries') {
        setQueriesLog(ev.data)
      } else if (ev.type === 'done') {
        setTotalElapsed(ev.elapsed ?? liveElapsed)
        setCandidates(pendingCandidates.current)
        setFraction(1)
        setStatus('done')
        if (ev.usage) setUsageStats(ev.usage)
        es.close()
        fetch('/api/cache').then(r => r.json()).then(setCacheInfo).catch(() => {})
      } else if (ev.type === 'error') {
        setStatus('error'); es.close()
      } else if (ev.type === 'sentinel') {
        setCandidates(pendingCandidates.current)
        setFraction(1)
        setStatus(s => s === 'running' ? 'done' : s)
        es.close()
      }
    }
    es.onerror = () => { setStatus('error'); es.close() }
  }

  // ── Derived values ────────────────────────────────────────────────────────────

  const allSorted      = [...candidates].sort((a, b) => b.overall_score - a.overall_score)
  const strong         = allSorted.filter(c => c.overall_score >= 7)
  const good           = allSorted.filter(c => c.overall_score >= 5 && c.overall_score < 7)
  const weak           = allSorted.filter(c => c.overall_score >= 3 && c.overall_score < 5)
  const strongShown    = strong.filter(c => c.overall_score >= minScore)
  const goodShown      = good.filter(c => c.overall_score >= minScore)
  const weakShown      = weak.filter(c => c.overall_score >= minScore)
  const reviewedCount  = candidates.filter(c => ratings[c.url]).length
  const openSignals    = allSorted.filter(c => (c.availability_signals?.length ?? 0) > 0).length
  const aiMlCount      = allSorted.filter(c => /\b(AI|ML|machine learning|LLM|NLP|data science|GPT|deep learning|neural|reinforcement)\b/i.test((c.headline || '') + ' ' + (c.snippet || ''))).length
  const startupCount   = allSorted.filter(c => /\b(startup|series [abc]|scaleup|scale-up|founder|early.stage)\b/i.test((c.headline || '') + ' ' + (c.snippet || ''))).length

  const isActive       = (status === 'running' || status === 'done') && !loadedFromSave
  const showResults    = candidates.length > 0 && status === 'done'
  const currentSave    = savedSearches.find(s => s.id === currentSavedId)
  const savedOutdated  = currentSave ? daysAgo(currentSave.saved_at) >= 7 : false
  const savedDaysAgo   = currentSave ? daysAgo(currentSave.saved_at) : 0

  return (
    <div className="min-h-screen" style={{ background: '#F7F8FA' }}>
      <Header onNewSearch={reset} showNew={status !== 'idle'} />

      <main className="max-w-5xl mx-auto px-6 py-8 space-y-5">

        {/* Search form */}
        <div className="card p-6 space-y-4 animate-fade-in-up">
          {cacheInfo && <CacheBar info={cacheInfo} onRefresh={() => startRun(true)} />}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-widest mb-2">
                Job Title <span className="text-red-400 normal-case font-normal tracking-normal">required</span>
              </label>
              <input className="field" value={jobTitle} onChange={e => setJobTitle(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && startRun()} placeholder="e.g. Senior AI Engineer" />
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-widest mb-2">
                Additional notes <span className="text-slate-300 normal-case font-normal tracking-normal">optional</span>
              </label>
              <input className="field" value={notes} onChange={e => setNotes(e.target.value)}
                placeholder="Hard requirements, deal-breakers, extra context…" />
            </div>
          </div>
          <div className="flex items-center gap-3 pt-1">
            <button onClick={() => startRun()} disabled={!jobTitle.trim() || status === 'running'}
              className="btn-gold flex items-center gap-2 text-sm px-5 py-2.5 rounded-xl disabled:opacity-40 disabled:cursor-not-allowed">
              {status === 'running'
                ? <><Loader2 size={15} className="animate-spin" /> Searching…</>
                : <><Search size={15} /> Find Candidates</>}
            </button>
            {status === 'error' && (
              <span className="text-xs text-red-500 flex items-center gap-1.5">
                <AlertCircle size={13} /> Run failed — see below
              </span>
            )}
          </div>
        </div>

        {/* Saved searches */}
        <SavedSearchesList
          searches={savedSearches}
          onLoad={loadSavedSearch}
          onDelete={deleteSavedSearch}
          currentId={currentSavedId}
        />

        {status === 'idle' && <IdleHero />}
        {status === 'error' && <ErrorCard onRetry={() => startRun()} />}

        {/* Live progress layout */}
        {isActive && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
            <div className="space-y-4">
              <ProgressPanel
                phases={phases}
                displayFraction={displayFraction}
                liveElapsed={liveElapsed}
                remaining={remaining}
                status={status}
                usage={usageStats}
              />
              <SearchStrategyPanel queries={queriesLog} />
              {jobDesc && <JobDescAccordion text={jobDesc} />}
            </div>
            <div className="lg:col-span-2">
              {icp ? <ICPCard icp={icp} /> : <ICPSkeleton />}
            </div>
          </div>
        )}

        {/* Outdated saved search banner */}
        {loadedFromSave && savedOutdated && (
          <div className="flex items-center justify-between bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 animate-fade-in">
            <span className="text-xs text-amber-700">
              These results are {savedDaysAgo} days old — candidates or availability may have changed.
            </span>
            <button onClick={() => { setLoadedFromSave(false); startRun() }}
              className="flex items-center gap-1.5 text-xs font-semibold text-amber-800 hover:text-amber-900 transition-colors ml-4 shrink-0">
              Re-run search <ArrowRight size={12} />
            </button>
          </div>
        )}

        {/* Results — only rendered after 'done' event */}
        {showResults && (
          <div className="space-y-3 animate-fade-in-up">

            {/* Header */}
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div>
                <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2 flex-wrap">
                  {strong.length}
                  <span className="text-slate-400 font-normal text-base">strong match{strong.length !== 1 ? 'es' : ''}</span>
                  {good.length > 0 && <span className="text-slate-400 font-normal text-base">· {good.length} good</span>}
                  {currentSavedId && !savePromptOpen && (
                    <span className="text-[11px] font-medium px-2.5 py-0.5 rounded-full flex items-center gap-1"
                      style={{ background: 'rgba(232,184,75,0.15)', color: '#92610a', border: '1px solid rgba(232,184,75,0.3)' }}>
                      <Bookmark size={10} /> {currentSave?.name ?? 'Saved'}
                    </span>
                  )}
                </h2>
                <p className="text-xs text-slate-400 mt-0.5 flex items-center gap-3 flex-wrap">
                  <span>{candidates.length} candidates evaluated{totalElapsed > 0 && ` · ${totalElapsed}s`}</span>
                  {aiMlCount > 0 && <span>AI/ML: {aiMlCount}</span>}
                  {startupCount > 0 && <span>Startup: {startupCount}</span>}
                  {openSignals > 0 && <span>Open signals: {openSignals}</span>}
                  {reviewedCount > 0 && (
                    <span className="flex items-center gap-1 text-slate-500">
                      <CheckCircle size={11} className="text-emerald-500" />
                      {reviewedCount}/{candidates.length} reviewed
                    </span>
                  )}
                </p>
              </div>

              <div className="flex items-center gap-3 flex-wrap">
                {!loadedFromSave && !currentSavedId && (
                  savePromptOpen ? (
                    <div className="flex items-center gap-2 card px-3 py-1.5">
                      <input
                        className="text-xs text-slate-700 bg-transparent outline-none w-48"
                        value={saveName}
                        onChange={e => setSaveName(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') doSave(); if (e.key === 'Escape') setSavePromptOpen(false) }}
                        autoFocus
                        placeholder={defaultSaveName()}
                      />
                      <button onClick={doSave} className="btn-gold text-[11px] font-semibold px-2.5 py-1 rounded-lg shrink-0">Save</button>
                      <button onClick={() => setSavePromptOpen(false)} className="text-slate-300 hover:text-slate-500 transition-colors"><X size={13} /></button>
                    </div>
                  ) : (
                    <button
                      onClick={() => { setSaveName(defaultSaveName()); setSavePromptOpen(true) }}
                      className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 card px-3 py-2 transition-colors duration-150">
                      <Bookmark size={13} /> Save search
                    </button>
                  )
                )}
                <div className="flex items-center gap-3 card px-4 py-2">
                  <span className="text-[11px] text-slate-400 font-medium shrink-0">Min score</span>
                  <input type="range" min={0} max={10} value={minScore}
                    onChange={e => setMinScore(Number(e.target.value))} className="w-24" />
                  <span className="text-sm font-bold w-4 tabular-nums"
                    style={{ color: minScore >= 7 ? '#059669' : minScore >= 5 ? '#d97706' : '#ef4444' }}>
                    {minScore}
                  </span>
                </div>
              </div>
            </div>

            {/* Zero strong matches diagnostic */}
            {strong.length === 0 && candidates.length > 0 && (
              <div className="card p-5 animate-fade-in">
                <div className="flex gap-3">
                  <AlertCircle size={16} className="text-amber-500 shrink-0 mt-0.5" />
                  <div>
                    <h3 className="font-semibold text-slate-700 text-sm mb-1">No strong matches (score ≥ 7)</h3>
                    <p className="text-xs text-slate-400 leading-relaxed mb-3">
                      {candidates.length} candidates scored but none hit 7+.
                      {queriesLog.length > 0 && ` ${queriesLog.length} search strategies ran.`}
                      {' '}Try adding role-specific notes or use a broader title.
                    </p>
                    <div className="flex gap-2 flex-wrap">
                      <button onClick={() => startRun()}
                        className="text-xs text-amber-700 bg-amber-50 hover:bg-amber-100 px-3 py-1.5 rounded-lg font-medium transition-colors duration-150">
                        Re-run search
                      </button>
                      <button onClick={reset}
                        className="text-xs text-slate-500 hover:text-slate-700 border border-slate-200 px-3 py-1.5 rounded-lg transition-colors duration-150">
                        Try different title
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Strong */}
            {strongShown.length > 0 && (
              <div>
                <TierHeader label="Strong matches" count={strongShown.length} color="text-emerald-600" />
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {strongShown.map((c, i) => (
                    <CandidateCard key={c.url} c={c} index={i}
                      rating={ratings[c.url]} onRate={r => rate(c.url, r)}
                      onSelect={() => setSelectedCandidate(c)}
                      isSelected={selectedCandidate?.url === c.url}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* Good */}
            {goodShown.length > 0 && (
              <div>
                <TierHeader label="Good matches" count={goodShown.length} color="text-amber-600" />
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {goodShown.map((c, i) => (
                    <CandidateCard key={c.url} c={c} index={strongShown.length + i}
                      rating={ratings[c.url]} onRate={r => rate(c.url, r)}
                      onSelect={() => setSelectedCandidate(c)}
                      isSelected={selectedCandidate?.url === c.url}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* Weak / manual review */}
            {weakShown.length > 0 && (
              <div>
                <button onClick={() => setWeakOpen(v => !v)}
                  className="flex items-center gap-2 text-xs text-slate-400 hover:text-slate-600 transition-colors duration-150 py-2">
                  {weakOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                  <span className="font-semibold">{weakShown.length} to review manually</span>
                  <span className="text-slate-300">· scores 3–4 · collapsed by default</span>
                </button>
                {weakOpen && (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4 animate-fade-in-up">
                    {weakShown.map((c, i) => (
                      <CandidateCard key={c.url} c={c} index={strongShown.length + goodShown.length + i}
                        rating={ratings[c.url]} onRate={r => rate(c.url, r)}
                        onSelect={() => setSelectedCandidate(c)}
                        isSelected={selectedCandidate?.url === c.url}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Empty filter state */}
            {strongShown.length === 0 && goodShown.length === 0 && weakShown.length === 0 && (
              <div className="card p-12 text-center animate-fade-in">
                <div className="w-12 h-12 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-4">
                  <Search size={20} className="text-slate-300" />
                </div>
                <p className="text-sm font-semibold text-slate-500 mb-1">No candidates at score ≥ {minScore}</p>
                <p className="text-xs text-slate-400">Lower the minimum score filter to see more results</p>
              </div>
            )}
          </div>
        )}

      </main>

      {/* Drawer rendered outside main so it overlays the full viewport */}
      <CandidateDrawer candidate={selectedCandidate} onClose={closeDrawer} />
    </div>
  )
}
