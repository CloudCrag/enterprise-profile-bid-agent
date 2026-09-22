<template>
  <section class="competition-page">
    <header class="page-hero competition-hero">
      <div>
        <span class="eyebrow">独立竞争分析详情</span>
        <h2>{{ detail?.project.project_name || '正在读取项目名称' }}</h2>
        <p>查看当前企业与该项目的竞争分析、数据覆盖和行动建议。</p>
      </div>
      <button type="button" class="secondary-link" @click="closeDetailPage">关闭竞争分析页</button>
    </header>

    <ErrorPanel :error="error" />

    <section v-if="loading" class="card state-card">
      <h3>正在读取竞争分析</h3>
      <p>系统会优先复用相同企业画像版本、项目版本和竞争数据版本下的有效结果。</p>
    </section>

    <template v-else-if="detail">
      <section v-if="analysis?.data_is_demo" class="card demo-warning">
        <b>竞争对手演示数据</b>
        <span>{{ analysis.data_warning || '演示数据，不代表真实企业参与情况。' }}</span>
        <span>当前仅用于验证竞争分析流程和界面，不参与投标决策综合评分。</span>
      </section>

      <section v-if="detail.status === 'STALE'" class="card stale-warning">
        <h3>分析结果已过期</h3>
        <p>{{ staleReasonLabel(detail.stale_reason) }}。旧结果不会作为当前结论展示。</p>
      </section>

      <section class="summary-grid">
        <article class="card">
          <span class="eyebrow">真实项目</span>
          <h3>{{ detail.project.project_name }}</h3>
          <dl>
            <div><dt>采购人</dt><dd>{{ detail.project.buyer_name }}</dd></div>
            <div><dt>地区 / 行业</dt><dd>{{ detail.project.region }} / {{ detail.project.industry }}</dd></div>
            <div><dt>预算</dt><dd>{{ money(detail.project.budget) }}</dd></div>
            <div><dt>项目状态</dt><dd>{{ projectStatusLabel(detail.project.project_status) }}</dd></div>
            <div><dt>开标时间</dt><dd>{{ detail.project.bid_open_time ? localTime(detail.project.bid_open_time) : '数据缺失' }}</dd></div>
            <div><dt>时间字段说明</dt><dd>{{ detail.project.time_field_note || 'bid_open_time不作为投标截止时间或评分依据。' }}</dd></div>
          </dl>
        </article>

        <article class="card">
          <span class="eyebrow">当前企业</span>
          <h3>{{ detail.company.company_name || detail.company.company_id }}</h3>
          <dl>
            <div><dt>资格状态</dt><dd>{{ eligibilityLabel(detail.company.qualification_status) }}</dd></div>
            <div><dt>中标机会</dt><dd>{{ opportunityLabel(detail.company.win_opportunity_level) }}</dd></div>
            <div><dt>数据链</dt><dd>真实企业 + 真实项目 + 演示竞争候选</dd></div>
          </dl>
          <h4>核心相关能力</h4>
          <div class="tag-list">
            <span v-for="value in detail.company.core_capabilities" :key="value" class="pill">{{ value }}</span>
            <span v-if="!detail.company.core_capabilities.length">缺少已审核能力证据</span>
          </div>
        </article>
      </section>

      <section v-if="detail.status === 'PENDING' || detail.status === 'RUNNING'" class="card state-card">
        <h3>{{ detail.status === 'RUNNING' ? '竞争分析正在执行' : '尚无有效竞争分析' }}</h3>
        <p>系统不会把“未查到竞争者”解释成“竞争较低”。</p>
        <button type="button" class="primary-action" :disabled="loading" @click="loadDetail(true)">读取或生成分析</button>
      </section>

      <template v-if="analysis && detail.status !== 'STALE'">
        <section class="card landscape-card">
          <div class="section-title">
            <div><span class="eyebrow">竞争格局</span><h3>{{ competitionLabel(analysis.competitive_intensity, analysis.data_coverage) }}</h3></div>
            <span class="pill">{{ analysisStatusLabel(analysis.status) }}</span>
          </div>
          <div class="metric-grid">
            <div><b>覆盖率</b><span>{{ percent(analysis.data_coverage) }}</span></div>
            <div><b>真实确认竞争者</b><span>{{ analysis.confirmed_competitors.length }}</span></div>
            <div><b>潜在演示竞争者</b><span>{{ analysis.potential_competitors.length }}</span></div>
            <div><b>生成时间</b><span>{{ localTime(analysis.generated_at) }}</span></div>
          </div>
        </section>

        <section class="competitor-grid">
          <article class="card confirmed-zone">
            <span class="eyebrow">已确认竞争者</span>
            <h3>必须有当前项目直接证据</h3>
            <ul>
              <li v-for="competitor in analysis.confirmed_competitors" :key="competitor.company_id"><b>{{ competitor.company_name }}</b><p>{{ competitor.basis }}</p></li>
              <li v-if="!analysis.confirmed_competitors.length">当前没有真实确认数据，不代表竞争较低。</li>
            </ul>
          </article>
          <article class="card potential-zone">
            <span class="eyebrow">潜在竞争者</span>
            <h3>{{ analysis.data_is_demo ? '当前为演示候选' : '基于真实历史线索推断' }}</h3>
            <ul>
              <li v-for="competitor in analysis.potential_competitors" :key="competitor.company_id"><b>{{ competitor.company_name }}</b><p>{{ competitor.basis }}</p></li>
              <li v-if="!analysis.potential_competitors.length">当前没有潜在竞争者记录。</li>
            </ul>
          </article>
        </section>

        <section class="analysis-grid">
          <article class="card"><span class="eyebrow">已确认事实</span><ul><li v-for="item in analysis.confirmed_facts" :key="item.statement">{{ item.statement }}<small>证据：{{ item.evidence_ids.join('、') || '无' }}</small></li><li v-if="!analysis.confirmed_facts.length">暂无。</li></ul></article>
          <article class="card"><span class="eyebrow">模型推断</span><ul><li v-for="item in analysis.inferences" :key="item.statement">{{ item.statement }}<small>可信程度：{{ confidenceLabel(item.confidence) }}</small></li><li v-if="!analysis.inferences.length">暂无。</li></ul></article>
          <article class="card"><span class="eyebrow">未知项</span><ul><li v-for="item in analysis.unknowns" :key="item">{{ item }}</li><li v-if="!analysis.unknowns.length">无。</li></ul></article>
        </section>

        <section class="analysis-grid">
          <article class="card"><span class="eyebrow">我方优势</span><ul><li v-for="item in analysis.company_advantages" :key="item.statement">{{ item.statement }}</li><li v-if="!analysis.company_advantages.length">暂无有证据优势。</li></ul></article>
          <article class="card"><span class="eyebrow">我方短板</span><ul><li v-for="item in analysis.company_weaknesses" :key="item.statement">{{ item.statement }}</li><li v-if="!analysis.company_weaknesses.length">暂无有证据短板。</li></ul></article>
          <article class="card"><span class="eyebrow">行动建议</span><ul><li v-for="item in analysis.strategies" :key="item">{{ item }}</li><li v-if="!analysis.strategies.length">暂无建议。</li></ul></article>
        </section>

        <section class="card metadata-card">
          <details><summary>查看技术记录与证据编号</summary><p>模型版本：{{ analysis.model_version }}；Prompt 版本：{{ analysis.prompt_version }}</p><p>竞争数据版本：{{ analysis.competition_data_version }}</p><code>{{ analysis.evidence_ids.join('\n') || '无' }}</code></details>
        </section>
      </template>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ErrorPanel from '../components/ErrorPanel.vue'
