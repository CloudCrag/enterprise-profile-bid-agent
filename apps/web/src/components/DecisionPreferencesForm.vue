<template>
  <section class="card preference-form">
    <div class="preference-form-heading">
      <div>
        <span class="eyebrow">经营偏好</span>
        <h3>告诉助手企业想承接什么项目</h3>
        <p class="muted">这些内容由企业人员确认，用于后续投标决策；不知道的项目可以留空。</p>
      </div>
      <button v-if="!editing" class="secondary" @click="startEdit">填写或修改</button>
    </div>
    <div v-if="message" class="success">{{ message }}</div>
    <div v-if="errorMessage" class="error">{{ errorMessage }}</div>
    <form v-if="editing" @submit.prevent="save">
      <div class="preference-form-grid">
        <label>重点行业<input v-model="draft.strategic_industries" placeholder="例如：医疗信息化、政企数字化" /></label>
        <label>重点地区<input v-model="draft.strategic_regions" placeholder="例如：山西省、北京市" /></label>
        <label>期望项目预算下限（元）<input v-model.number="draft.budget_minimum" type="number" min="0" placeholder="可不填" /></label>
        <label>期望项目预算上限（元）<input v-model.number="draft.budget_maximum" type="number" min="0" placeholder="可不填" /></label>
        <label>偏好的采购方式<input v-model="draft.procurement_method_preferences" placeholder="例如：公开招标、竞争性磋商" /></label>
        <label>是否接受联合体
          <select v-model="draft.consortium_acceptance"><option value="">暂不确定</option><option value="true">接受</option><option value="false">不接受</option></select>
        </label>
        <label>风险偏好
          <select v-model="draft.risk_preference"><option value="">暂不确定</option><option value="conservative">谨慎</option><option value="balanced">平衡</option><option value="aggressive">积极</option></select>
        </label>
        <label>最多同时投入项目数<input v-model.number="draft.max_concurrent_projects" type="number" min="0" placeholder="可不填" /></label>
        <label>人员和资源限制<textarea v-model="draft.personnel_resource_constraints" placeholder="例如：近期最多可安排2个项目团队" /></label>
        <label>明确不参与的项目<textarea v-model="draft.explicit_exclusions" placeholder="例如：工期少于30天的项目" /></label>
        <label>重点采购人<input v-model="draft.key_buyers" placeholder="例如：某某集团、某某医院" /></label>
        <label>当前经营目标<textarea v-model="draft.current_business_goals" placeholder="例如：拓展政企数字化项目" /></label>
      </div>
      <div class="button-row">
        <button :disabled="saving">{{ saving ? '正在保存…' : '确认并保存' }}</button>
        <button type="button" class="secondary" :disabled="saving" @click="editing=false">取消</button>
      </div>
    </form>
    <p v-else class="muted">已确认 {{ confirmedCount }} 项经营偏好。点击“填写或修改”可以继续完善。</p>
  </section>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { api, toApiError } from '../api'

