import {
  formatShareClock,
  formatShareDateTime,
  formatShareDay,
  formatShareMessageStamp,
  isSameShareDay,
  parseShareTime,
} from '../../frontend/src/utils/shareTime.ts'

const checks = []
const check = (name, actual, expected) => {
  checks.push({ name, ok: actual === expected, actual, expected })
}

check('合法时间解析', parseShareTime('2026-10-28T14:23:05') instanceof Date, true)
check('空值解析为 null', parseShareTime(null), null)
check('非法字符串解析为 null', parseShareTime('not-a-date'), null)

check('完整日期时间', formatShareDateTime('2026-10-28T14:23:05'), '2026-10-28 14:23')
check('时钟', formatShareClock('2026-10-28T14:23:05'), '14:23')
check('日期', formatShareDay('2026-10-28T14:23:05'), '2026-10-28')
check('单数字补零', formatShareClock('2026-01-05T09:07:00'), '09:07')
check('空值给空串', formatShareDateTime(''), '')
check('非法值给空串', formatShareClock('bad'), '')

check('同一天', isSameShareDay('2026-10-28T01:00:00', '2026-10-28T23:59:00'), true)
check('跨天', isSameShareDay('2026-10-28T23:59:00', '2026-10-29T00:01:00'), false)
check('任一无效则不算同天', isSameShareDay('bad', '2026-10-28T23:59:00'), false)

check('首条带完整日期', formatShareMessageStamp('2026-10-28T14:23:05'), '2026-10-28 14:23')
check('同日仅时钟', formatShareMessageStamp('2026-10-28T15:02:00', '2026-10-28T14:23:05'), '15:02')
check('跨日补月日', formatShareMessageStamp('2026-10-29T09:10:00', '2026-10-28T14:23:05'), '10-29 09:10')
check('无上一时刻当首条', formatShareMessageStamp('2026-10-28T14:23:05', null), '2026-10-28 14:23')
check('空时刻给空串', formatShareMessageStamp(''), '')

const failed = checks.filter((c) => !c.ok)
if (failed.length > 0) {
  console.error('FAILED:')
  for (const f of failed) console.error(`  ${f.name}: 实际 ${JSON.stringify(f.actual)} 期望 ${JSON.stringify(f.expected)}`)
  process.exit(1)
}
console.log(`share_time.mjs: ${checks.length} 项全部通过`)
