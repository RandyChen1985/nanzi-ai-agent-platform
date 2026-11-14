import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'

const require = createRequire(resolve('frontend/package.json'))
const ts = require('typescript')
const source = readFileSync('frontend/src/utils/mcpCredentialProfiles.ts', 'utf8')
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText
// 只替换 HTTP 依赖，测试两个表单实际使用的文本转换。
const offlineJs = js.replace(/import axios from ['"]@\/utils\/axios['"];?/, 'const axios = undefined;')
const { splitMcpResultPaths } = await import('data:text/javascript;base64,' + Buffer.from(offlineJs).toString('base64'))

assert.equal(typeof splitMcpResultPaths, 'function')
assert.deepEqual(splitMcpResultPaths(' \r\n\t '), [])
assert.deepEqual(splitMcpResultPaths([
  '  $ ',
  '$.messages[*]',
  "$.messages[?search(@.subject, 'a,b')]['special.key', 'subject']",
  'messages[].sender.address',
].join('\r\n')), [
  '$',
  '$.messages[*]',
  "$.messages[?search(@.subject, 'a,b')]['special.key', 'subject']",
  'messages[].sender.address',
])
assert.deepEqual(splitMcpResultPaths([
  "$['escaped\\']', 'br[acket']",
  '  .messages[',
  '    ?search(@.subject, "a,b")',
  '',
  '    , 0',
  '  ]',
  '  [1]',
  '$..messages[*]',
].join('\n')), [
  "$['escaped\\']', 'br[acket']\n  .messages[\n    ?search(@.subject, \"a,b\")\n\n    , 0\n  ]\n  [1]",
  '$..messages[*]',
])
assert.deepEqual(splitMcpResultPaths('$.broken[\n$.messages[*]'), ['$.broken[\n$.messages[*]'])
assert.deepEqual(splitMcpResultPaths('$.missing\nunrecognizable extra text'), ['$.missing', 'unrecognizable extra text'])
