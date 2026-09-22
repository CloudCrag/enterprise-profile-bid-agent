import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const bid = fs.readFileSync(new URL('../src/views/BidDecision.vue', import.meta.url), 'utf8')
const app = fs.readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const competition = fs.readFileSync(new URL('../src/views/CompetitionDetail.vue', import.meta.url), 'utf8')

test('bid page explains complete V2 scoring rule and backend pagination', () => {
  assert.match(bid, /TEMP-BID-SCORE-V2|临时投标决策评分 V2/)
  assert.match(bid, /<details class="score-rule-details" open>/)
  assert.match(bid, /查看完整评分规则/)
  assert.match(bid, /资格可投性[\s\S]*40/)
  assert.match(bid, /企业能力匹配[\s\S]*40/)
  assert.match(bid, /资源可执行性[\s\S]*20/)
  assert.match(bid, /getBidProjects\(projectQuery\.value, page, pageSize\)/)
})

test('bid page separates current preference from objective score', () => {
  assert.match(bid, /本次偏好识别结果/)
  assert.match(bid, /不计入.*客观基础分/)
  assert.match(bid, /影响AI.*最终优先级.*组合/)
})

test('qualification supplement is shown by project and requirement', () => {
  assert.match(bid, /qualification_gaps/)
  assert.match(bid, /公告\/数据库中的要求/)
  assert.match(bid, /请逐项填写/)
  assert.match(bid, /gap\.input_fields/)
  assert.match(bid, /必填字段完整且未发现与公告冲突/)
})

test('bid_open_time is labelled as opening time and excluded from score', () => {
  assert.match(bid, /开标时间（数据库 bid_open_time）/)
  assert.match(bid, /不作为投标截止时间或评分依据/)
})

test('startup failure blocks normal business shell', () => {
  assert.match(app, /startup-error|系统启动失败/)
  assert.match(app, /v-else class="shell"/)
})

test('competition demo data is prominently labelled', () => {
  assert.match(competition, /演示数据，不代表真实企业参与情况/)
})


test('bid qualification supplement has no review workflow', () => {
  assert.match(bid, /提交后立即重新核验/)
  assert.match(bid, /不再进入审核流程/)
  assert.match(bid, /未做外部真实性核验/)
  assert.doesNotMatch(bid, /待审核证据/)
})
