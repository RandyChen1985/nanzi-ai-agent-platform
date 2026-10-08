// 用 Node 真跑纯函数：静态字符串断言看不出「大整数会不会被 Number 改写精度」，
// 这里直接调用真实实现做行为验证。
import assert from 'node:assert/strict'

import {
  extraDataJsonToPairs,
  isExtraDataJsonObject,
  pairsToExtraDataJson,
} from '../../frontend/src/utils/userExtraData.ts'

let passed = 0
const check = (label, fn) => {
  fn()
  passed += 1
  console.log('  ✓ ' + label)
}

// --- 回归：第三方同步写入的雪花 ID 是字符串，序列化后必须仍是字符串且精度不丢 ---
check('大整数 teamId 序列化后仍是原样字符串', () => {
  const json = pairsToExtraDataJson([{ key: 'teamId', value: '116573125898872954882' }])
  // 文本层面就必须带引号，否则任何 JSON.parse 都会引入精度丢失
  assert.ok(json.includes('"116573125898872954882"'), `期望带引号的字符串，实际：${json}`)
  assert.ok(!json.includes('116573125898872960000'), '不得出现被 Number 改写后的值')
  const parsed = JSON.parse(json)
  assert.equal(typeof parsed.teamId, 'string')
  assert.equal(parsed.teamId, '116573125898872954882')
})

check('布尔样文本保持字符串', () => {
  const parsed = JSON.parse(pairsToExtraDataJson([{ key: 'a', value: 'true' }]))
  assert.equal(parsed.a, 'true')
  assert.equal(typeof parsed.a, 'string')
  const parsed2 = JSON.parse(pairsToExtraDataJson([{ key: 'b', value: 'False' }]))
  assert.equal(parsed2.b, 'False')
})

check('数字样文本保持字符串（含 0 / 科学计数 / 前导零 / 空白包裹）', () => {
  for (const [key, value] of [
    ['zero', '0'],
    ['sci', '1e3'],
    ['leading', '007'],
    ['float', '1.50'],
    ['padded', '  123  '],
  ]) {
    const parsed = JSON.parse(pairsToExtraDataJson([{ key, value }]))
    assert.equal(typeof parsed[key], 'string', `${key} 必须是字符串`)
    assert.equal(parsed[key], value, `${key} 必须原样保留`)
  }
})

// --- 可视化编辑器的常规行为 ---
check('空 key 的键值对被忽略，key 两端空白被裁剪', () => {
  const parsed = JSON.parse(
    pairsToExtraDataJson([
      { key: '   ', value: 'x' },
      { key: '  region  ', value: 'east' },
    ]),
  )
  assert.deepEqual(parsed, { region: 'east' })
})

check('没有任何有效键值对时输出空串', () => {
  assert.equal(pairsToExtraDataJson([]), '')
  assert.equal(pairsToExtraDataJson([{ key: '', value: 'v' }]), '')
})

check('可视化 → JSON → 可视化 往返后键值完全一致', () => {
  const pairs = [
    { key: 'teamId', value: '116573125898872954882' },
    { key: 'region', value: 'east' },
    { key: 'level', value: '3' },
  ]
  assert.deepEqual(extraDataJsonToPairs(pairsToExtraDataJson(pairs)), pairs)
})

// --- 解析方向 ---
check('解析 JSON 文本时字符串值原样取出', () => {
  const pairs = extraDataJsonToPairs('{"teamId": "116573125898872954882"}')
  assert.deepEqual(pairs, [{ key: 'teamId', value: '116573125898872954882' }])
})

check('解析历史遗留的非字符串值时统一转成字符串展示', () => {
  assert.deepEqual(extraDataJsonToPairs('{"level": 3, "on": true, "none": null}'), [
    { key: 'level', value: '3' },
    { key: 'on', value: 'true' },
    { key: 'none', value: 'null' },
  ])
})

check('非法 JSON 或非对象 JSON 返回空数组而不抛错', () => {
  assert.deepEqual(extraDataJsonToPairs(''), [])
  assert.deepEqual(extraDataJsonToPairs('   '), [])
  assert.deepEqual(extraDataJsonToPairs('{不是 JSON'), [])
  assert.deepEqual(extraDataJsonToPairs('[1, 2]'), [])
  assert.deepEqual(extraDataJsonToPairs('123'), [])
  assert.deepEqual(extraDataJsonToPairs('"text"'), [])
  assert.deepEqual(extraDataJsonToPairs('null'), [])
})

check('解析已经丢过精度的历史数字值时不抛错（尽量原样回显）', () => {
  assert.deepEqual(extraDataJsonToPairs('{"teamId": 116573125898872960000}'), [
    { key: 'teamId', value: '116573125898872960000' },
  ])
})

check('isExtraDataJsonObject 只认合法的 JSON 对象', () => {
  assert.equal(isExtraDataJsonObject('{"a": "b"}'), true)
  assert.equal(isExtraDataJsonObject('{}'), true)
  assert.equal(isExtraDataJsonObject('   {"a": 1}   '), true)
  assert.equal(isExtraDataJsonObject(''), false)
  assert.equal(isExtraDataJsonObject('   '), false)
  assert.equal(isExtraDataJsonObject('{不是 JSON'), false)
  assert.equal(isExtraDataJsonObject('[1, 2]'), false)
  assert.equal(isExtraDataJsonObject('123'), false)
  assert.equal(isExtraDataJsonObject('null'), false)
})

console.log(`用户扩展数据序列化测试全部通过（${passed} 项）`)
