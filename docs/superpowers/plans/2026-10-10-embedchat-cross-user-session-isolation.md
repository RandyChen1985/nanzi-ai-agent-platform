# EmbedChat 跨用户会话隔离：实施计划

- 设计文档：`docs/superpowers/specs/2026-10-10-embedchat-cross-user-session-isolation-design.md`
- 起因：用户提出「embedchat 打开后是如何加载会话的？我切换用户登录后，有没有可能加载到其他用户的会话？」
- 结论：**会**。并且不止读——还会把新用户的提问写进上一位用户的会话。
- 口径（最终）：**聊天面（EmbedChat 与常规 `/api/v1/chat/*` 端点）任何角色都不得跨用户访问会话，`admin` 也不例外**；**聊天日志审计页（ChatLogs）保留 admin 跨用户查阅能力**，但落到独立的 `/api/v1/chat/admin/*` 审计端点上（`require_admin`），不复用聊天面的端点。
- 口径演进：先按"admin 也不能查别人"实现（连 ChatLogs 一并收缩），用户随后确认审计能力应保留 → 见本文「七、审计能力回归」。

## 一、缺陷清单（为什么切换用户会串会话）

| # | 层 | 缺陷 | 位置 |
| --- | --- | --- | --- |
| 1 | 后端 | `GET /chat/history` 对 admin 去掉用户过滤，返回**全库**任意用户会话 | `app/api/v1/endpoints/chat.py` `get_history` |
| 2 | 后端 | `GET /chat/logs/{trace_id}` 对 admin 不做归属校验，可读他人完整执行链 | 同上 `get_trace_logs` |
| 3 | 后端 | `DELETE /chat/history/{trace_id}` admin 可删任意用户记录 | 同上 `delete_history` |
| 4 | 后端 | `POST /chat/history/batch-delete` admin 可删任意用户会话（含对方 Redis 记忆） | 同上 `batch_delete_history` |
| 5 | 后端 | `GET /chat/export/data/{trace_id}` admin 可导出任意用户工具数据 | 同上 `export_trace_data` |
| 6 | 后端 | `POST /chat/active` 不校验归属：别人残留的会话 ID 会被登记成「我的活跃会话」 | 同上 `set_active_conversation` |
| 7 | 后端 | `POST /chat/completions` 不校验归属：提问直接写进他人会话 | 同上 `create_chat_completion` |
| 8 | 后端 | 沙箱降级提示 Redis 键只按 `conversation_id`，他人可读到本会话的运行提示 | `app/services/ai/runtime/sandbox_degradation.py` |
| 9 | 前端 | 会话 ID 存 localStorage **只按 instance 分桶**，没有用户维度 | `frontend/src/views/EmbedChat.vue` |
| 10 | 前端 | 身份变化不清内存现场（`messages`／`conversationId`／分页／资源范围） | 同上 `initChat` |
| 11 | 前端 | 首屏历史为空时**不清空** `messages`（空结果是他人会话的典型表现） | 同上 `fetchConversationHistory` |
| 12 | 前端 | 流式草稿快照同样无用户维度，会把他人会话正文还原到新用户界面 | 同上 `streamSnapshotStorageKey` |
| 13 | 前端 | 本 tab 存储的嵌入令牌属于上一位用户时，仍按旧身份继续请求（Cookie 里已是新用户） | 同上 `validateToken` |
| 14 | 前端 | 存储令牌被拒后**不回落到 Cookie**，换人登录只会停在骨架屏（迫使用户清缓存） | 同上 |

## 二、后端改动

