// 首屏骨架闸门：骨架屏的语义是「当前线程还没准备好」，不是「鉴权中」。
//
// 打开 embedchat 时，鉴权一过就撤骨架 → `messages` 还是空的 → 模板先渲染欢迎页 →
// 历史回来再整块替换。用户看到的就是「欢迎页闪一下，跳成消息列表」。
// 这里把「什么时候该显示骨架」抽成纯函数并逐条跑一遍。
import assert from 'node:assert/strict'

import {
  FIRST_SCREEN_SKELETON_TIMEOUT_MS,
  THREAD_SKELETON_MIN_VISIBLE_MS,
  shouldStartThreadSkeleton,
  threadSkeletonRemainingVisibleMs,
} from '../../frontend/src/utils/embedFirstScreen.ts'

assert.equal(
  shouldStartThreadSkeleton({ hasMessages: false }),
  true,
  '空列表时必须显示骨架，否则欢迎页会在历史到达前先露出来',
)

assert.equal(
  shouldStartThreadSkeleton({ hasMessages: true }),
  false,
  '已有消息时不该再盖骨架：宿主重推 INIT_CONFIG 会把看得见的对话糊掉再闪回来',
)

assert.equal(
  shouldStartThreadSkeleton({ hasMessages: false, force: true }),
  true,
  '切会话清空后没有消息，强制标记仍须显示骨架',
)

assert.equal(
  shouldStartThreadSkeleton({ hasMessages: true, force: true }),
  true,
  '切会话必须强制遮挡清理到历史落地之间的空档',
)

assert.ok(
  Number.isFinite(FIRST_SCREEN_SKELETON_TIMEOUT_MS) &&
    FIRST_SCREEN_SKELETON_TIMEOUT_MS >= 3000 &&
    FIRST_SCREEN_SKELETON_TIMEOUT_MS <= 20000,
  `超时兜底必须是一个合理的正数，当前为 ${FIRST_SCREEN_SKELETON_TIMEOUT_MS}`,
)

// 最短可见时长：本地历史请求十几毫秒就返回时，骨架只出现一两帧同样像「闪一下」。
assert.ok(
  THREAD_SKELETON_MIN_VISIBLE_MS >= 120 && THREAD_SKELETON_MIN_VISIBLE_MS <= 400,
  `最短可见时长必须落在 120-400ms，当前为 ${THREAD_SKELETON_MIN_VISIBLE_MS}`,
)

assert.equal(
  threadSkeletonRemainingVisibleMs(0),
  THREAD_SKELETON_MIN_VISIBLE_MS,
  '刚出现就撤时，需要补足整段最短可见时长',
)

assert.equal(
  threadSkeletonRemainingVisibleMs(THREAD_SKELETON_MIN_VISIBLE_MS - 60),
  60,
  '已展示一部分时应只补差额',
)

assert.equal(
  threadSkeletonRemainingVisibleMs(THREAD_SKELETON_MIN_VISIBLE_MS),
  0,
  '展示够久后应允许立即撤除',
)

assert.equal(
  threadSkeletonRemainingVisibleMs(5000),
  0,
  '超过最短时长不得返回负数（否则会变成"延时很久才撤"）',
)

assert.equal(
  threadSkeletonRemainingVisibleMs(-10),
  THREAD_SKELETON_MIN_VISIBLE_MS,
  '时钟回拨产生的负耗时按"刚出现"处理',
)

assert.equal(
  threadSkeletonRemainingVisibleMs(Number.NaN),
  0,
  '不可用的耗时不应阻塞撤除',
)

console.log('首屏骨架闸门测试全部通过')
