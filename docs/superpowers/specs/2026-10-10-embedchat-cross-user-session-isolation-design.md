# EmbedChat 换用户后串会话：全链路用户隔离设计

## 背景

同一浏览器/同一标签页切换登录用户后，EmbedChat 可能加载到上一位用户的会话。已定位的成因：

1. 会话 ID 存储在 `localStorage` 的 `yovole_embed_conv_id`（或 `:instance_id`）里，**没有用户维度**，且门户登出 `clearUserSession()` 不清理它；平台内嵌是 Cookie 认证（`config.token` 为空），`shouldUseServerActiveConversation()` 恒假 → 服务端活跃会话根本不查询，旧用户的会话 ID 被新用户完整继承。
2. `applyInitConfigPayload` 只在 `instance_id` 变化或带 `agent_id` 时清空 `messages`。宿主换人后重发 `INIT_CONFIG`（新凭据、同 instance）时，旧用户已渲染的消息留在内存里。
3. `fetchConversationHistory` 的落库分支被 `if (newHistoryBatch.length > 0)` 包住：新用户查旧会话返回 0 条时**不重置消息列表**，上一位用户的对话就这样留在屏幕上。
4. 嵌入会话令牌存于 `sessionStorage` 的 `nzi_embed_session_token[:instance]`，同样**没有用户维度**；`emb_ses_` 令牌 24 小时滑动续期，而门户登出只吊销请求里带到的那一个凭据（`X-API-Key` → `portal_session` → `embed_session`），iframe 内的存储令牌可能存活 → 新用户被以旧用户身份认证。令牌被拒时，又因为 `storedSessionCredential` 非空而**跳过 Cookie 兜底** → 新用户直接登录失败。
5. 服务端 `/api/v1/chat/history` 对 `role=admin` 把 `user_id` 置 `None`（只按 `conversation_id` 过滤），`/chat/logs/{trace_id}`、`DELETE /chat/history/{trace_id}`、`POST /chat/history/batch-delete`、`GET /chat/export/data/{trace_id}` 同样存在 admin 旁路 → 管理员登录后一旦继承到别人的会话 ID，就会**真的读到/删掉别人的会话**。

## 不变量

> **聊天面**（常规 `/api/v1/chat/*` 端点的会话级读写、EmbedChat 界面）只能访问**当前认证用户名下**的会话数据，**任何角色都不再拥有跨用户会话能力**——`role == "admin"` 同样只能看到自己的会话。
>
> **聊天日志审计页（ChatLogs）例外，且是显式例外**：它通过独立的 `/api/v1/chat/admin/*` 端点（`Depends(require_admin)`）保留 admin 跨用户查阅聊天日志的能力。两条链路在代码里是**不同端点**，因此聊天面不可能"顺手"用上跨用户视图。
>
> 口径演进：本文最初按"admin 也不能查别人"实现（连 ChatLogs 一并收缩）；用户随后确认**审计能力应当保留**，于是把它落到独立端点上（见「审计能力（admin 跨用户）」一节）。

## 后端设计

| 端点 | 现状 | 设计 |
| --- | --- | --- |
| `GET /chat/history` | admin 旁路（`history_user_id=None`） | **始终**按当前用户过滤（admin 同样；删除用户名归属映射等跨用户分支） |
| `GET /chat/logs/{trace_id}` | admin 可读任意 trace | **始终**自隔离；他人 trace → 404（不泄露存在性） |
| `DELETE /chat/history/{trace_id}` | admin 可删任意记录 | 先按 `user_id` 过滤再判定，他人记录 → 404 且不删除 |
| `POST /chat/history/batch-delete` | admin 可删任意会话（含对方 Redis 记忆） | 去掉 admin 分支，仅删当前用户记录与自己的 Redis 记忆 |
| `GET /chat/export/data/{trace_id}` | admin 可导出任意 trace | 始终自隔离 |
| `POST /chat/history/truncate` | 已自隔离 | 不变 |
| `GET/PUT /chat/conversation/{id}/resource-scope`、`GET /chat/conversation/{id}`、`run-status`、`stream-events`、`context-usage`、`context_compactions`、`model_calls` | 已按 `user_id` / `user_id+conversation_id` 键隔离 | 不变 |
| `GET /chat/reusable-results` | 归属守卫未透传实例 | 透传 `instance_id`（活跃会话按实例分桶，漏传会退回默认桶把「自己的活跃会话」判成不存在） |
| `GET /chat/conversation/{id}/ownership` | 无 | **新增**，返回 `{owned, foreign}`（可选 `instance_id`，活跃会话按实例分桶），供前端在采用会话 ID 前校验（自隔离，admin 亦按自己判定） |
| `POST /chat/active` | 任意 conversation_id 都能登记为活跃会话 | 拒绝登记「已属于他人」的会话 ID |
| `POST /chat/completions` | 未校验 conversation_id 归属 | 拒绝携带「已属于他人」的会话 ID（403） |
| `GET /chat/admin/history` | 无 | **新增**审计端点，`require_admin`，跨用户查询（自隔离端点不受影响） |
| `GET /chat/admin/logs/{trace_id}` | 无 | **新增**审计端点，`require_admin`，跨用户执行链路 |
| `GET /chat/admin/conversation/{id}/context_compactions` | 无（他人会话的压缩时间线**一直读不到**） | **新增**审计端点，按会话归属人读取 |

