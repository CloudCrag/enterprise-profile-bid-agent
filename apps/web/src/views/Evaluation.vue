<template>
  <section class="evaluation-page">
    <header class="page-header">
      <div>
        <span class="eyebrow">企业评价</span>
        <h2>{{ companyName }}</h2>
        <p>按照企业评价度量模型，对当前已有资料进行评分，并说明每项得分依据。</p>
      </div>
      <button v-if="embedded" class="secondary" type="button" @click="emit('show-profile')">返回企业画像</button>
    </header>

    <ErrorPanel :error="error" />

    <section v-if="profile" class="score-overview">
      <article class="card total-score">
        <span>企业综合得分</span>
        <strong>{{ displayScore(profile.summary?.official_total_score) }}</strong>
      </article>
      <article class="card">
        <span>重点指标参考得分</span>
        <strong>{{ displayScore(profile.summary?.core_27_normalized_observed_score) }}</strong>
      </article>
      <article class="card">
        <span>有效数据覆盖率</span>
        <strong>{{ percent(profile.summary?.active_44_coverage_ratio) }}</strong>
      </article>
      <article class="card">
        <span>已有数据可评分指标</span>
        <strong>{{ profile.summary?.scored_indicator_count || 0 }} / {{ profile.summary?.active_indicator_count || 44 }}</strong>
      </article>
    </section>

    <section class="card evaluation-mode-panel">
      <div class="section-heading">
        <div><h3>选择评价方式</h3><p>两种方式使用同一套评分规则，不会改变指标权重。</p></div>
      </div>
      <div class="evaluation-mode-grid">
        <label :class="{ selected: evaluationMode === 'automatic' }">
          <input v-model="evaluationMode" type="radio" value="automatic" />
          <span><b>自动评价</b><small>根据已有有效资料评分，缺少数据的指标暂不计入。</small></span>
        </label>
        <label :class="{ selected: evaluationMode === 'interactive' }">
          <input v-model="evaluationMode" type="radio" value="interactive" />
          <span><b>交互评价</b><small>在本页面补充评价所需资料，提交后立即重新评分。</small></span>
        </label>
      </div>
      <div class="button-row">
        <button :disabled="busy" type="button" @click="startEvaluation">
          {{ busy ? '正在评价…' : evaluationMode === 'interactive' ? '开始交互评价' : '开始自动评价' }}
        </button>
        <button v-if="profile" class="secondary" :disabled="busy" type="button" @click="loadLatest">刷新评价结果</button>
      </div>
    </section>

    <section v-if="evaluationMode === 'interactive' && profile" class="card interactive-panel">
      <div class="section-heading">
        <div>
          <h3>需要补充的评价信息</h3>
          <p>选择需要回答的项目。不确定的内容可以本轮跳过，不会影响已经完成的评分。</p>
        </div>
        <span class="count-pill">{{ interactiveGaps.length }} 项</span>
      </div>
      <p v-if="!interactiveGaps.length" class="success">当前没有需要补充的评价信息。</p>
      <article v-for="item in interactiveGaps" :key="item.indicator_code" class="evaluation-question">
        <div class="question-main">
          <div>
            <b>{{ item.indicator_name || indicatorName(item.indicator_code) }}</b>
            <p>{{ missingDescription(item) }}</p>
          </div>
          <div class="question-actions">
            <button type="button" class="secondary" @click="toggleQuestion(item.indicator_code)">
              {{ openQuestions.includes(item.indicator_code) ? '收起回答' : '回答并补充材料' }}
            </button>
            <button type="button" class="link-button" @click="toggleSkip(item.indicator_code)">本轮跳过</button>
          </div>
        </div>
        <div v-if="openQuestions.includes(item.indicator_code)" class="evaluation-answer">
          <template v-if="item.indicator_code === 'A1.3'">
            <label>注册资本（元）<input v-model.number="answerFor(item.indicator_code).registered_capital" type="number" min="0" /></label>
            <label>实缴资本（元）<input v-model.number="answerFor(item.indicator_code).paid_in_capital" type="number" min="0" /></label>
          </template>
          <label v-else-if="item.indicator_code === 'A3.2'">税务信用等级
            <select :value="answerFor(item.indicator_code).rating" @change="setAnswerSelection(item.indicator_code, 'rating', $event)">
              <option value="">请选择</option>
              <option v-for="rating in ['A','B','M','C','D']" :key="rating" :value="rating">{{ rating }}</option>
            </select>
          </label>
          <template v-else-if="['A3.3','C3.4'].includes(item.indicator_code)">
            <label>资质或证书名称<input v-model.trim="answerFor(item.indicator_code).qualification_name" /></label>
            <label>证书编号<input v-model.trim="answerFor(item.indicator_code).certificate_number" /></label>
            <label>发证机构<input v-model.trim="answerFor(item.indicator_code).issuer" /></label>
            <label>有效期至<input v-model="answerFor(item.indicator_code).valid_until" type="date" /></label>
          </template>
          <label v-else-if="riskIndicatorCodes.has(item.indicator_code)">当前情况
            <select :value="answerFor(item.indicator_code).risk_status" @change="setAnswerSelection(item.indicator_code, 'risk_status', $event)">
              <option value="">请选择</option>
              <option value="none">经查询暂无相关记录</option>
              <option value="resolved">有历史记录但已处理</option>
              <option value="current">当前存在相关记录</option>
            </select>
          </label>
          <label v-else>补充说明
            <textarea v-model.trim="answerFor(item.indicator_code).current_situation" placeholder="请说明企业在该指标方面的实际情况"></textarea>
          </label>
          <label class="material-field">证明材料或信息来源（必填）
            <input v-model.trim="answerFor(item.indicator_code).material_reference" placeholder="例如：企业证明文件、权威查询页面或内部确认记录" />
          </label>
        </div>
      </article>
      <div v-if="gapItems.length" class="button-row">
        <button :disabled="busy" type="button" @click="submitEvaluationAnswers">{{ busy ? '正在重新评价…' : '提交回答并重新评价' }}</button>
      </div>
      <p v-if="answerFeedback" class="feedback">{{ answerFeedback }}</p>
      <p v-if="skippedIndicators.length" class="muted">本轮已选择跳过 {{ skippedIndicators.length }} 项。</p>
    </section>

    <template v-if="profile">
      <section v-if="dimensionRows.length" class="card">
        <div class="section-heading"><div><h3>评价维度</h3><p>查看各方面得分、数据覆盖情况以及当前优势和风险。</p></div></div>
        <div class="dimension-grid">
          <article v-for="item in dimensionRows" :key="item.code" class="dimension-card">
            <div class="dimension-title"><b>{{ item.name }}</b><strong>{{ displayScore(item.score) }}</strong></div>
            <div class="dimension-meta"><span>数据覆盖 {{ percent(item.coverage) }}</span><span>已评分 {{ item.scored }}/{{ item.active }}</span></div>
            <progress :value="Number(item.score) || 0" max="100"></progress>
            <p v-if="item.strengths.length"><b>主要优势：</b>{{ item.strengths.join('；') }}</p>
            <p v-if="item.risks.length"><b>需要关注：</b>{{ item.risks.join('；') }}</p>
          </article>
        </div>
      </section>

      <section class="card indicator-panel">
        <div class="section-heading">
          <div><h3>评分指标明细</h3><p>共44项启用指标。每项均展示评分状态、得分和原因；只有低分项展示诊断与改善建议。</p></div>
          <span class="count-pill">{{ activeIndicators.length }} 项</span>
        </div>
        <div class="indicator-list">
          <article v-for="item in activeIndicators" :key="item.indicator_code" class="indicator-card">
            <div class="indicator-heading">
              <div><span class="indicator-code">{{ item.indicator_code }}</span><h4>{{ item.indicator_name }}</h4></div>
              <div class="indicator-score" :class="{ missing: item.raw_score == null }">{{ item.raw_score == null ? '暂无数据' : `${displayScore(item.raw_score)} 分` }}</div>
            </div>
            <div class="indicator-status">
              <span>{{ item.acquisition_priority === 1 ? '重点指标' : '一般指标' }}</span>
              <span>{{ scoringStatusName(item.scoring_status) }}</span>
              <span>{{ nextStepLabel(item) }}</span>
            </div>
            <p class="score-reason"><b>评分原因：</b>{{ friendlyExplanation(item) }}</p>
            <div v-if="item.raw_score != null && Number(item.raw_score) < 60 && (item.low_score_diagnosis || item.improvement_suggestion)" class="low-score-advice">
              <p v-if="item.low_score_diagnosis"><b>低分诊断：</b>{{ item.low_score_diagnosis }}</p>
              <p v-if="item.improvement_suggestion"><b>改善建议：</b>{{ item.improvement_suggestion }}</p>
            </div>
          </article>
        </div>
      </section>
    </template>

    <section v-else-if="!busy" class="card empty-state">
      <h3>尚未生成企业评价</h3>
      <p>请选择自动评价或交互评价，系统会根据当前企业资料生成评分结果。</p>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, toApiError, type ApiError } from '../api'
