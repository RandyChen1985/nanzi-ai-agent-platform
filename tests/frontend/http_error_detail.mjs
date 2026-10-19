// 用 Node 真跑 Blob 错误体解析：responseType=blob 时直接读 .detail 必定是 undefined，
// 必须先把 Blob 还原成文本。这里覆盖 JSON Blob、纯文本 Blob 与普通对象。
import assert from 'node:assert/strict'

import { readErrorDetail } from '../../frontend/src/utils/httpErrorDetail.ts'

assert.equal(
  await readErrorDetail(
    new Blob([JSON.stringify({ detail: '目标不是文件，无法下载。' })], {
      type: 'application/json',
    }),
  ),
  '目标不是文件，无法下载。',
  'JSON Blob 必须能取出 detail',
)

assert.equal(
  await readErrorDetail({ detail: '普通对象错误' }),
  '普通对象错误',
  '非 Blob 的 JSON 响应体应保持原行为',
)

assert.equal(
  await readErrorDetail('  纯文本错误  '),
  '纯文本错误',
  '非 JSON 文本应原样返回并去除首尾空白',
)

assert.equal(
  await readErrorDetail(new Blob(['not json'])),
  'not json',
  '纯文本 Blob 也应返回可读信息',
)

assert.equal(await readErrorDetail(null), undefined)
assert.equal(await readErrorDetail(''), undefined)
assert.equal(await readErrorDetail({}), undefined)
assert.equal(await readErrorDetail({ detail: '' }), undefined)

console.log('HTTP 错误详情提取测试全部通过')