归属判定 `_conversation_owned_by_other_user(db, user_id, conversation_id)`：该 `conversation_id` 在 `ai_agent_execution_history` 中是否存在 `user_id != 当前用户` 的行。仅用于**写侧**守卫；读侧本来就按用户过滤。新会话（尚无任何行）不受影响。

## 前端设计（EmbedChat）

1. **会话 ID 存储按用户分桶**：`yovole_embed_conv_id:u:<uid>`（无实例）/ `yovole_embed_conv_id:u:<uid>:<instance>`（有实例）。**不再读取**无用户维度的旧键（旧键无法证明归属），读到即清除，避免任何跨用户继承；旧数据的代价只是首次进聊天时换一个新会话（历史列表与服务端记录不受影响）。
2. **身份变化即重置整个界面现场**：认证成功后记录稳定用户 ID；与上一次不同则执行 `resetSessionForIdentityChange()`。除 `messages`、`conversationId`、`requestedConversationId`、资源范围、历史分页状态、流式草稿快照外，还必须清掉**只清数据不足以覆盖**的那些界面状态，否则换人后仍会看到/操作上一位用户的内容：
   - 历史侧栏列表与筛选（列表项里就有对方的提问原文与摘要）、侧栏点击的采用序列；
   - 浏览器面板会话（`browserSessionId` / `browserViewerToken` / 可见性）与网页预览 URL——服务端 viewer token 是**会话级凭证、不绑用户**，不断开等于把对方正在跑的浏览器画面（含输入转发）留给新用户；同时在途的面板打开请求要作废（`browserOpenGeneration += 1`）；
   - 输入区草稿文字、已上传附件（含图片）、本轮挂载的数据集芯片；
   - 回溯/统计弹窗的内存内容（执行链、统计）、远程运行轮询与沙箱降级提示、流式续显的两个标志位；
   - 智能体列表与路由偏好的「已拉取」标记（置为未拉取，让新身份重新拉一次；纯 Cookie 换身份不会触发 token watcher 的强制刷新）；
   - 宿主业务上下文（`injectedContext`）与欢迎语（后者含上一位用户的姓名，按「宿主覆盖文案优先、否则用新身份重算」恢复）。
