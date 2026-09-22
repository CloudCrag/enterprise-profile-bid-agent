import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const source = fs.readFileSync(new URL('../src/index.mjs', import.meta.url), 'utf8')

test('gateway exposes startup readiness and listens only on localhost', () => {
  assert.match(source, /\/api\/system\/readiness/)
  assert.match(source, /'127\.0\.0\.1'/)
})

test('gateway contains no reset or replay business endpoints', () => {
  assert.doesNotMatch(source, /demo\/reset|review-all-fixtures|evaluation\/batches|enterprise-data\/search/)
})
