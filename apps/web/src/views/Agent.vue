<template>
  <section>
    <header v-if="!embedded" class="page-header">
      <div><span class="eyebrow">画像助手</span><h2>逐步完善企业信息</h2>
      <p>助手只询问当前真正需要的信息。你可以回答、暂时跳过，重要变化会在写入前请你确认。</p></div>
    </header>
    <ErrorPanel :error="error" />

    <div class="agent-start card">
      <div><h3>选择要完成的事情</h3><p>两个功能默认收起。打开其中一个时，另一个会自动收起。</p></div>
      <div class="button-row">
        <button :class="{secondary:selectedTool==='check'}" :disabled="processingLocked" @click="toggleTool('check')">资料检查</button>
        <button :class="{secondary:selectedTool==='decision'}" :disabled="processingLocked" @click="toggleTool('decision')">修改决策画像</button>
      </div>
    </div>
    <section v-if="selectedTool==='check'" class="card assistant-tool-panel">
      <div><h3>检查事实与能力资料</h3><p>发现事实画像和能力画像缺少的关键信息，不会询问或修改经营偏好。</p></div>
      <button v-if="!targetedCapability" :disabled="processingLocked" @click="startInteractive">{{ processingLocked ? '正在处理，请稍候…' : '开始检查' }}</button>
    </section>
    <section v-if="selectedTool==='check' && targetedCapability==='personnel_resource_capability'" class="card targeted-capability-form">
      <div>
        <span class="question-label">人员和资源能力</span>
        <h3>补充人员规模信息</h3>
        <p>填写当前能够确认的数据。保存后会直接更新事实画像和人员资源能力，不需要再进行一次通用检查。</p>
      </div>
      <div class="profile-input-grid">
        <label>员工人数
          <input v-model.number="personnelDraft.employeeCount" type="number" min="0" placeholder="例如：120" />
        </label>
        <label>社保缴纳人数
          <input v-model.number="personnelDraft.socialInsuranceCount" type="number" min="0" placeholder="例如：105" />
        </label>
        <label class="wide">统计口径
          <input v-model.trim="personnelDraft.countScope" placeholder="例如：截至本年度6月底的在册员工" />
        </label>
        <label class="wide">证明材料或信息来源（必填）
          <input v-model.trim="personnelDraft.sourceDescription" placeholder="例如：员工花名册、社保缴纳记录或企业确认数据" />
        </label>
      </div>
      <button type="button" :disabled="busy" @click="submitPersonnel">
        {{ busy ? '正在保存并更新画像…' : '保存并更新人员资源能力' }}
      </button>
      <div v-if="submissionFeedback" class="success submission-feedback">{{ submissionFeedback }}</div>
    </section>
    <DecisionPreferencesForm
      v-if="selectedTool==='decision'"
      :company-id="companyId"
      auto-edit
      @saved="handleDecisionSaved"
    />
    <div v-if="!embedded" id="profile-check-result"></div>

    <div v-if="capabilityUnavailableMessage" class="notice">{{ capabilityUnavailableMessage }}</div>

    <div v-if="run && selectedTool==='check' && !targetedCapability" class="card profile-check-result">
      <div class="run-heading"><div><h3>{{ statusName(run.workflow_state || run.status) }}</h3></div></div>
      <div v-if="submissionFeedback" class="success submission-feedback">{{ submissionFeedback }}</div>

      <template v-if="run.waiting_for === 'responses' && !processingLocked">
        <h3>请补充关键信息</h3>
        <div v-for="question in run.question_plan?.question_items || []" :key="question.question_item_id" class="card">
          <span class="question-label">问题</span><h3>{{ questionText(question) }}</h3>
          <label>我现在可以
            <select v-model="answers[question.question_item_id].kind">
              <option value="provide">提供这项信息</option>
              <option value="unable">暂时无法提供，稍后再补</option>
            </select>
          </label>
          <template v-if="answers[question.question_item_id].kind === 'provide'">
            <div v-if="question.target_code === 'performance'" class="profile-input-grid">
              <label>项目名称（必填）<input v-model.trim="answers[question.question_item_id].projectName" placeholder="例如：某某信息化平台建设项目" /></label>
              <label>项目地区（必填）<input v-model.trim="answers[question.question_item_id].region" placeholder="例如：山西省太原市" /></label>
              <label>采购人<input v-model.trim="answers[question.question_item_id].buyerName" placeholder="可不填" /></label>
              <label>所属行业<input v-model.trim="answers[question.question_item_id].industry" placeholder="例如：政务信息化" /></label>
              <label>合同金额（元）<input v-model.number="answers[question.question_item_id].amount" type="number" min="0" placeholder="可不填" /></label>
              <label>开始日期<input v-model="answers[question.question_item_id].startDate" type="date" /></label>
              <label>完成日期<input v-model="answers[question.question_item_id].endDate" type="date" /></label>
              <label class="wide">项目内容（必填）<textarea v-model.trim="answers[question.question_item_id].scope" placeholder="说明企业在项目中实际完成的工作" /></label>
              <label class="wide">证明材料或信息来源（必填）<input v-model.trim="answers[question.question_item_id].sourceDescription" placeholder="例如：项目合同、验收报告，或企业确认的项目记录" /></label>
            </div>
            <textarea
              v-else-if="question.information_request_type !== 'decision_confirmation'"
              v-model="answers[question.question_item_id].text"
              :placeholder="question.information_request_type === 'conflict_clarification' ? '请说明哪项信息准确，或填写来源编号' : '请填写相关信息'"
            />
            <label v-if="question.target_code !== 'performance' && question.information_request_type !== 'decision_confirmation'">
              证明材料或信息来源（必填）
              <input v-model.trim="answers[question.question_item_id].sourceDescription" placeholder="例如：企业资质文件、内部记录或企业确认信息" />
            </label>
            <div v-else-if="question.target_code === 'budget_preference'" class="grid">
              <label>预算下限（元，可空）<input v-model.number="answers[question.question_item_id].minimum" type="number" /></label>
              <label>预算上限（元，可空）<input v-model.number="answers[question.question_item_id].maximum" type="number" /></label>
            </div>
            <label v-else-if="question.target_code === 'consortium_acceptance'">
              是否接受联合体投标
              <select v-model="answers[question.question_item_id].text">
                <option value="是">是</option>
                <option value="否">否</option>
              </select>
            </label>
            <label v-else-if="question.target_code === 'risk_preference'">
              风险偏好
              <select v-model="answers[question.question_item_id].text">
                <option value="conservative">谨慎</option>
                <option value="balanced">均衡</option>
                <option value="aggressive">积极</option>
              </select>
            </label>
            <label v-else-if="question.target_code === 'max_concurrent_projects'">
              可同时承担的项目数量
              <input v-model.number="answers[question.question_item_id].minimum" type="number" min="1" />
            </label>
            <textarea
              v-else
              v-model="answers[question.question_item_id].text"
              :placeholder="decisionAnswerPlaceholder(question.target_code)"
            />
            <details v-if="question.expected_response?.material_allowed" class="material-details">
              <summary>添加证明材料（可选）</summary>
              <fieldset>
                <label>材料编号<input v-model="answers[question.question_item_id].materialId" /></label>
                <label>材料名称<input v-model="answers[question.question_item_id].filename" /></label>
                <label>材料格式<input v-model="answers[question.question_item_id].mediaType" /></label>
                <label>材料校验码<input v-model="answers[question.question_item_id].sha256" /></label>
                <label>文件大小（字节）<input v-model.number="answers[question.question_item_id].sizeBytes" type="number" min="1" /></label>
              </fieldset>
            </details>
          </template>
        </div>
        <button :disabled="busy" @click="submitAnswers">{{ busy ? '正在提交…' : '提交本轮回答' }}</button>
      </template>

      <section v-if="run.waiting_for !== 'responses' && missingPerformanceDetails.length" class="performance-supplement">
        <h3>继续补充项目关键信息</h3>
        <p>下面的信息仍会影响能力画像。填写后会立即更新事实画像、能力画像和企业评价。</p>
        <div class="profile-input-grid">
          <label v-if="missingPerformanceDetails.includes('buyer')">
            采购人
            <input v-model.trim="performanceSupplement.buyerName" placeholder="请输入该项目的采购人名称" />
          </label>
          <label v-if="missingPerformanceDetails.includes('amount')">
            合同金额（元）
            <input v-model.number="performanceSupplement.amount" type="number" min="0" placeholder="请输入合同金额" />
          </label>
          <label class="wide">
            证明材料或信息来源（必填）
            <input v-model.trim="performanceSupplement.sourceDescription" placeholder="例如：项目合同、验收报告或企业确认记录" />
          </label>
        </div>
        <button type="button" :disabled="busy" @click="submitPerformanceSupplement">
          {{ busy ? '正在保存并更新画像…' : '保存补充信息并更新画像' }}
        </button>
      </section>

    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useEnterpriseContextStore } from '../enterpriseContext'
