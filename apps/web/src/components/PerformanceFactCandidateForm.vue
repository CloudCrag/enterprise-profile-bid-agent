<template>
  <form class="card" @submit.prevent="submit">
    <h3>历史业绩事实候选补充</h3>
    <p class="muted">用于补充企业历史项目业绩。提交后会先进入人工核对，不会直接修改正式企业画像。</p>
    <div class="grid">
      <label>项目名称（必填）<input v-model.trim="form.projectName" required /></label>
      <label>采购人<input v-model.trim="form.buyerName" /></label>
      <label>行业<input v-model.trim="form.industry" /></label>
      <label>地区<input v-model.trim="form.region" /></label>
      <label>合同金额（元）<input v-model.number="form.amount" type="number" min="0" step="0.01" /></label>
      <label>开始时间<input v-model="form.startDate" type="date" /></label>
      <label>完成时间<input v-model="form.endDate" type="date" /></label>
    </div>
    <label>业绩范围<textarea v-model.trim="form.scope" /></label>
    <fieldset>
      <legend>证明材料引用（可选，不上传文件）</legend>
      <div class="grid">
        <label>材料记录编号<input v-model.trim="form.materialId" /></label>
        <label>文件名<input v-model.trim="form.filename" /></label>
        <label>文件类型<input v-model.trim="form.mediaType" placeholder="例如：PDF文档" /></label>
        <label>材料校验码<input v-model.trim="form.sha256" placeholder="专业选项：64位小写十六进制，可不填" /></label>
        <label>文件大小（字节）<input v-model.number="form.sizeBytes" type="number" min="1" /></label>
      </div>
    </fieldset>
    <p v-if="validationError" class="error">{{ validationError }}</p>
    <button :disabled="submitting">{{ submitting ? '提交中…' : '提交业绩信息' }}</button>
    <span v-if="message" class="success">{{ message }}</span>
  </form>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { api, toApiError } from '../api'

const props = defineProps<{ companyId: string }>()
const emit = defineEmits<{ submitted: [] }>()
const form = reactive({
  projectName: '', buyerName: '', industry: '', region: '', amount: null as number | null,
  startDate: '', endDate: '', scope: '', materialId: '', filename: '', mediaType: 'application/pdf',
  sha256: '', sizeBytes: 1,
})
const submitting = ref(false)
const message = ref('')
const validationError = ref('')

function validate(): boolean {
  validationError.value = ''
  if (!form.projectName) validationError.value = '请填写项目名称。'
  else if (form.startDate && form.endDate && form.endDate < form.startDate) validationError.value = '完成时间不能早于开始时间。'
  else if (form.materialId && (!/^[0-9a-f]{64}$/.test(form.sha256) || !form.filename)) validationError.value = '填写材料记录编号时，必须同时填写文件名和64位材料校验码。'
  return !validationError.value
}

async function submit(): Promise<void> {
  if (!validate()) return
  submitting.value = true
  message.value = ''
  try {
    const amount = form.amount == null ? null : {
      raw_value: form.amount, value: form.amount, unit_raw: '元', currency_raw: 'CNY',
      normalized_value: form.amount, normalized_unit: 'yuan', normalized_currency: 'CNY',
      value_parse_status: 'parsed', semantic_status: 'confirmed', normalization_status: 'normalized',
    }
    const materialRefs = form.materialId ? [{
      material_id: form.materialId, material_content_sha256: form.sha256,
      original_filename: form.filename, media_type: form.mediaType, size_bytes: form.sizeBytes,
      description: '历史业绩证明材料引用',
    }] : []
    const now = new Date().toISOString()
    const result = await api<{ status: string; review_task_id: string }>('/api/integration/fact-candidates', {
      method: 'POST',
      body: JSON.stringify({
        submission_id: `performance-ui-${Date.now()}`,
        company_id: props.companyId,
        source_type: 'user_upload', source_system: 'section6-performance-form',
        source_record_id: `performance-ui-${Date.now()}`, source_url: null, collected_at: now,
        fact_type: 'performance',
        payload: {
          project_name: form.projectName,
          contract_number: null,
          buyer_name: form.buyerName || null,
          industry: form.industry || null,
          region: form.region || null,
          contract_amount: amount,
          start_date: form.startDate || null,
          end_date: form.endDate || null,
          performance_scope: form.scope || null,
        },
        material_refs: materialRefs,
        submitted_by: { actor_type: 'user', actor_id: 'LOCAL_OPERATOR', display_name: '本机操作人' },
        idempotency_key: `performance-form-${form.projectName}-${form.startDate}-${form.amount ?? 'na'}`,
      }),
    })
    message.value = result.status === 'DUPLICATE' ? '该候选已提交，未重复创建任务。' : `已创建审核任务：${result.review_task_id}`
    emit('submitted')
  } catch (caught) {
    validationError.value = toApiError(caught).userMessage
  } finally {
    submitting.value = false
  }
}
</script>