1. **五个 admin 旁路全部删除**（不改成权限码门控，而是直接收缩为自隔离）：`get_history`（含 group_by / 非 group_by 两条 admin 分支与用户名归属映射一并删除）、`get_trace_logs`、`delete_history`、`batch_delete_history`、`export_trace_data`。他人数据一律按「不存在」处理（读/删 404），不泄露存在性。
2. **新增归属查询** `GET /conversation/{conversation_id}/ownership` → `{owned, foreign}`：
   - `foreign`：DB 中存在**其他用户**的同一 `conversation_id` 轮次；
   - `owned`：非 foreign 且满足 `_conversation_belongs_to_user`（DB 行 / 该用户 Redis 历史 / 该用户**该实例**的活跃会话）。
   - 全新会话 `{owned: false, foreign: false}` → 前端可安全采用。
   - 可选查询参数 `instance_id`：活跃会话按实例分桶存储，丢掉它会把多实例嵌入下「自己的活跃会话」误判为不归属（前端调用时本就带着 `instance_id`，必须透传）。
3. **写侧归属守卫** `_conversation_owned_by_other_user()`：`POST /chat/active`（403）、`POST /chat/completions`（403，放在最前面，避免进入模型执行）。历史遗留 `user_id IS NULL` 行不算他人所有。
4. **可复用结果元数据富化不再对 admin 跳过**（原特例的存在理由是「admin 可能串用他人 Redis 身份」，自隔离后该理由消失，特例只会让 admin 看不到自己会话的数据徽标）。
5. **沙箱降级提示键补 user 维度**：`sandbox:degraded:{user_id}:{conversation_id}`；写入方用 runner 的 `_runtime_user_id()`，读取方用当前请求用户。

## 三、前端改动（EmbedChat.vue）

1. **会话存储键带身份维度**：`yovole_embed_conv_id:u:<identity>[:<instance>]`；身份未知时不读不写；**旧的无维度键只清理、绝不读取**（代价是升级后重新开一个空会话）。
2. **流式草稿快照同样带身份维度**：`nzi_embed_stream_snapshot:u:<identity>:<instance>:<cid>`，旧键只清理。
3. **身份变化即重置会话现场** `resetSessionForIdentityChange()`：清 `messages`、`conversationId`、分页游标、资源范围、草稿引用与续显游标、旧草稿快照；保留本次 `INIT_CONFIG` 下发的 resume id（它属于新身份）。挂在 `initChat` 鉴权成功之后、采用会话 ID 之前。
4. **采用任何会话 ID 前先归属校验** `isConversationAdoptable()`：宿主下发、本地残留、服务端活跃会话三处都过一遍；`foreign` 或校验失败（网络/服务异常）一律不采用——本地键清掉、宿主 ID 连同 `conversationId` 一起清掉、服务端活跃指针交由「新建会话」覆盖。
5. **首屏历史无条件覆盖**：`newHistoryBatch.length === 0 && !isLoadMore` 时 `messages.value = []`（此前空历史会保留上一位用户已渲染的消息）。
6. **存储令牌让位给 Cookie 身份** `switchToCookieIdentityIfChanged()`：仅当命中的是「本 tab 存储的令牌」（非本次显式下发的 token）时才做身份比对，不一致则以 Cookie 身份重新认证并丢弃存储令牌；被拒后允许回落 Cookie。**显式凭据仍然 fail-closed**（宿主传错 token 不得以浏览器里残留的会话「成功」进入）。

## 四、测试

- 后端新增 `tests/api/v1/test_chat_session_user_isolation.py`（13 项）：admin／普通用户读不到他人 `history`／`logs`／`export`；他人记录删不掉；批量删除不碰他人会话；`/ownership` 三态＋`instance_id` 透传（活跃会话按实例分桶）；`/chat/active` 与 `/chat/completions` 拒绝他人会话（`chat_completion` 用 `AsyncMock` 钉住「未进入执行」）；本人历史仍能读（防过度收紧）。
- 前端新增 `tests/frontend/test_embed_cross_user_session_isolation_contract.py`（7 项）＋更新 `test_embed_instance_session_contract.py`、`test_embed_explicit_token_no_cookie_fallback_contract.py`。
- 同步修正的既有前端契约：`test_chat_surface_refactor_contract.py` 的顺序断言改为只在 `stopGeneration` 函数体内比较「先请求后端取消、再断开 SSE」（新增的身份切换重置里也会 abort，全文 `find` 会命中错位置）；变异验证：把 abort 提到 cancel 之前即失败。
- 随改动同步的后端用例：`test_active_conversation_instance_contract.py`（端点新增 `db` 参数 + 新增拒绝用例）、`test_chat_completions.py`（SSE 契约用例的 DB 替身需返回「查不到他人会话」）、`test_reusable_result_api.py`（富化不再对 admin 跳过）。

