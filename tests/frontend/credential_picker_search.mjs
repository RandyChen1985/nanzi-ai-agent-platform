// 凭据选择器搜索的行为验证：用 Node 真跑 `frontend/src/utils/credentialPicker.ts`，
// 而不是断言源码里有没有 `filter` 字样——后者在逻辑被改坏时照样通过。
//
// 覆盖的坑都来自真实场景：一个人的凭据几十条、名字是「支付密钥」而标识是
// `stripe_key`、只启用了部分使用场景、多关键词要同时命中。
import assert from 'node:assert/strict'

import {
  credentialMatchesQuery,
  credentialMatchesScene,
  filterCredentialItems,
  highlightCredentialField,
  normalizeCredentialQuery,
} from '../../frontend/src/utils/credentialPicker.ts'

const item = (overrides) => ({
  id: 'id-1',
  secret_key: 'stripe_key',
  name: '支付密钥',
  secret_type: 'secret',
  category: 'payment',
  status: 'active',
  allowed_scenarios: ['mcp'],
  value_masked: '****',
  ...overrides,
})

const stripe = item({ id: 'a', name: '支付密钥', secret_key: 'stripe_key', category: 'payment' })
const mailer = item({ id: 'b', name: '邮箱名', secret_key: 'mailuser', category: null })
const platformKey = item({
  id: 'c',
  name: '平台版本号',
  secret_key: 'platversion',
  category: 'system',
  secret_type: 'variable',
  allowed_scenarios: ['mcp', 'api_tool'],
})
const scriptOnly = item({
  id: 'd',
  name: '脚本变量',
  secret_key: 'SCRIPT_ENV_TOKEN',
  category: 'script',
  allowed_scenarios: ['script_env'],
})
const all = [stripe, mailer, platformKey, scriptOnly]

// --- 1. 场景过滤先于搜索：没启用 mcp 的凭据不能出现在 mcp 绑定点 ---
assert.deepEqual(
  filterCredentialItems(all, 'mcp', '').map((entry) => entry.id),
  ['a', 'b', 'c'],
)
assert.equal(credentialMatchesScene(scriptOnly, 'mcp'), false)
assert.equal(credentialMatchesScene(scriptOnly, ''), true, '未指定场景时不按场景过滤')

// --- 2. 名称、标识、分类三者任一命中即可 ---
assert.deepEqual(filterCredentialItems(all, '', '支付').map((entry) => entry.id), ['a'])
assert.deepEqual(filterCredentialItems(all, '', 'stripe').map((entry) => entry.id), ['a'])
assert.deepEqual(filterCredentialItems(all, '', 'payment').map((entry) => entry.id), ['a'])
assert.deepEqual(filterCredentialItems(all, '', 'script').map((entry) => entry.id), ['d'])

// --- 3. 大小写不敏感，前后空白不影响 ---
assert.deepEqual(filterCredentialItems(all, '', '  STRIPE  ').map((entry) => entry.id), ['a'])
assert.deepEqual(filterCredentialItems(all, '', 'PlAtVeRsIoN').map((entry) => entry.id), ['c'])

// --- 4. 多关键词是 AND：两个词都命中才留下 ---
assert.deepEqual(filterCredentialItems(all, '', '邮箱 mailuser').map((entry) => entry.id), ['b'])
assert.deepEqual(filterCredentialItems(all, '', '邮箱 stripe').map((entry) => entry.id), [])
assert.deepEqual(normalizeCredentialQuery('  a   A  b '), ['a', 'b'], '拆词去重且转小写')

// --- 5. 无命中返回空数组，而不是退回全量 ---
assert.deepEqual(filterCredentialItems(all, 'mcp', '不存在的名字'), [])

// --- 6. 值的影子不参与搜索：避免「搜什么就能验证什么」 ---
const masked = item({ id: 'e', value_masked: 'sk_live_abcdef' })
assert.equal(credentialMatchesQuery(masked, normalizeCredentialQuery('sk_live')), false)

// --- 7. 高亮：命中处切段，未命中处保留原文 ---
const single = highlightCredentialField('stripe_key', ['stripe'])
assert.deepEqual(single, [
  { text: 'stripe', hit: true },
  { text: '_key', hit: false },
])

const middle = highlightCredentialField('my_stripe_key', ['stripe'])
assert.deepEqual(middle, [
  { text: 'my_', hit: false },
  { text: 'stripe', hit: true },
  { text: '_key', hit: false },
])

// 多处命中都要标出来
const repeated = highlightCredentialField('a1a2a', ['a'])
assert.deepEqual(repeated, [
  { text: 'a', hit: true },
  { text: '1', hit: false },
  { text: 'a', hit: true },
  { text: '2', hit: false },
  { text: 'a', hit: true },
])

// 重叠区间合并成一段，不能把同一段字切碎
const overlapped = highlightCredentialField('abcdef', ['abc', 'bcd'])
assert.deepEqual(overlapped, [
  { text: 'abcd', hit: true },
  { text: 'ef', hit: false },
])

// 任意切分都不许丢字、不许改字
for (const [text, tokens] of [
  ['stripe_key', ['stripe', 'key']],
  ['平台版本号 platversion', ['plat', '版本']],
  ['', ['x']],
  ['普通文本', []],
]) {
  const rebuilt = highlightCredentialField(text, tokens)
    .map((segment) => segment.text)
    .join('')
  assert.equal(rebuilt, text, `切段后必须还原原文：${text}`)
}

// 无命中 / 无关键词时只有一段，且不打标
assert.deepEqual(highlightCredentialField('mailuser', ['zzz']), [{ text: 'mailuser', hit: false }])
assert.deepEqual(highlightCredentialField('mailuser', []), [{ text: 'mailuser', hit: false }])
assert.deepEqual(highlightCredentialField('', ['a']), [])

console.log('credential_picker_search: all assertions passed')
