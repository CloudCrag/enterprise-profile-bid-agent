<template>
  <section class="bid-decision-page">
    <header class="page-hero decision-hero">
      <div>
        <span class="eyebrow">投标决策与竞争分析</span>
        <h2>投标决策工作台</h2>
        <p>使用真实企业画像、远程真实项目数据和智谱 GLM，支持单项目分析与多项目组合比较。</p>
      </div>
      <span class="service-badge" :class="serviceReady ? 'ready' : 'offline'">
        {{ serviceReady ? '核心服务正常' : '核心服务不可用' }}
      </span>
    </header>

    <ErrorPanel :error="error" />

    <section class="card temporary-rule-warning">
      <div>
        <b>临时投标决策评分 V2</b>
        <span class="temporary-tag">临时规则</span>
      </div>
      <p>客观基础分只使用资格、企业能力和资源三类当前可解释数据，总分100分。不代表中标概率，也不是后期正式推荐算法。</p>
      <details class="score-rule-details" open>
        <summary>查看完整评分规则</summary>
        <div class="rule-table-wrap">
          <table class="rule-table">
            <thead><tr><th>评分维度</th><th>满分</th><th>规则</th></tr></thead>
            <tbody>
              <tr><td>资格可投性</td><td>40</td><td>PASS=40；UNKNOWN=20；FAIL=0且固定NO_GO</td></tr>
              <tr><td>企业能力匹配</td><td>40</td><td>行业15 + 技术与同类业绩15 + 地区5 + 项目规模5</td></tr>
              <tr><td>资源可执行性</td><td>20</td><td>团队槽位10 + 关键人员和证书资源10</td></tr>
            </tbody>
          </table>
        </div>
        <h4>企业能力子项</h4>
        <ul>
          <li>行业：明确匹配15分，数据不足7.5分，明确不匹配0分。</li>
          <li>技术与同类业绩：明确匹配15分，部分匹配8分，数据不足7.5分，明确不匹配0分。</li>
          <li>地区：明确具备5分，数据不足2.5分，明确不具备0分。</li>
          <li>项目规模：明确可承接5分，数据不足2.5分，明确超过能力0分。</li>
        </ul>
        <h4>资源子项</h4>
        <ul>
          <li>投标团队槽位：充足10分，不足0分。</li>
          <li>关键人员和证书：明确满足10分，数据不足5分，明确不满足0分。</li>
        </ul>
        <h4>优先级与硬约束</h4>
        <ul>
          <li>资格PASS：80分及以上为高，65—79.99为中，低于65为低。</li>
          <li>资格UNKNOWN：最高只能为中优先级，并始终标注资格待核验。</li>
          <li>资格FAIL：无论其他分数多高，均为NO_GO，不进入组合。</li>
        </ul>
        <p><b>不参与客观基础分：</b>本次偏好、bid_open_time、准备时间、合同风险、演示竞争对手、中标概率和旧推荐分。</p>
        <p><b>本次偏好会影响：</b>AI分析、最终优先级和组合选择；但不会修改资格事实或客观基础分。</p>
      </details>
      <p><b>竞争对手当前为演示数据，只用于流程展示，不参与客观基础分。</b></p>
    </section>

    <section class="card status-card">
      <div><span class="eyebrow">当前企业</span><h3>{{ companyDisplayName }}</h3><small>{{ companyId }}</small></div>
      <div class="status-grid">
        <div><b>运行模式</b><span>本机单用户 · 真实数据链</span></div>
        <div><b>企业信息</b><span>本地真实企业 JSON</span></div>
        <div><b>项目数据</b><span>远程真实项目数据库</span></div>
        <div><b>模型</b><span>{{ status?.llm_provider === 'zhipu' ? `智谱 ${status.llm_model || ''}` : '读取中' }}</span></div>
        <div><b>任务恢复</b><span>{{ status?.checkpointer === 'sqlite' ? '已开启' : '读取中' }}</span></div>
        <div><b>评分规则</b><span>{{ status?.score_rule_version || 'TEMP-BID-SCORE-V2' }}</span></div>
      </div>
    </section>

    <section class="card create-card">
      <div class="section-title">
        <div><span class="eyebrow">新建任务</span><h3>选择并比较真实项目</h3></div>
        <button type="button" class="secondary-link" :disabled="busy" @click="restoreLastTask()">恢复最近任务</button>
      </div>
      <label>本次目标与偏好
        <textarea v-model="userGoal" rows="4" placeholder="例如：优先山西省电力工程项目；预算不超过3000万元；不接受联合体；优先人员冲突少的项目；风险偏好保守。" />
      </label>
      <p class="field-help">智谱GLM会解析本次行业、地区、预算、风险偏好、优先因素和排除条件。它们会影响AI分析、最终优先级和组合，但不会改变资格三态或客观100分。</p>
      <section v-if="interpretedGoal" class="preference-panel">
        <h4>本次偏好识别结果</h4>
        <div class="preference-grid">
          <div><b>优先行业</b><span>{{ listText(interpretedGoal.target_industries) }}</span></div>
          <div><b>优先地区</b><span>{{ listText(interpretedGoal.target_regions) }}</span></div>
          <div><b>预算范围</b><span>{{ budgetPreferenceText(interpretedGoal) }}</span></div>
          <div><b>风险偏好</b><span>{{ riskPreferenceText(interpretedGoal) }}</span></div>
          <div><b>明确排除</b><span>{{ listText(interpretedGoal.explicit_exclusions) }}</span></div>
          <div><b>优先因素</b><span>{{ listText(interpretedGoal.priority_factors) }}</span></div>
        </div>
      </section>
      <div class="search-row">
        <label>搜索项目
          <input v-model.trim="projectQuery" placeholder="输入项目名称、行业、地区或采购人" @keyup.enter="searchProjects" />
        </label>
        <button type="button" class="secondary-link" :disabled="projectsLoading" @click="searchProjects">
          {{ projectsLoading ? '正在查询…' : '查询' }}
        </button>
      </div>
      <div class="project-search-summary">
        <span>符合条件的真实项目共 {{ projectTotal }} 个</span>
        <span>已选择 {{ selectedProjectIds.length }} 个</span>
      </div>
      <fieldset class="project-picker">
        <legend>第 {{ currentPage }} / {{ pageCount }} 页</legend>
        <label v-for="project in projectOptions" :key="project.id" class="project-option">
          <input v-model="selectedProjectIds" type="checkbox" :value="project.id" />
          <span>
            <b>{{ project.name }}</b>
            <small>{{ project.region }} · {{ project.industry }} · {{ project.budget }}</small>
            <small>采购人：{{ project.buyer }}</small>
            <small>开标时间（数据库 bid_open_time）：{{ project.openTime }}</small>
            <small class="time-note">{{ project.timeNote }}</small>
          </span>
        </label>
        <p v-if="!projectOptions.length && !projectsLoading" class="field-help">没有找到状态为 TENDER / PLAN 的当前项目。</p>
      </fieldset>
      <nav v-if="pageCount > 1" class="project-pagination" aria-label="项目列表翻页">
        <button type="button" class="page-button" :disabled="currentPage === 1 || projectsLoading" @click="goToPage(currentPage - 1)">上一页</button>
        <button v-for="page in pageNumbers" :key="page" type="button" class="page-button" :class="{ active: currentPage === page }" :disabled="projectsLoading" @click="goToPage(page)">{{ page }}</button>
        <button type="button" class="page-button" :disabled="currentPage === pageCount || projectsLoading" @click="goToPage(currentPage + 1)">下一页</button>
      </nav>
      <p class="field-help">可以跨页勾选多个项目。项目金额缺失时显示“金额待补充”，不会当作零金额。</p>
      <label>可用投标团队槽位
        <input v-model.number="teamSlots" type="number" min="0" max="20" />
      </label>
      <button type="button" class="primary-action" :disabled="busy || !serviceReady || !companyId" @click="startTask">
        {{ busy ? '处理中…' : '创建投标决策任务' }}
      </button>
    </section>

    <section v-if="task" class="card task-card">
      <div class="section-title">
        <div><span class="eyebrow">分析任务</span><h3>{{ taskStatusLabel(task.task_status) }}</h3></div>
        <button type="button" class="secondary-link" :disabled="busy" @click="refreshTask">刷新状态</button>
      </div>
      <details><summary>查看任务编号</summary><p>任务：{{ task.task_id }}</p><p>流程：{{ task.thread_id }}</p></details>
      <p v-if="task.task_status === 'NEEDS_HUMAN_REVIEW' && !task.pending_confirmation" class="hard-stop">该任务在执行或恢复时发生错误，旧检查点不再允许重复提交。请查看错误详情后重新创建一个新任务。</p>

      <section v-if="task.pending_confirmation" class="confirmation-panel">
        <span class="eyebrow">{{ confirmationTypeLabel(task.pending_confirmation.confirmation_type) }}</span>
        <h3>需要处理资格缺口</h3>
        <p>{{ task.pending_confirmation.message }}</p>
        <p><b>影响项目：</b>{{ task.pending_confirmation.affected_project_ids.map(projectName).join('、') }}</p>
        <p v-if="task.pending_confirmation.confirmation_type === 'CRITICAL_UNKNOWN'" class="unknown-note">以下内容按项目、按资格项展示。补充一项只绑定该项目的该项要求，提交后立即重新核验；不会影响其他资格项，也不再进入审核流程。补充内容按本次任务的用户声明直接使用，未做外部真实性核验。</p>
        <div v-if="task.pending_confirmation.confirmation_type === 'CRITICAL_UNKNOWN'" class="qualification-gap-list">
          <div v-if="qualificationGaps.length" class="qualification-gap-pagination">
            <div class="qualification-gap-progress">
              <b>资格项 {{ qualificationGapPage }} / {{ qualificationGaps.length }}</b>
              <span>可在线补充项已完成 {{ completedSupplementGapCount }} / {{ onlineSupplementGaps.length }}</span>
            </div>
            <nav class="qualification-page-nav" aria-label="资格补充翻页">
              <button type="button" class="page-button" :disabled="qualificationGapPage <= 1" @click="goToQualificationGapPage(qualificationGapPage - 1)">上一项</button>
              <button type="button" class="page-button" :disabled="qualificationGapPage >= qualificationGapPageCount" @click="goToQualificationGapPage(qualificationGapPage + 1)">下一项</button>
            </nav>
          </div>
          <article v-for="gap in visibleQualificationGaps" :key="`${gap.project_id}:${gap.requirement_id}`" class="qualification-gap-card">
            <span class="eyebrow">{{ gap.project_name }}</span>
            <h4>{{ qualificationCategoryLabel(gap.requirement_category) }}</h4>
            <div class="requirement-text"><b>公告/数据库中的要求</b><p>{{ gap.requirement_text }}</p></div>
            <p><b>当前状态：</b>UNKNOWN（资格待核验）</p>
            <p><b>无法确认原因：</b>{{ gap.reason }}</p>
            <template v-if="gap.supplement_allowed && gap.supplement_key && gap.input_fields.length">
              <p><b>请逐项填写：</b>只有必填字段完整且未发现与公告冲突，才可能判定为 PASS；缺字段仍为 UNKNOWN，明确不满足则为 FAIL。</p>
              <div class="structured-supplement-form">
                <label v-for="field in gap.input_fields" :key="field.key" class="structured-field">
                  <span>{{ field.label }} <em v-if="field.required">必填</em></span>
                  <textarea
                    v-if="field.input_type === 'textarea'"
                    v-model.trim="providedFields[supplementKey(gap)][field.key]"
                    rows="3"
                    :placeholder="field.placeholder"
                  />
                  <select
                    v-else-if="field.input_type === 'select' || field.input_type === 'boolean'"
                    v-model="providedFields[supplementKey(gap)][field.key]"
                  >
                    <option value="">请选择</option>
                    <option v-for="option in field.options" :key="String(option.value)" :value="option.value">{{ option.label }}</option>
                  </select>
                  <input
                    v-else
                    v-model="providedFields[supplementKey(gap)][field.key]"
                    :type="field.input_type"
                    :min="field.min_value ?? undefined"
                    :placeholder="field.placeholder"
                  />
                  <small v-if="field.help_text">{{ field.help_text }}</small>
                </label>
              </div>
              <small>此次内容只会重新核验：{{ gap.project_name }} / {{ qualificationCategoryLabel(gap.requirement_category) }}。系统按字段完整性和公告规则核验，不再接受“符合要求”这类空泛说明。</small>
            </template>
            <p v-else class="data-gap"><b>当前不能在线补充：</b>该项目资格要求尚未完成结构化提取。请保持UNKNOWN继续分析，或查看公告原文后重新发起分析。</p>
          </article>
          <nav v-if="qualificationGaps.length > 1" class="qualification-page-nav qualification-page-nav-bottom" aria-label="资格补充翻页">
            <button type="button" class="page-button" :disabled="qualificationGapPage <= 1" @click="goToQualificationGapPage(qualificationGapPage - 1)">上一项</button>
            <span>第 {{ qualificationGapPage }} / {{ qualificationGapPageCount }} 项</span>
            <button type="button" class="page-button" :disabled="qualificationGapPage >= qualificationGapPageCount" @click="goToQualificationGapPage(qualificationGapPage + 1)">下一项</button>
          </nav>
        </div>
        <label>确认说明（可选）<input v-model="confirmationComment" /></label>
        <p v-if="confirmationNotice" class="confirmation-notice">{{ confirmationNotice }}</p>
        <p v-if="confirmationError" class="confirmation-error">{{ confirmationError }}</p>
        <div class="action-row">
          <button v-for="action in task.pending_confirmation.allowed_actions" :key="action" type="button" :class="action === 'REJECT' ? 'danger-action' : 'primary-action'" :disabled="busy" @click="submitConfirmation(action)">
            {{ confirmationSubmittingAction === action ? '正在提交…' : actionLabel(action) }}
          </button>
        </div>
      </section>
    </section>

    <template v-if="result">
      <section class="card summary-card">
        <div class="section-title">
          <div><span class="eyebrow">最终决策</span><h3>{{ taskStatusLabel(result.status) }}</h3></div>
          <span class="temporary-tag">{{ result.score_rule_version }}</span>
        </div>

        <div class="decision-overview-grid">
          <article><span>参与比较</span><b>{{ result.project_decisions.length }} 个项目</b></article>
          <article><span>可用投标团队</span><b>{{ teamSlots }} 个</b></article>
          <article><span>资格符合 / 待核验 / 不符合</span><b>{{ qualificationCounts.pass }} / {{ qualificationCounts.unknown }} / {{ qualificationCounts.fail }}</b></article>
          <article><span>最终组合</span><b>{{ portfolioItems.length }} 个项目</b></article>
        </div>

        <section class="preference-summary-card">
          <h4>本次目标与偏好</h4>
          <p>偏好已用于智谱 GLM 的解释、项目最终顺序和组合选择，但不计入客观基础分，也不能改变资格事实。</p>
          <div v-if="interpretedGoal" class="preference-grid">
            <div><b>优先行业</b><span>{{ listText(interpretedGoal.target_industries) }}</span></div>
            <div><b>优先地区</b><span>{{ listText(interpretedGoal.target_regions) }}</span></div>
            <div><b>预算范围</b><span>{{ budgetPreferenceText(interpretedGoal) }}</span></div>
            <div><b>风险偏好</b><span>{{ riskPreferenceText(interpretedGoal) }}</span></div>
            <div><b>明确排除</b><span>{{ listText(interpretedGoal.explicit_exclusions) }}</span></div>
            <div><b>优先因素</b><span>{{ listText(interpretedGoal.priority_factors) }}</span></div>
          </div>
        </section>

        <section>
          <h4>推荐投标组合</h4>
          <div v-if="portfolioItems.length" class="portfolio-list">
            <article v-for="entry in portfolioItems" :key="entry.projectId" class="portfolio-item">
              <span class="portfolio-rank">第 {{ entry.comparison?.portfolio_rank || '—' }} 名</span>
              <div>
                <h4>{{ entry.projectName }}</h4>
                <p>
                  客观基础分 {{ entry.comparison?.composite_score ?? 0 }} / 100 ·
                  {{ eligibilityLabel(entry.decision?.eligibility || entry.comparison?.eligibility || 'UNKNOWN') }} ·
                  {{ decisionLabel(entry.decision?.decision || 'INSUFFICIENT_DATA') }}
                </p>
                <p>本次偏好匹配：{{ preferenceLevelLabel(entry.comparison?.preference_match_level || 'NOT_SPECIFIED') }}</p>
              </div>
            </article>
          </div>
          <p v-else class="empty-state">当前没有项目进入最终组合。</p>
        </section>

        <section v-if="notSelectedItems.length">
          <h4>未进入组合的项目</h4>
          <ul class="excluded-project-list">
            <li v-for="entry in notSelectedItems" :key="entry.projectId">
              <b>{{ entry.projectName }}</b>
              <span>{{ notSelectedReason(entry.comparison, entry.decision) }}</span>
            </li>
          </ul>
        </section>

        <section v-if="result.resource_conflicts.length">
          <h4>资源冲突</h4>
          <ul><li v-for="text in result.resource_conflicts" :key="text">{{ humanizeText(text) }}</li></ul>
        </section>

        <details class="ai-summary-details">
          <summary>查看 AI 分析说明</summary>
          <p>{{ humanizeText(result.decision_explanation.comparison_summary) }}</p>
          <p>{{ humanizeText(result.decision_explanation.portfolio_explanation) }}</p>
        </details>
      </section>

      <section class="decision-list">
        <article v-for="item in result.project_decisions" :key="item.project_id" class="card decision-card">
          <div class="section-title">
            <div><span class="eyebrow">真实项目</span><h3>{{ item.project_name || projectName(item.project_id) }}</h3><h4>{{ decisionLabel(item.decision) }}</h4></div>
            <div class="pill-row">
              <span class="score-pill">{{ comparisonFor(item.project_id)?.composite_score ?? 0 }} / 100</span>
              <span class="pill" :class="eligibilityClass(item.eligibility)">{{ eligibilityLabel(item.eligibility) }}</span>
              <span class="pill">{{ priorityLabel(item.priority) }}</span>
            </div>
          </div>

          <p v-if="item.eligibility === 'FAIL'" class="hard-stop">存在明确硬性资格不符合：该项目固定为不建议投标，其他分项不能覆盖硬门槛。</p>
          <p v-else-if="item.eligibility === 'UNKNOWN'" class="unknown-note">资格仍待核验，可继续比较，但不能视为明确可投，也不会标为高优先级。</p>

          <section v-if="comparisonFor(item.project_id)" class="score-section">
            <div class="section-title"><h4>客观基础分计算明细</h4><span class="temporary-tag">TEMP-BID-SCORE-V2</span></div>
            <div class="score-grid">
              <div v-for="entry in scoreEntries(comparisonFor(item.project_id)!)" :key="entry.name">
                <b>{{ entry.name }}</b><span>{{ entry.value }} / {{ entry.max }}</span>
                <progress :max="entry.max" :value="entry.value" />
              </div>
            </div>
            <details v-for="dimension in comparisonFor(item.project_id)!.score_details" :key="dimension.name" class="dimension-details">
              <summary>{{ dimension.name }}：{{ dimension.score }} / {{ dimension.max_score }}</summary>
              <article v-for="detail in dimension.items" :key="detail.name" class="score-detail-item">
                <div class="section-title"><b>{{ detail.name }}</b><span>{{ detail.score }} / {{ detail.max_score }}</span></div>
                <p><b>统一规则：</b>{{ detail.rule }}</p>
                <p><b>本项目判断：</b>{{ scoreStatusLabel(detail.status) }}</p>
                <p><b>得分原因：</b>{{ detail.reason }}</p>
                <p><b>使用数据：</b>{{ detail.data_sources.map(dataSourceLabel).join('；') }}</p>
                <p v-if="detail.missing_data.length" class="data-gap"><b>缺失数据：</b>{{ detail.missing_data.join('、') }}</p>
              </article>
            </details>
            <section class="preference-match-card" :class="{ excluded: comparisonFor(item.project_id)!.preference_excluded }">
              <h4>本次偏好匹配：{{ preferenceLevelLabel(comparisonFor(item.project_id)!.preference_match_level) }}</h4>
              <p>匹配度 {{ comparisonFor(item.project_id)!.preference_match_score }} / 100。该值不计入客观基础分，但会影响 AI 最终优先级和组合选择。</p>
              <ul><li v-for="reason in comparisonFor(item.project_id)!.preference_reasons" :key="reason">{{ reason }}</li></ul>
              <p v-if="comparisonFor(item.project_id)!.preference_excluded" class="hard-stop">命中明确排除条件，本次不进入组合。</p>
            </section>
            <p v-if="comparisonFor(item.project_id)!.data_gaps.length" class="data-gap"><b>待核验：</b>{{ comparisonFor(item.project_id)!.data_gaps.join('；') }}</p>
            <p><b>组合状态：</b>{{ comparisonFor(item.project_id)!.selected_for_portfolio ? `已进入组合（第 ${comparisonFor(item.project_id)!.portfolio_rank} 位）` : '未进入当前组合' }}</p>
          </section>

          <div class="three-column">
            <div><h4>优势</h4><ul><li v-for="text in item.strengths" :key="text">{{ humanizeText(text) }}</li><li v-if="!item.strengths.length">暂无</li></ul></div>
            <div><h4>风险</h4><ul><li v-for="text in item.risks" :key="text">{{ humanizeText(text) }}</li><li v-if="!item.risks.length">暂无</li></ul></div>
            <div><h4>未知项</h4><ul><li v-for="text in item.unknowns" :key="text">{{ humanizeText(text) }}</li><li v-if="!item.unknowns.length">无</li></ul></div>
          </div>

          <div v-if="item.competition_assessment" class="competition-summary demo-competition-summary">
            <b>竞争分析：演示数据</b>
            <span>{{ item.competition_assessment.data_warning || '演示数据，不代表真实企业参与情况。' }}</span>
            <span>潜在演示企业 {{ item.competition_assessment.potential_competitor_count }} 家；不参与评分</span>
          </div>
          <div v-if="item.win_opportunity" class="opportunity-summary">
            <b>中标机会：</b><span>{{ opportunityLabel(item.win_opportunity.opportunity_level, item.win_opportunity.status) }}</span><span>不参与临时评分</span>
          </div>

          <div><h4>推荐行动</h4><ul><li v-for="text in item.recommended_actions" :key="text">{{ humanizeText(text) }}</li><li v-if="!item.recommended_actions.length">暂无</li></ul></div>
          <div class="action-row" v-if="item.eligibility !== 'FAIL'">
            <RouterLink
              v-if="validProjectId(item.project_id)"
              class="secondary-link detail-link"
              :to="competitionRoute(item)"
              target="_blank"
              rel="noopener noreferrer"
            >查看竞争分析（新标签页）</RouterLink>
          </div>
        </article>
      </section>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, type RouteLocationRaw } from 'vue-router'