## 五、验证结果与边界

- 后端：`tests/api` + `tests/core` **704 passed / 8 xfailed**（第一阶段为 689，第二阶段加审计用例、复审轮补 4 项），唯一失败 `test_list_artifacts_uses_configured_public_url` 已用 `git stash` 对照确认**改动前即失败**（与本次无关）。
- `tests/services`：**1218 passed**，2 项失败（`test_agent_runtime_max_image_num_is_hardcoded_to_5`、`test_normalize_external_user`）同样对照确认改动前即失败。
- 前端契约：`pytest --confcutdir=tests/frontend tests/frontend` 全绿；`vue-tsc --noEmit` 无新增错误。
- 不改数据库结构、无迁移；未执行 `./dev.sh`、未提交 git。（跨用户审计端点见下方「七」——它是**独立端点**，不复用聊天面端点。）
- **本次未覆盖（需另行决策）**：`GET /chat/generated-files/{artifact_id}` 是公开能力型下载链接（`artifact_id + token` 即凭证，无用户维度）；`GET /chat/agents/{agent_id}/chat` 未透传 `user_info`/`db` 因而必然 401（fail-closed，非越权）；引用详情 Redis 键仅按 `trace_id`（读取入口已收敛到本人历史，暂未加 user 维度）。

## 六、测试补记（前端）

`test_chat_surface_refactor_contract.py` 的「先请求后端取消、再断开 SSE」顺序断言原先用全文 `find`，会被新增的「身份切换重置里也会 abort」顶掉命中位置；已改为只在 `stopGeneration` 函数体内比较顺序，并用变异验证确认仍有牙（把 abort 提到 cancel 之前即失败）。

## 七、审计能力回归（口径修订后的第二个阶段）

第一阶段把 admin 的跨用户能力整体删掉（含 ChatLogs）。用户随后确认：**审计页要保留 admin 查阅他人聊天日志的能力**。这一阶段把这个能力放到**独立端点**上，而不是恢复常规端点里的 admin 旁路——否则 EmbedChat 又会重新拥有跨用户通道。

### 实现

| 项 | 做法 |
| --- | --- |
| 实现共用 | `get_history` / `get_trace_logs` 的正文抽成 `_build_chat_history_response` / `_build_trace_logs_response`，路由壳子传 `cross_user` |
| 常规路由 | `/history`、`/logs/{trace_id}` 固定 `cross_user=False`（聊天面自隔离不变） |
| 审计路由 | `/admin/history`、`/admin/logs/{trace_id}`、`/admin/conversation/{id}/context_compactions`，固定 `cross_user=True` + `Depends(require_admin)` |
| 参数签名 | 抽 `_ChatHistoryQueryParams` 依赖类，两个历史路由共用，避免签名漂移 |
| 审计资源范围 | 按「归属用户 + 会话」读 `get_many_for_owners`；`_resolve_owner_user_ids_by_username` 为 `user_id` 为空的老行补归属人 |
| 审计富化 | 显式 `not cross_user` 跳过 Redis 可复用结果富化（Redis 按「用户 + 会话」存储，无跨用户语义） |
| 压缩时间线 | `_resolve_conversation_owner_user_id` 先解析归属人，再读 `context_compaction_log_service.list_records`（此前该能力不存在，他人会话的压缩页签一直是空的） |
| 前端 | `agentApi` 新增 `getAdminChatHistory` / `getAdminChatTrace` / `getAdminContextCompactions`；`ChatLogs.vue` 用 `historyApi`/`traceApi`/`compactionsApi` 三个 computed 按 `isAdmin` 切换；非 admin 退化为只看自己 |
| 连带场景 1 | `AgentHistoryModal.vue`（智能体「对话历史」弹窗，只按 `agent_id` 过滤）经 `useUser().isAdmin` 切到审计端点，恢复 admin 的跨用户智能体排障视角 |
| 连带场景 2 | `TaskCenter.vue` 的「展开步骤」（admin 可打开他人任务）经 `traceApi` computed 切到审计轨迹端点；否则他人任务返回 404，而该处 catch 只打 console，表现为步骤静默为空 |

