<template>
  <section class="profile-page">
    <header class="page-header">
      <div><span class="eyebrow">企业画像</span><h2>{{ data?.enterprise?.name || '企业信息' }}</h2></div>
      <button class="primary-action" @click="setWorkspaceView(workspaceView==='assistant'?'profile':'assistant')">
        {{ workspaceView === 'assistant' ? '返回企业画像' : '使用画像助手' }}
      </button>
    </header>
    <div class="tabs profile-workspace-tabs">
      <button :class="{active:workspaceView==='profile'}" @click="setWorkspaceView('profile')">企业画像</button>
      <button :class="{active:workspaceView==='assistant'}" @click="setWorkspaceView('assistant')">画像助手</button>
      <button :class="{active:workspaceView==='evaluation'}" @click="setWorkspaceView('evaluation')">企业评价</button>
    </div>
    <div id="profile-check-result"></div>
    <Agent v-if="workspaceView==='assistant'" embedded @show-profile="setWorkspaceView('profile')" @open-evaluation="setWorkspaceView('evaluation')" @profile-updated="loadProfile(route.params.companyId)" />
    <Evaluation v-if="workspaceView==='evaluation'" embedded @open-assistant="setWorkspaceView('assistant')" @show-profile="setWorkspaceView('profile')" />
    <template v-if="workspaceView==='profile'">
    <div v-if="error" class="error">{{ error }}</div>
    <div v-else-if="!data" class="card empty-state">正在加载企业画像…</div>
    <template v-else>
      <div v-if="profileGapCount" class="notice profile-gap-notice">
        <div><b>{{ profileGapCount }} 项信息尚未形成有效结论</b></div>
        <div class="button-row"><button @click="setWorkspaceView('assistant')">现在补充</button><button class="secondary" @click="setWorkspaceView('evaluation')">查看企业评价</button></div>
      </div>
      <section class="card profile-conclusion">
        <span class="eyebrow">结论摘要</span>
        <h3>{{ profileConclusion }}</h3>
        <div v-if="evaluation" class="conclusion-evaluation">
          <b>企业评价：{{ evaluationScore }}</b>
          <span>数据覆盖率 {{ evaluationCoverage }}</span>
          <button class="link-button" @click="setWorkspaceView('evaluation')">查看评价详情 →</button>
        </div>
      </section>

      <div class="tabs friendly-tabs">
        <button v-for="item in userTabs" :key="item" :class="{active:tab===item}" @click="tab=item">{{ item }}</button>
        <button class="technical-tab" :class="{active:tab==='数据与证据'}" @click="tab='数据与证据'">数据与证据</button>
      </div>

      <div v-if="tab==='画像概览'" class="profile-overview">
        <section class="card intelligent-overview">
          <div class="overview-heading">
            <div><span class="eyebrow">综合画像结论</span><h3>企业当前投标特征</h3></div>
            <span class="overview-update-note">随画像更新自动调整</span>
          </div>
          <div class="overview-insights">
            <article v-for="item in overviewInsights" :key="item.title">
              <span>{{ item.label }}</span>
              <h4>{{ item.title }}</h4>
              <ul v-if="item.lines?.length" class="overview-insight-lines">
                <li v-for="line in item.lines" :key="line">{{ line }}</li>
              </ul>
              <p v-else>{{ item.text }}</p>
            </article>
          </div>
          <div class="overview-actions">
            <button class="link-button" @click="tab='事实画像'">查看事实</button>
            <button class="link-button" @click="tab='能力画像'">查看能力</button>
            <button class="link-button" @click="tab='决策画像'">查看经营偏好</button>
          </div>
        </section>
      </div>

      <div v-else-if="tab==='事实画像'" class="fact-result-list">
        <article v-for="group in factGroups" :key="group.type" class="card fact-result-card">
          <div><h3>{{ factTypeName(group.type) }}</h3><p class="fact-summary">{{ group.summary }}</p></div>
        </article>
      </div>

      <div v-else-if="tab==='能力画像'" class="grid capability-grid">
        <article v-for="domain in visibleCapabilityDomains" :key="domain.capability_type" class="card capability-card">
          <div class="capability-heading"><span class="capability-symbol">◆</span><div><h3>{{ capabilityName(domain.capability_type) }}</h3><span class="status-pill" :class="domain.support_status">{{ supportName(domain.support_status) }}</span></div></div>
          <ul class="capability-conclusion-list">
            <li v-for="(line,index) in capabilityConclusionLines(domain)" :key="line" :class="{primary:index===0}">{{ line }}</li>
          </ul>
          <details v-if="additionalCapabilityEvidence(domain).length"><summary>查看依据</summary>
            <ul class="result-list"><li v-for="text in additionalCapabilityEvidence(domain)" :key="text">{{ friendlyCapabilityText(text) }}</li></ul>
          </details>
        </article>
        <article v-if="missingCapabilityDomains.length" class="card capability-card capability-missing-card">
          <div class="capability-heading"><span class="capability-symbol">◇</span><div><h3>尚待补充的能力</h3><span class="status-pill">资料不足</span></div></div>
          <ul class="result-list">
            <li v-for="domain in missingCapabilityDomains" :key="domain.capability_type" class="missing-capability-item">
              <span><b>{{ capabilityName(domain.capability_type) }}</b>：{{ missingCapabilityReason(domain.capability_type) }}</span>
              <button
                v-if="domain.capability_type==='personnel_resource_capability'"
                type="button"
                class="link-button"
                @click="openCapabilityAssistant(domain.capability_type)"
              >补充这项信息 →</button>
            </li>
          </ul>
        </article>
      </div>

      <div v-else-if="tab==='决策画像'" class="decision-list">
        <section class="card decision-display-guide">
          <div><h3>企业确认的经营方向和投标边界</h3><p>这里仅展示已经确认的内容。新增或修改信息请使用画像助手，更新后会自动返回企业画像。</p></div>
          <button @click="openDecisionAssistant">前往画像助手修改</button>
        </section>
        <div v-if="!decisionFields.length" class="card empty-state">尚未填写经营偏好。画像成果暂时保留为空，不会由系统猜测。</div>
        <article v-for="field in decisionFields" :key="field.field_code" class="card decision-row">
          <div><h3>{{ field.field_name }}</h3><p>{{ decisionHint(field.field_code) }}</p></div>
          <div class="decision-value"><span class="status-pill" :class="{supported:field.confirmation_status==='confirmed'}">{{ field.confirmation_status==='confirmed' ? '已确认' : '待填写' }}</span><b>{{ displayDecisionValue(field) }}</b></div>
        </article>
      </div>

      <div v-else>
        <details class="technical-panel"><summary>企业事实明细</summary><div v-for="(items,type) in factsByType" :key="type" class="card"><h3>{{ factTypeName(String(type)) }}（{{items.length}}）</h3><div class="table-wrap"><table><tr v-for="fact in items" :key="fact.fact_id"><td><b>事实记录</b><details><summary>查看记录编号和原始数据</summary><code>{{ fact.fact_id }}</code><pre>{{ JSON.stringify(fact.payload,null,2) }}</pre></details></td><td>{{ uiLabel(fact.verification_status) }} / {{ uiLabel(fact.fact_status) }}</td><td>{{ uiLabel(fact.source?.source_type, '来源待确认') }}<br>{{ fact.temporal?.available_at }}</td></tr></table></div></div></details>
        <details class="technical-panel"><summary>企业信息标签</summary><div class="table-wrap"><table><thead><tr><th>层级</th><th>编码</th><th>名称</th><th>状态</th><th>来源事实</th></tr></thead><tbody><tr v-for="item in data.tag_profile.tags" :key="item.tag_code"><td>{{item.primary_name}} / {{item.secondary_name}}</td><td>{{item.tag_code}}</td><td>{{item.tag_name}}</td><td>{{uiLabel(item.data_status)}}</td><td>{{item.supporting_facts?.fact_ids?.join(', ') || '暂无关联事实'}}</td></tr></tbody></table></div></details>
      </div>
    </template>
    </template>
  </section>