import ErrorPanel from '../components/ErrorPanel.vue'
import {
  api,
  createBidDecision,
  getBidAgentStatus,
  getBidDecision,
  getBidProjects,
  resumeBidDecision,
  toApiError,
  type ApiError,
  type BidAgentStatus,
  type BidTaskRecord,
  type ConfirmationAction,
  type InterpretedUserGoal,
  type QualificationGap,
  type ProjectComparison,
  type ProjectDecision,
} from '../api'
import { useEnterpriseContextStore } from '../enterpriseContext'

const route = useRoute()
const context = useEnterpriseContextStore()
const { normalizedCompanyId: companyId } = storeToRefs(context)
const status = ref<BidAgentStatus | null>(null)
const task = ref<BidTaskRecord | null>(null)
const error = ref<ApiError | null>(null)
const busy = ref(false)
const userGoal = ref('在可用团队槽位内比较项目，保留资格硬约束，并给出可执行的投标组合建议。')
const teamSlots = ref(2)
const providedFields = ref<Record<string, Record<string, any>>>({})
const confirmationComment = ref('')
const confirmationNotice = ref('')
const confirmationError = ref('')
const confirmationSubmittingAction = ref<ConfirmationAction | ''>('')
const serviceReady = computed(() => Boolean(status.value))
const result = computed(() => task.value?.result || null)
const interpretedGoal = computed<InterpretedUserGoal | null>(() => task.value?.pending_confirmation?.interpreted_user_goal || result.value?.interpreted_user_goal || null)
const companyDisplayName = ref('当前企业')
const pageSize = 5
const currentPage = ref(1)
const pageCount = ref(1)
const projectTotal = ref(0)
const projectQuery = ref('')
const projectsLoading = ref(false)
const selectedProjectIds = ref<string[]>([])
const qualificationGapPage = ref(1)
const qualificationGapPageSize = 1
const qualificationGaps = computed<QualificationGap[]>(() => task.value?.pending_confirmation?.qualification_gaps || [])
const qualificationGapPageCount = computed(() => Math.max(1, Math.ceil(qualificationGaps.value.length / qualificationGapPageSize)))
const visibleQualificationGaps = computed(() => {
  const start = (qualificationGapPage.value - 1) * qualificationGapPageSize
  return qualificationGaps.value.slice(start, start + qualificationGapPageSize)
})
const onlineSupplementGaps = computed(() => qualificationGaps.value.filter(gap => gap.supplement_allowed && gap.supplement_key && gap.input_fields.length))
const completedSupplementGapCount = computed(() => onlineSupplementGaps.value.filter(gapRequiredFieldsComplete).length)