import { api, type ApiError, toApiError } from '../api'
import ErrorPanel from '../components/ErrorPanel.vue'
import DecisionPreferencesForm from '../components/DecisionPreferencesForm.vue'
import { TARGET_NAMES, type GapInventory } from '../profileUi'

defineProps<{ embedded?: boolean }>()
const emit = defineEmits<{
  (event: 'show-profile' | 'open-evaluation' | 'profile-updated'): void
}>()

type QuestionItem = {
  question_item_id: string
  target_layer: string
  target_code: string
  reason_code: string
  information_request_type: string
  expected_response?: { material_allowed?: boolean }
}
type ProviderStatus = {
  provider: string
  configured: boolean
  api_key_configured: boolean
  model?: string | null
  network_provider: boolean
}
type RunState = Record<string, unknown> & {
  run_id: string
  status: string
  workflow_state?: string
  waiting_for?: string | null
  round_index?: number
  max_rounds?: number
  current_node?: string
  termination_reason?: string | null
  gap_inventory?: GapInventory
  remaining_gap_inventory?: GapInventory
  question_plan?: { question_items?: QuestionItem[]; plan_summary?: Record<string, number> }
  processing_worklist?: { processing_items?: Array<Record<string, unknown>> }
  pending_review_items?: Array<Record<string, unknown>>
  llm_node_executed?: boolean
  llm_semantic_candidates?: Record<string, unknown>
  warnings?: Array<{ code?: string; message?: string }>
  trace?: Array<Record<string, string>>
  decision_context_status?: Record<string, unknown> & { is_stale?: boolean }
}
type AnswerState = {
  kind: 'provide' | 'unable'; text: string; minimum: number | null; maximum: number | null
  materialId: string; filename: string; mediaType: string; sha256: string; sizeBytes: number
  projectName: string; buyerName: string; industry: string; region: string; amount: number | null
  startDate: string; endDate: string; scope: string; sourceDescription: string
}

