# EmbedChat 首屏骨架延后：消除「欢迎页闪一下再跳消息列表」

日期：2026-10-27

## 现象

打开 `/embed/chat`（含宿主 iframe 与独立打开）时，界面先出现欢迎页（`WelcomeDashboard`：
问候语 + 快捷卡片 + 我的资源），紧接着整块被会话历史替换成消息列表。整个过程没有
loading 的过渡感，看起来像「页面渲染错了又纠正」。

切换历史会话（点侧栏另一条会话）时同样会闪一次欢迎页。

## 根因

`EmbedChat.vue` 的消息区是三选一渲染：

```html
<div v-if="isInitialLoading">骨架</div>
<WelcomeDashboard v-else-if="messages.length === 0" />
<!-- 消息列表 v-for -->
```

旧实现把 `isInitialLoading` 当成「鉴权中」指示器：`initChat()` 里 `validateToken()` 一过就
`isInitialLoading.value = false`。但后续步骤仍在串行 await：

1. 身份校验、置顶拉取（本地）
2. 确定 `conversationId`：宿主 resume id / 本地指针 / `GET /chat/active` / 兜底新建
3. `loadConversationRouting()`（网络）
4. `fetchConversationHistory()`（网络）
5. `restoreEmbedStreamSnapshot()`（本地快照，但在历史之后）

第 1 步结束到第 4 步落地之间，`messages` 仍是空数组，`messages.length === 0` 分支必然
先渲染欢迎页；历史到达后 `messages.value = newHistoryBatch` 整块替换。这段空档在本地
几十毫秒，跨网/宿主环境可达数百毫秒，肉眼就是一次闪现。

切会话路径（`switchConversation`）在 `messages.value = []` 之后才发起历史请求，同样的
空档同样会命中欢迎页分支。

## 方案

骨架语义改为「当前线程还没准备好」，撤销出口收敛为两个函数。

### 状态与出口

- `beginThreadSkeleton(options?: { force?: boolean })`：显示骨架并返回**代会令牌**；
  按 `shouldStartThreadSkeleton({ force, hasMessages })` 决定是否显示——`initChat` 可能被
  宿主重推 `INIT_CONFIG` 再次调用，此时界面上已有内容，再盖骨架等于把对话糊掉再闪回来。
  同时启动 `FIRST_SCREEN_SKELETON_TIMEOUT_MS`（8s）兜底：历史/快照请求挂死时不能让骨架
  永久停留。
- `finishThreadSkeleton(token)`：令牌匹配才撤；若目标会话历史仍在飞
  （`isLoadingHistory` 为真），先挂起为 `pendingThreadSkeletonReleaseToken`，由
  `fetchConversationHistory` 的 `finally` 在历史落地后补撤——否则会出现「骨架撤了、
  历史还没到」的空窗，又闪欢迎页。
- `abortThreadSkeleton()`：失败/异常分支无条件撤骨架，并自增令牌，防止并发的旧加载链
  之后把新一代刚立起来的骨架撤掉。

### 时序变化

`initChat`：

1. 函数入口 `beginThreadSkeleton()`（在 `try` 之前）
2. 第 1 步鉴权失败、第 5 步 URL agent 锁定失败：`abortThreadSkeleton()` 后 return
3. 第 2 步**不再撤骨架**（原先的 `isInitialLoading.value = false` 删除）
4. 第 8 步改为链式收尾，**刻意不 await**：
   `void fetchConversationHistory()` → `.then(() => restoreEmbedStreamSnapshot())` →
   `.finally(() => finishThreadSkeleton(skeletonToken))`
5. 无 `conversationId` 时直接 `finishThreadSkeleton()`，之后才做第 9 步侧栏补拉

**为什么第 8 步不能 await**：宿主 `INIT_CONFIG` 的处理链是 `await initChat(...)`，而
「正在获取登录信息」遮罩挂在 `hostInitInFlight` 上。让 `initChat` 等一次历史往返，遮罩就会
一直盖到历史落地——遮罩压在骨架之上，用户看不到刚立的骨架动效，观感仍是「遮罩撤掉时内容
已经就位」。骨架本身就是「等历史」的指示器，初始化握手不必跟着等。

`switchConversation`：`messages.value = []` 之后立即
`beginThreadSkeleton({ force: true })`（清空后没有消息，必须强制遮挡），
历史 + 快照落地后 `finishThreadSkeleton()`。

### 视觉

- 骨架自带 `animate-pulse` 与跳动点，已足够表达「正在同步」。
- **最短可见时长** `THREAD_SKELETON_MIN_VISIBLE_MS`（180ms）：本地/内网历史请求可能十几
  毫秒返回，骨架只出现一两帧同样像「闪一下」；`finishThreadSkeleton` 按
  `threadSkeletonRemainingVisibleMs(elapsed)` 补足剩余时长再撤。
- 消息项本来就有 `animate-fade-in-up` 入场动画，骨架撤除即内容淡入，不额外加
  `Transition`（避免和消息级动画叠加）。
- 首屏骨架期间隐藏「正在加载历史记录...」小胶囊（`isLoadingHistory && !isInitialLoading`），
  否则两套 loading 叠加。

## 边界与取舍

- 代价：首屏内容出现晚约一次历史请求的耗时。换来全程动效连贯、无跳变。
- 欢迎页此后只在真正的空会话出现（新建会话、清空会话后），这是它的本意。
- 骨架期看不到欢迎页的「我的资源」统计，但 `loadWorkbenchHome()` 在鉴权成功后即并行发起，
  骨架撤除时通常已经就绪。
- 宿主重推 `INIT_CONFIG` 时若界面已有消息，不显示骨架（不遮挡已有内容）。

## 验证

- `frontend/src/utils/embedFirstScreen.ts` 为纯函数模块（沿用
  `conversationHistoryRequest.ts` 的零依赖模式），Node 可直接跑。
- `tests/frontend/embed_first_screen_skeleton.mjs`：四种 `shouldStartThreadSkeleton`
  组合、超时常量范围、最短可见时长的 6 种耗时取值（含负数与 NaN）。
- `tests/frontend/test_embed_first_screen_skeleton_contract.py`：源码契约 12 项——
  `initChat` 内不得再出现裸的 `isInitialLoading` 赋值、撤骨架必须挂在历史与快照之链的
  链尾、初始化不得 await 历史（避免拖长宿主遮罩）、失败分支各撤一次、切会话强制遮挡、
  胶囊隐藏、超时兜底、历史在飞时挂起并在 `finally` 补撤、最短可见时长接线、卸载清理
  定时器。
- `vue-tsc --noEmit` 0 错误。

## 未做

- 未执行 `./dev.sh`（服务启停由用户在控制台执行），未做真机/控制台手工验收。
- 极端并发场景（首屏未完成时点击会话列表导致的请求去重早退）只做了挂起兜底，
  未单独建模。