### 测试

- 新增 `tests/api/v1/test_chat_admin_audit_access.py`（14 项，先红后绿：实现前 8 failed）：admin 跨用户读历史（含归属用户名）、`username` 筛选命中/不命中、admin 读他人轨迹、非 admin 403、未认证 401、不存在 trace 404、压缩时间线按归属人读取（`AsyncMock` 钉住传给服务的 user_id）、两条反向护栏（常规 `/history` 对 admin 仍空、常规 `/logs/{trace_id}` 对 admin 仍 404），以及复审后补的三项：跨用户资源范围取**归属人**桶（两身份同会话放不同项目名，断言取到归属人的）、`group_by_conversation` 的跨用户聚合、`/admin/conversation/{id}/context_compactions` 未认证 401。
- 新增 `tests/frontend/test_chat_logs_admin_audit_contract.py`（6 项）：三个审计端点与开关的绑定、ChatLogs 内不得直接调用自隔离 API、**EmbedChat 源码不得出现 `/chat/admin/` 或 `getAdmin*`**，以及两处连带场景（智能体对话历史弹窗、任务中心展开步骤）在 admin 下切到审计端点且不再直接调用自隔离 API。
- 同步修正两份既有契约的切片方式（断言意图不变）：`test_knowledge_citation_history_contract.py`（切片起点改到共用实现）、`test_reusable_result_api.py`（`inspect.getsource` 改到共用实现 + 新增 `not cross_user` 断言）。

### 验证

`tests/api` + `tests/core` **704 passed / 8 xfailed**（唯一失败 `test_list_artifacts_uses_configured_public_url` 为改动前既有）；前端契约 **1660 passed**；`vue-tsc --noEmit` 零错误。

### 复审（第三轮：自查 + 独立复审）

对整份改动做了一次自查与两路独立复审（后端越权面 + 前端身份状态机）。自查发现并修复一处新代码缺陷：`/chat/conversation/{id}/ownership` 未接收前端本就会传的 `instance_id`，导致 `_conversation_belongs_to_user` 里的活跃会话判定退回默认实例桶——多实例嵌入下会把「自己的活跃会话」判成 `owned=false`（前端只依赖 `foreign`，故未造成越权，但接口语义失真）。已按 TDD 先补失败用例再透传 `instance_id`，并同步 spec/plan。

其余复审结论（均**非**本次改动引入）：

| 发现 | 结论 |
| --- | --- |
| `POST /chat/permissions/{id}/confirm`、`POST /chat/external-executions/{id}/resume` 是否可操作他人的挂起请求 | 已隔离：`peek_async(..., user_id=当前用户)` → `user_id` 比对不符即报错 |
| `GET /artifacts`、`/artifacts/counts`、`/reusable-results`、`/upload`、`/conversation/{id}/*` 全量端点 | 逐个核对：读按 `user_id`（或 `user_id+cid` 键）过滤，写有归属守卫，无跨用户通道 |
| `GET /chat/agents/{agent_id}/chat` | **改动前即坏**：直调 `create_chat_completion(completion_request, request)` 未传 `user_info`/`db`，`Depends` 占位对象进 `_require_chat_user_id` → 恒 **401**（已实测确认）。fail-closed、无越权、前端与测试均无调用方；修法是把 `user_info`/`db` 一并透传（约 2 行），本次**未改**以待确认 |
| `_conversation_belongs_to_user` 的 `unknown` 分桶 | 写入方 `_runtime_user_id()` 在 `user_info` 缺失时返回 `None` → 键为 `sandbox:degraded:unknown:{cid}`，已认证读取方永远查不到；相比改动前「任何知道 cid 的人都能读到该提示」，这是刻意的收紧，无用户身份的运行本就无法通过 `run-status` 鉴权读取 |
| 会话被历史污染（同一 cid 混有他人行） | `/ownership` 会判 `foreign=true`，该用户界面将不再采用这个「自己也有一半」的 cid 而改用新会话——刻意的取舍：cid 归属已不可证明 |