3. **采用会话 ID 前校验归属**：宿主下发的 `requestedConversationId`、本地存储的 ID、服务端活跃会话指针三处，采用前都要调 `/chat/conversation/{id}/ownership`，且**每次 await 回来都要复核初始化代次**（`conversationInitializationGeneration`）后才允许使用结论——否则上一代初始化的结论会写进新一代刚重置完的状态里。校验结论分三态：`adoptable`（采用）/ `foreign`（不采用**并清掉**本地记录）/ `unknown`（网络或服务异常，不采用但**不清**本地记录，避免一次抖动永久删掉用户自己的续接）。侧栏点选切换会话同样要走这一校验。
4. **待采用的会话 ID 在通过校验前不得落盘**：`applyInitConfigPayload` 只登记 `requestedConversationId`，不写 `conversationId.value`、不写 localStorage——否则会把一个未校验的 ID 写进当前身份的桶，覆盖本人原有的续接记录。
5. **首屏历史为空也要重置消息列表**：把重置逻辑移出 `newHistoryBatch.length > 0` 守卫。
6. **流式快照按用户分桶**：`nzi_embed_stream_snapshot:<uid>:<instance>:<cid>`，并在身份变化时清理；**身份未知时不产生键**（读写清三处一律短路），不得回退到 `unknown`/共享桶——草稿正文就是会话内容。
7. **旧的无维度会话键全仓库「只清理、不读取」**：不只是 `EmbedChat` 自己；`ChatCanvas.vue` 与 `MessageRenderer.vue` 曾在该键缺失时回退读 `yovole_embed_conv_id`（两者调用方都已传入 prop，回退分支是死代码却读的是上一位使用者的会话 ID），已删除。
8. **嵌入会话令牌不得越过当前门户身份**：
   - 存储令牌校验成功后，若同源 Cookie 会话存在且用户 ID 与之不一致，则丢弃存储令牌、以 Cookie 身份重新认证（当前坐在键盘前的人是谁，以门户会话为准）；
   - **显式**传入的 `?token=` / `INIT_CONFIG` 凭据被拒即判定失败：不得回落到本 tab 存储的令牌（那是上一位使用者的），也不得回落到 Cookie——否则宿主想切到 B、页面却静默以 A 的身份继续跑；
   - 只有「本 tab 存储的令牌」被拒时才允许回落 Cookie 身份（换人登录的主场景）。

## 审计能力（admin 跨用户）

审计页需要跨用户视图（排障、客服、合规），但**不能**以"让常规端点对 admin 放行"的方式实现——那正是本轮修掉的串会话通道。因此：

1. **实现共用、入口分开**：`get_history` / `get_trace_logs` 的实现各自抽成 `_build_chat_history_response` / `_build_trace_logs_response`，由路由壳子传 `cross_user` 开关。常规路由固定 `False`，`/chat/admin/*` 固定 `True` 并挂 `Depends(require_admin)`。
2. **参数签名不漂移**：历史查询参数抽成 `_ChatHistoryQueryParams` 依赖类，两个路由共用同一份定义。
3. **审计视图的取舍**：
   - `history_user_id=None`（不按用户过滤），`username` 筛选照常可用；
   - 资源范围按「归属用户 + 会话」逐个读取（`get_many_for_owners`），返回值与当前页的行**一一对应**（不是 `cid -> scope` 映射：一页里同一个 `conversation_id` 可能分属两个归属人——历史遗留的串号会话——按 cid 建映射会互相覆盖，把项目名显示成另一个人的）；并用 `_resolve_owner_user_ids_by_username` 为早期"只有 username、user_id 为空"的历史行补出归属人；
   - **跳过**可复用结果元数据的 Redis 富化（Redis 历史按「用户 + 会话」存储，用管理员身份读他人会话没有跨用户语义）——写成显式的 `not cross_user`，即旧实现里"admin 跳过富化"特例的本意；
   - 压缩时间线用 `_resolve_conversation_owner_user_id` 由历史行解析归属人后再读（此前该能力不存在，审计页「上下文」页签在看他人会话时是空的）。
4. **前端**：`agentApi` 新增三个 `getAdmin*` 方法；`ChatLogs.vue` 用 `historyApi` / `traceApi` / `compactionsApi` 三个 computed 开关按 `isAdmin` 切换数据源，所有取数都经过开关；非 admin 但拥有 `menu:chat_logs` 的用户退化为只看自己的会话。
5. **既有 admin 场景的连带对齐**：`/chat/history`、`/chat/logs/*` 的 admin 旁路被删除后，还有两个界面此前是靠它顺带具备跨用户能力的，需一并切到审计端点：`AgentHistoryModal.vue`（智能体「对话历史」弹窗，此前 admin 能看到所有用户在该智能体下的执行记录）、`TaskCenter.vue`（admin 打开他人任务后「展开步骤」：此前能读他人 trace，收紧后对该 trace 返回 404，而该处的 catch 只打 console、失败表现为「步骤静默为空」）。两处都用各文件已有的角色信息（`useUser().isAdmin` / `userInfo.role`）做分流，不做新的权限码。
6. **权限粒度**：沿用 `Depends(require_admin)`，即"所有 admin 等价"。**未**引入独立审计员权限码——本仓库 `require_permission` 对 admin 直接放行，用权限码并不能把其它 admin 挡在外面，真正要区分需要新的角色/权限模型（见"不在本次范围内"）。