type DisplayProject = { id: string; name: string; region: string; industry: string; budget: string; buyer: string; openTime: string; timeNote: string }
const projectOptions = ref<DisplayProject[]>([])
const projectNameCache = ref<Record<string, string>>({})
const pageNumbers = computed(() => {
  const count = pageCount.value
  const page = currentPage.value
  const start = Math.max(1, Math.min(page - 2, Math.max(1, count - 4)))
  return Array.from({ length: Math.min(5, count) }, (_, index) => start + index)
})

function storageKey(): string { return `enterprise-profile-bid-task-id:${companyId.value}` }
function newId(prefix: string): string {
  const value = typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`
  return `${prefix}-${value.replaceAll('-', '').slice(0, 16)}`
}
function projectName(projectId: string): string {
  const decisionName = result.value?.project_decisions.find(item => item.project_id === projectId)?.project_name
  return String(decisionName || projectNameCache.value[projectId] || '项目名称待读取')
}
const qualificationCounts = computed(() => {
  const values = result.value?.project_decisions || []
  return {
    pass: values.filter(item => item.eligibility === 'PASS').length,
    unknown: values.filter(item => item.eligibility === 'UNKNOWN').length,
    fail: values.filter(item => item.eligibility === 'FAIL').length,
  }
})
type PortfolioDisplayEntry = { projectId: string; projectName: string; comparison?: ProjectComparison; decision?: ProjectDecision }
function normalizePortfolioProjectId(value: string): string | null {
  if (!result.value) return null
  const knownIds = new Set([
    ...result.value.project_comparisons.map(item => item.project_id),
    ...result.value.project_decisions.map(item => item.project_id),
  ])
  if (knownIds.has(value)) return value
  for (const id of knownIds) if (value.includes(id)) return id
  return null
}
const portfolioProjectIds = computed(() => {
  const ids = (result.value?.portfolio_recommendation || [])
    .map(normalizePortfolioProjectId)
    .filter((value): value is string => Boolean(value))
  return Array.from(new Set(ids))
})
const portfolioItems = computed<PortfolioDisplayEntry[]>(() => {
  if (!result.value) return []
  return portfolioProjectIds.value.map(projectId => ({
    projectId,
    projectName: projectName(projectId),
    comparison: comparisonFor(projectId),
    decision: result.value?.project_decisions.find(item => item.project_id === projectId),
  }))
})
const notSelectedItems = computed<PortfolioDisplayEntry[]>(() => {
  if (!result.value) return []
  const selected = new Set(portfolioProjectIds.value)
  return result.value.project_decisions.filter(item => !selected.has(item.project_id)).map(item => ({
    projectId: item.project_id,
    projectName: item.project_name || projectName(item.project_id),
    comparison: comparisonFor(item.project_id),
    decision: item,
  }))
})
function notSelectedReason(comparison?: ProjectComparison, decision?: ProjectDecision): string {
  if (decision?.eligibility === 'FAIL') return '存在明确资格不符合，不能进入投标组合。'
  if (comparison?.preference_excluded) return '命中本次目标中的明确排除条件。'
  if (comparison?.conflict_project_ids?.length) return `与更高优先级项目存在团队或关键人员冲突：${comparison.conflict_project_ids.map(projectName).join('、')}。`
  if (decision?.eligibility === 'UNKNOWN') return '资格仍待核验，且当前团队槽位优先分配给综合排序更高的项目。'
  return '受团队槽位或项目组合排序限制，未进入当前组合。'
}
function syncProjectNames(record: BidTaskRecord | null): void {
  initializeSupplementForms(record)
  const decisions = record?.result?.project_decisions || []
  for (const item of decisions) if (item.project_name) projectNameCache.value[item.project_id] = item.project_name
  const gaps = record?.pending_confirmation?.qualification_gaps || []
  for (const gap of gaps) if (gap.project_name) projectNameCache.value[gap.project_id] = gap.project_name
}
function comparisonFor(projectId: string): ProjectComparison | undefined { return result.value?.project_comparisons.find(item => item.project_id === projectId) }
function scoreEntries(item: ProjectComparison): Array<{ name: string; value: number; max: number }> {
  const values = item.temporary_score_breakdown
  return [
    { name: '资格可投性', value: Number(values['资格可投性'] || 0), max: 40 },
    { name: '企业能力匹配', value: Number(values['企业能力匹配'] || 0), max: 40 },
    { name: '资源可执行性', value: Number(values['资源可执行性'] || 0), max: 20 },
  ]
}
function displayBudget(value: string | null): string {
  if (value === null || value === '') return '金额待补充'
  const amount = Number(value)
  if (!Number.isFinite(amount)) return `预算 ${value}`
  return amount >= 100000000 ? `预算 ${(amount / 100000000).toFixed(2)}亿元` : amount >= 10000 ? `预算 ${(amount / 10000).toFixed(2)}万元` : `预算 ${amount.toFixed(2)}元`
}
function displayDate(value: string | null): string {
  if (!value) return '数据缺失'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '数据格式异常' : date.toLocaleString('zh-CN', { hour12: false })
}
function listText(values: string[]): string { return values.length ? values.join('、') : '本次未指定' }
function budgetPreferenceText(goal: InterpretedUserGoal): string {
  const min = goal.budget_preferences.min_amount
  const max = goal.budget_preferences.max_amount
  if (min == null && max == null) return '本次未指定'
  if (min != null && max != null) return `${min} 至 ${max} 元`
  return min != null ? `不低于 ${min} 元` : `不超过 ${max} 元`
}
function riskPreferenceText(goal: InterpretedUserGoal): string { if (!goal.specified_preferences.includes('risk')) return '本次未指定'; const level = goal.risk_preferences.level; return ({ LOW: '保守', MEDIUM: '平衡', HIGH: '积极' } as Record<string, string>)[level] || level }
function qualificationCategoryLabel(value: string): string { return ({ BUSINESS_REGISTRATION: '企业主体与营业执照', QUALIFICATION_CERTIFICATE: '企业资质或行政许可', PERSONNEL: '项目负责人及人员证书', PERFORMANCE: '同类项目业绩', RELATIONSHIP: '负责人、控股及管理关系', CREDIT: '信用与失信记录', CONSORTIUM: '联合体要求', UNSTRUCTURED: '资格要求尚未结构化' } as Record<string, string>)[value] || '其他资格要求' }
function requiredFieldLabel(field: string): string { const gap = task.value?.pending_confirmation?.qualification_gaps.find(item => item.supplement_key === field); return gap ? `${gap.project_name} / ${qualificationCategoryLabel(gap.requirement_category)}` : field }
function supplementKey(gap: { supplement_key: string | null }): string { return String(gap.supplement_key || '') }
function initializeSupplementForms(record: BidTaskRecord | null): void {
  for (const gap of record?.pending_confirmation?.qualification_gaps || []) {
    if (gap.supplement_key && !providedFields.value[gap.supplement_key]) providedFields.value[gap.supplement_key] = {}
  }
}
function fieldIsMissing(value: unknown): boolean { return value === null || value === undefined || value === '' }
function gapRequiredFieldsComplete(gap: QualificationGap): boolean {
  if (!gap.supplement_allowed || !gap.supplement_key || !gap.input_fields.length) return false
  const values = providedFields.value[gap.supplement_key] || {}
  return gap.input_fields.filter(field => field.required).every(field => !fieldIsMissing(values[field.key]))
}
function goToQualificationGapPage(page: number): void {
  qualificationGapPage.value = Math.min(Math.max(1, page), qualificationGapPageCount.value)
}
function firstIncompleteQualificationGapPage(pending: NonNullable<BidTaskRecord['pending_confirmation']>): number | null {
  for (let index = 0; index < pending.qualification_gaps.length; index += 1) {
    const gap = pending.qualification_gaps[index]
    if (!gap.supplement_key || !pending.required_fields.includes(gap.supplement_key)) continue
    if (!gapRequiredFieldsComplete(gap)) return Math.floor(index / qualificationGapPageSize) + 1
  }
  return null
}
function missingStructuredFields(pending: NonNullable<BidTaskRecord['pending_confirmation']>): string[] {
  const missing: string[] = []
  for (const gap of pending.qualification_gaps) {
    if (!gap.supplement_key || !pending.required_fields.includes(gap.supplement_key)) continue
    const values = providedFields.value[gap.supplement_key] || {}
    for (const field of gap.input_fields.filter(item => item.required)) {
      if (fieldIsMissing(values[field.key])) missing.push(`${gap.project_name} / ${field.label}`)
    }
  }
  return missing
}
function dataSourceLabel(value: string): string {
  const labels: Record<string, string> = {
    'project.industry': '项目行业',
    'company.capability_profile.industry_capability': '企业已确认行业能力',
    'project.technical_scope/performance_requirements': '项目技术范围与业绩要求',
    'company.capability_profile.technical_capability/similar_performance_capability': '企业技术能力与同类业绩',
    'project.region': '项目地区',
    'company.capability_profile.regional_delivery_capability': '企业地区交付能力',
    'project.budget/maximum_price': '项目预算或最高限价',
    'company.amount_experience_capability.max_amount': '企业历史最大承接金额',
    'request.available_bid_team_slots': '本次可用投标团队槽位',
    'project_team_requirements': '项目团队需求',
    'project.personnel_requirements': '项目关键人员要求',
    'company.personnel_resource_capability': '企业人员与证书资源',
  }
  return labels[value] || value
}
function scoreStatusLabel(value: string): string { return ({ PASS: '资格明确符合', UNKNOWN: '资格待核验', FAIL: '资格明确不符合', MATCH: '明确匹配', PARTIAL: '部分匹配', NO_MATCH: '明确不匹配', INSUFFICIENT_DATA: '数据不足，使用中性分' } as Record<string, string>)[value] || value }
function preferenceLevelLabel(value: string): string { return ({ HIGH: '高', MEDIUM: '中', LOW: '低', NOT_SPECIFIED: '本次未指定可计算偏好' } as Record<string, string>)[value] || value }
async function loadCompanyName(): Promise<void> {
  if (!companyId.value) return
  try {
    const payload = await api<Record<string, any>>(`/api/companies/${encodeURIComponent(companyId.value)}/profile`)
    companyDisplayName.value = String(payload?.enterprise?.name || payload?.company_name || payload?.enterprise_name || companyId.value)
  } catch { companyDisplayName.value = companyId.value }
}
async function loadProjects(page = currentPage.value): Promise<void> {
  projectsLoading.value = true
  error.value = null
  try {
    const response = await getBidProjects(projectQuery.value, page, pageSize)
    currentPage.value = response.page
    pageCount.value = Math.max(1, response.total_pages)
    projectTotal.value = response.total
    projectOptions.value = response.items.map(item => {
      projectNameCache.value[item.project_id] = item.project_name
      return {
        id: item.project_id,
        name: item.project_name,
        region: item.region,
        industry: item.industry,
        budget: displayBudget(item.budget),
        buyer: item.buyer_name,
        openTime: displayDate(item.bid_open_time),
        timeNote: item.time_field_note || 'bid_open_time仅按开标时间展示，不作为投标截止时间或评分依据。',
      }
    })
  } catch (caught) { error.value = toApiError(caught) }
  finally { projectsLoading.value = false }
}
async function searchProjects(): Promise<void> { await loadProjects(1) }
async function goToPage(page: number): Promise<void> { if (page >= 1 && page <= pageCount.value) await loadProjects(page) }
function resetForCompany(): void {
  selectedProjectIds.value = []
  currentPage.value = 1
  task.value = null
  providedFields.value = {}
  qualificationGapPage.value = 1
  projectNameCache.value = {}
  loadCompanyName()
  loadProjects(1)
}
watch(companyId, resetForCompany)
watch(
  [() => task.value?.pending_confirmation?.confirmation_id, () => qualificationGaps.value.length],
  () => { qualificationGapPage.value = 1 },
)

function humanizeText(value: string): string {
  let text = String(value || '')
  const names: Record<string, string> = { ...projectNameCache.value }
  for (const item of result.value?.project_decisions || []) if (item.project_name) names[item.project_id] = item.project_name
  for (const [id, name] of Object.entries(names)) text = text.replaceAll(id, name)
  return text
    .replace(/db-project-\d+/g, '项目名称待读取')
    .replace(/\boriginal_rank=\d+\b/g, '')
    .replace(/\bportfolio_rank=\d+\b/g, '')
    .replace(/\(\s*,?\s*\)/g, '')
    .replaceAll('performance_contract_amount_page', '同类项目合同金额证明')
    .replaceAll('contract_amount_page', '项目合同金额证明')
    .replaceAll('MANDATORY_CERT_MISSING', '缺少必需资质证明')
    .replaceAll('PERFORMANCE_AMOUNT_MISSING', '缺少业绩金额证明')
    .replaceAll('NOT_SPECIFIED', '本次未设置偏好')
    .replaceAll('CONDITIONAL_GO', '有条件建议投标')
    .replaceAll('NO_GO', '不建议投标')
    .replaceAll('UNKNOWN', '资料待核实')
    .replaceAll('PASS', '资格符合')
    .replaceAll('FAIL', '资格不符合')
    .replace(/\s+([，。；])/g, '$1')
}
function actionLabel(action: ConfirmationAction): string {
  return ({ SUPPLY_AND_CONTINUE: '补充材料并继续', ACCEPT_CONDITIONS: '接受待核验条件继续', APPROVE_FINAL: '确认最终组合', REJECT: '结束这些项目分析' } as Record<ConfirmationAction, string>)[action]
}
function taskStatusLabel(value: string): string { return ({ DECIDED: '分析完成', WAITING_USER_CONFIRMATION: '等待确认', NEEDS_HUMAN_REVIEW: '需要确认', INSUFFICIENT_DATA: '资料仍不充分', RUNNING: '正在分析', REJECTED: '已终止', COMPLETED: '已完成' } as Record<string, string>)[value] || '处理中' }
function confirmationTypeLabel(value: string): string { return value === 'CRITICAL_UNKNOWN' ? '资格资料缺口' : '最终组合确认' }
function eligibilityLabel(value: string): string { return ({ PASS: '资格符合', FAIL: '资格不符合', UNKNOWN: '资格待核验' } as Record<string, string>)[value] || value }
function priorityLabel(value: string): string { return ({ HIGH: '高优先级', MEDIUM: '中优先级', LOW: '低优先级', NOT_APPLICABLE: '不适用' } as Record<string, string>)[value] || '待确定' }
function decisionLabel(value: string): string { return ({ GO: '建议投标', NO_GO: '不建议投标', CONDITIONAL_GO: '附条件投标', INSUFFICIENT_DATA: '资料不足，暂缓决策' } as Record<string, string>)[value] || '暂未形成建议' }
function eligibilityClass(value: string): string { return `eligibility-${value.toLowerCase()}` }
function opportunityLabel(level: string, status: string): string {
  if (status === 'INSUFFICIENT_DATA') return '真实数据不足，暂不估计'
  if (status === 'NOT_APPLICABLE') return '不适用'
  return ({ HIGH: '较高', MEDIUM: '中等', LOW: '较低', UNKNOWN: '暂不能判断' } as Record<string, string>)[level] || '暂不能判断'
}
async function loadStatus(): Promise<void> { try { status.value = await getBidAgentStatus() } catch (caught) { error.value = toApiError(caught) } }
async function startTask(): Promise<void> {
  error.value = null; task.value = null; localStorage.removeItem(storageKey()); busy.value = true
  try {
    if (!companyId.value) throw new Error('没有可用企业。')
    if (!selectedProjectIds.value.length) throw new Error('请至少选择一个项目。')
    const taskId = newId('task'); const threadId = newId('thread')
    task.value = await createBidDecision({
      task_id: taskId,
      thread_id: threadId,
      company_id: companyId.value,
      user_goal: userGoal.value,
      requested_project_ids: [...selectedProjectIds.value],
      resource_constraints: { available_bid_team_slots: teamSlots.value },
      as_of_time: new Date().toISOString(),
    })
    syncProjectNames(task.value)
    localStorage.setItem(storageKey(), taskId)
  } catch (caught) { error.value = toApiError(caught) }
  finally { busy.value = false }
}
async function refreshTask(): Promise<void> {
  if (!task.value) return
  error.value = null; busy.value = true
  try { task.value = await getBidDecision(task.value.task_id); syncProjectNames(task.value) } catch (caught) { error.value = toApiError(caught) } finally { busy.value = false }
}
async function restoreLastTask(taskIdFromRoute?: string): Promise<void> {
  const taskId = taskIdFromRoute || localStorage.getItem(storageKey())
  if (!taskId) return
  error.value = null; busy.value = true
  try {
    task.value = await getBidDecision(taskId)
    syncProjectNames(task.value)
  } catch (caught) {
    const apiError = toApiError(caught)
    if (apiError.httpStatus === 404) {
      localStorage.removeItem(storageKey())
      task.value = null
      return
    }
    error.value = apiError
  } finally { busy.value = false }
}
async function submitConfirmation(action: ConfirmationAction): Promise<void> {
  const pending = task.value?.pending_confirmation
  if (!task.value || !pending || !pending.allowed_actions.includes(action)) return
  confirmationError.value = ''; confirmationNotice.value = ''
  let suppliedFields: Record<string, unknown> = {}
  if (action === 'SUPPLY_AND_CONTINUE') {
    const missing = missingStructuredFields(pending)
    if (missing.length) {
      qualificationGapPage.value = firstIncompleteQualificationGapPage(pending) || 1
      confirmationError.value = `还有 ${missing.length} 个必填字段未填写，已跳到第一处缺失项：${missing.slice(0, 3).join('、')}${missing.length > 3 ? '等' : ''}。`
      return
    }
    suppliedFields = Object.fromEntries(pending.required_fields.map(key => [key, providedFields.value[key] || {}]))
  }
  error.value = null; busy.value = true; confirmationSubmittingAction.value = action
  try {
    task.value = await resumeBidDecision(task.value.task_id, {
      confirmation_id: pending.confirmation_id,
      task_id: task.value.task_id,
      thread_id: task.value.thread_id,
      action,
      provided_fields: suppliedFields,
      comment: confirmationComment.value || undefined,
    })
    syncProjectNames(task.value)
    providedFields.value = {}; confirmationComment.value = ''; qualificationGapPage.value = 1
    confirmationNotice.value = action === 'SUPPLY_AND_CONTINUE' ? '补充内容已保存，并已立即用于对应资格项重新核验。' : '选择已提交，分析结果已更新。'
  } catch (caught) { const apiError = toApiError(caught); error.value = apiError; confirmationError.value = apiError.userMessage }
  finally { busy.value = false; confirmationSubmittingAction.value = '' }
}
function validProjectId(value: string): boolean { return /^[A-Za-z0-9._:-]+$/.test(value) }
function competitionRoute(item: ProjectDecision): RouteLocationRaw {
  const current = result.value
  if (!current) return '/bid-decision'
  return { name: 'competition-detail', params: { projectId: item.project_id }, query: {
    companyId: current.company_id,
    companyProfileVersion: current.company_profile_version,
    projectVersion: item.project_version,
    competitionDataVersion: current.competition_data_versions?.[item.project_id],
    taskId: current.task_id,
    threadId: current.thread_id,
  } }
}

onMounted(async () => {
  await Promise.all([loadStatus(), loadCompanyName()])
  await loadProjects(1)
  const taskFromRoute = Array.isArray(route.query.taskId) ? route.query.taskId[0] : route.query.taskId
  await restoreLastTask(taskFromRoute || undefined)
})
</script>

<style scoped>
.bid-decision-page{display:grid;gap:1rem}.decision-hero,.section-title,.status-card{display:flex;justify-content:space-between;gap:1rem;align-items:flex-start}.service-badge,.pill,.temporary-tag,.score-pill{border-radius:999px;padding:.45rem .75rem;font-weight:700}.service-badge.ready,.eligibility-pass{background:#e7f8ef;color:#176b43}.service-badge.offline,.eligibility-fail{background:#fde9e9;color:#9d2323}.eligibility-unknown{background:#fff4d6;color:#745400}.temporary-rule-warning{border-left:5px solid #b88b2d;background:#fff8e7}.temporary-rule-warning div{display:flex;gap:.7rem;align-items:center}.temporary-rule-warning p{margin:.25rem 0}.temporary-tag{background:#fff0c2;color:#745400;width:max-content}.score-pill{background:#173d36;color:#fff}.status-card{flex-wrap:wrap}.status-grid{display:grid;grid-template-columns:repeat(3,minmax(170px,1fr));gap:.75rem;flex:1}.status-grid div{display:grid;gap:.25rem}.create-card,.task-card,.summary-card,.competition-detail{display:grid;gap:1rem}.search-row{display:grid;grid-template-columns:1fr auto;gap:.75rem;align-items:end}.project-search-summary{display:flex;justify-content:space-between;gap:1rem;color:#536d68}.project-picker{display:grid;gap:.7rem;border:1px solid #dbe5e2;border-radius:.75rem;padding:1rem}.project-option{display:flex;gap:.7rem;padding:.8rem;border:1px solid #e3e9e7;border-radius:.65rem;background:#fff}.project-option input{width:auto;margin-top:.2rem}.project-option span{display:grid;gap:.25rem}.project-option small{color:#667085;font-weight:400}.project-pagination,.action-row,.pill-row{display:flex;justify-content:center;align-items:center;gap:.5rem;flex-wrap:wrap}.page-button{min-width:40px;padding:.55rem .75rem;background:#fff;color:#176b5d;border:1px solid #c6d8d4}.page-button.active{background:#196b5d;color:#fff}.field-help{margin:0;color:#667085}.confirmation-panel{padding:1rem;border:1px solid #e4c25f;border-radius:.75rem;background:#fffaf0}.supplement-fields{display:grid;gap:.8rem;padding:1rem;background:#fff}.confirmation-notice,.confirmation-error{padding:.7rem;border-radius:.5rem;font-weight:700}.confirmation-notice{background:#e7f8ef;color:#176b43}.confirmation-error,.hard-stop{background:#fde9e9;color:#8e1f1f}.decision-list{display:grid;grid-template-columns:minmax(0,1fr);gap:1rem;align-items:start}.decision-card{display:grid;gap:1rem;align-self:start;min-width:0}.unknown-note,.data-gap,.demo-warning{padding:.75rem;background:#fff4d6;border-left:4px solid #b88b2d}.hard-stop{padding:.75rem;border-left:4px solid #9d2323}.score-section{display:grid;gap:.75rem;padding:1rem;border:1px solid #dce7e4;border-radius:.75rem;background:#f8fbfa}.score-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.75rem}.score-grid div{display:grid;gap:.35rem;padding:.7rem;background:#fff;border-radius:.5rem}.score-grid progress{width:100%}.three-column{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem}.two-column{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem}.competition-summary,.opportunity-summary{display:flex;gap:.7rem;flex-wrap:wrap;padding:.75rem;border-radius:.5rem;background:#f5f7fa}.demo-competition-summary{border:1px solid #e4c25f;background:#fff8e7}.detail-link{display:inline-flex;text-decoration:none}.action-row{justify-content:flex-start}.score-rule-details{margin-top:.75rem}.rule-table-wrap{overflow-x:auto}.rule-table{width:100%;border-collapse:collapse;background:#fff}.rule-table th,.rule-table td{border:1px solid #dce7e4;padding:.65rem;text-align:left}.preference-panel,.preference-match-card{padding:1rem;border:1px solid #b9d7d0;border-radius:.75rem;background:#f2faf8}.preference-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.7rem}.preference-grid div{display:grid;gap:.25rem}.time-note{color:#8a5b00!important}.qualification-gap-list{display:grid;gap:1rem}.qualification-gap-pagination{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:.75rem 1rem;border:1px solid #b9d7d0;border-radius:.65rem;background:#f2faf8}.qualification-gap-progress{display:grid;gap:.2rem}.qualification-gap-progress span{color:#536d68;font-size:.9rem}.qualification-page-nav{display:flex;align-items:center;justify-content:center;gap:.65rem;flex-wrap:wrap}.qualification-page-nav-bottom{padding-top:.25rem}.structured-supplement-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:.85rem;padding:.85rem;background:#f8fbfa;border:1px solid #dce7e4;border-radius:.65rem}.structured-field{display:grid;gap:.35rem;align-content:start}.structured-field span{font-weight:700}.structured-field em{font-style:normal;font-size:.75rem;color:#9d2323;background:#fde9e9;border-radius:999px;padding:.15rem .4rem}.structured-field textarea{min-height:90px}.qualification-gap-card{padding:1rem;border:1px solid #e4c25f;border-radius:.75rem;background:#fff}.requirement-text{padding:.75rem;background:#f7f8fa;border-radius:.5rem}.requirement-text p{white-space:pre-wrap;margin:.35rem 0}.dimension-details{border:1px solid #dce7e4;border-radius:.6rem;background:#fff}.dimension-details summary{padding:.75rem;font-weight:800;cursor:pointer}.score-detail-item{padding:.8rem;border-top:1px solid #e7eeec}.score-detail-item p{margin:.35rem 0}.preference-match-card.excluded{border-color:#d88;background:#fff3f3}@media(max-width:1100px){.three-column{grid-template-columns:1fr}}@media(max-width:820px){.decision-hero,.section-title,.status-card{display:grid}.status-grid{grid-template-columns:1fr}.search-row{grid-template-columns:1fr}.qualification-gap-pagination{display:grid}.qualification-page-nav{justify-content:flex-start}}

.decision-overview-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.75rem}.decision-overview-grid article{display:grid;gap:.25rem;padding:1rem;border:1px solid #dce7e4;border-radius:.75rem;background:#f8fbfa}.decision-overview-grid span{color:#667085;font-size:.88rem}.decision-overview-grid b{font-size:1.05rem;color:#173d36}.preference-summary-card{padding:1rem;border:1px solid #b9d7d0;border-radius:.75rem;background:#f2faf8}.preference-summary-card h4,.portfolio-item h4{margin:.1rem 0}.portfolio-list{display:grid;gap:.75rem}.portfolio-item{display:grid;grid-template-columns:auto 1fr;gap:1rem;align-items:flex-start;padding:1rem;border:1px solid #cfe0dc;border-radius:.75rem;background:#fff}.portfolio-item p{margin:.3rem 0;color:#536d68}.portfolio-rank{display:grid;place-items:center;min-width:72px;padding:.5rem .7rem;border-radius:999px;background:#196b5d;color:#fff;font-weight:800}.excluded-project-list{display:grid;gap:.65rem;padding:0;list-style:none}.excluded-project-list li{display:grid;gap:.25rem;padding:.85rem;border-left:4px solid #b88b2d;background:#fff8e7}.excluded-project-list span{color:#667085}.ai-summary-details{padding:1rem;border:1px solid #dce7e4;border-radius:.75rem;background:#f8faf9}.ai-summary-details p{line-height:1.75}.secondary-link,.detail-link{display:inline-flex!important;align-items:center!important;justify-content:center!important;min-height:42px!important;padding:.65rem 1rem!important;border:1px solid #176b5d!important;border-radius:9px!important;background:#fff!important;color:#176b5d!important;font-weight:750!important;text-decoration:none!important}.secondary-link:hover,.detail-link:hover{background:#edf7f4!important;color:#104f45!important;border-color:#104f45!important}.secondary-link:disabled{background:#eef2f1!important;color:#667b78!important;border-color:#cbd5d2!important}.primary-action{background:#196b5d!important;color:#fff!important;border:1px solid #196b5d!important}.primary-action:hover{background:#104f45!important;color:#fff!important}.danger-action{background:#9d2323!important;color:#fff!important;border:1px solid #9d2323!important}.danger-action:hover{background:#751818!important;color:#fff!important}@media(max-width:900px){.decision-overview-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:560px){.decision-overview-grid{grid-template-columns:1fr}.portfolio-item{grid-template-columns:1fr}.portfolio-rank{width:max-content}}
.bid-decision-page button:disabled,
.bid-decision-page button:disabled:hover,
.bid-decision-page .primary-action:disabled,
.bid-decision-page .primary-action:disabled:hover,
.bid-decision-page .secondary-link:disabled,
.bid-decision-page .secondary-link:disabled:hover,
.bid-decision-page .danger-action:disabled,
.bid-decision-page .danger-action:disabled:hover,
.bid-decision-page .page-button:disabled,
.bid-decision-page .page-button:disabled:hover {
  background: #e3e8e6 !important;
  color: #596966 !important;
  border-color: #c8d1cf !important;
  box-shadow: none !important;
  transform: none !important;
  cursor: not-allowed !important;
  opacity: 1 !important;
}
</style>
