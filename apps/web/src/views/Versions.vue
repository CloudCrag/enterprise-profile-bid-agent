<template>
  <section>
    <header class="page-header">
      <div>
        <span class="eyebrow">更新记录</span>
        <h2>画像版本历史</h2>
        <p>这里记录企业事实、能力和经营偏好的每次正式更新，便于了解“什么时候改了什么”。历史版本只用于查看和追溯，不会自动恢复或覆盖当前画像。</p>
      </div>
    </header>

    <div v-if="loading" class="card empty-state">正在加载版本历史…</div>
    <div v-else-if="error" class="card error">
      <strong>{{ error.userMessage }}</strong>
      <details>
        <summary>技术详情</summary>
        <code>{{ error.code }}</code>
        <pre>{{ error.technicalMessage }}</pre>
      </details>
    </div>

    <template v-else>
      <div class="card">
        <h3>比较两次画像更新</h3>
        <p class="muted">只有同一类画像存在两个及以上版本时才能比较。</p>
        <div class="grid">
          <label>
            画像层
            <select v-model="layer" @change="syncVersionSelection">
              <option value="fact">事实画像</option>
              <option value="capability">能力画像</option>
              <option value="decision">决策画像</option>
            </select>
          </label>
          <label>
            从版本
            <select v-model.number="fromVersion" :disabled="availableVersions.length < 2">
              <option v-for="version in availableVersions" :key="`from-${version}`" :value="version">第 {{ version }} 版</option>
            </select>
          </label>
          <label>
            到版本
            <select v-model.number="toVersion" :disabled="availableVersions.length < 2">
              <option v-for="version in availableVersions" :key="`to-${version}`" :value="version">第 {{ version }} 版</option>
            </select>
          </label>
        </div>
        <button :disabled="diffLoading || availableVersions.length < 2 || fromVersion === toVersion" @click="loadDiff">
          {{ diffLoading ? '查询中…' : '查看差异' }}
        </button>
        <p v-if="availableVersions.length < 2" class="empty-state">当前{{ layerName(layer) }}只有一个版本，暂时没有可比较的更新。</p>
        <p v-else-if="fromVersion === toVersion" class="empty-state">请选择两个不同版本。</p>
        <p v-if="diffError" class="error">{{ diffError.userMessage }}</p>
        <div v-if="diff" class="diff-result">
          <p v-if="!diff.changes?.length" class="empty-state">所选版本之间没有可展示的变化。</p>
          <div v-else class="table-wrap">
            <table>
              <thead><tr><th>变化对象</th><th>变化类型</th><th>说明</th></tr></thead>
              <tbody>
                <tr v-for="(change, index) in diff.changes" :key="index">
                  <td>{{ changeName(change) }}</td>
                  <td>{{ changeTypeName(change.change_type) }}</td>
                  <td>{{ changeSummary(change) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div v-for="itemLayer in layers" :key="itemLayer.code" class="version-section">
        <h3>{{ itemLayer.name }}</h3>
        <div v-if="!history?.[itemLayer.code]?.length" class="card empty-state">
          暂无{{ itemLayer.name }}历史版本。
        </div>
        <div v-else class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>版本</th>
                <th>生成时间</th>
                <th>更新来源</th>
                <th>更新字段</th>
                <th>记录信息</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="version in history[itemLayer.code]" :key="version.version">
                <td>v{{ version.version }}</td>
                <td>{{ formatTime(version.generated_at_utc || version.recorded_at_utc) }}</td>
                <td>{{ sourceName(version.update_source) }}</td>
                <td>{{ version.updated_fields?.join('、') || '完整画像记录' }}</td>
                <td><details><summary>查看记录编号</summary><small>{{ version.profile_id || '-' }}</small><br><small>校验码：{{ version.content_hash?.slice(0, 16) || '-' }}</small></details></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useEnterpriseContextStore } from '../enterpriseContext'
import { api, ApiError, toApiError } from '../api'

type LayerCode = 'fact' | 'capability' | 'decision'

type VersionEntry = {
  version: number
  profile_id?: string
  content_hash?: string
  previous_profile_id?: string | null
  generated_at_utc?: string | null
  recorded_at_utc?: string | null
  update_source?: string
  updated_fields?: string[]
}

type HistoryResponse = Record<LayerCode, VersionEntry[]>

type DiffResponse = {
  layer: LayerCode
  from_version: number
  to_version: number
  changes: Array<Record<string, any>>
}

const enterpriseContext = useEnterpriseContextStore()
const { normalizedCompanyId: companyId } = storeToRefs(enterpriseContext)
const layers: Array<{ code: LayerCode; name: string }> = [
  { code: 'fact', name: '事实画像' },
  { code: 'capability', name: '能力画像' },
  { code: 'decision', name: '决策画像' },
]

const history = ref<HistoryResponse | null>(null)
const diff = ref<DiffResponse | null>(null)
const layer = ref<LayerCode>('decision')
const fromVersion = ref(1)
const toVersion = ref(2)
const loading = ref(true)
const diffLoading = ref(false)
const error = ref<ApiError | null>(null)
const diffError = ref<ApiError | null>(null)
const availableVersions = computed(() =>
  (history.value?.[layer.value] || []).map((entry) => entry.version).sort((a, b) => a - b),
)

function layerName(value: LayerCode): string { return layers.find((item) => item.code === value)?.name || '画像' }
function syncVersionSelection(): void {
  const values = availableVersions.value
  fromVersion.value = values.length >= 2 ? values[values.length - 2] : values[0] || 1
  toVersion.value = values[values.length - 1] || 1
  diff.value = null
  diffError.value = null
}
function formatTime(value?: string | null): string {
  if (!value) return '时间未记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}
function sourceName(value?: string): string {
  if (!value) return '系统初始化'
  if (value === 'BASELINE') return '初始画像'
  if (value === 'ZHIPU_SEMANTIC_REVIEW') return '智谱能力建议经用户确认'
  if (value.includes('FACT')) return '企业事实经用户确认'
  if (value.includes('DECISION')) return '经营偏好经用户确认'
  if (value.includes('CAPABILITY')) return '能力画像更新'
  return '系统更新'
}
function changeTypeName(value: unknown): string {
  return ({ added: '新增', removed: '移除', modified: '调整' } as Record<string, string>)[String(value)] || '发生变化'
}
function changeName(change: Record<string, any>): string {
  return change.after?.field_name || change.before?.field_name || change.after?.capability_name || change.before?.capability_name || change.field_code || change.id || '画像内容'
}
function changeSummary(change: Record<string, any>): string {
  if (change.change_type === 'added') return '这个内容从本版本开始加入画像。'
  if (change.change_type === 'removed') return '这个内容在新版本中不再保留。'
  return '这个内容的状态、依据或确认值发生了调整。'
}

async function loadHistory(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    history.value = await api<HistoryResponse>(`/api/companies/${companyId.value}/profile/history`)
    syncVersionSelection()
  } catch (caught) {
    error.value = toApiError(caught)
  } finally {
    loading.value = false
  }
}

async function loadDiff(): Promise<void> {
  diffLoading.value = true
  diffError.value = null
  diff.value = null
  try {
    const query = new URLSearchParams({
      layer: layer.value,
      from_version: String(fromVersion.value),
      to_version: String(toVersion.value),
    })
    diff.value = await api<DiffResponse>(
      `/api/companies/${companyId.value}/profile/diff?${query.toString()}`,
    )
  } catch (caught) {
    diffError.value = toApiError(caught)
  } finally {
    diffLoading.value = false
  }
}

onMounted(loadHistory)
</script>