## 验证策略

- 后端 API 用例：普通用户与管理员在 `/chat/history`、`/chat/logs/{trace_id}` 都读不到他人会话；`/ownership` 判定正确；`POST /chat/active`、`/chat/completions` 拒绝他人会话；删除类端点（单条 / 批量）不触碰他人记录；导出端点同样自隔离。
- 审计端点用例：admin 能按会话/按用户名跨用户读取历史、能读他人轨迹、压缩时间线按归属人读取；非 admin 一律 403、未认证 401、不存在的 trace 404；**反向护栏**：常规 `/history` 对 admin 仍返回空、常规 `/logs/{trace_id}` 对 admin 仍 404。
- 前端契约用例：存储键含用户维度且不再读取旧键；身份变化重置函数存在且被调用；首屏空历史重置；采用会话 ID 前调用归属校验；令牌与 Cookie 身份比对。
- 回归：`tests/frontend`、`tests/api`、`tests/core`、`vue-tsc --noEmit`。

## 不在本次范围内

- 不改数据库结构（无迁移）。
- 不改门户登出的凭据吊销顺序（本次通过「Cookie 身份优先于存储令牌」在前端消除影响）。
- **未**给跨用户读/导出补访问审计留痕（`AuditService` 已有请求级审计与脱敏，未在 ChatLogs 路径上额外记录"谁看了谁的会话"）。
- **未**把导出收紧到后端校验 `element:chat_logs:export`（该权限码目前只在前端隐藏按钮）。
- **未**引入独立"审计员"角色或非直通的权限码：`Depends(require_admin)` 的语义是"所有 admin 等价"。
- **未**处理 `/api/portal/audit/*` 仅挂 `require_api_key`（任何已登录用户可读他人请求日志）的问题——与本主题相邻，需单独决策。

## 复审轮新增的已知限制（明确不做，记录在案）

| 项 | 说明与理由 |
| --- | --- |
| 会话存在性探测面 | `/chat/conversation/{id}/ownership` 返回 `foreign=true`、写侧拒绝时回 403「该会话不属于当前用户」，二者合起来让**已认证**用户能判断「某 cid 是否存在且属于他人」。cid 是随机串（不可枚举），且换成中性错误会显著损害排障可读性；接受该低风险信息披露（未加限流、未加访问留痕）。 |
| 浏览器查看令牌不绑用户 | 服务端 `resolve_viewer_token` 只校验**过期 + 令牌哈希**，WebSocket 入口只比对 `session.id`，不校验连接者是否该会话的归属人——viewer token 本质是会话级 bearer。前端已在身份变化时断开面板并丢弃 token；**服务端侧绑定（握手时校验归属人）未做**，属于浏览器子系统改动。 |
| `AgentDebug` 的会话键 | 调试页仍用无用户维度的 `agent_debug_conv_id`。写入侧守卫上线后，同浏览器换人打开调试页会沿用上一位用户的调试会话：发消息得到 403（fail-closed、不泄露内容），但页面不会提示，用户需手动新建会话。修它要给该页做身份分桶 + 归属校验，未在本轮范围内。 |
| `ReusableResultList.vue` 未带实例 | 该抽屉组件拿不到实例上下文，`/chat/reusable-results` 仍不带 `instance_id`（后端已支持、`EmbedChat` 直调处已带）。实际不可达：可复用结果存在即意味着该会话已有 DB 行，归属判定不依赖活跃会话桶。 |
| 匿名降级提示的 `unknown` 桶 | 无用户身份的运行仍写 `sandbox:degraded:unknown:{cid}`；任何已认证读取方都查不到该键（fail-closed），相比改动前「知道 cid 就能读到」是收紧。保留写入以便匿名运行时至少留下痕迹。 |
| 界面级偏好仍按浏览器共享 | 抽屉固定、显示开关、主题等 localStorage 项不含会话数据，换人后沿用（有意为之，不属隔离范围）；「数据集本轮挂载」「输入草稿/附件」等**会话相关**状态已随身份变化清空。 |
