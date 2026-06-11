export interface ICP {
  company_summary: string
  typical_roles: string[]
  typical_backgrounds: string[]
  common_company_types: string[]
  career_trajectory_patterns: string[]
  key_skills: string[]
  green_flags: string[]
  red_flags: string[]
  location_context: string
  seniority_range: string
  culture_notes: string
}

export interface CareerSnapshotItem {
  company: string
  role: string
  duration?: string
}

export interface Candidate {
  url: string
  name: string
  headline: string
  snippet: string
  role_fit_score: number
  culture_fit_score: number
  interest_score: number
  overall_score: number
  reasoning: string
  availability_signals: string[]
  outreach_hook?: string
  // Drawer fields — added in batch-scoring pass
  role_fit_explanation?: string
  culture_fit_explanation?: string
  interest_explanation?: string
  why_yes?: string[]
  why_no?: string[]
  career_snapshot?: CareerSnapshotItem[]
}

export interface PhaseEntry {
  phase: string
  message: string
  from_cache: boolean
  fraction: number
  elapsed: number
  remaining: number
}

export interface CacheInfo {
  has_employees: boolean
  employee_count: number
  employees_days_ago: number | null
  employees_fresh: boolean
  has_icp: boolean
  icp_days_ago: number | null
  icp_fresh: boolean
  role_count: number
}
