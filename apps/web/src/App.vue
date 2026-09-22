<template>
  <div v-if="startupLoading" class="startup-screen">
    <section class="startup-card"><div class="startup-spinner"></div><h1>正在检查系统核心依赖</h1><p>企业数据、远程项目数据库和智谱 GLM 必须全部可用。</p></section>
  </div>

  <div v-else-if="startupError" class="startup-screen startup-error-page">
    <section class="startup-card error-card">
      <span class="error-mark">!</span>
      <h1>系统启动失败</h1>
      <p>核心依赖没有全部通过检查，因此不会进入业务页面，也不会使用任何备用或虚构结果。</p>
      <div class="startup-checks">
        <article v-for="check in startupChecks" :key="check.name" :class="check.status === 'PASS' ? 'check-pass' : 'check-fail'">
          <b>{{ check.name }}：{{ check.status === 'PASS' ? '通过' : '失败' }}</b>
          <p>{{ check.message }}</p>
          <small v-if="check.advice">处理建议：{{ check.advice }}</small>
        </article>
      </div>
      <details><summary>查看技术错误</summary><code>{{ startupError.technicalMessage }}</code></details>
      <button type="button" class="primary-action" @click="initialize">重新检查</button>
    </section>
  </div>

  <div v-else class="shell">
    <aside>
      <div class="brand">
        <span class="brand-mark">企</span>
        <div><h1>招投标决策智能体系统</h1><small>企业画像与投标决策</small></div>
      </div>
      <div class="environment-note"><span class="status-dot"></span>本机单用户 · 真实数据链</div>
      <label class="company-search-label">搜索企业
        <input v-model="companySearch" class="company-search" placeholder="输入企业名称或信用代码" aria-label="搜索企业" />
      </label>
      <label class="company-context">当前查看的企业
        <select v-model="draftCompanyId" @change="applyCompany" aria-label="选择企业">
          <option v-for="company in visibleCompanies" :key="company.company_id" :value="company.company_id">
            {{ company.enterprise?.name || company.company_id }}
          </option>
        </select>
      </label>
      <small class="company-context-help">
        已载入 {{ companies.length }} 家真实企业；原始企业 JSON 只读，补充内容和计算结果另行保存。
      </small>
      <nav aria-label="主要功能">
        <span class="nav-label">日常使用</span>
        <RouterLink to="/"><span>⌂</span>工作首页</RouterLink>
        <RouterLink :to="`/companies/${companyId}/profile`"><span>▦</span>企业画像</RouterLink>
        <RouterLink to="/bid-decision"><span>◇</span>投标决策助手</RouterLink>
        <button class="nav-section" type="button" :aria-expanded="showProfessional" @click="showProfessional = !showProfessional">
          <span>管理与专业工具</span><b>{{ showProfessional ? '−' : '+' }}</b>
        </button>
        <div v-show="showProfessional" class="professional-links">
          <RouterLink to="/versions">版本记录</RouterLink>
          <RouterLink to="/about">系统说明</RouterLink>
        </div>
      </nav>
      <p class="sidebar-help">系统不包含登录功能，仅监听本机 127.0.0.1。</p>
    </aside>
    <main><RouterView :key="companyId" /></main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { api, getSystemReadiness, toApiError, type ApiError } from './api'
import { useEnterpriseContextStore } from './enterpriseContext'

const context = useEnterpriseContextStore()
const route = useRoute()
const router = useRouter()
const { normalizedCompanyId: companyId } = storeToRefs(context)
const draftCompanyId = ref(companyId.value)
const companies = ref<Array<{ company_id: string; enterprise?: { name?: string } }>>([])
const companySearch = ref('')
const startupLoading = ref(true)
const startupError = ref<ApiError | null>(null)
const startupChecks = ref<Array<{ name: string; status: 'PASS' | 'FAIL'; message: string; advice?: string }>>([])

const visibleCompanies = computed(() => {
  const keywords = companySearch.value.trim().toLowerCase().split(/\s+/).filter(Boolean)
  const matched = keywords.length
    ? companies.value.filter(item => {
        const searchable = `${item.enterprise?.name || ''} ${item.company_id}`.toLowerCase()
        return keywords.every(keyword => searchable.includes(keyword))
      })
    : companies.value.slice(0, 30)
  const current = companies.value.find(item => item.company_id === draftCompanyId.value)
  return current && !matched.some(item => item.company_id === current.company_id)
    ? [current, ...matched].slice(0, 30)
    : matched.slice(0, 30)
})
const professionalPaths = ['/versions', '/about']
const showProfessional = ref(professionalPaths.includes(route.path))

function applyCompany(): void {
  context.selectCompany(draftCompanyId.value)
  if (route.path.includes('/profile')) router.replace(`/companies/${encodeURIComponent(companyId.value)}/profile`)
}
watch(() => route.path, path => { if (professionalPaths.includes(path)) showProfessional.value = true })
watch(() => route.params.companyId, routeCompanyId => {
  if (typeof routeCompanyId === 'string' && routeCompanyId && routeCompanyId !== companyId.value) {
    context.selectCompany(routeCompanyId)
    draftCompanyId.value = routeCompanyId
  }
}, { immediate: true })

async function initialize(): Promise<void> {
  startupLoading.value = true
  startupError.value = null
  startupChecks.value = []
  try {
    const readiness = await getSystemReadiness()
    startupChecks.value = readiness.checks
    companies.value = await api('/api/companies')
    if (!companies.value.length) throw new Error('企业原始数据目录中没有可用企业。')
    if (!companies.value.some(item => item.company_id === companyId.value)) context.selectCompany(companies.value[0].company_id)
    draftCompanyId.value = companyId.value
  } catch (caught) {
    const error = toApiError(caught)
    startupError.value = error
    const checks = error.details?.checks
    startupChecks.value = Array.isArray(checks)
      ? checks as Array<{ name: string; status: 'PASS' | 'FAIL'; message: string; advice?: string }>
      : [{ name: '统一 API', status: 'FAIL', message: error.technicalMessage, advice: '运行根目录 start.ps1，并根据启动错误页检查配置。' }]
  } finally {
    startupLoading.value = false
  }
}

onMounted(initialize)
</script>

<style scoped>
.startup-screen{min-height:100vh;display:grid;place-items:center;padding:24px;background:#eef5f3}.startup-card{width:min(760px,100%);display:grid;gap:16px;padding:32px;border-radius:18px;background:#fff;box-shadow:0 20px 60px #173d3620}.startup-card h1,.startup-card p{margin:0}.startup-spinner{width:44px;height:44px;border:5px solid #d6e6e2;border-top-color:#196b5d;border-radius:50%;animation:spin 1s linear infinite}.error-card{border-top:7px solid #9d2323}.error-mark{display:grid;place-items:center;width:52px;height:52px;border-radius:50%;background:#fde9e9;color:#9d2323;font-size:30px;font-weight:900}.startup-checks{display:grid;gap:10px}.startup-checks article{padding:14px;border-radius:10px}.startup-checks article p{margin:6px 0}.check-pass{background:#e7f8ef;color:#176b43}.check-fail{background:#fde9e9;color:#7d1b1b}.startup-card code{display:block;margin-top:10px;white-space:pre-wrap;word-break:break-word}@keyframes spin{to{transform:rotate(360deg)}}
</style>
