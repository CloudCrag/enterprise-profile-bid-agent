const labels: Record<string, string> = {
  industry_capability: '行业能力',
  technical_capability: '技术能力',
  similar_performance_capability: '同类业绩能力',
  regional_delivery_capability: '地区交付能力',
  amount_experience_capability: '金额承接能力',
  amount_capacity: '金额承接能力',
  personnel_resource_capability: '人员和资源能力',
  buyer_relationship_capability: '采购人关系',
  buyer_relationship: '采购人关系',
  tender_performance_capability: '历史投标表现',
  historical_bidding_performance: '历史投标表现',
  supported: '证据充分',
  partially_supported: '部分证据支持',
  ambiguous: '需要进一步确认',
  insufficient_data: '信息不足',
  available: '信息完整',
  partial: '信息不完整',
  missing_data: '缺少数据',
  PENDING: '等待审核',
  APPROVED: '已通过',
  REJECTED: '已驳回',
  NEEDS_MORE_INFORMATION: '需要补充信息',
  COMPLETED: '已完成',
  COMPLETED_WITH_PENDING_REVIEW: '本轮已完成，部分信息等待核对',
  WAITING_USER_INPUT: '等待用户补充信息',
  COMPLETED_WITH_PARTIAL_COVERAGE: '已完成，部分信息不足',
  WAITING_REVIEW: '等待人工核对',
  WAITING_DATA_PROVIDER: '等待数据',
  PROVIDER_NOT_CONFIGURED: '数据来源尚未配置',
  MANUAL_INTERVENTION_REQUIRED: '需要人工处理',
  RUNNING: '正在处理',
  FAILED: '处理失败',
  LOCAL_PROFILE: '本地真实企业画像',
  HEALTHY: '正常',
  UNKNOWN: '状态未知',
  POLICY_NOT_CONFIRMED: '评价规则尚未最终确认',
  RULES_CONFIRMED: '评分规则已启用',
  CORE_RETAINED: '重点保留',
  RETAINED: '普通保留',
  DELETE_CANDIDATE: '已删减',
  DETERMINISTIC_IMPLEMENTED: '可直接计算',
  DETERMINISTIC_NEEDS_FEATURE: '缺少计算所需数据',
  DATA_RESOLVABLE: '补充企业资料',
  PROVIDER_RESOLVABLE: '通过数据接口补充',
  FEATURE_RESOLVABLE: '系统整理后计算',
  CALIBRATION_REQUIRED: '评价标准待校准',
  MODEL_CONTENT_CONFLICT: '评价规则存在冲突',
  EXCLUDED_BY_MODEL_SELECTION: '不参与当前评价',
  SCORED: '已评价',
  MISSING_DATA: '缺少数据',
  PENDING_RULE: '评价规则待确认',
  API: '外部数据接口',
  MANUAL_REVIEW: '人工核对',
  USER_INPUT: '用户补充',
  NOT_RESOLVABLE: '暂时无法补齐',
  CAPABILITY_SEMANTIC: '能力分析结果核对',
  FACT_CANDIDATE: '企业事实核对',
  USER_CONFIRMED: '用户已确认',
  DERIVED: '系统根据事实计算',
  active: '有效',
  inactive: '已停用',
  verified: '已核验',
  unverified: '尚未核验',
  conflicting: '信息冲突',
  crawler: '公开信息采集',
  official_api: '官方数据接口',
  external_data: '外部数据',
  user_upload: '用户提交材料',
  manual_review: '人工核对',
  legacy_excel: '历史表格',
  legacy_csv: '历史数据表',
  SYSTEM_DEFAULT: '系统通用画像任务',
  system_derived: '系统计算',
  SUCCESS: '成功',
  NOT_RUN: '尚未运行',
  NOT_CHECKED: '尚未检查',
  missing: '尚未提供',
  not_provided: '尚未确认',
  supporting_material_required: '需要补充证明材料',
  user_confirmation_required: '需要用户确认',
  capability_review_required: '需要核对能力信息',
  conflict_review_required: '需要确认冲突信息',
  fact_verification_required: '需要核验企业事实',
  manual_review_required: '需要人工核对',
  pending: '等待处理',
  resolved: '已解决',
}

export function uiLabel(value: unknown, fallback = '待确认'): string {
  if (value == null || value === '') return fallback
  const key = String(value)
  return labels[key] ?? fallback
}

export function capabilityLabel(value: unknown, suppliedName?: unknown): string {
  const key = value == null ? '' : String(value)
  if (labels[key]) return labels[key]
  if (typeof suppliedName === 'string' && suppliedName.trim() && !/[_A-Z]{3,}/.test(suppliedName)) return suppliedName
  return '企业能力'
}

export function providerLabel(value: unknown, displayName?: unknown): string {
  const key = value == null ? '' : String(value)
  if (labels[key]) return labels[key]
  if (typeof displayName === 'string' && displayName.trim()) return displayName
  return '企业数据来源'
}

export function nodeLabel(value: unknown): string {
  const nodes: Record<string, string> = {
    load_company_identity: '确认企业身份',
    load_current_profiles: '读取企业画像',
    load_evaluation_model: '读取评价规则',
    resolve_active_indicator_set: '确定本次评价指标',
    derive_features: '整理可用数据',
    evaluate_active_indicators: '计算指标结果',
    classify_evaluation_gaps: '识别缺少的信息',
    build_data_acquisition_plan: '制定数据补充计划',
    call_enterprise_data_provider: '获取企业数据',
    normalize_provider_results: '整理企业数据',
    ingest_facts_or_create_reviews: '写入事实或等待核对',
    wait_for_review: '等待人工核对',
    refresh_profiles_after_review: '更新企业画像',
    aggregate_secondary_dimensions: '汇总二级维度',
    aggregate_primary_dimensions: '汇总一级维度',
    build_evaluation_profile: '生成企业评价',
    build_evaluation_card: '生成评价摘要',
    finalize: '完成',
  }
  return nodes[String(value ?? '')] ?? '处理中'
}