import { getCompetitionDetail, toApiError, type ApiError, type CompetitionDetailResponse } from '../api'
import { useEnterpriseContextStore } from '../enterpriseContext'

const route = useRoute()
const router = useRouter()
const context = useEnterpriseContextStore()
const detail = ref<CompetitionDetailResponse | null>(null)
const error = ref<ApiError | null>(null)
const loading = ref(false)
const projectId = computed(() => String(route.params.projectId || ''))
const companyId = computed(() => String(route.query.companyId || context.normalizedCompanyId || ''))
const analysis = computed(() => detail.value?.analysis || null)

function firstQuery(name: string): string | undefined { const value = route.query[name]; const first = Array.isArray(value) ? value[0] : value; return typeof first === 'string' && first ? first : undefined }
async function loadDetail(ensureAnalysis = true): Promise<void> {
  if (!projectId.value || !companyId.value) { error.value = toApiError(new Error('竞争详情 URL 缺少企业或项目编号。')); return }
  loading.value = true; error.value = null
  try {
    detail.value = await getCompetitionDetail({
      companyId: companyId.value,
      projectId: projectId.value,
      companyProfileVersion: firstQuery('companyProfileVersion'),
      projectVersion: firstQuery('projectVersion'),
      competitionDataVersion: firstQuery('competitionDataVersion'),
      taskId: firstQuery('taskId'),
      threadId: firstQuery('threadId'),
      ensureAnalysis,
    })
  } catch (caught) { error.value = toApiError(caught) } finally { loading.value = false }
}
function closeDetailPage(): void {
  window.close()
  if (!window.closed) { const taskId = firstQuery('taskId'); router.push({ path: '/bid-decision', query: taskId ? { taskId } : {} }) }
}
function percent(value: number): string { return `${Math.round(value * 100)}%` }
function localTime(value: string): string { return new Date(value).toLocaleString('zh-CN', { hour12: false }) }
function money(value: string | number | null): string { if (value === null || value === undefined || value === '') return '未提供'; const parsed = Number(value); return Number.isFinite(parsed) ? `¥${parsed.toLocaleString('zh-CN')}` : String(value) }
function projectStatusLabel(value: string): string { return ({ TENDER: '正在招标', PLAN: '招标计划', OPEN: '正在招标', CLOSED: '已关闭', TERMINATED: '已终止', AWARDED: '已完成招标' } as Record<string, string>)[value] || value || '待确认' }
function eligibilityLabel(value: string | null): string { if (!value) return '尚未关联决策任务'; return ({ PASS: '符合', FAIL: '不符合', UNKNOWN: '待核验' } as Record<string, string>)[value] || value }
function opportunityLabel(value: string | null): string { if (!value) return '暂无'; return ({ HIGH: '较高', MEDIUM: '中等', LOW: '较低', UNKNOWN: '暂不能判断', NOT_APPLICABLE: '不适用' } as Record<string, string>)[value] || '暂不能判断' }
function analysisStatusLabel(value: string): string { return value === 'COMPLETED' ? '分析完成' : '需要进一步核实' }
function confidenceLabel(value: string): string { return ({ HIGH: '高', MEDIUM: '中', LOW: '低' } as Record<string, string>)[value] || '待判断' }
function competitionLabel(intensity: string, coverage: number): string { if (coverage < 0.5) return '数据覆盖不足，暂不能可靠判断'; return ({ LOW: '竞争相对较低', MEDIUM: '竞争程度中等', HIGH: '竞争较为激烈', UNKNOWN: '竞争情况尚不明确' } as Record<string, string>)[intensity] || '竞争情况尚不明确' }
function staleReasonLabel(reason: string | null): string { return ({ PROFILE_VERSION_MISMATCH: '企业画像版本已变化', PROJECT_VERSION_MISMATCH: '项目版本已变化', COMPETITION_DATA_VERSION_MISMATCH: '竞争数据版本已变化', RESULT_EXPIRED: '分析有效期已结束', MODEL_VERSION_MISMATCH: '模型版本已变化' } as Record<string, string>)[reason || ''] || '依赖版本已变化' }

