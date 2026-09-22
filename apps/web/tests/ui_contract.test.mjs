import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const bid = fs.readFileSync(new URL('../src/views/BidDecision.vue', import.meta.url), 'utf8')
const competition = fs.readFileSync(new URL('../src/views/CompetitionDetail.vue', import.meta.url), 'utf8')
const evaluation = fs.readFileSync(new URL('../src/views/Evaluation.vue', import.meta.url), 'utf8')
const profileAgent = fs.readFileSync(new URL('../src/views/Agent.vue', import.meta.url), 'utf8')
const globalStyle = fs.readFileSync(new URL('../src/style.css', import.meta.url), 'utf8')

test('business output never exposes internal database project ids', () => {
  assert.match(bid, /project_name \|\| projectName/)
  assert.match(bid, /项目名称待读取/)
  assert.doesNotMatch(bid, /\{\{\s*projectId\s*\}\}/)
  assert.doesNotMatch(competition, /项目编号<\/dt>/)
})

test('competition analysis opens in a new browser tab and has no inline scroll panel', () => {
  assert.match(bid, /target="_blank"/)
  assert.match(bid, /查看竞争分析（新标签页）/)
  assert.doesNotMatch(bid, /在本页查看竞争明细/)
  assert.doesNotMatch(bid, /scrollIntoView/)
})

test('action buttons use explicit accessible foreground and background contrast', () => {
  assert.match(globalStyle, /button\.secondary-link[\s\S]*background:\s*#ffffff[^}]*color:\s*#176b5d/i)
  assert.match(globalStyle, /button\.primary-action[\s\S]*background:\s*#196b5d[^}]*color:\s*#ffffff/i)
  assert.match(globalStyle, /button\.danger-action[\s\S]*background:\s*#9d2323[^}]*color:\s*#ffffff/i)
  assert.match(bid, /\.bid-decision-page button:disabled,[\s\S]*cursor:\s*not-allowed\s*!important/)
})

test('enterprise evaluation frontend keeps the original workspace structure', () => {
  assert.match(evaluation, /企业综合得分/)
  assert.match(evaluation, /选择评价方式/)
  assert.match(evaluation, /评价维度/)
  assert.match(evaluation, /评分指标明细/)
  assert.match(evaluation, /评分原因/)
  assert.match(evaluation, /低分诊断/)
  assert.match(evaluation, /改善建议/)
  assert.doesNotMatch(evaluation, /智能分析服务状态|原始 JSON|版本校验码|查看技术运行信息/)
  assert.doesNotMatch(evaluation, /REPLAY|MOCK-|演示批量评价/)
})

test('interactive evaluation answers and rescoring stay inside evaluation workspace', () => {
  assert.match(evaluation, /需要补充的评价信息/)
  assert.match(evaluation, /提交回答并重新评价/)
  assert.match(evaluation, /submitEvaluationAnswers/)
  assert.match(evaluation, /evaluation\/runs\/\$\{encodeURIComponent\(run\.value\.run_id\)\}\/answers/)
  assert.doesNotMatch(evaluation, /前往画像助手补充|评价后展示关键缺项，可转到画像助手补充/)
})

test('enterprise profile assistant does not expose internal run state', () => {
  assert.doesNotMatch(profileAgent, /运行与技术详情|查看完整运行状态|当前步骤：\{\{ run\.current_node/)
})