const props=withDefaults(defineProps<{companyId:string;autoEdit?:boolean}>(),{autoEdit:false})
const emit=defineEmits<{(event:'saved'):void}>()
const editing=ref(false),saving=ref(false),message=ref(''),errorMessage=ref(''),profile=ref<any>(null)
const empty=()=>({strategic_industries:'',strategic_regions:'',budget_minimum:null as number|null,budget_maximum:null as number|null,procurement_method_preferences:'',consortium_acceptance:'',risk_preference:'',max_concurrent_projects:null as number|null,personnel_resource_constraints:'',explicit_exclusions:'',key_buyers:'',current_business_goals:''})
const draft=reactive(empty())
const fields=computed(()=>profile.value?.decision_profile?.decision_fields||[])
const confirmedCount=computed(()=>fields.value.filter((item:any)=>item.confirmation_status==='confirmed').length)
function listText(items:any[],key:string){return (items||[]).map((item:any)=>item?.[key]).filter(Boolean).join('、')}
async function load(){profile.value=await api(`/api/companies/${encodeURIComponent(props.companyId)}/profile`)}
async function startEdit(){
  message.value='';errorMessage.value='';await load();Object.assign(draft,empty())
  for(const field of fields.value){const value=field.value;if(!value)continue
    if(field.field_code==='strategic_industries')draft.strategic_industries=listText(value.industries,'industry_name')
    else if(field.field_code==='strategic_regions')draft.strategic_regions=listText(value.regions,'region_name')
    else if(field.field_code==='budget_preference'){draft.budget_minimum=value.minimum?.normalized_value??null;draft.budget_maximum=value.maximum?.normalized_value??null}
    else if(field.field_code==='procurement_method_preferences')draft.procurement_method_preferences=listText(value.methods,'method_name')
    else if(field.field_code==='consortium_acceptance')draft.consortium_acceptance=String(value.accepted)
    else if(field.field_code==='risk_preference')draft.risk_preference=value.normalized_code||''
    else if(field.field_code==='max_concurrent_projects')draft.max_concurrent_projects=value.maximum
    else if(field.field_code==='personnel_resource_constraints')draft.personnel_resource_constraints=listText(value.constraints,'constraint_name')
    else if(field.field_code==='explicit_exclusions')draft.explicit_exclusions=listText(value.items,'exclusion_subject')
    else if(field.field_code==='key_buyers')draft.key_buyers=listText(value.buyers,'buyer_name')
    else if(field.field_code==='current_business_goals')draft.current_business_goals=listText(value.goals,'goal_text')
  }
  editing.value=true
}
watch(()=>[props.companyId,props.autoEdit] as const,([,autoEdit])=>{
  if(autoEdit&&!editing.value)void startEdit()
},{immediate:true})
function values(){
  const split=(value:string)=>value.split(/[、,，；;\n]/).map(item=>item.trim()).filter(Boolean)
  const out:Record<string,unknown>={}
  if(draft.strategic_industries.trim())out.strategic_industries=split(draft.strategic_industries)
  if(draft.strategic_regions.trim())out.strategic_regions=split(draft.strategic_regions)
  if(draft.budget_minimum!=null||draft.budget_maximum!=null)out.budget_preference={minimum:draft.budget_minimum,maximum:draft.budget_maximum}
  if(draft.procurement_method_preferences.trim())out.procurement_method_preferences=split(draft.procurement_method_preferences)
  if(draft.consortium_acceptance)out.consortium_acceptance=draft.consortium_acceptance==='true'
  if(draft.risk_preference)out.risk_preference=draft.risk_preference
  if(draft.max_concurrent_projects!=null)out.max_concurrent_projects=draft.max_concurrent_projects
  if(draft.personnel_resource_constraints.trim())out.personnel_resource_constraints=split(draft.personnel_resource_constraints)
  if(draft.explicit_exclusions.trim())out.explicit_exclusions=split(draft.explicit_exclusions)
  if(draft.key_buyers.trim())out.key_buyers=split(draft.key_buyers)
  if(draft.current_business_goals.trim())out.current_business_goals=split(draft.current_business_goals)
  return out
}
async function save(){
  saving.value=true;message.value='';errorMessage.value=''
  try{
    await api(`/api/companies/${encodeURIComponent(props.companyId)}/decision-preferences`,{method:'POST',body:JSON.stringify({preferences:values(),cleared_fields:[],confirmation_actor:{actor_type:'user',actor_id:'LOCAL_OPERATOR',display_name:'本机操作人'},confirmed_at_utc:new Date().toISOString()})})
    editing.value=false;message.value='经营偏好已保存，并已更新到企业画像。';await load();emit('saved')
  }catch(caught){
    errorMessage.value=toApiError(caught).userMessage
  }finally{saving.value=false}
}
load().catch(()=>undefined)
</script>
