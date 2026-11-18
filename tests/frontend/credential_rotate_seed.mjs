// 轮换骨架落位的真实行为：源码字符串断言看不出时序问题，必须调用实现。
//
// 现场 bug：结构化凭据（自定义 JSON，有 user / pwd 两个 key）点轮换，弹窗里是空的，
// key 全丢。原因是弹窗一打开就先补了一行空白占位，而骨架是异步请求回来的，
// 「已有行就不覆盖」的守卫把它挡在门外。
import assert from 'node:assert/strict'

import { canAdoptSkeleton } from '../../frontend/src/utils/credentialRotateSeed.ts'

const SKELETON = JSON.stringify({ user: '', pwd: '' })

// --- 1. 骨架还没到：什么都不做（保持占位行）---
assert.equal(canAdoptSkeleton([{ key: '', value: '' }], ''), false)

// --- 2. 弹窗先补的空行 + 骨架到达：必须让骨架接管（这就是那个 bug）---
assert.equal(canAdoptSkeleton([{ key: '', value: '' }], SKELETON), true)

// --- 3. 表格还是空的：直接铺 ---
assert.equal(canAdoptSkeleton([], SKELETON), true)

// --- 4. 用户已经填了键名：不许覆盖（覆盖等于吞掉输入）---
assert.equal(canAdoptSkeleton([{ key: 'user', value: '' }], SKELETON), false)

// --- 5. 用户已经填了值（键名还空着）：同样不许覆盖 ---
assert.equal(canAdoptSkeleton([{ key: '', value: 'alice' }], SKELETON), false)

// --- 6. 用户自己加了多行：不许覆盖 ---
assert.equal(
  canAdoptSkeleton([{ key: '', value: '' }, { key: '', value: '' }], SKELETON),
  false,
)

// --- 7. 骨架是空串/空白，不能把占位行顶成空表 ---
assert.equal(canAdoptSkeleton([{ key: '', value: '' }], '   '), false)

// --- 8. 首尾空白也算「没填」：光标点过不算动过手 ---
assert.equal(canAdoptSkeleton([{ key: '  ', value: ' ' }], SKELETON), true)

console.log('credential_rotate_seed 行为测试通过')