import { useEnterpriseContextStore } from '../enterpriseContext'
import ErrorPanel from '../components/ErrorPanel.vue'

const props = withDefaults(defineProps<{ embedded?: boolean }>(), { embedded: false })
const emit = defineEmits<{ (event: 'show-profile'): void }>()
const context = useEnterpriseContextStore()
const companyId = computed(() => context.normalizedCompanyId)
const companyName = ref('当前企业')
const evaluationMode = ref<'automatic' | 'interactive'>('automatic')
const profile = ref<any>(null)
const gaps = ref<any>(null)
const run = ref<any>(null)
const busy = ref(false)
const error = ref<ApiError | null>(null)
const skippedIndicators = ref<string[]>([])
const openQuestions = ref<string[]>([])
const answerDrafts = ref<Record<string, Record<string, any>>>({})
const answerFeedback = ref('')

const gapItems = computed<any[]>(() => gaps.value?.items || profile.value?.data_gaps || [])
const interactiveGaps = computed(() => gapItems.value.filter(item => !skippedIndicators.value.includes(item.indicator_code)))
const activeIndicators = computed<any[]>(() =>
  (profile.value?.active_indicator_results || profile.value?.indicator_results?.filter((item: any) => item.active) || [])
    .slice()
    .sort((a: any, b: any) => String(a.indicator_code).localeCompare(String(b.indicator_code), 'zh-CN', { numeric: true }))
)
const riskIndicatorCodes = new Set(['C1.1','C1.2','C1.3','C1.5','C2.1','C2.2','C2.3','C2.4','C2.5','C3.1','C3.2','C3.3','C3.5'])
const dimensionRows = computed(() => {
  const dimensions = profile.value?.primary_dimensions || []
  return (Array.isArray(dimensions) ? dimensions : []).map((item: any, index: number) => ({
    code: item.code || item.dimension_code || String(index),
    name: item.name || item.dimension_name || `评价维度${index + 1}`,
    score: item.normalized_observed_score ?? item.observed_score ?? item.score,
    coverage: item.coverage_ratio ?? item.coverage,
    scored: item.scored_indicator_count ?? 0,
    active: item.active_indicator_count ?? 0,
    strengths: (item.key_strengths || []).map((entry: any) =>
      typeof entry === 'string' ? entry : `${entry.indicator_name || '优势指标'}（${displayScore(entry.raw_score)}分）`
    ),
    risks: (item.key_risks || []).map((entry: any) =>
      typeof entry === 'string' ? entry : (entry.diagnosis || `${entry.indicator_name || '风险指标'}得分较低`)
    ),
  }))
})