const enterpriseContext = useEnterpriseContextStore()
const route = useRoute()
const { normalizedCompanyId: companyId } = storeToRefs(enterpriseContext)
const run = ref<RunState | null>(null)
const latestGaps = ref<GapInventory | null>(null)
const error = ref<ApiError | null>(null)
const busy = ref(false)
const capabilityBusy = ref(false)
const answers = ref<Record<string, AnswerState>>({})
const providerStatus = ref<ProviderStatus | null>(null)
const selectedCapabilityTypes = ref<string[]>([
  'industry_capability','technical_capability','similar_performance_capability','regional_delivery_capability',
  'amount_experience_capability','personnel_resource_capability','buyer_relationship_capability','tender_performance_capability',
])
const submissionFeedback = ref('')
const performanceSupplement = ref({ buyerName: '', amount: null as number | null, sourceDescription: '' })
const personnelDraft = ref({
  employeeCount: null as number | null,
  socialInsuranceCount: null as number | null,
  countScope: '',
  sourceDescription: '',
})
const profileOutput = ref<any>(null)
const evaluationOutput = ref<any>(null)
const capabilityUnavailableMessage = ref('')
type AssistantTool = 'check' | 'decision' | null
const selectedTool = ref<AssistantTool>(
  route.query.action === 'decision' ? 'decision' : route.query.action === 'check' || route.query.capability ? 'check' : null,
)
const targetedCapability = computed(() => String(route.query.capability || ''))
watch(
  () => [route.query.action, route.query.capability],
  ([action, capability]) => {
    if (action === 'decision') selectedTool.value = 'decision'
    else if (action === 'check' || capability) selectedTool.value = 'check'
  },
)
const processingLocked = computed(() => busy.value || capabilityBusy.value || run.value?.status === 'RUNNING')

