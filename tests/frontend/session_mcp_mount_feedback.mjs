import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'

const require = createRequire(resolve('frontend/package.json'))
const ts = require('typescript')
const source = readFileSync('frontend/src/utils/mcpMountFeedback.ts', 'utf8')
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText
const { mcpMountFeedback } = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'))
const requested = [
  { id: 'mail', name: 'fixture-mail:list_mail' },
  { id: 'files', name: 'fixture-files:list' },
]

assert.equal(mcpMountFeedback(requested, []).type, 'error')
assert.equal(mcpMountFeedback(requested, [{ id: 'existing' }]).type, 'error')
const partial = mcpMountFeedback(requested, [{ id: 'mail' }])
assert.equal(partial.type, 'warning')
assert.match(partial.message, /已挂载 1 个/)
assert.equal(mcpMountFeedback(requested, [{ id: 'mail' }, { id: 'files' }]).type, 'success')
assert.match(mcpMountFeedback(requested.slice(0, 1), [{ id: 'mail' }]).message, /fixture-mail:list_mail/)