onMounted(() => loadDetail(true))
watch(() => route.fullPath, () => loadDetail(true))
</script>

<style scoped>
.competition-page{display:grid;gap:1rem}.competition-hero>.secondary-link{background:#fff!important;color:#176b5d!important;border:1px solid #176b5d!important}.competition-hero>.secondary-link:hover{background:#edf7f4!important;color:#104f45!important}.competition-hero,.section-title{display:flex;justify-content:space-between;gap:1rem;align-items:flex-start}.summary-grid,.competitor-grid,.analysis-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem}.card{display:grid;gap:.75rem}.card dl{display:grid;gap:.55rem}.card dl div{display:grid;grid-template-columns:120px 1fr;gap:.5rem}.card dd{margin:0}.tag-list{display:flex;gap:.5rem;flex-wrap:wrap}.pill{border-radius:999px;padding:.35rem .65rem;background:#eef2f6}.demo-warning{background:#fff8e7;border-left:5px solid #b88b2d}.stale-warning{background:#fde9e9;border-left:5px solid #9d2323}.state-card{background:#f5f7fa}.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:.75rem}.metric-grid div{display:grid;gap:.25rem;padding:.75rem;background:#f5f7fa;border-radius:.5rem}.confirmed-zone{border-top:4px solid #287a55}.potential-zone{border-top:4px solid #b88b2d}.card ul{display:grid;gap:.75rem;padding-left:1.25rem}.card li p{margin:.25rem 0}.card small{display:block;margin-top:.25rem;color:#667085}.metadata-card code{display:block;margin-top:.75rem;white-space:pre-wrap;word-break:break-word}@media(max-width:720px){.competition-hero,.section-title{display:grid}.card dl div{grid-template-columns:1fr}}
</style>
