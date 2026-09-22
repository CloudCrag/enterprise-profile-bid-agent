export type Gap = {
  gap_id?: string
  target_layer: 'fact' | 'capability' | 'decision' | string
  target_code: string
  importance?: string
  gap_status?: string
  reason_summary?: string
  resolution_type?: string
  question_item_id?: string | null
  review_task_ids?: string[]
  waiting_for_user?: boolean
  waiting_for_review?: boolean
}

export type GapInventory = {
  gaps?: Gap[]
  gap_details?: Gap[]
  grouped_gap_details?: Record<string, Gap[]>
  gap_summary?: Record<string, number>
  business_status?: string
  decision_context_status?: Record<string, unknown> | null
}

export const TARGET_NAMES: Record<string, string> = {
  business_registration: '工商主体', qualification: '资质证书', personnel: '人员',
  personnel_certificate: '人员证书', performance: '历史业绩', bid_participation: '历史投标',
  bid_award: '历史中标', fulfillment: '履约记录', buyer_relationship: '采购人关系事实',
  risk_penalty_credit: '风险与信用', enterprise_material: '企业材料', other_enterprise_fact: '其他事实',
  industry_capability: '行业能力', technical_capability: '技术能力',
  similar_performance_capability: '同类业绩能力', regional_delivery_capability: '地区交付能力',
  amount_experience_capability: '金额承接能力', personnel_resource_capability: '人员与资源能力',
  buyer_relationship_capability: '采购人关系能力', tender_performance_capability: '历史投标表现',
  strategic_industries: '战略行业', strategic_regions: '战略地区', budget_preference: '预算偏好',
  procurement_method_preferences: '招标方式偏好', consortium_acceptance: '联合体接受情况',
  risk_preference: '风险偏好', max_concurrent_projects: '并行项目数量',
  personnel_resource_constraints: '人员资源约束', explicit_exclusions: '明确排除项',
  key_buyers: '重点采购人', current_business_goals: '当前经营目标',
}

export function withRunAssociations(inventory: GapInventory | null | undefined, run: Record<string, unknown> | null): GapInventory | null {
  if (!inventory) return null
  if (inventory.gap_details) return inventory
  const plan = (run?.question_plan as { question_items?: Array<Record<string, unknown>> } | undefined)
  const tasks = (run?.pending_review_items as Array<Record<string, unknown>> | undefined) ?? []
  const questions = new Map<string, string>()
  for (const item of plan?.question_items ?? []) {
    questions.set(`${item.target_layer}:${item.target_code}`, String(item.question_item_id))
  }
  const reviewMap = new Map<string, string[]>()
  for (const task of tasks) {
    const key = `${task.target_layer}:${task.target_code}`
    reviewMap.set(key, [...(reviewMap.get(key) ?? []), String(task.review_task_id)])
  }
  const details = (inventory.gaps ?? []).map((gap) => {
    const key = `${gap.target_layer}:${gap.target_code}`
    const reviewIds = reviewMap.get(key) ?? []
    return {
      ...gap,
      question_item_id: questions.get(key) ?? null,
      review_task_ids: reviewIds,
      waiting_for_user: questions.has(key) && reviewIds.length === 0,
      waiting_for_review: reviewIds.length > 0,
    }
  })
  return { ...inventory, gap_details: details }
}