### 仍未做

跨用户读/导出的访问留痕；导出端点的 `element:chat_logs:export` 后端校验；独立"审计员"角色（`require_admin` 语义仍是"所有 admin 等价"）；`/api/portal/audit/*` 仅挂 `require_api_key` 的相邻问题。

### 复审（第四轮：两路独立复审发现的逐条落实）

两路独立复审（后端越权面 / 前端身份状态机）各自给出发现；以下每条都**先自行复核再动手**，能加测试的都先红后绿，能变异验证的都做了变异验证。

#### 已修（本轮）

| # | 问题 | 修法 | 验证 |
| --- | --- | --- | --- |
| 后端 1 | `_load_history_resource_scopes` 把 `(归属人, cid)` 折叠成 `cid` —— 相对 `HEAD`（按元组取）是**本次重构自己引入的回归**：审计页一页里同一 cid 分属两个归属人时（历史遗留串号），项目名会互相覆盖 | 返回值改为与行**一一对应**的列表（调用方按下标取），从根上消除键冲突 | 新增 2 项审计用例（两归属人同 cid 各取各的项目名；`user_id` 为空的老行按用户名补归属人），并变异验证：换回折叠映射立刻变红 |
| 后端 2 | `/chat/reusable-results` 的归属守卫漏传 `instance_id`，多实例嵌入下退回默认桶 | 端点接收 `instance_id` 并透传；`artifactApi.reusableResults` 支持实例参数，EmbedChat 直调处传入 | 新增 1 项用例钉住 `instance_id` 必须到达 `get_active_conversation` |
| 前端 C1 | 身份变化不断开浏览器面板：`browserSessionId` / `browserViewerToken` 残留，而服务端 viewer token 是会话级凭证、不绑用户 ⇒ 新用户能**看到并操作**上一位用户正在跑的浏览器 | 重置时关闭面板、清 session/token/预览 URL，并作废在途打开请求（`browserOpenGeneration += 1`） | 契约断言 + 变异验证 |
| 前端 C2 | `historyList` 不清（侧栏直接渲染对方的提问原文与摘要）；侧栏点选不做归属校验就采用 cid（界面切过去、后续提问被 403 拦住） | 重置清空列表/分页/筛选；`handleHistoryClick` 先过归属校验（含点击序列防串） | 契约断言 + 变异验证 |
| 前端 I1 | `applyInitConfigPayload` 把宿主下发的**未校验** cid 直接写内存并落盘，覆盖本人原有的续接记录 | 只登记 `requestedConversationId`，采用动作挪到校验通过之后 | 契约断言 |
| 前端 I2 | 三处 `await` 归属校验之后缺少代次复核：上一代初始化的结论会写进新一代刚重置完的状态 | 每处 await 后补 `initGeneration` 复核 | 契约断言（按 await 行邻域检查）+ 变异验证 |
| 前端 I3 | 显式凭据被拒后仍回落「本 tab 存储令牌」，与文档口径矛盾——宿主想切 B，页面却静默以 A 继续 | 显式凭据被拒即失败；只有存储令牌被拒才允许回落 Cookie | 契约断言 + 变异验证 |
| 前端 I4/I5/I6/I8/I9 | 智能体列表与路由偏好不刷新、输入草稿/附件/数据集挂载残留、欢迎语保留对方姓名、远程运行态与降级提示残留、回溯与统计弹窗内存残留 | 重置函数逐项清空；欢迎语按「宿主覆盖文案优先、否则新身份重算」恢复 | 契约断言（重置语句清单扩充到 20+ 项） |
| 前端 M1 | 草稿快照键回退 `unknown` 桶（草稿正文就是会话内容） | 身份未知时不产生键，读/写/清三处一律短路 | 契约断言 + 变异验证 |
| 前端 M2 | 归属校验把「网络异常」与「属于别人」都算不可采用，且都清掉本地记录 | 改为三态结论，只有 `foreign` 清记录 | 契约断言 |
| 前端 I7 | `ChatCanvas.vue` / `MessageRenderer.vue` 仍回退读无维度的旧会话键 | 两者调用方都已传 prop（回退是死代码），删除回退；并加「全仓库 `.vue` 不得读取该键」断言 | 契约断言（全目录扫描）+ 变异验证 |