</template>
<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { capabilityLabel, uiLabel } from '../uiText'
import Agent from './Agent.vue'
import Evaluation from './Evaluation.vue'
const route=useRoute(),router=useRouter(), data=ref<any>(), evaluation=ref<any>(null), error=ref(''), tab=ref('画像概览'),gapInventory=ref<any>(null)
type WorkspaceView='profile'|'assistant'|'evaluation'
const initialView=route.query.view==='assistant'?'assistant':route.query.view==='evaluation'?'evaluation':'profile'
const workspaceView=ref<WorkspaceView>(initialView)
const editingPreferences=ref(false),savingPreferences=ref(false),preferenceMessage=ref('')
const emptyDraft=()=>({strategic_industries:'',strategic_regions:'',budget_minimum:null as number|null,budget_maximum:null as number|null,procurement_method_preferences:'',consortium_acceptance:'',risk_preference:'',max_concurrent_projects:null as number|null,personnel_resource_constraints:'',explicit_exclusions:'',key_buyers:'',current_business_goals:''})
const preferenceDraft=reactive(emptyDraft())
let originalPreferenceValues:Record<string,unknown>={}
const userTabs=['画像概览','事实画像','能力画像','决策画像']
const factsByType=computed(()=>{const out:Record<string,any[]>={};for(const fact of data.value?.fact_profile?.facts||[])(out[fact.fact_type]??=[]).push(fact);return out})
const topFactTypes=computed(()=>Object.entries(factsByType.value).map(([key,value])=>[key,value.length] as [string,number]).sort((a,b)=>b[1]-a[1]).slice(0,5))
const supportedCapabilityCount=computed(()=>data.value?.capability_profile?.capability_domains?.filter((item:any)=>['supported','partially_supported'].includes(item.support_status)).length||0)
const supportedDomains=computed(()=>data.value?.capability_profile?.capability_domains?.filter((item:any)=>['supported','partially_supported','ambiguous'].includes(item.support_status))||[])
const visibleCapabilityDomains=computed(()=>data.value?.capability_profile?.capability_domains?.filter((item:any)=>item.support_status!=='insufficient_data')||[])
const missingCapabilityDomains=computed(()=>data.value?.capability_profile?.capability_domains?.filter((item:any)=>item.support_status==='insufficient_data')||[])
const confirmedDecisions=computed(()=>data.value?.decision_profile?.decision_fields?.filter((item:any)=>item.confirmation_status==='confirmed')||[])
const confirmedDecisionCount=computed(()=>confirmedDecisions.value.length)
const decisionFields=computed(()=>data.value?.decision_profile?.decision_fields||[])
const profileGapCount=computed(()=>gapInventory.value?.gap_summary?.gap_count||gapInventory.value?.item_count||0)
const evaluationScore=computed(()=>formatScore(evaluation.value?.summary?.official_total_score??evaluation.value?.summary?.active_44_normalized_observed_score??evaluation.value?.summary?.normalized_observed_score))
const evaluationCoverage=computed(()=>`${Math.round(Number(evaluation.value?.summary?.active_44_coverage_ratio||0)*100)}%`)
const evaluatedDimensions=computed(()=>evaluation.value?.primary_dimensions?.filter((item:any)=>item.normalized_observed_score!=null)||[])
const factGroupOrder=['business_registration','other_enterprise_fact','personnel','personnel_certificate','qualification','certificate','performance','bid_award','fulfillment','bid_participation','buyer_relationship','intellectual_property','risk_penalty_credit','risk_credit','tax_credit','branch_office']
const factGroups=computed(()=>Object.entries(factsByType.value).map(([type,items])=>({
  type,
  count:items.length,
  summary:summarizeFactGroup(type,items),
})).sort((a,b)=>{
  const ai=factGroupOrder.indexOf(a.type),bi=factGroupOrder.indexOf(b.type)
  return (ai<0?999:ai)-(bi<0?999:bi)
}))
const factHighlights=computed(()=>factGroups.value.map(item=>`${factTypeName(item.type)}：${item.summary}`))
const profileConclusion=computed(()=>{
  const abilityNames=supportedDomains.value
    .filter((item:any)=>['supported','partially_supported'].includes(item.support_status))
    .slice(0,3).map((item:any)=>capabilityName(item.capability_type))
  const abilityText=abilityNames.length?`现有事实已对${abilityNames.join('、')}形成支持`:'现有事实尚不足以形成明确能力结论'
  const preferenceText=confirmedDecisionCount.value?`，并已确认 ${confirmedDecisionCount.value} 项经营偏好`:'，经营偏好仍待企业确认'
  return `${abilityText}${preferenceText}。`
})
const overviewInsights=computed(()=>{
  const registration=(factsByType.value.business_registration||[])[0]?.payload||{}
  const industry=registration.registered_industry
  const activeDomains=supportedDomains.value.filter((item:any)=>['supported','partially_supported'].includes(item.support_status))
  const amount=activeDomains.find((item:any)=>item.capability_type==='amount_experience_capability')
  const buyers=activeDomains.find((item:any)=>item.capability_type==='buyer_relationship_capability')
  const region=supportedDomains.value.find((item:any)=>item.capability_type==='regional_delivery_capability')
  const strengthNames=activeDomains.map((item:any)=>capabilityName(item.capability_type))
  const strengthDetails=[
    amount&&capabilityConclusionLines(amount).slice(0,2).join('，'),
    buyers&&capabilityConclusionLines(buyers)[0],
    region&&capabilityConclusionLines(region)[0],
  ].filter(Boolean)
  const decisions=confirmedDecisions.value.slice(0,4).map((field:any)=>`${field.field_name}为${displayDecisionValue(field)}`)
  const missing=missingCapabilityDomains.value.map((item:any)=>capabilityName(item.capability_type))
  return [
    {
      label:'企业定位',
      title:industry||'企业主营方向尚待明确',
      text:industry
        ? `企业工商登记方向为${industry}。结合当前投标记录，应重点以已核验项目经历判断实际业务覆盖。`
        : '现有资料尚不能清楚说明企业主营方向，建议优先完善工商信息和产品服务资料。',
    },
    {
      label:'已体现优势',
      title:strengthNames.length?strengthNames.join('、'):'尚未形成明确优势结论',
      lines:strengthDetails.length
        ? strengthDetails.flatMap((item)=>String(item).split(/[；\n]+/).map((line)=>line.trim()).filter(Boolean))
        : [],
      text:strengthDetails.length?'':'目前缺少能够直接证明项目承接与履约能力的资料，暂不作扩展判断。',
    },
    {
      label:'投标方向',
      title:decisions.length?'已记录企业经营选择':'尚未确认经营方向',
      text:decisions.length
        ? `${decisions.join('；')}。后续项目推荐应遵守这些选择。`
        : '企业尚未确认重点行业、地区、预算和风险偏好，当前画像只能说明历史情况，不能代表未来投标意愿。',
    },
    {
      label:'当前短板',
      title:missing.length?`${missing.length}项能力资料不足`:'核心能力资料已基本覆盖',
      text:missing.length
        ? `当前主要缺少${missing.join('、')}的有效依据，补充后可进一步完善投标适配判断。`
        : '当前八项能力已有可用结论，可结合具体招标项目继续进行适配分析。',
    },
  ]
})
function setWorkspaceView(view:WorkspaceView){
  workspaceView.value=view
  const query:any={...route.query}
  if(view!=='profile')query.view=view
  else delete query.view
  if(view!=='assistant'){
    delete query.action
    delete query.capability
  }
  void router.replace({path:route.path,query})
  if(view==='profile')void loadProfile(route.params.companyId)
}
function openDecisionAssistant(){
  workspaceView.value='assistant'
  void router.replace({path:route.path,query:{...route.query,view:'assistant',action:'decision'}})
}
function openCapabilityAssistant(capabilityType:string){
  workspaceView.value='assistant'
  void router.replace({
    path:route.path,
    query:{...route.query,view:'assistant',action:'check',capability:capabilityType},
  })
}
const factNames:Record<string,string>={business_registration:'工商信息',registered_capital:'注册资本',qualification:'资质证书',certificate:'资质证书',personnel:'人员情况',personnel_certificate:'人员证书',performance:'项目业绩',bid_participation:'历史参标',bid_award:'中标记录',fulfillment:'履约记录',risk_credit:'风险信用',risk_penalty_credit:'风险与信用',buyer_relationship:'客户关系',intellectual_property:'知识产权',branch_office:'分支机构',tax_credit:'纳税信用',other_enterprise_fact:'产品与服务'}
function capabilityName(value:string){return capabilityLabel(value)}
function capabilityDescription(value:string){return ({industry_capability:'企业在不同行业中的项目积累与业务覆盖。',technical_capability:'企业技术成果、知识产权和技术交付基础。',similar_performance_capability:'与目标业务相近的历史项目经验。',regional_delivery_capability:'在不同地区开展项目和持续交付的经验。',amount_experience_capability:'企业过往项目金额的已核验经验范围。',personnel_resource_capability:'人员规模、专业资质和项目资源情况。',buyer_relationship_capability:'与采购人及重点客户的历史合作基础。',tender_performance_capability:'历史投标参与和明确中标表现。'} as Record<string,string>)[value]||'基于企业事实形成的能力观察。'}
function supportName(value:string){return uiLabel(value)}
function factTypeName(value:string){return factNames[value]||'其他企业信息'}
function originName(value:string){return ({PROFILE_AGENT_VERIFIED_JSON:'本地真实企业画像',STRUCTURED_UPSTREAM:'业务系统提供',EXTERNAL_CANDIDATE:'用户或外部系统补充',MANUAL_REVIEW:'人工审核补充'} as Record<string,string>)[value]||'真实企业画像来源'}
function uniqueText(values:any[]){return [...new Set(values.map(value=>String(value||'').trim()).filter(Boolean))]}
function summarizeFactGroup(type:string,items:any[]):string{
  const payloads=items.map(item=>item?.payload||{})
  if(type==='business_registration'){
    const item=payloads[0]||{}
    const capital=item.registered_capital
    return [
      item.registered_industry&&`所属行业：${item.registered_industry}`,
      (item.operation_status_raw||item.normalized_operation_status)&&`经营状态：${item.operation_status_raw||item.normalized_operation_status}`,
      item.established_date&&`成立日期：${item.established_date}`,
      capital?.value!=null&&`注册资本：${capital.value}${capital.unit_raw||''}`,
      item.business_scope&&`经营范围：${item.business_scope}`,
    ].filter(Boolean).join('\n')||'工商信息暂不完整'
  }
  if(type==='qualification'||type==='certificate'){
    const names=uniqueText(payloads.map(item=>{
      const name=item.name||item.qualification_name||item.certificate_name
      if(!name)return ''
      const level=item.qualification_level?`（${item.qualification_level}）`:''
      const status=item.status==='expired'?'，已到期':item.valid_until?`，有效期至 ${item.valid_until}`:''
      return `${name}${level}${status}`
    }))
    return names.length?names.join('\n'):'暂无明确的资质名称'
  }
  if(type==='personnel'||type==='personnel_certificate'){
    const counts=payloads.map(item=>item.employee_count).filter(value=>typeof value==='number')
    const insured=payloads.map(item=>item.social_insurance_count).filter(value=>typeof value==='number')
    return counts.length?`员工 ${Math.max(...counts)} 人${insured.length?`，参保 ${Math.max(...insured)} 人`:''}`:'人员规模暂未提供'
  }
  if(type==='bid_participation'){
    const industries=uniqueText(payloads.flatMap(item=>item.industry?[item.industry]:item.project_industry?[item.project_industry]:[]))
    const regions=uniqueText(payloads.flatMap(item=>{
      const region=item.region||item.project_region
      if(!region)return []
      if(typeof region==='string')return [region]
      return [region.original||region.city||region.province||region.county].filter(Boolean)
    }))
    const projects=uniqueText(payloads.filter(item=>item.record_status!=='invalid_record').map(item=>item.project_name))
    return [
      projects.length?`项目：${projects.slice(0,5).join('、')}${projects.length>5?'等':''}`:'暂无有效项目名称',
      industries.length?`涉及行业：${industries.slice(0,3).join('、')}`:'',
      regions.length?`涉及地区：${regions.slice(0,3).join('、')}`:'',
      '说明：以上为参标经历，不代表已经中标或履约。',
    ].filter(Boolean).join('\n')
  }
  if(type==='performance'||type==='fulfillment'||type==='bid_award'){
    const lines=payloads.slice(0,5).flatMap(item=>[
      (item.project_name||item.project_title)&&`项目：${item.project_name||item.project_title}`,
      item.industry&&`行业：${item.industry}`,
      item.region&&`地区：${typeof item.region==='string'?item.region:(item.region.original||item.region.city||item.region.province||'')}`,
      item.buyer_name&&`采购人：${item.buyer_name}`,
      item.contract_amount?.normalized_value!=null&&`合同金额：${Number(item.contract_amount.normalized_value).toLocaleString()}元`,
      item.performance_scope&&`项目内容：${item.performance_scope}`,
    ].filter(Boolean))
    return lines.length?lines.join('\n'):'暂无明确的项目名称'
  }
  if(type==='buyer_relationship'){
    const buyers=uniqueText(payloads.map(item=>item.buyer_name))
    return buyers.length?`合作采购人：${buyers.slice(0,5).join('、')}`:'暂无明确的采购人信息'
  }
  if(type==='other_enterprise_fact'){
    const services=uniqueText(payloads.flatMap(item=>[
      item.business_description,
      ...(item.products||[]).map((product:any)=>product.name),
      ...(item.service_regions||[]).map((region:any)=>`服务地区：${region}`),
    ]))
    return services.length?services.join('\n'):'产品和服务信息暂不完整'
  }
  if(type==='risk_penalty_credit'||type==='risk_credit'){
    const descriptions=uniqueText(payloads.map(item=>{
      if(item.category==='dishonesty'&&item.status==='none')return '未发现失信记录'
      if(item.category==='bankruptcy_liquidation'&&item.status==='none')return '未发现破产清算记录'
      if(item.category==='administrative_penalty'&&item.status==='minor_remediated')return '存在轻微行政处罚记录，已完成整改'
      if(item.category==='abnormal_operation'&&item.status==='historical_removed')return '曾列入经营异常名录，现已移出'
      return ''
    }))
    return descriptions.length?descriptions.join('\n'):'风险信用情况暂不明确'
  }
  const values=uniqueText(payloads.map(item=>readableValue(item)))
  return values.length?values.slice(0,5).join('；'):'该项信息暂不完整'
}
function readableValue(value:any):string{
  if(value==null)return ''
  if(typeof value==='string'||typeof value==='number')return String(value)
  if(Array.isArray(value))return value.map(readableValue).filter(Boolean).slice(0,5).join('、')
  if(typeof value==='object'){
    const direct=value['用户补充信息']||value.name||value.industry_name||value.region_name||value.buyer_name||value.project_name
    if(direct)return String(direct)
    const preferred=['registered_industry','business_scope','participation_industries','performance_industries','participation_regions','regions','products','employee_count','active_branch_count','included_record_count','recent_12_months_count','confirmed_buyer_count','buyer_relationships','min_confirmed_amount','max_confirmed_amount']
    const labels:Record<string,string>={registered_industry:'登记行业',business_scope:'经营范围',participation_industries:'历史参标行业',performance_industries:'已核验业绩行业',participation_regions:'历史参标地区',regions:'覆盖地区',products:'产品或服务',employee_count:'人员记录',active_branch_count:'有效分支机构',included_record_count:'有效参标记录',recent_12_months_count:'近12个月参标记录',confirmed_buyer_count:'已确认采购人',buyer_relationships:'采购人记录',min_confirmed_amount:'已核验最低项目金额',max_confirmed_amount:'已核验最高项目金额'}
    const parts=preferred.filter(key=>value[key]!=null).map(key=>{
      const displayed=key.endsWith('_amount')&&typeof value[key]==='number'
        ? `${(Number(value[key])/10000).toLocaleString('zh-CN',{maximumFractionDigits:2})}万元`
        : readableValue(value[key])
      return `${labels[key]}：${displayed}`
    }).filter(Boolean)
    return parts.join('；')
  }
  return ''
}
function missingCapabilityReason(type:string):string{
  const reasons:Record<string,string>={
    amount_experience_capability:'已提交的项目没有填写合同金额，暂时无法判断金额承接经验',
    buyer_relationship_capability:'已提交的项目没有填写采购人，暂时无法形成采购人合作关系',
  }
  return reasons[type]||'现有资料仍不足以形成可靠结论'
}
function capabilityEvidence(domain:any):string[]{
  const claims=(domain.capability_claims||[]).map((item:any)=>readableValue(item.capability_value)||item.basis_summary)
  const semantic=(domain.reviewed_semantic_observations||[]).map((item:any)=>item.inference_summary)
  const observations=(domain.observations||[]).map((item:any)=>readableValue(item.observation_value))
  return uniqueText([...claims,...semantic,...observations]).filter(Boolean).slice(0,6)
}
function capabilityConclusion(domain:any):string{
  const evidence=capabilityEvidence(domain)
  if(evidence.length)return friendlyCapabilityText(evidence[0])
  const reason=(domain.unknowns||[]).map((item:any)=>item.reason).filter(Boolean)[0]
  return reason?`暂不能确认：${reason}`:'现有事实不足，暂不能形成结论'
}
function capabilityConclusionLines(domain:any):string[]{
  return friendlyCapabilityText(capabilityConclusion(domain))
    .split(/[\n；]+/)
    .map((item)=>item.trim().replace(/[。；]+$/,''))
    .filter(Boolean)
}
function additionalCapabilityEvidence(domain:any):string[]{
  const conclusion=friendlyCapabilityText(capabilityConclusion(domain))
  return capabilityEvidence(domain)
    .map((item)=>friendlyCapabilityText(item))
    .filter((item)=>item && item!==conclusion)
}
function firstUnknown(domain:any):string{
  const value=(domain?.unknowns||[]).map((item:any)=>item.reason).filter(Boolean)[0]
  return friendlyCapabilityText(value||'需要补充相应事实或证明材料')
}
function friendlyCapabilityText(value:string):string{
  const replacements:Record<string,string>={
    'performance或fulfillment':'项目业绩或履约',
    'performance':'项目业绩',
    'fulfillment':'履约',
    'B1.2':'中标能力',
    'ambiguous':'证据不足',
    'insufficient_data':'资料不足',
    'supported':'已有充分依据',
    'partially_supported':'部分证据支持',
    'buyer_name':'采购人名称',
    'normalized_unit':'统一单位',
    'normalized_currency':'统一币种',
  }
  let text=String(value)
  for(const [source,target] of Object.entries(replacements))text=text.split(source).join(target)
  text=text.replace(/^基于\s*\d+\s*条[^，。]*事实[，,]\s*/,'')
  if(text.includes('历史参标行业：'))text=text.split('；').join('\n')
  return text
}
function formatScore(value:any):string{
  const number=Number(value)
  return Number.isFinite(number)?`${number.toFixed(1)} 分`:'暂不能评分'
}
function listText(items:any[],key:string){return (items||[]).map((item:any)=>item?.[key]).filter(Boolean).join('、')}
function naturalPreferences():Record<string,unknown>{
  const split=(value:string)=>value.split(/[、,，；;\n]/).map(item=>item.trim()).filter(Boolean)
  const result:Record<string,unknown>={}
  if(preferenceDraft.strategic_industries.trim())result.strategic_industries=split(preferenceDraft.strategic_industries)
  if(preferenceDraft.strategic_regions.trim())result.strategic_regions=split(preferenceDraft.strategic_regions)
  if(preferenceDraft.budget_minimum!=null||preferenceDraft.budget_maximum!=null)result.budget_preference={minimum:preferenceDraft.budget_minimum,maximum:preferenceDraft.budget_maximum}
  if(preferenceDraft.procurement_method_preferences.trim())result.procurement_method_preferences=split(preferenceDraft.procurement_method_preferences)
  if(preferenceDraft.consortium_acceptance)result.consortium_acceptance=preferenceDraft.consortium_acceptance==='true'
  if(preferenceDraft.risk_preference)result.risk_preference=preferenceDraft.risk_preference
  if(preferenceDraft.max_concurrent_projects!=null)result.max_concurrent_projects=preferenceDraft.max_concurrent_projects
  if(preferenceDraft.personnel_resource_constraints.trim())result.personnel_resource_constraints=split(preferenceDraft.personnel_resource_constraints)
  if(preferenceDraft.explicit_exclusions.trim())result.explicit_exclusions=split(preferenceDraft.explicit_exclusions)
  if(preferenceDraft.key_buyers.trim())result.key_buyers=split(preferenceDraft.key_buyers)
  if(preferenceDraft.current_business_goals.trim())result.current_business_goals=split(preferenceDraft.current_business_goals)
  return result
}
function populatePreferenceDraft(){
  Object.assign(preferenceDraft,emptyDraft())
  for(const field of decisionFields.value){
    const value=field.value
    if(!value)continue
    if(field.field_code==='strategic_industries')preferenceDraft.strategic_industries=listText(value.industries,'industry_name')
    else if(field.field_code==='strategic_regions')preferenceDraft.strategic_regions=listText(value.regions,'region_name')
    else if(field.field_code==='budget_preference'){preferenceDraft.budget_minimum=value.minimum?.normalized_value??null;preferenceDraft.budget_maximum=value.maximum?.normalized_value??null}
    else if(field.field_code==='procurement_method_preferences')preferenceDraft.procurement_method_preferences=listText(value.methods,'method_name')
    else if(field.field_code==='consortium_acceptance')preferenceDraft.consortium_acceptance=String(value.accepted)
    else if(field.field_code==='risk_preference')preferenceDraft.risk_preference=value.normalized_code||''
    else if(field.field_code==='max_concurrent_projects')preferenceDraft.max_concurrent_projects=value.maximum
    else if(field.field_code==='personnel_resource_constraints')preferenceDraft.personnel_resource_constraints=listText(value.constraints,'constraint_name')
    else if(field.field_code==='explicit_exclusions')preferenceDraft.explicit_exclusions=listText(value.items,'exclusion_subject')
    else if(field.field_code==='key_buyers')preferenceDraft.key_buyers=listText(value.buyers,'buyer_name')
    else if(field.field_code==='current_business_goals')preferenceDraft.current_business_goals=listText(value.goals,'goal_text')
  }
  originalPreferenceValues=naturalPreferences()
}
function startPreferenceEdit(){populatePreferenceDraft();preferenceMessage.value='';editingPreferences.value=true}
function displayDecisionValue(field:any){
  const value=field?.value;if(!value)return '尚未填写'
  if(field.field_code==='strategic_industries')return listText(value.industries,'industry_name')||'尚未填写'
  if(field.field_code==='strategic_regions')return listText(value.regions,'region_name')||'尚未填写'
  if(field.field_code==='budget_preference'){const min=value.minimum?.normalized_value,max=value.maximum?.normalized_value;return `${min==null?'不限':Number(min).toLocaleString()} ～ ${max==null?'不限':Number(max).toLocaleString()} 元`}
  if(field.field_code==='procurement_method_preferences')return listText(value.methods,'method_name')||'尚未填写'
  if(field.field_code==='consortium_acceptance')return value.accepted?'接受联合体':'不接受联合体'
  if(field.field_code==='risk_preference')return ({conservative:'谨慎',balanced:'平衡',aggressive:'积极'} as Record<string,string>)[value.normalized_code]||value.stated_preference
  if(field.field_code==='max_concurrent_projects')return `${value.maximum} 个`
  if(field.field_code==='personnel_resource_constraints')return listText(value.constraints,'constraint_name')||'尚未填写'
  if(field.field_code==='explicit_exclusions')return listText(value.items,'exclusion_subject')||'尚未填写'
  if(field.field_code==='key_buyers')return listText(value.buyers,'buyer_name')||'尚未填写'
  if(field.field_code==='current_business_goals')return listText(value.goals,'goal_text')||'尚未填写'
  return '已填写'
}
function decisionHint(code:string){return ({strategic_industries:'企业希望重点发展的行业',strategic_regions:'企业希望重点开拓的地区',budget_preference:'期望参与项目的预算范围',procurement_method_preferences:'偏好的采购方式',consortium_acceptance:'是否接受联合体投标',risk_preference:'对项目风险的接受程度',max_concurrent_projects:'可同时投入的项目数量',personnel_resource_constraints:'当前人员和资源限制',explicit_exclusions:'明确不参与的项目类型',key_buyers:'重点关注的采购人',current_business_goals:'当前阶段的经营目标'} as Record<string,string>)[code]||'用于后续项目推荐和投标决策'}
async function savePreferences(){
  const current=naturalPreferences(),preferences:Record<string,unknown>={},cleared_fields:string[]=[]
  for(const code of Object.keys({...originalPreferenceValues,...current})){
    if(JSON.stringify(originalPreferenceValues[code])===JSON.stringify(current[code]))continue
    if(code in current)preferences[code]=current[code];else cleared_fields.push(code)
  }
  if(!Object.keys(preferences).length&&!cleared_fields.length){preferenceMessage.value='没有需要保存的修改。';return}
  savingPreferences.value=true;error.value=''
  try{
    await api(`/api/companies/${encodeURIComponent(String(route.params.companyId))}/decision-preferences`,{method:'POST',body:JSON.stringify({preferences,cleared_fields,confirmation_actor:{actor_type:'user',actor_id:'LOCAL_OPERATOR',display_name:'本机操作人'},confirmed_at_utc:new Date().toISOString()})})
    await loadProfile(route.params.companyId);editingPreferences.value=false;preferenceMessage.value='经营偏好已确认并保存，可供投标决策使用。'
  }catch(caught){error.value=caught instanceof Error?caught.message:String(caught)}
  finally{savingPreferences.value=false}
}
async function loadProfile(companyId:unknown){
  data.value=undefined;evaluation.value=null;error.value=''
  try{
    const id=encodeURIComponent(String(companyId))
    ;[data.value,gapInventory.value,evaluation.value]=await Promise.all([
      api(`/api/companies/${id}/profile`),
      api(`/api/companies/${id}/gaps`).catch(()=>null),
      api(`/api/companies/${id}/evaluation/latest`).catch(()=>null),
    ])
  }catch(caught){error.value=caught instanceof Error?caught.message:String(caught)}
}
watch(()=>route.params.companyId,(companyId)=>loadProfile(companyId),{immediate:true})
</script>
<style scoped>
.fact-summary,.overview-result-card li{white-space:pre-line;line-height:1.75}
.profile-overview{display:grid;gap:16px}
.intelligent-overview{padding:24px}
.overview-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;margin-bottom:20px}
.overview-heading h3{margin:4px 0 0;font-size:1.45rem;color:#173d36}
.overview-update-note{padding:5px 10px;border-radius:999px;background:#edf5f2;color:#397067;font-size:.78rem;white-space:nowrap}
.overview-insights{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.overview-insights article{min-width:0;padding:17px 18px;border:1px solid #dfe9e6;border-radius:12px;background:#f9fbfa}
.overview-insights article>span{font-size:.78rem;font-weight:700;color:#9a6b15}
.overview-insights h4{margin:5px 0 8px;color:#1b4039;font-size:1.05rem;line-height:1.5}
.overview-insights p{margin:0;color:#536d68;line-height:1.75}
.overview-insight-lines{display:grid;gap:7px;margin:0;padding-left:20px;color:#536d68;line-height:1.7}
.overview-insight-lines li{padding-left:2px;overflow-wrap:anywhere}
.overview-actions{display:flex;gap:22px;flex-wrap:wrap;margin-top:20px;padding-top:16px;border-top:1px solid #e4ebe9}
.capability-card{display:flex;flex-direction:column;gap:10px}
.capability-card h3,.capability-card p{margin:0}
.capability-conclusion{font-size:1rem;line-height:1.7;color:#233b37;white-space:pre-line}
.capability-conclusion-list{display:grid;gap:0;margin:4px 0 2px;padding:0;list-style:none;border:1px solid #e0e9e6;border-radius:10px;overflow:hidden}
.capability-conclusion-list li{position:relative;padding:11px 14px 11px 34px;background:#fbfcfc;color:#405c57;line-height:1.65;overflow-wrap:anywhere}
.capability-conclusion-list li+li{border-top:1px solid #e7edeb}
.capability-conclusion-list li::before{content:' ';position:absolute;left:16px;top:20px;width:6px;height:6px;border-radius:50%;background:#7aa59c}
.capability-conclusion-list li.primary{background:#f2f8f6;color:#173d36;font-weight:700}
.capability-conclusion-list li.primary::before{background:#176b5b}
.capability-unknown{padding:10px 12px;border-radius:9px;background:#fff7e6;line-height:1.6}
.capability-missing-card{grid-column:1/-1}
.evaluation-dimensions{margin:8px 0}
.conclusion-evaluation{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:14px;padding-top:14px;border-top:1px solid #e3ebe9}
.conclusion-evaluation .link-button{margin-left:auto}
@media(max-width:820px){
  .overview-insights{grid-template-columns:1fr}
  .overview-heading{flex-direction:column}
}
</style>
