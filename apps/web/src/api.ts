export type ApiErrorPayload = {
  code?: string
  message?: string
  details?: Record<string, unknown>
}

const ERROR_MESSAGES: Record<string, string> = {
  gap_decision_context_dependency_mismatch:
    '事实或能力画像已经更新，当前决策画像基于旧版本，需要重新确认企业决策后才能继续使用决策层信息。',
  DECISION_CONTEXT_RECONFIRMATION_REQUIRED:
    '事实或能力画像已经更新，当前决策画像需要重新确认。系统没有自动修改任何长期决策。',
  PROFILE_SERVICE_UNAVAILABLE: '企业画像服务暂时不可用，请确认Python服务已经启动。',
  REQUEST_SCHEMA_INVALID: '提交内容格式不正确，请检查必填项和字段格式。',
  REVIEW_TASK_NOT_FOUND: '未找到对应的审核任务，请刷新页面后重试。',
  REVIEW_TASK_ALREADY_HANDLED: '这项内容已经处理，无需重复操作。页面将刷新为最新状态。',
  AGENT_RUN_NOT_FOUND: '未找到对应的Agent运行记录，请重新启动补全流程。',
  FACT_CANDIDATE_DUPLICATE: '该事实候选已经提交，系统没有重复创建审核任务。',
  PROFILE_HISTORY_VERSION_NOT_FOUND: '所选版本不存在，请从页面列出的已有版本中重新选择。',
  zhipu_network_error: '暂时无法连接智谱服务，请检查网络后重试。',
  zhipu_timeout: '智谱分析等待时间过长，请稍后重试。',
  zhipu_authentication_failed: '智谱服务密钥无效，请联系项目管理员检查配置。',
  zhipu_permission_denied: '当前智谱账号没有使用该模型的权限，请联系项目管理员。',
  zhipu_insufficient_balance: '智谱账号余额或可用额度不足，请联系项目管理员。',
  zhipu_rate_limited: '智能分析请求较多，请稍后再试。',
  zhipu_response_schema_invalid: '智能分析结果未能通过系统校验，请重新分析一次。',
  profile_service_timeout: '智能分析仍在处理中，请稍后重试或刷新页面查看结果。',
  DECISION_PREFERENCE_NO_CHANGE: '没有需要保存的经营偏好修改。',
  DECISION_PREFERENCE_INVALID: '经营偏好未能保存，请检查预算范围和填写内容。',
}

export class ApiError extends Error {
  readonly code: string
  readonly userMessage: string
  readonly technicalMessage: string
  readonly details: Record<string, unknown>
  readonly httpStatus: number

  constructor(args: {
    code: string
    userMessage: string
    technicalMessage: string
    details?: Record<string, unknown>
    httpStatus?: number
  }) {
    super(args.userMessage)
    this.name = 'ApiError'
    this.code = args.code
    this.userMessage = args.userMessage
    this.technicalMessage = args.technicalMessage
    this.details = args.details ?? {}
    this.httpStatus = args.httpStatus ?? 0
  }
}

export function toApiError(caught: unknown): ApiError {
  if (caught instanceof ApiError) return caught
  if (caught instanceof Error) {
    return new ApiError({
      code: 'CLIENT_REQUEST_FAILED',
      userMessage: '请求未能完成，请稍后重试。',
      technicalMessage: caught.message,
    })
  }
  return new ApiError({
    code: 'CLIENT_REQUEST_FAILED',
    userMessage: '请求未能完成，请稍后重试。',
    technicalMessage: String(caught),
  })
}