function displayScore(value: unknown): string {
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(1).replace(/\.0$/, '') : '暂无'
}
function percent(value: unknown): string {
  const number = Number(value)
  return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : '暂无'
}
function indicatorName(code: unknown): string {
  return activeIndicators.value.find(item => item.indicator_code === code)?.indicator_name || '待补充指标'
}
function scoringStatusName(value: unknown): string {
  const names: Record<string, string> = {
    SCORED: '已完成评分', MISSING_DATA: '缺少数据', NOT_APPLICABLE: '不适用',
    API_PENDING: '等待数据', API_FAILED: '数据暂不可用', CALCULATION_ERROR: '需要重新评价',
    PENDING_RULE: '评价依据待完善',
  }
  return names[String(value || '')] || '等待评价'
}
function nextStepLabel(item: any): string {
  if (item.scoring_status === 'SCORED') return '无需补充'
  if (item.scoring_status === 'CALCULATION_ERROR') return '重新评价'
  return '可在交互评价中补充'
}
function friendlyExplanation(item: any): string {
  const text = String(item.explanation || '').trim()
  if (text) return text
  if (item.raw_score == null) return '当前企业资料中尚未找到能够支持该指标评分的有效信息。'
  return '系统已依据现有企业资料和评价规则完成评分。'
}
function missingDescription(item: any): string {
  const count = Array.isArray(item.missing_fields) ? item.missing_fields.length : 0
  return count ? `还需要补充 ${count} 项相关资料，提交后系统会立即重新评价。` : '当前资料不足以支持该指标评分。'
}
function answerFor(code: string): Record<string, any> {
  return answerDrafts.value[code] ||= {
    material_reference: '', registered_capital: null, paid_in_capital: null,
    rating: '', qualification_name: '', certificate_number: '', issuer: '',
    valid_until: '', risk_status: '', current_situation: '',
  }
}
function toggleQuestion(code: string): void {
  answerFor(code)
  openQuestions.value = openQuestions.value.includes(code) ? openQuestions.value.filter(item => item !== code) : [code]
}
function toggleSkip(code: string): void {
  skippedIndicators.value = skippedIndicators.value.includes(code)
    ? skippedIndicators.value.filter(item => item !== code)
    : [...skippedIndicators.value, code]
}
function setAnswerSelection(code: string, field: 'rating' | 'risk_status', event: Event): void {
  answerFor(code)[field] = (event.target as HTMLSelectElement).value
}
async function loadCompany(): Promise<void> {
  const value = await api<Record<string, any>>(`/api/companies/${encodeURIComponent(companyId.value)}/profile`)
  companyName.value = String(value?.enterprise?.name || value?.company_name || companyId.value)
}
async function loadLatest(): Promise<void> {
  if (!companyId.value) return
  error.value = null
  await loadCompany()
  try {
    profile.value = await api(`/api/companies/${encodeURIComponent(companyId.value)}/evaluation/latest`)
    try { gaps.value = await api(`/api/companies/${encodeURIComponent(companyId.value)}/evaluation-data-gaps`) } catch { gaps.value = null }
  } catch (caught) {
    const parsed = toApiError(caught)
    if (parsed.httpStatus === 404) { profile.value = null; gaps.value = null; return }
    error.value = parsed
  }
}
async function startEvaluation(): Promise<void> {
  if (!companyId.value) return
  busy.value = true
  error.value = null
  answerFeedback.value = ''
  openQuestions.value = []
  answerDrafts.value = {}
  skippedIndicators.value = []
  try {
    const result = await api<any>('/api/evaluation/runs', {
      method: 'POST',
      body: JSON.stringify({
        company_id: companyId.value, workflow_mode: evaluationMode.value,
        provider_mode: 'LOCAL_PROFILE', max_api_calls: 0, max_rounds: 3,
      }),
    })
    run.value = result.run
    profile.value = result.profile || profile.value
    await loadLatest()
  } catch (caught) {
    error.value = toApiError(caught)
  } finally {
    busy.value = false
  }
}
async function submitEvaluationAnswers(): Promise<void> {
  const answeredCodes = Object.entries(answerDrafts.value)
    .filter(([, value]) => Object.entries(value).some(([key, field]) => key !== 'material_reference' && field !== '' && field != null))
    .map(([code]) => code)
  if (!answeredCodes.length && !skippedIndicators.value.length) {
    answerFeedback.value = '请先回答至少一项，或者选择“本轮跳过”。'
    return
  }
  for (const code of answeredCodes) {
    const draft = answerFor(code)
    if (!String(draft.material_reference || '').trim()) { answerFeedback.value = '请为已回答的指标填写证明材料或信息来源。'; return }
    if (code === 'A1.3' && (draft.registered_capital == null || draft.paid_in_capital == null)) { answerFeedback.value = '请完整填写注册资本和实缴资本。'; return }
    if (code === 'A3.2' && !draft.rating) { answerFeedback.value = '请选择税务信用等级。'; return }
    if (['A3.3','C3.4'].includes(code) && !['qualification_name','certificate_number','issuer','valid_until'].every(key => draft[key])) { answerFeedback.value = '请完整填写资质名称、证书编号、发证机构和有效期。'; return }
    if (riskIndicatorCodes.has(code) && !draft.risk_status) { answerFeedback.value = '请选择该风险指标的当前情况。'; return }
    if (!['A1.3','A3.2','A3.3','C3.4'].includes(code) && !riskIndicatorCodes.has(code) && !String(draft.current_situation || '').trim()) { answerFeedback.value = '请填写该指标的实际情况。'; return }
  }
  busy.value = true
  error.value = null
  answerFeedback.value = '正在保存回答并重新评价…'
  try {
    if (!run.value?.run_id) {
      const started = await api<any>('/api/evaluation/runs', {
        method: 'POST',
        body: JSON.stringify({ company_id: companyId.value, workflow_mode: 'interactive', provider_mode: 'LOCAL_PROFILE', max_api_calls: 0, max_rounds: 3 }),
      })
      run.value = started.run
    }
    if (!run.value?.run_id) throw new Error('未能建立交互评价任务，请重新开始。')
    const answers = [
      ...answeredCodes.map(code => {
        const values = { ...answerFor(code) }
        const material_reference = values.material_reference
        delete values.material_reference
        return { indicator_code: code, action: 'answer', material_reference, values }
      }),
      ...skippedIndicators.value.map(code => ({ indicator_code: code, action: 'skip', material_reference: null, values: {} })),
    ]
    const result = await api<any>(`/api/evaluation/runs/${encodeURIComponent(run.value.run_id)}/answers`, {
      method: 'POST', body: JSON.stringify({ answers }),
    })
    run.value = result.run || run.value
    await loadLatest()
    const applied = result.applied_indicator_codes?.length || 0
    const skipped = result.skipped_indicator_codes?.length || 0
    const unsupported = result.unsupported_indicator_codes?.length || 0
    answerFeedback.value = applied
      ? `已保存 ${applied} 项回答并重新完成评价。${unsupported ? `另有 ${unsupported} 项信息不完整，暂未采用。` : ''}`
      : skipped ? `已记录本轮跳过 ${skipped} 项。` : '本轮回答未形成可用数据，请检查填写内容。'
    openQuestions.value = []
    answerDrafts.value = {}
    skippedIndicators.value = []
  } catch (caught) {
    error.value = toApiError(caught)
  } finally {
    busy.value = false
  }
}

