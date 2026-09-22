<template>
  <div v-if="error" class="card error">
    <strong>{{ error.userMessage }}</strong>
    <details>
      <summary>技术详情</summary>
      <p><code>{{ error.code }}</code> · HTTP {{ error.httpStatus || '-' }}</p>
      <pre>{{ error.technicalMessage }}</pre>
      <pre v-if="Object.keys(error.details).length">{{ JSON.stringify(error.details, null, 2) }}</pre>
    </details>
  </div>
</template>

<script setup lang="ts">
import type { ApiError } from '../api'
defineProps<{ error: ApiError | null }>()
</script>
