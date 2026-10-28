# 会话历史在线分享：公开 / 密钥访问、有效期与我分享的

日期：2026-10-28

## 背景

会话历史侧栏（`frontend/src/components/ChatHistorySidebar.vue`，由 `EmbedChat.vue` 与
`AgentDebug.vue` 共用）的条目操作只有「置顶 / 导出 Markdown / 回溯日志 / 删除」。
用户要把一次分析结果发给同事，只能导出 `.md` 文件再手动传，对方收到的是一份离线文本，
既不能在线看，也没法收回。

目标：给会话历史加「在线分享」——生成一个只读链接，可选公开或密钥访问、可设有效期，
并在侧栏新增「我分享的」视图，支持复制链接/密码、作废、查看被打开次数。

## 已确认的产品决策

| 决策点 | 结论 |
|---|---|
| 分享内容来源 | **分享即快照**：创建时冻结消息副本，原会话继续对话/被删除/Redis 过期都不影响 |
| 密钥交付 | **地址与密码分离**，链接固定为 `/share/<id>`，访客在页面上输入密码；密码可自动生成 8 位、也可自定义；分享人自己打开免输 |
| 展示粒度 | **仅用户提问 + 助手最终回复正文**，不含思考过程、工具调用、SQL、文件路径 |
| 有效期 | 预设 1 小时 / 24 小时 / 7 天（默认）/ 30 天 / 永久，另有自定义数字 + 小时或天 |
| 分享管理 | 复制链接、复制密码、作废；不做删除记录、不改有效期、不重置密码、不做访问明细表 |
| 入口权限 | 不加 `element:*` 权限码，登录用户即可用（与导出 Markdown 同级） |
| 访问计数 | 成功拿到内容才 +1（含同一人刷新），同时记录最近一次打开时间 |

## 总体架构

```
分享人（登录）                         访客（匿名）
  │                                      │
  │ POST /api/portal/chat-shares         │ GET  /share/:shareId        （前端 public 路由）
  │   → 拉会话全部轮次 → 清洗 → 冻快照     │   ├─ GET  /api/v1/share/:id         元信息
  │ GET  /api/portal/chat-shares         │   ├─ POST /api/v1/share/:id/unlock  换 token
  │ POST /api/portal/chat-shares/:id/revoke │  └─ GET  /api/v1/share/:id/content 快照（+1 计数）
```

- 所有者侧沿用现有 `require_api_key`（`app/core/dependencies.py`），挂在
  `app/api/portal/api.py`。
- 访客侧挂在 `app/api/v1/api.py` 的公开区（与 `chat.public_router`、`embed.public_router`
  同级，不经 `require_api_key` / `verify_v1_api_access`）。
- 前端分享页是新路由 `/share/:shareId`，`meta.public = true`，不套 Dashboard 布局。
  不复用 1.4 万行的 `EmbedChat.vue`：那里的流式状态机、工具面板、侧栏、输入框都要为只读
  裁剪，回归面远大于新写一个只读页。

## 数据模型

新表 `ai_conversation_shares`（MySQL 与 PostgreSQL 各一份迁移，两套不混用）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BigInteger PK | 内部主键 |
| `share_id` | String(32) unique | 对外链接 ID，`secrets.token_urlsafe(18)`，不可枚举 |
| `conversation_id` | String(50) index | 被分享会话 ID，仅追溯用，读取时不查原会话 |
| `owner_user_id` | String(64) index | 分享人 |
| `owner_username` | String(64) | 冗余展示名 |
| `agent_id` / `agent_name` | String(36) / String(128) | 分享时智能体标识与显示名快照 |
| `title` | String(500) | 首轮提问截断，列表与分享页标题 |
| `access_mode` | String(16) | `public` / `secret` |
| `secret_cipher` | String(512) null | 访问密码的 Fernet 密文；`public` 模式为空 |
| `content_json` | LONGTEXT（PG: TEXT） | 冻结的消息数组 `[{role, content, created_at}]` |
| `message_count` | Integer | 消息条数 |
| `turn_count` | Integer | 问答轮数 |
| `expires_at` | DateTime null | 空 = 永久；过期状态由时间计算，不落库 |
| `status` | String(16) | `active` / `revoked` |
| `revoked_at` | DateTime null | 作废时间 |
| `view_count` | Integer default 0 | 成功打开次数 |
| `last_viewed_at` | DateTime null | 最近一次成功打开 |
| `created_at` | DateTime index | 创建时间 |

索引：`uk_ai_conversation_shares_share_id(share_id)`、
`idx_ai_conversation_shares_owner(owner_user_id, created_at)`、
`idx_ai_conversation_shares_conversation(conversation_id)`。