watch(() => context.normalizedCompanyId, async value => {
  if (!value) return
  run.value = null
  answerFeedback.value = ''
  await loadLatest()
})
onMounted(loadLatest)
</script>

<style scoped>
.evaluation-page{display:grid;gap:16px;min-width:0}.page-header,.section-heading,.question-main,.question-actions,.indicator-heading,.dimension-title,.dimension-meta{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.page-header h2,.section-heading h3{margin:.25rem 0}.page-header p,.section-heading p{margin:.25rem 0;color:#667b78}.score-overview{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.score-overview article{display:grid;gap:8px}.score-overview strong{font-size:1.8rem}.score-overview .total-score{background:#edf8f5;border-color:#75ad9f}.evaluation-mode-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:14px}.evaluation-mode-grid label{display:flex;gap:10px;padding:16px;border:1px solid #dce5e3;border-radius:12px;background:#fff;cursor:pointer}.evaluation-mode-grid label.selected{border-color:#55a091;background:#f0f8f6}.evaluation-mode-grid input{width:auto}.evaluation-mode-grid span{display:grid;gap:5px}.evaluation-mode-grid small{color:#667b78}.button-row{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}.count-pill,.indicator-code,.indicator-status span{display:inline-flex;width:max-content;padding:.3rem .6rem;border-radius:999px;background:#edf4f2;color:#315e55;font-size:.85rem}.evaluation-question{padding:16px 0;border-bottom:1px solid #e5ebe9}.question-main p{margin:.35rem 0 0;color:#667b78}.question-actions{align-items:center;flex:0 0 auto}.evaluation-answer{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:14px;padding:14px;border-radius:10px;background:#f5f8f7}.evaluation-answer label{display:grid;gap:6px}.material-field{grid-column:1/-1}.feedback{padding:12px;border-radius:8px;background:#edf8f5;color:#176b43}.dimension-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-top:14px}.dimension-card{padding:14px;border:1px solid #dce5e3;border-radius:10px}.dimension-title strong{font-size:1.35rem;color:#176b5d}.dimension-meta{margin:8px 0;color:#667b78;font-size:.9rem}.dimension-card progress{width:100%}.dimension-card p{margin:.65rem 0 0;line-height:1.7}.indicator-list{display:grid;gap:12px;margin-top:14px}.indicator-card{padding:16px;border:1px solid #dce5e3;border-radius:12px;background:#fff}.indicator-heading{align-items:center}.indicator-heading>div:first-child{display:flex;gap:10px;align-items:center}.indicator-heading h4{margin:0}.indicator-score{font-size:1.15rem;font-weight:800;color:#176b5d}.indicator-score.missing{color:#8a6a20}.indicator-status{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}.score-reason,.low-score-advice p{line-height:1.75}.low-score-advice{padding:12px;border-left:4px solid #d19a34;background:#fff8e8}.empty-state{text-align:center;padding:32px}@media(max-width:1000px){.score-overview{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:760px){.page-header,.section-heading,.question-main,.question-actions,.indicator-heading,.dimension-title,.dimension-meta{flex-direction:column;align-items:stretch}.score-overview,.evaluation-mode-grid,.evaluation-answer{grid-template-columns:1fr}.material-field{grid-column:auto}.indicator-heading>div:first-child{align-items:flex-start}}
</style>