#### 复核后判定「无需修改」或「不在本轮」

| 发现 | 复核结论 |
| --- | --- |
| 后端复审建议去掉 `/ownership` 的 `foreign` 或加限流（会话存在性探测面） | 保留：前端必须区分「属于别人」与「校验不出来」（后者不清本地记录）；风险与取舍写入设计文档「已知限制」 |
| 前端复审建议 `identityKeyFromUserData` 增加 `data?.id` 兜底 | **不做**：逐一核对三处身份载荷（ticket `user_info`、`/portal/auth/user_apikey`、`/portal/auth/me`）都带 `user_id`；`id` 兜底当前无用，且在未来载荷里可能语义不同 |
| 前端复审建议清空 `welcomeCards` | **不做**：卡片来自 `/portal/agents/{id}/welcome-cards`，是**智能体维度**而非用户数据；清掉后纯 Cookie 场景没有可靠的重新加载点，会白掉一块首屏 |
| 前端复审建议 `injectedContext` 一并清空 | **已采纳并实现**（宿主页面上下文随身份作废） |
| 前端复审建议把界面偏好一并清空 | 抽屉固定/主题等纯界面偏好保持按浏览器共享，写入「已知限制」 |
| 后端复审建议服务端把 viewer token 绑到归属人 | 属浏览器子系统改动，写入「已知限制」，本轮不动 |
| `AgentDebug` 仍用无维度会话键 | 写入「已知限制」：失效模式是 403 fail-closed、不泄露内容，但页面无提示 |
| `/chat/agents/{agent_id}/chat` 恒 401、`/api/portal/audit/*` 仅挂 `require_api_key` | 均为改动前既有问题，非本次引入，保持记录 |

#### 最终验证（第四轮）

- 后端：`tests/api` + `tests/core` **707 passed / 8 xfailed / 2 xpassed / 1 failed**（唯一失败 `test_list_artifacts_uses_configured_public_url` 为改动前既有）。
- `tests/services`（用 `.venv` 的 3.11；3.13 缺 `langfuse` 无法收集）：**1397 passed / 11 skipped / 5 xfailed / 1 failed**（失败项 `test_normalize_external_user` 为改动前既有）。
- 前端契约：`pytest --confcutdir=tests/frontend tests/frontend` **1667 passed**；`vue-tsc --noEmit` **零错误**（exit 0）。
- 变异验证：本轮新增/收紧的 6 条前端契约 + 1 条后端契约，全部在「去掉修复」后变红，确认断言有牙（脚本临时文件未入库）。

### 复审（第五轮：用户点名的两项残留）

第四轮汇报时把两项「浏览器子系统 / 调试页」的残留写进了「已知限制」，用户随即点名：「修掉吧」。两项都按 TDD 走（先写用例、确认变红、再实现），并补了变异验证。

#### 已修（本轮）

