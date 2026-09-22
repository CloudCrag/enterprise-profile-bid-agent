<template>
  <section class="card gap-panel">
    <h3>{{ title }}</h3>
    <div v-if="inventory?.business_status === 'DECISION_CONTEXT_RECONFIRMATION_REQUIRED'" class="error">
      事实或能力画像已更新，决策画像上下文需要重新确认。系统没有自动沿用或修改长期决策。
    </div>
    <p v-if="!details.length" class="success">当前任务相关缺口为0，已完成本阶段核对。</p>
    <template v-else>
      <div v-for="group in groups" :key="group.code" class="gap-group">
        <h4 v-if="group.items.length">{{ group.name }}（{{ group.items.length }}）</h4>
        <article v-for="gap in group.items" :key="gap.gap_id || `${gap.target_layer}:${gap.target_code}`" class="gap-item">
          <div>
            <strong>{{ targetName(gap.target_code) }}</strong>
          </div>
          <p>{{ friendlyReason(gap) }}</p>
          <div class="badges">
            <span class="badge">{{ importanceName(gap.importance) }}</span>
            <span class="badge">{{ uiLabel(gap.gap_status, '等待补充') }}</span>
            <span class="badge">{{ uiLabel(gap.resolution_type, '需要进一步处理') }}</span>
            <span v-if="gap.waiting_for_user" class="badge success-badge">等待用户</span>
            <span v-if="gap.waiting_for_review" class="badge warning-badge">等待审核</span>
          </div>
          <details v-if="gap.question_item_id || gap.review_task_ids?.length">
            <summary>查看内部处理记录</summary>
            <small>问题记录：{{ gap.question_item_id || '无' }}</small><br />
            <small>审核记录：{{ gap.review_task_ids?.join('、') || '无' }}</small>
          </details>
        </article>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { TARGET_NAMES, type Gap, type GapInventory } from '../profileUi'
import { uiLabel } from '../uiText'

const props = defineProps<{ title: string; inventory: GapInventory | null | undefined }>()
const details = computed(() => props.inventory?.gap_details ?? props.inventory?.gaps ?? [])
const groups = computed(() => [
  { code: 'fact', name: '需要补充的企业事实', items: details.value.filter((item) => item.target_layer === 'fact') },
  { code: 'capability', name: '需要进一步说明的企业能力', items: details.value.filter((item) => item.target_layer === 'capability') },
  { code: 'decision', name: '需要确认的经营偏好', items: details.value.filter((item) => item.target_layer === 'decision') },
])
function targetName(code: string): string { return TARGET_NAMES[code] ?? '待补企业信息' }
function importanceName(value?: string): string {
  return ({ blocking: '必须补充', important: '重要信息', optional: '可选信息' } as Record<string, string>)[value || ''] || '重要信息'
}
function friendlyReason(gap: Gap): string {
  const name = targetName(gap.target_code)
  if (gap.target_layer === 'decision') return `为了让后续推荐更符合企业实际情况，需要确认“${name}”。`
  if (gap.gap_status === 'ambiguous') return `现有“${name}”信息存在不确定之处，需要进一步说明或核对。`
  if (gap.gap_status === 'insufficient_data') return `现有资料不足以判断“${name}”，建议补充相关说明或证明材料。`
  if (gap.gap_status === 'conflicting') return `系统中存在不同的“${name}”记录，需要确认哪项信息准确。`
  if (gap.gap_status === 'unverified') return `“${name}”尚未完成核验，需要补充可信来源或证明材料。`
  return `当前任务需要使用“${name}”，但企业画像中还没有足够的信息。`
}
</script>
