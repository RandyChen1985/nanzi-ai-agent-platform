// 宿主 postMessage 信任判定的行为测试（Node 真跑纯函数）。
//
// 背景：`/embed/*` 允许被任意站点 iframe 引用，消息里带 INIT_CONFIG / CLEAR_SESSION，
// 旧实现**完全没有 origin 校验**（注释写着 "Security check logic here in production"）。
import assert from 'node:assert/strict'

import {
  evaluateHostMessageTrust,
  parseOrigin,
  resolveHostTargetOrigin,
} from '../../frontend/src/utils/embedHostTrust.ts'

const SELF = 'https://platform.example.com'
const HOST = 'https://crm.example.com'
const EVIL = 'https://evil.example.com'

const trust = (over = {}) =>
  evaluateHostMessageTrust({
    origin: HOST,
    fromParentWindow: true,
    selfOrigin: SELF,
    allowedOrigins: [],
    ...over,
  })

// --- 1. 来源不是父窗口：一律拒绝（这条不依赖任何配置） ---
assert.deepEqual(trust({ fromParentWindow: false }), { trusted: false, unverified: false })
assert.deepEqual(trust({ fromParentWindow: false, allowedOrigins: [HOST] }), {
  trusted: false,
  unverified: false,
})

// --- 2. 非法/空 origin：拒绝 ---
assert.equal(trust({ origin: '' }).trusted, false)
assert.equal(trust({ origin: 'null' }).trusted, false)

// --- 3. 同源（门户壳同源 iframe）：信任且无需白名单 ---
assert.deepEqual(trust({ origin: SELF, allowedOrigins: [] }), {
  trusted: true,
  unverified: false,
})

// --- 4. 白名单命中：信任 ---
assert.deepEqual(trust({ allowedOrigins: [HOST] }), { trusted: true, unverified: false })
assert.deepEqual(trust({ allowedOrigins: ['*'] }), { trusted: true, unverified: false })

// --- 5. 白名单存在但未命中：拒绝（含同为父窗口的情况） ---
assert.equal(trust({ origin: EVIL, allowedOrigins: [HOST] }).trusted, false)
// 白名单项带路径/斜杠也要能被归一化后命中
assert.equal(trust({ allowedOrigins: ['https://crm.example.com/'] }).trusted, true)

// --- 6. 未配置白名单：兼容放行，但标记 unverified（调用方告警 + 提示补配置） ---
assert.deepEqual(trust({ allowedOrigins: [] }), { trusted: true, unverified: true })

// --- 7. 回传 targetOrigin 的选择顺序 ---
assert.equal(
  resolveHostTargetOrigin({ allowedOrigins: [HOST], referrer: `${HOST}/deal/1` }),
  HOST,
)
assert.equal(
  resolveHostTargetOrigin({ allowedOrigins: [HOST], referrer: `${EVIL}/x` }),
  HOST,
  'referrer 不在白名单时必须回落到白名单首项，而不是发给 referrer',
)
assert.equal(resolveHostTargetOrigin({ allowedOrigins: [], referrer: `${HOST}/page` }), HOST)
// ⚠️ 回归点：宿主设置了 Referrer-Policy: no-referrer 时 referrer 为空。
// 此时**必须**用通配兜底——若回落到本页 origin，跨域父窗口收不到消息，
// 表现为 NANZI_WIDGET_READY 送不出去、挂件永远停在骨架屏。
assert.equal(
  resolveHostTargetOrigin({ allowedOrigins: [], referrer: '' }),
  '*',
  '无法确定父窗口 origin 时必须用通配，否则跨域集成会静默失效',
)
assert.equal(
  resolveHostTargetOrigin({ allowedOrigins: [HOST], referrer: '' }),
  HOST,
  '单项白名单 + 无 referrer：能确定宿主就是它',
)
// 多项白名单 + 无 referrer：无法判断是其中哪一项，填首项会把消息发给错的 origin，
// 必须退回通配保证送达（否则表现为挂件收不到 INIT_SUCCESS）。
assert.equal(
  resolveHostTargetOrigin({ allowedOrigins: [HOST, 'https://erp.example.com'], referrer: '' }),
  '*',
)
assert.equal(
  resolveHostTargetOrigin({
    allowedOrigins: [HOST, 'https://erp.example.com'],
    referrer: 'https://erp.example.com/x',
  }),
  'https://erp.example.com',
  '有 referrer 时按命中项走，不需要通配',
)

// --- 8. parseOrigin 边界 ---
assert.equal(parseOrigin('https://a.example.com/x?y=1'), 'https://a.example.com')
assert.equal(parseOrigin('https://a.example.com:8443/x'), 'https://a.example.com:8443')
assert.equal(parseOrigin('null'), '')
assert.equal(parseOrigin(''), '')
assert.equal(parseOrigin('not a url'), '')

console.log('embed_host_trust: all assertions passed')
