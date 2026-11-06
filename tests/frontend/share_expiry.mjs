// 用 Node 真跑有效期换算与密码生成：前后端对「7 天」「3 天」这类的换算必须一致，
// 否则会出现「弹窗写着 7 天、列表显示 6 天后过期」。
import assert from 'node:assert/strict'

import {
  EXPIRY_PRESETS,
  deriveShareStatus,
  describeExpiry,
  formatRemaining,
  generateShareSecret,
  isExpiryPresetActive,
  resolveExpiry,
} from '../../frontend/src/utils/shareExpiry.ts'

const NOW = new Date('2026-10-28T13:20:00+08:00')

assert.equal(EXPIRY_PRESETS.length, 5)
assert.deepEqual(EXPIRY_PRESETS.map((p) => p.value), ['1h', '24h', '7d', '30d', 'permanent'])
assert.equal(EXPIRY_PRESETS.find((p) => p.value === '7d').label, '7 天')

assert.equal(resolveExpiry({ mode: 'permanent' }, NOW), null)
assert.equal(
  resolveExpiry({ mode: 'preset', preset: '1h' }, NOW).toISOString(),
  new Date('2026-10-28T14:20:00+08:00').toISOString(),
)
assert.equal(
  resolveExpiry({ mode: 'custom', value: 3, unit: 'day' }, NOW).toISOString(),
  new Date('2026-10-31T13:20:00+08:00').toISOString(),
)
assert.equal(
  resolveExpiry({ mode: 'custom', value: 6, unit: 'hour' }, NOW).toISOString(),
  new Date('2026-10-28T19:20:00+08:00').toISOString(),
)

assert.equal(describeExpiry({ mode: 'preset', preset: '7d' }), '7 天后失效')
assert.equal(describeExpiry({ mode: 'permanent' }), '永久有效')
assert.equal(describeExpiry({ mode: 'custom', value: 3, unit: 'day' }), '3 天后失效')

// 选中态：「永久」用 mode='permanent' 表达，按钮高亮必须认这个模式，
// 否则点完 5 个按钮全灰，用户以为「永久」点不动。
assert.equal(isExpiryPresetActive({ mode: 'permanent' }, 'permanent'), true)
assert.equal(isExpiryPresetActive({ mode: 'permanent' }, '7d'), false)
assert.equal(isExpiryPresetActive({ mode: 'preset', preset: '7d' }, '7d'), true)
assert.equal(isExpiryPresetActive({ mode: 'preset', preset: '7d' }, '30d'), false)
assert.equal(isExpiryPresetActive({ mode: 'preset', preset: '7d' }, 'permanent'), false)
// 缺省 preset 时按默认 7 天兜底，与 resolveExpiry 的兜底保持一致
assert.equal(isExpiryPresetActive({ mode: 'preset' }, '7d'), true)
// 自定义模式下不该有任何预设按钮高亮（焦点在自定义输入框上）
assert.equal(isExpiryPresetActive({ mode: 'custom', value: 3, unit: 'day' }, '7d'), false)
assert.equal(isExpiryPresetActive({ mode: 'custom', value: 3, unit: 'day' }, 'permanent'), false)

// 每一个可提交的预设/永久选择都必须恰好点亮一个按钮：0 个会让人以为没选上，2 个更糟
for (const choice of [
  { mode: 'preset', preset: '1h' },
  { mode: 'preset', preset: '24h' },
  { mode: 'preset', preset: '7d' },
  { mode: 'preset', preset: '30d' },
  { mode: 'permanent' },
]) {
  const active = EXPIRY_PRESETS.filter((p) => isExpiryPresetActive(choice, p.value))
  assert.equal(active.length, 1, JSON.stringify(choice))
}

// 密码：8 位、去掉易混字符、每次不同
const seen = new Set()
for (let i = 0; i < 40; i += 1) {
  const secret = generateShareSecret()
  assert.equal(secret.length, 8)
  assert.equal(/[0O1lI]/.test(secret), false, secret)
  assert.equal(/^[a-z0-9]+$/.test(secret), true, secret)
  seen.add(secret)
}
assert.ok(seen.size > 30, '随机源可能被写死')

// 状态推导：作废优先于过期
assert.equal(deriveShareStatus({ status: 'revoked', expires_at: null }, NOW), 'revoked')
assert.equal(
  deriveShareStatus({ status: 'active', expires_at: '2026-10-28T13:00:00+08:00' }, NOW),
  'expired',
)
assert.equal(
  deriveShareStatus({ status: 'active', expires_at: '2026-11-01T13:00:00+08:00' }, NOW),
  'active',
)
assert.equal(deriveShareStatus({ status: 'active', expires_at: null }, NOW), 'active')

assert.equal(formatRemaining(null, NOW), '永久有效')
assert.equal(formatRemaining('2026-10-30T13:20:00+08:00', NOW), '2 天后过期')
assert.equal(formatRemaining('2026-10-28T18:20:00+08:00', NOW), '5 小时后过期')
assert.equal(formatRemaining('2026-10-28T13:10:00+08:00', NOW), '已过期')

console.log('会话分享有效期测试全部通过')