| # | 问题 | 修法 | 验证 |
| --- | --- | --- | --- |
| 1 | viewer token 是会话级 bearer：`resolve_viewer_token` 只校验过期 + 哈希，WebSocket 入口只比对 `session.id` ⇒ 拿到令牌即可连上并**转发鼠标/键盘/导航**（前端「身份变化即断开面板」只堵继承路径） | 握手阶段要求连接者证明自己就是归属人：身份来自子协议 `browser-auth.<credential>`（跨站 iframe 带不了 `SameSite=Lax` Cookie，必须有这条路）或同源 `portal_session`/`embed_session`；**先判归属再 `accept()`**，取不到身份 / 不是归属人一律 `close(4403)`，且不得触碰 `browser_runtime`；admin 无例外 | 新增 `tests/api/v1/test_browser_viewer_owner_binding.py` 10 项（端点级：无身份、他人身份、admin 他人身份 → 4403 且未 accept、未 `open_session`；归属人成功流收到首帧）+ 6 项前端契约；变异：删守卫 / 只认 Cookie / 先 accept 再判 / 不带身份子协议 / 凭据变化不重连，5 条全部变红 |
| 2 | 前端没有把身份送上握手：面板只用 `browser-viewer.<token>` 子协议 | `viewerSocketProtocols()` 复用父级已有的 `:auth-token="config.token"` 追加 `browser-auth.<credential>`；子协议值做 HTTP token 字符白名单（非法字符会让 `new WebSocket()` 抛 `SyntaxError`，不合格就退回 Cookie 判定）；`authToken` 纳入重连依赖 | 前端契约 6 项（含字符集守卫、重连依赖、EmbedChat 传参）；变异 2 条变红 |
| 3 | `AgentDebug.vue` 的调试会话指针无用户维度，且 `onMounted` **同步**读取 ⇒ `fetchCurrentUser()` 未返回就采用上一位使用者的会话 | 键改 `agent_debug_conv_id:u:<identity>`（身份未知即空键：不读不写）；旧键只清理不读取；抽 `bootstrapDebugConversation()` 先 `await fetchCurrentUser()` 再读存储；采用前过 `/ownership` 三态（`adoptable` 采用 / `foreign` 清指针重开 / `unknown` 只换内存会话并保留指针） | 新增 `tests/frontend/test_agent_debug_conversation_isolation_contract.py` 7 项；变异 4 条（回读旧键、跳过归属校验、foreign 不清指针、`persist` 失效）全部变红 |

#### 保留（本轮明确不做）

| 发现 | 复核结论 |
| --- | --- |
| `GET /sessions/{id}/screenshot` 未绑归属 | 保留为令牌/查看 Cookie 能力 URL：`<img src>` 无法自定义请求头，跨站 iframe 又没有 Cookie，绑身份会直接打断画面读取；其会话 ID 为 uuid4 不可枚举，新用户侧面板状态已清空、交付 `screenshot_ref` 的 WebSocket 也已绑归属 |
| 跨站 iframe 的 `SameSite=Lax` Cookie | 仍是既有约束（`_set_embed_session_cookie` 注释里已记为后续阶段）；本轮的身份子协议正是为绕开它而设，`SameSite=None` 的切换不在本次范围 |

#### 最终验证（第五轮）

- 后端：`tests/api` + `tests/core` **717 passed / 8 xfailed / 2 xpassed / 1 failed**（唯一失败 `test_list_artifacts_uses_configured_public_url` 为改动前既有；707 → 717 即本轮新增 10 项）。
- 前端契约：`pytest --confcutdir=tests/frontend tests/frontend` **1680 passed**（1667 → 1680，本轮新增 13 项）；`vue-tsc --noEmit` **exit 0、零错误**。
- 变异验证：**9 条**（后端 3 + 前端 2 + 调试页 4）全部在「去掉修复」后变红，且每个文件按 md5 校验原样恢复；变异脚本为临时文件、未入库。

