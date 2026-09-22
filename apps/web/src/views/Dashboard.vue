<template>
  <section class="dashboard-page">
    <header class="page-hero">
      <div><span class="eyebrow">工作首页</span><h2>企业画像与投标决策</h2><p>查看当前企业真实画像，或开始真实项目的投标分析。</p></div>
      <div class="hero-actions">
        <RouterLink class="secondary-action" :to="`/companies/${companyId}/profile`">查看企业画像</RouterLink>
        <RouterLink class="secondary-action" to="/bid-decision">进入投标决策助手</RouterLink>
      </div>
    </header>
    <div v-if="error" class="error">{{ error }}</div><div v-else-if="!data">加载中…</div>
    <template v-else>
      <div class="company-banner card">
        <div><span class="muted">当前企业</span><h3>{{ data.profile.enterprise.name }}</h3></div>
        <span class="badge">真实企业数据</span>
      </div>
      <div class="grid metric-grid">
        <div class="card metric-card"><div class="metric-icon blue">✓</div><div><span>已收录企业事实</span><div class="metric">{{ data.profile.summaries.fact_count }}</div><small>资质、人员、业绩等信息</small></div></div>
        <div class="card metric-card"><div class="metric-icon green">◆</div><div><span>能力分析维度</span><div class="metric">8</div><small>行业、技术、交付等能力</small></div></div>
        <div class="card metric-card"><div class="metric-icon amber">!</div><div><span>待补关键信息</span><div class="metric">{{ data.profile.summaries.gap_count }}</div><small>由画像助手逐项引导补充</small></div></div>
        <div class="card metric-card"><div class="metric-icon purple">◎</div><div><span>已确认经营偏好</span><div class="metric">{{ data.profile.summaries.confirmed_decision_count }}<small>/11</small></div><small>用于项目决策</small></div></div>
      </div>
      <h3 class="section-title">接下来可以做什么</h3>
      <div class="grid action-grid">
        <RouterLink class="card action-card" :to="`/companies/${companyId}/profile`"><span class="action-number">01</span><div><h3>查看企业画像</h3><p>查看企业事实、能力和经营偏好。</p></div><b>→</b></RouterLink>
        <RouterLink class="card action-card" to="/bid-decision"><span class="action-number">02</span><div><h3>开始投标决策</h3><p>比较真实项目、核验资格并查看竞争分析。</p></div><b>→</b></RouterLink>
      </div>
    </template>
  </section>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { api } from '../api'
import { useEnterpriseContextStore } from '../enterpriseContext'
const context = useEnterpriseContextStore(); const { normalizedCompanyId: companyId } = storeToRefs(context)
const data = ref<any>(); const error = ref('')
async function load(): Promise<void> { try { data.value = await api(`/api/dashboard?company_id=${encodeURIComponent(companyId.value)}`) } catch (caught) { error.value = caught instanceof Error ? caught.message : String(caught) } }
onMounted(load)
</script>
<style scoped>
.hero-actions{display:grid;gap:.65rem;min-width:190px}.secondary-action{display:inline-flex;align-items:center;justify-content:center;min-height:42px;padding:.65rem 1rem;border:1px solid #65a497;border-radius:9px;background:#fff;color:#176b5d;text-decoration:none;font-weight:700}.action-grid{grid-template-columns:repeat(2,minmax(0,1fr))}@media(max-width:720px){.hero-actions{width:100%;min-width:0}.action-grid{grid-template-columns:1fr}}
</style>