export async function api<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      headers: { 'content-type': 'application/json', ...(options.headers || {}) },
      ...options,
    })
  } catch (caught) {
    throw toApiError(caught)
  }

  let payload: {
    success?: boolean
    data?: T
    error?: ApiErrorPayload
    error_code?: string
    message?: string
    details?: Record<string, unknown>
    trace_id?: string
  }
  try {
    payload = await response.json()
  } catch (caught) {
    throw new ApiError({
      code: 'INVALID_API_RESPONSE',
      userMessage: '服务返回了无法识别的响应。',
      technicalMessage: caught instanceof Error ? caught.message : String(caught),
      httpStatus: response.status,
    })
  }

  if (!payload.success) {
    const code = payload.error_code || payload.error?.code || 'API_REQUEST_FAILED'
    const technicalMessage = payload.message || payload.error?.message || `HTTP ${response.status}`
    const error = new ApiError({
      code,
      userMessage: ERROR_MESSAGES[code] || '操作未完成，请查看技术详情或联系项目开发人员。',
      technicalMessage,
      details: payload.details ?? payload.error?.details ?? {},
      httpStatus: response.status,
    })
    console.error('[EnterpriseProfileApiError]', {
      code: error.code,
      technicalMessage: error.technicalMessage,
      details: error.details,
      httpStatus: error.httpStatus,
    })
    throw error
  }
  return payload.data as T
}


export type BidDataSourceMode = 'REAL_LOCAL_ENTERPRISE_REMOTE_PROJECT'
export type EligibilityState = 'PASS' | 'FAIL' | 'UNKNOWN'
export type BidDecisionState = 'GO' | 'CONDITIONAL_GO' | 'NO_GO' | 'INSUFFICIENT_DATA'
export type BidTaskStatus = 'RUNNING' | 'WAITING_USER_CONFIRMATION' | 'DECIDED' | 'NEEDS_HUMAN_REVIEW' | 'INSUFFICIENT_DATA'
export type ConfirmationAction = 'SUPPLY_AND_CONTINUE' | 'ACCEPT_CONDITIONS' | 'REJECT' | 'APPROVE_FINAL'

export interface SystemReadiness {
  ready: boolean
  checked_at: string
  checks: Array<{ name: string; status: 'PASS' | 'FAIL'; message: string; advice?: string }>
}

export interface BidAgentStatus {
  data_provider: 'real'
  data_source_mode: BidDataSourceMode
  company_profile_data_source: string
  enterprise_evaluation_data_source: string
  project_data_source: string
  production_project_data_available: boolean
  llm_provider: 'zhipu'
  llm_model?: string | null
  llm_model_version?: string | null
  llm_api_key_configured?: boolean
  llm_live_required?: boolean
  provider_registry: string[]
  network_used: boolean
  checkpointer: string
  competition_data_mode: 'DEMO'
  competition_data_warning: string
  score_rule_version: 'TEMP-BID-SCORE-V2'
}

export interface BidProjectOption {
  project_id: string
  project_name: string
  region: string
  industry: string
  budget: string | null
  bid_deadline: string | null
  bid_open_time: string | null
  time_field_note: string
  project_status: string
  buyer_name: string
  data_source: string
}

export interface InterpretedUserGoal {
  target_industries: string[]
  target_regions: string[]
  budget_preferences: { min_amount: string | number | null; max_amount: string | number | null }
  available_bid_team_slots: number
  risk_preferences: { level: 'LOW' | 'MEDIUM' | 'HIGH'; avoid_conditions: string[] }
  explicit_exclusions: string[]
  priority_factors: string[]
  specified_preferences: string[]
  current_task_constraints: Record<string, unknown>
}

export interface QualificationInputOption {
  label: string
  value: string | boolean | number
}

export interface QualificationInputField {
  key: string
  label: string
  input_type: 'text' | 'textarea' | 'date' | 'number' | 'select' | 'boolean'
  required: boolean
  placeholder: string
  help_text: string
  options: QualificationInputOption[]
  min_value?: number | null
}

export interface QualificationGap {
  project_id: string
  project_name: string
  requirement_id: string
  requirement_text: string
  requirement_category: string
  status: EligibilityState
  reason: string
  required_materials: string[]
  supplement_key: string | null
  supplement_allowed: boolean
  evidence_ids: string[]
  input_fields: QualificationInputField[]
  validation_rule: Record<string, unknown>
}