const providerReady = computed(() =>
  providerStatus.value?.provider === 'zhipu'
  && providerStatus.value.configured
  && providerStatus.value.api_key_configured
  && providerStatus.value.model === 'glm-5.2',
)
const profileFactCount = computed(() => profileOutput.value?.fact_profile?.facts?.length || 0)
const supportedCapabilityCount = computed(() =>
  profileOutput.value?.capability_profile?.capability_domains?.filter(
    (item: any) => ['supported', 'partially_supported'].includes(item.support_status),
  ).length || 0,
)
const confirmedDecisionCount = computed(() =>
  profileOutput.value?.decision_profile?.decision_fields?.filter(
    (item: any) => item.confirmation_status === 'confirmed',
  ).length || 0,
)
const evaluationScore = computed(() => {
  const score = evaluationOutput.value?.summary?.active_44_normalized_observed_score
  return typeof score === 'number' ? `${score.toFixed(1)} 分` : '尚未评价'
})
const performanceFacts = computed(() =>
  (profileOutput.value?.fact_profile?.facts || []).filter((item:any) => item.fact_type === 'performance' && item.fact_status !== 'inactive'),
)
const missingPerformanceDetails = computed<Array<'buyer'|'amount'>>(() => {
  if (!performanceFacts.value.length) return []
  const missing:Array<'buyer'|'amount'> = []
  if (!performanceFacts.value.some((item:any) => Boolean(item.payload?.buyer_name))) missing.push('buyer')
  if (!performanceFacts.value.some((item:any) => item.payload?.contract_amount?.normalized_value != null)) missing.push('amount')
  return missing
})
const evaluationHint = computed(() =>
  evaluationOutput.value
    ? '按当前启用的 44 项指标形成的观察结果'
    : '可前往企业评价生成，不影响画像使用',
)
function targetName(code: string): string { return TARGET_NAMES[code] ?? '企业信息' }
function friendlyWarning(warning?: { code?: string; message?: string }): string {
  const messages: Record<string, string> = {
    zhipu_api_key_missing: '尚未配置智谱服务密钥。',
    zhipu_model_missing: '尚未配置智谱模型。',
    zhipu_network_error: '当前运行环境无法连接智谱服务。请确认网络可用，并从启动脚本重新启动系统后重试。',
  }
  return messages[warning?.code || ''] || warning?.message || '请检查智谱服务配置后重试。'
}
function statusName(value?: string): string { return ({ RUNNING:'正在整理企业画像',WAITING_USER_INPUT:'等待你补充信息',WAITING_REVIEW:'部分异常信息已被隔离',COMPLETED:'企业画像已经更新',COMPLETED_WITH_PENDING_REVIEW:'本轮处理已经完成',DECISION_CONTEXT_RECONFIRMATION_REQUIRED:'经营偏好需要重新确认',FAILED:'运行遇到问题' } as Record<string,string>)[value || ''] || '画像处理中' }
function questionText(question: QuestionItem): string {
  const name=targetName(question.target_code)
  if(question.information_request_type==='decision_confirmation') return `请确认企业的${name}`
  if(question.information_request_type==='conflict_clarification') return `关于${name}存在不同记录，请确认哪项准确`
  if(question.information_request_type==='capability_clarification') return `请补充能够说明“${name}”的情况`
  return `请补充企业的${name}`
}
function setError(caught: unknown): void { error.value = toApiError(caught) }
function initAnswers(): void {
  answers.value = {}
  for (const question of run.value?.question_plan?.question_items ?? []) {
    answers.value[question.question_item_id] = {
      kind: 'provide',
      text: question.target_code === 'consortium_acceptance' ? '是' : question.target_code === 'risk_preference' ? 'balanced' : '',
      minimum: null, maximum: null,
      materialId: '', filename: '', mediaType: 'application/json', sha256: '', sizeBytes: 1,
      projectName: '', buyerName: '', industry: '', region: '', amount: null,
      startDate: '', endDate: '', scope: '', sourceDescription: '',
    }
  }
}
async function refreshLatestGaps(): Promise<void> {
  try { latestGaps.value = await api<GapInventory>(`/api/companies/${companyId.value}/gaps`) }
  catch (caught) { setError(caught) }
}
async function loadOutputCard(): Promise<void> {
  try {
    profileOutput.value = await api(`/api/companies/${encodeURIComponent(companyId.value)}/profile`)
  } catch (caught) {
    setError(caught)
  }
  try {
    evaluationOutput.value = await api(`/api/companies/${encodeURIComponent(companyId.value)}/evaluation/latest`)
  } catch (caught) {
    const parsed = toApiError(caught)
    if (parsed.httpStatus === 404) evaluationOutput.value = null
    else setError(caught)
  }
}
async function handleProfileUpdated(): Promise<void> {
  await loadOutputCard()
  emit('profile-updated')
}
async function handleDecisionSaved(): Promise<void> {
  submissionFeedback.value = '经营偏好已保存，并已更新到企业画像。'
  await handleProfileUpdated()
}
function toggleTool(tool: Exclude<AssistantTool, null>): void {
  selectedTool.value = selectedTool.value === tool ? null : tool
}
async function startInteractive(): Promise<void> {
  if (processingLocked.value) return
  submissionFeedback.value = ''
  capabilityUnavailableMessage.value = ''
  busy.value = true; error.value = null
  try {
    run.value = await api<RunState>('/api/agent/runs', { method: 'POST', body: JSON.stringify({ company_id: companyId.value, mode: 'interactive', max_questions_per_batch: 3, include_optional: false, max_rounds: 5, decision_context_policy: 'PAUSE_DECISION_LAYER', include_decision_questions: false }) })
    initAnswers(); await refreshLatestGaps()
    if (!['responses','decision_selection'].includes(run.value?.waiting_for || '')) {
      await loadOutputCard()
      submissionFeedback.value=missingPerformanceDetails.value.length
        ? '本轮资料检查已完成。请继续填写下方仍缺少的项目关键信息。'
        : '企业资料已补充完整，画像已按现有信息更新。'
    }
  } catch (caught) { setError(caught) } finally { busy.value = false }
}
async function submitPerformanceSupplement():Promise<void>{
  if(!performanceSupplement.value.sourceDescription){
    submissionFeedback.value='请填写证明材料或信息来源。'
    return
  }
  const hasNewValue =
    (missingPerformanceDetails.value.includes('buyer') && Boolean(performanceSupplement.value.buyerName))
    || (missingPerformanceDetails.value.includes('amount') && performanceSupplement.value.amount != null)
  if(!hasNewValue){
    submissionFeedback.value='请至少填写采购人或合同金额。'
    return
  }
  busy.value=true
  error.value=null
  submissionFeedback.value='正在保存补充信息并更新画像与评价，请稍候…'
  try{
    await api(`/api/companies/${encodeURIComponent(companyId.value)}/profile-inputs/performance-supplement`,{
      method:'POST',
      body:JSON.stringify({
        buyer_name:performanceSupplement.value.buyerName||null,
        contract_amount:performanceSupplement.value.amount,
        source_description:performanceSupplement.value.sourceDescription,
        submitted_at_utc:new Date().toISOString(),
      }),
    })
    await Promise.all([refreshLatestGaps(),loadOutputCard()])
    emit('profile-updated')
    performanceSupplement.value={buyerName:'',amount:null,sourceDescription:''}
    submissionFeedback.value=missingPerformanceDetails.value.length
      ? '补充信息已经保存。下方仍有未填写的项目关键信息，可以继续补充。'
      : '采购人和合同金额已经保存，事实画像、能力画像和企业评价均已更新。'
  }catch(caught){setError(caught)}
  finally{busy.value=false}
}
async function submitPersonnel():Promise<void>{
  if(personnelDraft.value.employeeCount==null && personnelDraft.value.socialInsuranceCount==null){
    submissionFeedback.value='请至少填写员工人数或社保缴纳人数。'
    return
  }
  if(!personnelDraft.value.sourceDescription){
    submissionFeedback.value='请填写证明材料或信息来源。'
    return
  }
  busy.value=true
  error.value=null
  submissionFeedback.value='正在保存人员信息并更新画像与评价，请稍候…'
  try{
    await api(`/api/companies/${encodeURIComponent(companyId.value)}/profile-inputs/personnel`,{
      method:'POST',
      body:JSON.stringify({
        employee_count:personnelDraft.value.employeeCount,
        social_insurance_count:personnelDraft.value.socialInsuranceCount,
        count_scope:personnelDraft.value.countScope||null,
        source_description:personnelDraft.value.sourceDescription,
        submitted_at_utc:new Date().toISOString(),
      }),
    })
    await Promise.all([refreshLatestGaps(),loadOutputCard()])
    emit('profile-updated')
    submissionFeedback.value='人员信息已保存，人员和资源能力已经更新。返回企业画像即可查看结果。'
  }catch(caught){setError(caught)}
  finally{busy.value=false}
}
async function loadProviderStatus(): Promise<void> {
  try { providerStatus.value = await api<ProviderStatus>('/api/ai/capability-provider/status') }
  catch (caught) { setError(caught) }
}
async function startCapabilityAnalysis(): Promise<void> {
  if (!providerReady.value || selectedCapabilityTypes.value.length === 0) return
  const hadPendingStructuredValidation = (run.value?.pending_review_items?.length || 0) > 0
  capabilityBusy.value = true
  error.value = null
  try {
    run.value = await api<RunState>('/api/agent/runs', {
      method: 'POST',
      body: JSON.stringify({
        company_id: companyId.value,
        mode: 'interactive',
        max_questions_per_batch: 3,
        include_optional: false,
        max_rounds: 5,
        decision_context_policy: 'STRICT_BLOCK',
        capability_ai_enabled: true,
        analysis_only: true,
        requested_capability_types: selectedCapabilityTypes.value,
      }),
    })
    submissionFeedback.value = hadPendingStructuredValidation
      ? '智能整理已经完成；尚未通过结构化核验的回答仍不会直接写入正式画像。'
      : '企业画像已根据最新资料重新生成。'
    await loadOutputCard()
    emit('profile-updated')
    await loadProviderStatus()
  } catch (caught) {
    const parsed=toApiError(caught)
    capabilityUnavailableMessage.value=`企业资料已经保存，但本次智能整理未完成：${friendlyWarning({code:parsed.code,message:parsed.message})} 你可以稍后重新整理，已有资料不会丢失。`
  } finally {
    capabilityBusy.value = false
  }
}
async function continueToCapabilityAnalysis():Promise<void>{
  if(!providerReady.value){
    capabilityUnavailableMessage.value='企业资料检查已经完成。智能能力分析服务当前不可用，已有事实和经营偏好仍会正常保留。'
    return
  }
  await startCapabilityAnalysis()
}
function materialReferences(answer: AnswerState): Array<Record<string, unknown>> {
  return answer.materialId ? [{ material_id: answer.materialId, material_content_sha256: answer.sha256, original_filename: answer.filename, media_type: answer.mediaType, size_bytes: answer.sizeBytes }] : []
}
function splitDecisionText(value: string): string[] {
  return value.split(/[，,；;\n]/).map((item) => item.trim()).filter(Boolean)
}
function decisionAnswerPlaceholder(code: string): string {
  const values: Record<string, string> = {
    strategic_industries: '例如：政务信息化、医疗信息化',
    strategic_regions: '例如：山西省、北京市',
    procurement_method_preferences: '例如：公开招标、竞争性磋商',
    personnel_resource_constraints: '请填写当前人员或资源限制',
    explicit_exclusions: '请填写明确不参与的项目类型',
    key_buyers: '请填写重点关注的采购人',
    current_business_goals: '例如：拓展政企数字化项目',
  }
  return values[code] || '请填写企业确认的信息'
}
function decisionValue(code: string, answer: AnswerState): Record<string, unknown> {
  const items = splitDecisionText(answer.text)
  if (code === 'budget_preference') {
    const money = (value: number | null) => value == null ? null : { raw_value: `${value}元`, normalized_value: value, normalized_unit: 'yuan', normalized_currency: 'CNY' }
    return { minimum: money(answer.minimum), maximum: money(answer.maximum) }
  }
  if (code === 'strategic_industries') return { industries: items.map((industry_name) => ({ industry_name, industry_code: null })) }
  if (code === 'strategic_regions') return { regions: items.map((region_name) => ({ region_name, region_code: null })) }
  if (code === 'procurement_method_preferences') return { methods: items.map((method_name) => ({ method_name, method_code: null })) }
  if (code === 'consortium_acceptance') return { accepted: answer.text === '是' }
  if (code === 'risk_preference') {
    const names: Record<string, string> = { conservative: '谨慎', balanced: '均衡', aggressive: '积极' }
    return { stated_preference: names[answer.text] || answer.text, normalized_code: answer.text }
  }
  if (code === 'max_concurrent_projects') return { maximum: Number(answer.minimum) }
  if (code === 'personnel_resource_constraints') return { constraints: items.map((item) => ({ constraint_name: item, constraint_value: item, unit: null, notes: null })) }
  if (code === 'explicit_exclusions') return { items: items.map((item) => ({ exclusion_subject: item, scope_type: null, scope_value: null, notes: null })) }
  if (code === 'key_buyers') return { buyers: items.map((buyer_name) => ({ buyer_name, buyer_identifier: null })) }
  if (code === 'current_business_goals') return { goals: items.map((goal_text) => ({ goal_text, target_date: null })) }
  return { text: answer.text }
}
async function submitAnswers(): Promise<void> {
  if (processingLocked.value) return
  if (!run.value?.question_plan?.question_items) return
  busy.value = true; error.value = null
  try {
    let directlyApplied=0
    const performanceQuestion=run.value.question_plan.question_items.find(question=>question.target_code==='performance')
    const performanceAnswer=performanceQuestion?answers.value[performanceQuestion.question_item_id]:null
    if(performanceAnswer?.kind==='provide'){
      if(!performanceAnswer.projectName||!performanceAnswer.region||!performanceAnswer.scope||!performanceAnswer.sourceDescription){
        submissionFeedback.value='请完整填写项目名称、项目地区、项目内容以及证明材料或信息来源。'
        return
      }
      if(performanceAnswer.startDate&&performanceAnswer.endDate&&performanceAnswer.endDate<performanceAnswer.startDate){
        submissionFeedback.value='项目完成日期不能早于开始日期。'
        return
      }
      await api(`/api/companies/${encodeURIComponent(companyId.value)}/profile-inputs/performance`,{
        method:'POST',
        body:JSON.stringify({
          project_name:performanceAnswer.projectName,
          buyer_name:performanceAnswer.buyerName||null,
          industry:performanceAnswer.industry||null,
          region:performanceAnswer.region,
          contract_amount:performanceAnswer.amount,
          start_date:performanceAnswer.startDate||null,
          end_date:performanceAnswer.endDate||null,
          performance_scope:performanceAnswer.scope,
          source_description:performanceAnswer.sourceDescription,
          submitted_at_utc:new Date().toISOString(),
        }),
      })
      directlyApplied++
    }
    for(const question of run.value.question_plan.question_items){
      if(question.target_code==='performance'||question.information_request_type==='decision_confirmation')continue
      const answer=answers.value[question.question_item_id]
      if(answer?.kind!=='provide')continue
      if(!answer.text?.trim()){
        submissionFeedback.value=`请填写“${questionText(question)}”的具体内容。`
        return
      }
      if(!answer.sourceDescription?.trim()){
        submissionFeedback.value=`请填写“${questionText(question)}”的信息来源。`
        return
      }
      await api(`/api/companies/${encodeURIComponent(companyId.value)}/profile-inputs/general`,{
        method:'POST',
        body:JSON.stringify({
          target_layer:question.target_layer,
          target_code:question.target_code,
          content:answer.text.trim(),
          source_description:answer.sourceDescription.trim(),
          submitted_at_utc:new Date().toISOString(),
        }),
      })
      directlyApplied++
    }
    if(directlyApplied){
      submissionFeedback.value=`已保存 ${directlyApplied} 项补充信息，正在重新检查企业画像。`
      await Promise.all([refreshLatestGaps(),loadOutputCard()])
      emit('profile-updated')
      run.value = await api<RunState>('/api/agent/runs', { method: 'POST', body: JSON.stringify({ company_id: companyId.value, mode: 'interactive', max_questions_per_batch: 3, include_optional: false, max_rounds: 5, decision_context_policy: 'PAUSE_DECISION_LAYER', include_decision_questions: false }) })
      initAnswers()
      await refreshLatestGaps()
      submissionFeedback.value=`已保存 ${directlyApplied} 项补充信息，企业画像已经更新。`
      return
    }
    const responses: Array<Record<string, unknown>> = []
    for (const question of run.value.question_plan.question_items) {
      const answer = answers.value[question.question_item_id]
      if (answer.kind === 'unable') { responses.push({ question_item_id: question.question_item_id, response_outcome: 'unable_to_provide', unable_to_provide_note: null }); continue }
      const material_references = materialReferences(answer)
      if (question.information_request_type === 'decision_confirmation') {
        responses.push({ question_item_id: question.question_item_id, response_outcome: 'provided', decision_value: decisionValue(question.target_code, answer) })
      } else if (question.information_request_type === 'capability_clarification') {
        responses.push({ question_item_id: question.question_item_id, response_outcome: 'provided', answer: { answer_type: 'clarification', clarification_text: answer.text || '用户提供的能力澄清', structured_context: null }, material_references })
      } else if (question.information_request_type === 'conflict_clarification') {
        responses.push({ question_item_id: question.question_item_id, response_outcome: 'provided', answer: { answer_type: 'conflict_selection', selected_source_reference_ids: answer.text.split(',').map((value) => value.trim()).filter(Boolean), selection_note: null }, material_references })
      } else {
        responses.push({ question_item_id: question.question_item_id, response_outcome: 'provided', answer: material_references.length && !answer.text ? null : { answer_type: 'structured_value', value: { text: answer.text || '用户提供的结构化信息' }, user_note: null }, material_references })
      }
    }
    run.value = await api<RunState>(`/api/agent/runs/${run.value.run_id}/responses`, { method: 'POST', body: JSON.stringify({ submission: { submission_mode: 'full_current_batch', submitted_by: { actor_type: 'user', actor_id: 'LOCAL_OPERATOR', display_name: '本机操作人' }, submitted_at_utc: new Date().toISOString(), responses } }) })
    submissionFeedback.value =
      run.value.waiting_for === 'responses'
        ? '本轮回答已提交。系统已生成下一轮问题，请继续补充。'
        : run.value.waiting_for === 'decision_selection'
          ? '本轮回答已提交。请在下方确认需要更新的经营偏好。'
          : (run.value.pending_review_items?.length || 0) > 0
            ? '本轮回答已经收到，但其中的信息尚未完成结构化核验，因此暂时不会消除对应缺口。'
            : '本轮回答已提交，正在根据最新资料更新企业画像。'
    initAnswers()
    await Promise.all([refreshLatestGaps(), loadOutputCard()])
    document.getElementById('profile-check-result')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    if (!['responses','decision_selection'].includes(run.value?.waiting_for || '')) {
      submissionFeedback.value=(run.value.pending_review_items?.length || 0) > 0
        ? '回答已保存。尚未通过结构化核验的内容不会直接改写正式画像。'
        : '企业资料检查已完成，正在重新生成企业画像。'
      setTimeout(()=>void continueToCapabilityAnalysis(),0)
    }
  } catch (caught) { setError(caught) } finally { busy.value = false }
}
async function requestReconfirmation(): Promise<void> {
  if (!run.value) return
  try {
    await api(`/api/agent/runs/${run.value.run_id}/decision-reconfirmation`, { method: 'POST', body: JSON.stringify({ run_id: run.value.run_id, strategy: 'STRICT_BLOCK', requested_by: { actor_type: 'user', actor_id: 'LOCAL_OPERATOR', display_name: '本机操作人' }, requested_at_utc: new Date().toISOString(), field_codes: [] }) })
  } catch (caught) { setError(caught) }
}
let restoreTimer:ReturnType<typeof setTimeout>|null=null
function scheduleRestore():void{
  if(restoreTimer)clearTimeout(restoreTimer)
  restoreTimer=setTimeout(()=>void restoreLatestRun(),1200)
}
async function restoreLatestRun():Promise<void>{
  try{
    const latest=await api<RunState>(`/api/companies/${encodeURIComponent(companyId.value)}/agent-runs/latest`)
    run.value=latest
    if(latest.status==='RUNNING')selectedTool.value='check'
    initAnswers()
    capabilityBusy.value=latest.status==='RUNNING'
    if(latest.status==='RUNNING'){
      scheduleRestore()
    }else if(latest.status==='COMPLETED'&&latest.llm_node_executed){
      submissionFeedback.value='企业画像已经完成更新。'
      await loadOutputCard()
      emit('profile-updated')
    }
  }catch(caught){
    const parsed=toApiError(caught)
    if(parsed.httpStatus!==404)setError(caught)
  }
}
watch(companyId,()=>void restoreLatestRun(),{immediate:true})
watch(()=>route.query.action,(action)=>{if(action==='decision')selectedTool.value='decision'})
onMounted(() => Promise.all([loadProviderStatus(), loadOutputCard()]))
onUnmounted(()=>{if(restoreTimer)clearTimeout(restoreTimer)})
</script>

<style scoped>
.profile-input-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.profile-input-grid .wide{grid-column:1/-1}
@media(max-width:760px){.profile-input-grid{grid-template-columns:1fr}.profile-input-grid .wide{grid-column:auto}}
</style>