建模沿用仓库既有惯例（参考 `app/models/agent_share.py`）：不建物理外键，删除用户由
管理侧清理；长文本用 `Text().with_variant(mysql.LONGTEXT(), "mysql")`。

**密码为什么存密文而不是哈希**：需求要求列表里能反复「复制密码」发给别人，哈希不可逆。
因此用 `app/utils/encryption.py` 同一套 `ENCRYPTION_KEY` 派生出独立 Fernet 实例加密存储，
既有所有权校验，也能回看。

## 后端接口

### 所有者侧：`/api/portal/chat-shares`

`POST /` 创建

```json
// 请求
{ "conversation_id": "conv_xxx", "access_mode": "secret",
  "secret": "a7k2m9x4",
  "expiry": { "mode": "preset", "preset": "7d" } }
// 或 { "expiry": { "mode": "custom", "value": 3, "unit": "day" } }
// 或 { "expiry": { "mode": "permanent" } }

// 响应
{ "share_id": "…", "url": "https://host/share/…", "access_mode": "secret",
  "secret": "a7k2m9x4", "expires_at": "2026-11-04T13:20:00", "turn_count": 3 }
```

- 会话归属校验：用 `build_history_query(user_id=当前用户, conversation_id=…)` 查；查不到
  返回 404，不区分「不是你的」和「不存在」。
- 快照装配后为空 → 400「该会话还没有可分享的内容」。
- 快照正文合并后超过 2 MB 或超过 500 轮 → 400「会话内容过长，暂不支持分享」。

`GET /?page=&page_size=&status=` 我分享的列表；每条含 `share_id / url / title / agent_name /
access_mode / secret（密钥模式回看的明文）/ status 推导值 / expires_at / created_at /
view_count / last_viewed_at`。状态推导：`revoked` → 已作废；`expires_at` 已过 → 已过期；
否则生效中。

`POST /{share_id}/revoke` 作废，幂等；非所有者返回 404。

### 访客侧：`/api/v1/share`

`GET /{share_id}` → `{ title, agent_name, access_mode, status, expires_at,
remaining_seconds, message_count, turn_count, created_at, owner_display }`，**不含消息内容**。
`access_mode` 即决定前端是否先弹密码卡片，不再单独给 `requires_secret` 这类重复字段。

`POST /{share_id}/unlock` `{ "secret": "…" }` → `{ token, expires_in }`。
token = Fernet 加密的 `{share_id, exp, v:1}`，有效期取 `min(12 小时, 剩余分享有效期)`。
密码错 → 401「访问密码不正确」；`public` 模式调用 → 400。

`GET /{share_id}/content?token=` → `{ title, agent_name, created_at, messages: [...] }`。
`public` 模式免 token。校验通过后用 `UPDATE … SET view_count = view_count + 1,
last_viewed_at = now()` 原子自增（不做读改写，避免并发丢计数）。

分享人自己打开免输密码：content 接口做**可选鉴权**——若请求带了有效登录凭据
（`X-API-Key` / `portal_session`，复用 `AuthService.verify_api_key`）且 `user_id` 等于
`owner_user_id`，跳过 token 校验；凭据无效时按匿名访客处理，**不因凭据错误拒绝请求**，
避免浏览器残留的过期 Cookie 把分享页变成 401。

失效策略：不存在 / 已作废 / 已过期统一 `404` +「链接不存在或已失效」，不暴露具体原因。

## 快照装配与内容清洗

1. 按 `conversation_id` + `user_id` 拉该会话全部轮次（按 `created_at` 升序）。
2. 每轮产出两条消息：`{"role": "user", "content": query}`、
   `{"role": "assistant", "content": summary}`；任一为空则跳过该条。
3. 正文清洗（只作用于快照副本，不动原数据）：
   - 去掉快捷按钮标记（与 `frontend/src/utils/quickButtons.ts` 对应的后端等价正则）；
   - 移除 `:::analysis …:::`（AI 分析推理过程）与 `<thought>…</thought>` 段：这两段渲染出来
     就是内部推理，属于「仅问答正文」的排除项；
   - 移除 `<sql_plan>…</sql_plan>`：里面是内部 SQL 与执行计划，分享页不渲染 SQL 卡片；
   - 把 `/api/v1/chat/generated-files/<id>?token=…` 形式的链接替换为
     `📎 <文件名>（文件未随分享提供）`，避免访客点开吃 401；
   - 不写入 `reasoning_content`、`process_timeline`、工具日志、附件、`trace_id`、`user_id`。
