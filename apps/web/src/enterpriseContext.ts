import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export const useEnterpriseContextStore = defineStore('enterpriseContext', () => {
  const companyId = ref(localStorage.getItem('enterprise-profile-company-id') || '')
  const normalizedCompanyId = computed(() => companyId.value.trim())

  function selectCompany(value: string): void {
    const normalized = value.trim()
    companyId.value = normalized
    if (normalized) localStorage.setItem('enterprise-profile-company-id', normalized)
    else localStorage.removeItem('enterprise-profile-company-id')
  }

  return { companyId, normalizedCompanyId, selectCompany }
})