export interface BidProjectPage {
  items: BidProjectOption[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface HumanConfirmation {
  confirmation_id: string
  task_id: string
  thread_id: string
  confirmation_type: 'CRITICAL_UNKNOWN' | 'FINAL_DECISION'
  message: string
  affected_project_ids: string[]
  required_fields: string[]
  qualification_gaps: QualificationGap[]
  interpreted_user_goal: InterpretedUserGoal | null
  allowed_actions: ConfirmationAction[]
}

export interface CompetitionAssessment {
  intensity: 'LOW' | 'MEDIUM' | 'HIGH' | 'UNKNOWN'
  data_coverage: number
  confirmed_competitor_count: number
  potential_competitor_count: number
  summary: string
  data_is_demo: boolean
  data_warning: string | null
}

export interface WinOpportunity {
  status: 'AVAILABLE' | 'INSUFFICIENT_DATA' | 'NOT_APPLICABLE'
  opportunity_level: 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN' | 'NOT_APPLICABLE'
  probability: number | null
  probability_range: [number, number] | null
  confidence: 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN'
  data_coverage: number
  positive_factors: string[]
  negative_factors: string[]
  reason_codes: string[]
  model_version: string
}

export interface ProjectDecision {
  project_id: string
  project_name?: string | null
  project_version: string
  decision: BidDecisionState
  priority: string
  eligibility: EligibilityState
  strengths: string[]
  risks: string[]
  unknowns: string[]
  conditions: string[]
  competition_assessment: CompetitionAssessment | null
  win_opportunity: WinOpportunity | null
  recommended_actions: string[]
  evidence_ids: string[]
  valid_until: string
}

export interface ScoreItemDetail {
  name: string
  score: number
  max_score: number
  status: 'PASS' | 'UNKNOWN' | 'FAIL' | 'MATCH' | 'PARTIAL' | 'NO_MATCH' | 'INSUFFICIENT_DATA'
  rule: string
  reason: string
  data_sources: string[]
  missing_data: string[]
}

export interface ScoreDimensionDetail {
  name: string
  score: number
  max_score: number
  items: ScoreItemDetail[]
}

export interface ProjectComparison {
  project_id: string
  project_version: string
  eligibility: EligibilityState
  original_rank: number
  original_rank_score: number
  recommendation_score_breakdown: Record<string, number>
  capability_match_score: number
  competitive_intensity: 'LOW' | 'MEDIUM' | 'HIGH' | 'UNKNOWN'
  competition_score: number
  competition_data_coverage: number
  win_opportunity_level: string
  win_opportunity_score: number
  remaining_preparation_days: number
  preparation_time_score: number
  contract_risk_count: number
  contract_risk_score: number
  strategic_alignment_score: number
  resource_fit_score: number
  team_slots_required: number
  composite_score: number
  portfolio_rank: number | null
  selected_for_portfolio: boolean
  conflict_project_ids: string[]
  reasons: string[]
  score_rule_version: 'TEMP-BID-SCORE-V2'
  score_is_temporary: true
  temporary_score_breakdown: Record<string, number>
  score_details: ScoreDimensionDetail[]
  preference_match_score: number
  preference_match_level: 'HIGH' | 'MEDIUM' | 'LOW' | 'NOT_SPECIFIED'
  preference_reasons: string[]
  preference_excluded: boolean
  data_gaps: string[]
}

export interface BidDecisionResult {
  task_id: string
  thread_id: string
  company_id: string
  company_profile_version: string
  user_goal: string
  interpreted_user_goal: InterpretedUserGoal
  project_comparisons: ProjectComparison[]
  project_decisions: ProjectDecision[]
  portfolio_recommendation: string[]
  resource_conflicts: string[]
  pending_confirmation: HumanConfirmation | null
  status: 'WAITING_USER_CONFIRMATION' | 'DECIDED' | 'NEEDS_HUMAN_REVIEW' | 'INSUFFICIENT_DATA'
  generated_at: string
  fact_profile_version?: string | null
  capability_profile_version?: string | null
  decision_profile_version?: string | null
  evaluation_version?: string | null
  project_versions?: Record<string, string>
  competition_data_versions?: Record<string, string>
  model_version?: string | null
  prompt_version?: string | null
  valid_until?: string | null
  data_provider?: string
  score_rule_version: 'TEMP-BID-SCORE-V2'
  score_is_temporary: true
  decision_explanation: {
    comparison_summary: string
    portfolio_explanation: string
    evidence_ids: string[]
  }
}

export interface BidTaskRecord {
  task_id: string
  thread_id: string
  task_status: BidTaskStatus
  status: BidTaskStatus
  pending_confirmation?: HumanConfirmation
  result?: BidDecisionResult
}

export interface CompetitionResult {
  company_id: string
  project_id: string
  company_profile_version: string
  project_version: string
  competition_data_version: string
  confirmed_competitors: Array<{ company_id: string; company_name: string; basis: string; evidence_ids: string[] }>
  potential_competitors: Array<{ company_id: string; company_name: string; basis: string; evidence_ids: string[] }>
  confirmed_facts: Array<{ statement: string; evidence_ids: string[] }>
  inferences: Array<{ statement: string; based_on_evidence_ids: string[]; confidence: 'LOW' | 'MEDIUM' | 'HIGH' }>
  unknowns: string[]
  data_coverage: number
  competitive_intensity: 'LOW' | 'MEDIUM' | 'HIGH' | 'UNKNOWN'
  company_advantages: Array<{ statement: string; evidence_ids: string[] }>
  company_weaknesses: Array<{ statement: string; evidence_ids: string[] }>
  strategies: string[]
  evidence_ids: string[]
  status: 'COMPLETED' | 'NEEDS_HUMAN_REVIEW'
  model_version: string
  prompt_version: string
  generated_at: string
  valid_until: string
  data_is_demo: boolean
  data_warning: string | null
}

export interface CompetitionDetailResponse {
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'NEEDS_HUMAN_REVIEW' | 'FAILED' | 'STALE'
  stale_reason: string | null
  recompute_required: boolean
  task_id: string | null
  thread_id: string | null
  data_provider: string
  project: {
    project_id: string
    project_version: string
    project_name: string
    buyer_name: string
    region: string
    industry: string
    budget: string | number | null
    bid_deadline: string | null
    bid_open_time: string | null
    time_field_note: string | null
    project_status: string
  }
  company: {
    company_id: string
    company_name: string | null
    company_profile_version: string
    qualification_status: EligibilityState | null
    core_capabilities: string[]
    win_opportunity_level: string | null
  }
  analysis: CompetitionResult | null
  generated_at: string
}

type PublicCompetitionResult = Record<string, unknown>

function normalizeCompetitionParty(value: unknown): CompetitionResult['confirmed_competitors'][number] {
  const item = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>
  return {
    company_id: String(item.company_id ?? item.companyId ?? ''),
    company_name: String(item.company_name ?? item.companyName ?? ''),
    basis: String(item.basis ?? ''),
    evidence_ids: Array.isArray(item.evidence_ids ?? item.evidenceIds)
      ? (item.evidence_ids ?? item.evidenceIds) as string[]
      : [],
  }
}

function normalizeCompetitionStatement(value: unknown): { statement: string; evidence_ids: string[] } {
  const item = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>
  return {
    statement: String(item.statement ?? ''),
    evidence_ids: Array.isArray(item.evidence_ids ?? item.evidenceIds)
      ? (item.evidence_ids ?? item.evidenceIds) as string[]
      : [],
  }
}

function normalizeCompetitionInference(value: unknown): CompetitionResult['inferences'][number] {
  const item = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>
  const evidence = item.based_on_evidence_ids ?? item.basedOnEvidenceIds
  return {
    statement: String(item.statement ?? ''),
    based_on_evidence_ids: Array.isArray(evidence) ? evidence as string[] : [],
    confidence: String(item.confidence ?? 'LOW') as CompetitionResult['inferences'][number]['confidence'],
  }
}

function normalizeCompetitionResult(value: PublicCompetitionResult): CompetitionResult {
  const confirmed = value.confirmed_competitors ?? value.confirmedCompetitors
  const potential = value.potential_competitors ?? value.potentialCompetitors
  const advantages = value.company_advantages ?? value.companyAdvantages
  const weaknesses = value.company_weaknesses ?? value.companyWeaknesses
  const confirmedFacts = value.confirmed_facts ?? value.confirmedFacts
  const evidence = value.evidence_ids ?? value.evidenceIds
  return {
    company_id: String(value.company_id ?? value.companyId ?? ''),
    project_id: String(value.project_id ?? value.projectId ?? ''),
    company_profile_version: String(value.company_profile_version ?? value.companyProfileVersion ?? ''),
    project_version: String(value.project_version ?? value.projectVersion ?? ''),
    competition_data_version: String(value.competition_data_version ?? value.competitionDataVersion ?? ''),
    confirmed_competitors: Array.isArray(confirmed) ? confirmed.map(normalizeCompetitionParty) : [],
    potential_competitors: Array.isArray(potential) ? potential.map(normalizeCompetitionParty) : [],
    confirmed_facts: Array.isArray(confirmedFacts) ? confirmedFacts.map(normalizeCompetitionStatement) : [],
    inferences: Array.isArray(value.inferences) ? value.inferences.map(normalizeCompetitionInference) : [],
    unknowns: Array.isArray(value.unknowns) ? value.unknowns as string[] : [],
    data_coverage: Number(value.data_coverage ?? value.dataCoverage ?? 0),
    competitive_intensity: String(value.competitive_intensity ?? value.competitiveIntensity ?? 'UNKNOWN') as CompetitionResult['competitive_intensity'],
    company_advantages: Array.isArray(advantages) ? advantages.map(normalizeCompetitionStatement) : [],
    company_weaknesses: Array.isArray(weaknesses) ? weaknesses.map(normalizeCompetitionStatement) : [],
    strategies: Array.isArray(value.strategies) ? value.strategies as string[] : [],
    evidence_ids: Array.isArray(evidence) ? evidence as string[] : [],
    status: String(value.status ?? 'COMPLETED') as CompetitionResult['status'],
    model_version: String(value.model_version ?? value.modelVersion ?? ''),
    prompt_version: String(value.prompt_version ?? value.promptVersion ?? ''),
    generated_at: String(value.generated_at ?? value.generatedAt ?? ''),
    valid_until: String(value.valid_until ?? value.validUntil ?? ''),
    data_is_demo: Boolean(value.data_is_demo ?? value.dataIsDemo ?? false),
    data_warning: (value.data_warning ?? value.dataWarning ?? null) as string | null,
  }
}

export function getSystemReadiness(): Promise<SystemReadiness> {
  return api<SystemReadiness>('/api/system/readiness')
}

export function getBidAgentStatus(): Promise<BidAgentStatus> {
  return api<BidAgentStatus>('/api/bid-agent/status')
}

export function getBidProjects(query = '', page = 1, pageSize = 5): Promise<BidProjectPage> {
  const params = new URLSearchParams({ query, page: String(page), page_size: String(pageSize) })
  return api<BidProjectPage>(`/api/bid-agent/projects?${params}`)
}

export function createBidDecision(input: {
  task_id: string
  thread_id: string
  company_id: string
  user_goal: string
  requested_project_ids: string[]
  resource_constraints?: Record<string, unknown>
  as_of_time: string
}): Promise<BidTaskRecord> {
  return api<BidTaskRecord>('/api/bid-decisions', { method: 'POST', body: JSON.stringify(input) })
}

export function getBidDecision(taskId: string): Promise<BidTaskRecord> {
  return api<BidTaskRecord>(`/api/bid-decisions/${encodeURIComponent(taskId)}`)
}

export function resumeBidDecision(taskId: string, input: {
  confirmation_id: string
  task_id: string
  thread_id: string
  action: ConfirmationAction
  provided_fields?: Record<string, unknown>
  comment?: string
}): Promise<BidTaskRecord> {
  return api<BidTaskRecord>(`/api/bid-decisions/${encodeURIComponent(taskId)}/resume`, {
    method: 'POST', body: JSON.stringify(input),
  })
}

export async function analyzeCompetition(input: {
  company_id: string
  project_id: string
  company_profile_version: string
  project_version: string
  as_of_time: string
}): Promise<CompetitionResult> {
  const result = await api<PublicCompetitionResult>('/api/competition/analyze', {
    method: 'POST', body: JSON.stringify(input),
  })
  return normalizeCompetitionResult(result)
}

export async function getCompetition(companyId: string, projectId: string): Promise<CompetitionResult> {
  const result = await api<PublicCompetitionResult>(
    `/api/competition/${encodeURIComponent(companyId)}/${encodeURIComponent(projectId)}`,
  )
  return normalizeCompetitionResult(result)
}

function normalizeCompetitionDetail(value: unknown): CompetitionDetailResponse {
  const source = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>
  const projectSource = (source.project && typeof source.project === 'object' ? source.project : {}) as Record<string, unknown>
  const companySource = (source.company && typeof source.company === 'object' ? source.company : {}) as Record<string, unknown>
  const rawAnalysis = source.analysis
  const coreCapabilities = companySource.core_capabilities ?? companySource.coreCapabilities
  return {
    status: String(source.status ?? 'PENDING') as CompetitionDetailResponse['status'],
    stale_reason: (source.stale_reason ?? source.staleReason ?? null) as string | null,
    recompute_required: Boolean(source.recompute_required ?? source.recomputeRequired ?? false),
    task_id: (source.task_id ?? source.taskId ?? null) as string | null,
    thread_id: (source.thread_id ?? source.threadId ?? null) as string | null,
    data_provider: String(source.data_provider ?? source.dataProvider ?? 'REAL_PROJECT_WITH_DEMO_COMPETITION'),
    project: {
      project_id: String(projectSource.project_id ?? projectSource.projectId ?? ''),
      project_version: String(projectSource.project_version ?? projectSource.projectVersion ?? ''),
      project_name: String(projectSource.project_name ?? projectSource.projectName ?? ''),
      buyer_name: String(projectSource.buyer_name ?? projectSource.buyerName ?? ''),
      region: String(projectSource.region ?? ''),
      industry: String(projectSource.industry ?? ''),
      budget: (projectSource.budget ?? null) as string | number | null,
      bid_deadline: (projectSource.bid_deadline ?? projectSource.bidDeadline ?? null) as string | null,
      bid_open_time: (projectSource.bid_open_time ?? projectSource.bidOpenTime ?? null) as string | null,
      time_field_note: (projectSource.time_field_note ?? projectSource.timeFieldNote ?? null) as string | null,
      project_status: String(projectSource.project_status ?? projectSource.projectStatus ?? ''),
    },
    company: {
      company_id: String(companySource.company_id ?? companySource.companyId ?? ''),
      company_name: (companySource.company_name ?? companySource.companyName ?? null) as string | null,
      company_profile_version: String(companySource.company_profile_version ?? companySource.companyProfileVersion ?? ''),
      qualification_status: (companySource.qualification_status ?? companySource.qualificationStatus ?? null) as EligibilityState | null,
      core_capabilities: Array.isArray(coreCapabilities) ? coreCapabilities as string[] : [],
      win_opportunity_level: (companySource.win_opportunity_level ?? companySource.winOpportunityLevel ?? null) as string | null,
    },
    analysis: rawAnalysis && typeof rawAnalysis === 'object' ? normalizeCompetitionResult(rawAnalysis as PublicCompetitionResult) : null,
    generated_at: String(source.generated_at ?? source.generatedAt ?? ''),
  }
}

export async function getCompetitionDetail(input: {
  companyId: string
  projectId: string
  companyProfileVersion?: string
  projectVersion?: string
  competitionDataVersion?: string
  taskId?: string
  threadId?: string
  ensureAnalysis?: boolean
}): Promise<CompetitionDetailResponse> {
  const query = new URLSearchParams()
  if (input.companyProfileVersion) query.set('company_profile_version', input.companyProfileVersion)
  if (input.projectVersion) query.set('project_version', input.projectVersion)
  if (input.competitionDataVersion) query.set('competition_data_version', input.competitionDataVersion)
  if (input.taskId) query.set('task_id', input.taskId)
  if (input.threadId) query.set('thread_id', input.threadId)
  query.set('ensure_analysis', input.ensureAnalysis === false ? 'false' : 'true')
  const result = await api<unknown>(
    `/api/competition/${encodeURIComponent(input.companyId)}/${encodeURIComponent(input.projectId)}/detail?${query.toString()}`,
  )
  return normalizeCompetitionDetail(result)
}