4. 标题取首条 user 内容前 120 字；`agent_name` 取历史行的 `agent_id` 反查智能体名，
   查不到时回落 `agent_id`。
5. 保留 `<chart>` / ```` ```echarts ```` / ```` ```mermaid ```` 标记原样入库——它们由
   `MessageRenderer` 在前端渲染，属于「正文」的一部分，后端不做解析。

## 安全

- `share_id` 由 `secrets.token_urlsafe(18)` 生成，24 字符，不含顺序信息。
- 密码：自动生成 8 位，字符集去掉易混字符（排除 `0O1lI`）；自定义长度 6–32；校验用
  `hmac.compare_digest`。
- 解锁限流：Redis `share:unlock:fail:{share_id}:{client_ip}`，10 分钟窗口内 5 次失败后
  返回 429「尝试次数过多，请稍后再试」。
- 分享链接域名走 `app/services/app_public_url_service.py` 的 `get_app_public_url()`（与下载
  链接、密码重置同一口径）；未配置时退回请求 `base_url`。
- 公开端点响应头加 `X-Robots-Tag: noindex, nofollow`；前端分享页 `meta` 同步
  `robots noindex`，防止被搜索引擎收录。
- 分享页不返回任何所有者身份细节，`owner_display` 只到显示名。
- 访客侧不提供下载、导出、继续对话入口；正文里的外链保持可点，但不追加平台的文件预览能力。

## 前端

### 侧栏

`ChatHistorySidebar.vue`：标题栏下加 tab「会话历史 / 我分享的」；搜索框与筛选面板只在
「会话历史」tab 内渲染。条目操作区在「导出」右侧加分享按钮。

新增 prop `enableShare`（默认 `false`）：只有 `EmbedChat.vue` 传 `true`；`AgentDebug.vue`
不显示该按钮，避免调试页出现点了没反应的入口。

「我分享的」渲染独立组件 `SharedLinksPanel.vue`（侧栏已 900 行，不再往里塞列表逻辑），
条目结构：

```
标题（首轮提问截断）
[公开/密钥] [生效中/已过期/已作废]  已打开 7 次 · 最近 10 分钟前
7 天后过期 · 10-28 13:20 创建
                        [复制链接] [复制密码] [作废]
```

作废用行内二次确认（复用现有删除确认浮层的样式）。已过期/已作废条目保留展示，只留
复制链接。

### 创建弹窗 `ShareConversationDialog.vue`

- 访问方式：两张卡片（公开链接 / 密钥访问），默认密钥。
- 密码：8 位自动生成，可编辑、可「换一个」。
- 有效期：胶囊按钮 1 小时 / 24 小时 / 7 天（默认）/ 30 天 / 永久 +「自定义」数字与单位。
- 会话正在流式输出时提示「只包含已完成的 N 轮」；该会话已有生效分享时提示条数。
- 创建成功后切到结果态：链接 + 复制、密码 + 复制、「打开预览」。

有效期换算、密码生成字符集、状态推导抽到 `frontend/src/utils/shareExpiry.ts` 纯函数，
供契约测试直接引用。

### 分享页 `SharedConversation.vue`（`/share/:shareId`）

- 加载顺序：先请求元信息 → 若 `access_mode = public` 或当前浏览器已持有 `sessionStorage`
  token，直接取内容；取内容返回 401 且是密钥模式，才渲染密码卡片。分享人本人带登录态
  访问时后端直接放行，前端不会看到密码卡片。
- 正文**复用** `MessageRenderer.vue`，不另写渲染器（两套 Markdown 实现必然漂移）。它能提供：
  Markdown + KaTeX + 代码高亮（`utils/markdown.ts`）、ECharts（`utils/chartRenderer.ts` +
  `vue-echarts`，含折线/柱状/饼/K线/表格视图切换与深色主题）、Mermaid（`MermaidRenderer.vue`）、
  表格移动端卡片视图。为此给组件新增 `readonly` 开关，只跳过四处**依赖登录态或宿主能力**
  的逻辑：`fs/preview` 路径重映射、产物下载按钮、`[ID:n]` 引用角标、`canvas_html`/`canvas_code`
  画布卡片降级为普通代码块。`hideQuickButtons` 沿用。
- 分享页用 `defineAsyncComponent` 懒加载 `MessageRenderer`（`vue-echarts` 约 300KB gzip），
  文字先出、图表后到，不阻塞首屏。
- 内容区最大宽度 860px。
- 顶部：平台品牌名与 Logo（取现有公开配置端点 `/api/portal/auth/config/public`，不硬编码）
  +「只读分享」徽标 + 会话标题 + 「由 `<owner_display>` 分享」 + 轮次与创建时间；底部固定
  「本页面是分享时刻的只读快照 · 由 NanZi 智能体平台生成」。
- 失效/过期/作废统一一个友好错误页。
- 移动端单列布局；分享页不加载侧栏、不依赖 `localStorage` 用户快照。

## 边界与错误处理

| 场景 | 行为 |
|---|---|
| 会话 0 条已完成消息 | 创建按钮禁用 + 说明文案 |
| 会话正在流式输出 | 允许分享，仅冻结已完成轮次，弹窗提示 |
| 同一会话多次分享 | 允许，各链接独立有效期与计数 |
| 原会话被删除 / 继续对话 / Redis 过期 | 分享内容不变 |
| 作废后访客已打开旧页面 | 刷新即失效，不做实时踢出 |
| 分享链接在第三方 iframe 内打开 | 不使用 Cookie 承载访问态（token 在 `sessionStorage`），避免第三方 Cookie 被拦 |
| 数据库时间与前端时区 | 沿用仓库 `datetime.now` 本地时间口径，接口返回 ISO 字符串由前端本地化 |

## 测试计划

后端 pytest：

- 创建：快照内容与轮次正确、公开/密钥两种模式、预设与自定义有效期换算、越权会话 404、
  空会话 400、超长会话 400。
- 访客：公开模式免 token 可取内容；密钥模式未解锁 401、密码错 401、正确换 token、
  token 过期 401、作废后 404、过期后 404。
- 计数：只有 content 成功才 `view_count + 1`，元信息与失败的 unlock 都不计数。
- 限流：同一 IP 连续 5 次错误密码后 429。
- 清洗：快照不含 `reasoning_content` / 工具日志，`generated-files` 链接已降级为文本。

迁移契约测试：`db-prod/V100-*.sql` 与 `db-prod-pg/V95-*.sql` 的表结构、索引、默认值一致
（沿用 `tests/test_*_migration_contract.py` 的写法）。

前端契约测试（`tests/frontend/*.mjs` + pytest 包装）：有效期换算（预设/自定义/永久 →
`expires_at`）、密码生成字符集与长度、状态推导（生效/过期/作废）；
`MessageRenderer` 的 `readonly` 降级（不追加产物下载按钮、不把本地路径重映射到
`/api/v1/chat/fs/preview`、不产出 citation 角标、长代码不再变成画布卡片）。

静态检查：`vue-tsc --noEmit`；同步更新 `tests/CHECKLIST.md`。

## 改动清单

后端新增：

- `app/models/conversation_share.py`
- `app/services/conversation_share_service.py`（快照装配、清洗、密码与 token、计数）
- `app/api/portal/endpoints/chat_shares.py`
- `app/api/v1/endpoints/share.py`
- `db-prod/V100-create-conversation-shares.sql`
- `db-prod-pg/V95-create-conversation-shares.sql`

后端修改：`app/api/portal/api.py`、`app/api/v1/api.py`（注册路由）。

前端新增：

- `frontend/src/api/chatShare.ts`
- `frontend/src/components/ShareConversationDialog.vue`
- `frontend/src/components/SharedLinksPanel.vue`
- `frontend/src/views/SharedConversation.vue`
- `frontend/src/utils/shareExpiry.ts`

前端修改：`ChatHistorySidebar.vue`（tab + 分享按钮 + `enableShare`）、`EmbedChat.vue`
（接线弹窗与分享面板）、`MessageRenderer.vue`（新增 `readonly` 开关）、`router/index.ts`
（`/share/:shareId` public 路由）。

## 不在本期范围

- 二维码分享、社交平台一键转发。
- 访问明细表（时间/IP 流水）、按访客去重统计。
- 修改已创建分享的有效期、重置密码、删除记录。
- 全平台分享总开关、`element:*` 权限码。
- 分享内容包含生成文件（附件需另做只读下载票据，涉及存储与鉴权边界，单独立项）。

## 风险

- **快照体积**：长会话（数百轮）会让 `content_json` 达到 MB 级，列表接口必须不返回该字段，
  content 接口按需返回整包；超过阈值在创建时拒绝。
- **密码可回看**依赖 `ENCRYPTION_KEY`；密钥轮换会导致历史分享密码无法解密。轮换时需保留旧
  密钥或接受这些分享失效，实现时在 service 里对解密失败降级为「密码不可用，请重建分享」。
- **分享内容外泄**：链接一旦转发即不可控，只能靠有效期与作废收敛；文案上明确提示"请只分享
  可对外内容"。
- **匿名端点被扫**：`share_id` 高熵 + 限流可挡住枚举，但公开链接本质是"知道即访问"，需在
  产品文案里说清。
