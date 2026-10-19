# 专家选择下沉到会话级 — 设计文档

- 日期：2026-10-19
- 状态：待实施
- 涉及模块：EmbedChat 前端、chat API、Redis 会话存储

## 1. 背景

当前输入框（及「专家中心」弹窗）里切换专家，写入的是**账号级**偏好：

- 前端 `switchToExpert` / `switchToAuto` → `saveRoutingPreference`（`frontend/src/views/EmbedChat.vue`）
- 后端 `PUT /api/portal/portal-prefs/routing`，写入 Redis 键 `agent:portal_prefs:{user_id}` 的 `routing_mode` + `expert_agent_id`

这只键是账号级、跨设备共享的（与 `history_sidebar_open` 同类）。因此：

- 同一账号的另一个窗口刷新后会“继承”别人刚切好的专家；
- 每个会话无法记住自己用过哪个专家；
- 多窗口同时使用时，最后一次切换会覆盖全局默认。

用户期望的是：**每个会话各自记住自己的专家，新建会话沿用账号默认**。

## 2. 目标

1. 切换专家时，把「路由模式 + 专家 ID」记录到该会话维度。
2. 切到一个历史会话时，输入框自动恢复该会话上次选择的专家。
3. 新建会话（尚无会话 ID 时）沿用账号级默认专家。
4. 改造前已存在、没有任何记录的旧会话，回落账号默认。
5. 保持 URL 锁定专家的嵌入链接语义不变。

## 3. 非目标

- 不做多标签页实时同步（仍是刷新后读到）。
- 不在会话列表/侧栏展示每个会话的专家名。
- 不改动账号级偏好本身（`routing_mode` / `expert_agent_id` 继续作为“新会话默认值”）。
- 不改 chat 主链路的配置解析与权限校验逻辑。
- 不新增数据库表或迁移。

## 4. 现状可复用点

| 需求 | 现成实现 |
| --- | --- |
| 会话级 Redis 存储模式 | `app/services/conversation_resource_service.py`：键 `conversation:{user_id}:{cid}:resource_scope_v1`，TTL 2592000 |
| 会话级 GET/PUT 端点先例 | `app/api/v1/endpoints/chat.py` 的 `/conversation/{cid}/resource-scope` |
| 用户身份提取 | 同文件 `_require_chat_user_id` |
| 专家可用性校验 | `AgentManagerService.resolve_embed_agent_access` + `get_active_agent_config`（见 `portal_prefs.py` 的 routing 更新逻辑） |
| 切会话唯一入口 | `frontend/src/views/EmbedChat.vue` 的 `conversationId.value = target` 分支 |
| 过期响应丢弃 | 项目既有 `historyAdoptionSequence` 递增序号模式 |

## 5. 设计

### 5.1 存储

新建 `app/services/conversation_routing_service.py`，类 `ConversationRoutingService`，结构对齐 `ConversationResourceService`：

```python
KEY_PREFIX = "conversation"
SUFFIX = "routing_v1"
TTL_SECONDS = 2592000

@classmethod
def _key(cls, user_id, conversation_id) -> str:
    return f"{cls.KEY_PREFIX}:{require_user_id(user_id)}:{conversation_id}:{cls.SUFFIX}"
```

值结构：

```json
{"routing_mode": "auto", "expert_agent_id": ""}
{"routing_mode": "expert", "expert_agent_id": "<agent uuid>"}
```

方法：

- `get(user_id, conversation_id) -> dict | None`
  - 无记录返回 `None`（**刻意区别于资源范围的返回空对象**：前端必须能区分“没有记录”与“记录为 auto”，前者回落账号默认）。
  - Redis 不可用或解码失败返回 `None`，不抛异常。
- `replace(user_id, conversation_id, routing_mode, expert_agent_id) -> dict`
  - 归一化：`auto` 模式强制 `expert_agent_id = ""`。
  - 写 Redis 时带 `ex=TTL_SECONDS`；异常静默吞掉并返回归一化结果（与资源范围一致）。

### 5.2 接口

在 `app/api/v1/endpoints/chat.py` 中，紧邻 `resource-scope` 新增两个端点，共用 `_require_chat_user_id`：

```
GET /api/v1/chat/conversation/{conversation_id}/routing
  → data: {"routing_mode": "...", "expert_agent_id": "..."} | null

PUT /api/v1/chat/conversation/{conversation_id}/routing
  body: {"routing_mode": "auto"|"expert", "expert_agent_id": "..."}
  → data: 归一化后的记录
```

PUT 校验（复用现有语义）：

- `routing_mode == "expert"` 且 `expert_agent_id` 为空 → 422。
- 智能体不存在 / 已停用 / 未发布 → 404。
- 无权使用该智能体 → 403。

不做额外的会话归属校验：键本身含 `user_id`，写入的是调用者自己的命名空间；与 `resource-scope` 的现状保持一致。

### 5.3 前端

新增两个函数（`frontend/src/views/EmbedChat.vue`）：

```ts
const loadConversationRouting = async (cid: string) => { ... }
const saveConversationRouting = async (cid: string, mode: "auto"|"expert", agentId: string) => { ... }
```

接入点：

1. **切会话**：唯一入口处 `conversationId.value = target` 之后调用 `loadConversationRouting(target)`。有记录 → 覆盖 `config.routingMode` / `config.expertAgentId`；返回 null 或请求失败 → 保持当前值（即账号默认）。
2. **切专家**：`switchToExpert` / `switchToAuto` 内，在原有账号偏好写入之外，当 `conversationId.value` 非空且未被 URL 锁定时调用 `saveConversationRouting`。
3. **初始化/恢复**：会话 ID 落地后（`requested` / `savedId` / `activeCid` 三条分支）走一次 `loadConversationRouting`。
4. **新会话固化**：新会话首次生成 `conversation_id` 后，立即用当前 routing 写一次，保证会话从第一天起有记录。

竞态处理：`loadConversationRouting` 内使用递增序号，响应返回时若序号已过期则丢弃，避免连续切换会话时旧响应覆盖新会话的专家。

不做 URL 锁定链接的读写：`isRoutingSettingsLocked` 为真时两个函数都直接返回。

### 5.4 数据流

```
用户切专家
  → config.routingMode/expertAgentId 立即生效（本地）
  → PUT /portal-prefs/routing（账号默认，保留）
  → PUT /chat/conversation/{cid}/routing（会话记录，新增）

用户切会话
  → conversationId = target
  → GET /chat/conversation/{target}/routing
      ├─ 有记录 → 覆盖 config
      └─ null / 失败 → 保持账号默认

用户新建会话（无 cid）
  → 只写账号默认
  → cid 生成后补写一次会话记录
```

## 6. 边界与错误处理

| 情况 | 行为 |
| --- | --- |
| Redis 不可用 | GET 返回 `null` → 回落账号默认；PUT 静默失败，前端本地切换仍生效 |
| 解码失败 / 脏数据 | 视为无记录，回落账号默认 |
| URL 锁定专家 | 不读不写，保持锁定语义 |
| 旧会话无记录 | 回落账号默认 |
| 无 cid 的新会话 | 只更新账号默认；cid 落地后补写 |
| 多标签页 | 不做实时推送，刷新后读到最新记录 |
| 会话删除 | 不主动清理，靠 30 天 TTL 自然过期 |
| 智能体随后被停用 | GET 仍返回旧记录；前端展示时若该专家已不在 `allowedAgents` 中，退回智能委派 |

## 7. 测试

后端（pytest）：

- `get` 无记录返回 `None`；写入后 `get` 能读回；`auto` 模式强制清空 `expert_agent_id`。
- Redis 不可用时 `get`/`replace` 不抛异常。
- PUT：expert 缺 agent → 422；未发布/不存在 → 404；无权 → 403。
- 写入后 TTL 为 2592000。

前端契约测试（`pytest --confcutdir=tests/frontend`）：

- 请求路径与字段名（`routing_mode` / `expert_agent_id`）。
- `EmbedChat.vue` 中切会话与切专家两条链路确实调用了对应函数。
- URL 锁定场景不发起请求。

## 8. 影响面与回滚

- 改动文件：新增 1 个服务文件、`chat.py` 增 2 个端点、`EmbedChat.vue` 增 2 个函数与 5 个调用点（切会话 1、切专家 2、初始化恢复 1、新会话固化 1）、新增测试。
- 无数据库变更，无迁移。
- 回滚：前端移除这些调用点即恢复账号级行为；Redis 中的 `routing_v1` 键会随 TTL 自动消失，无需清理。

## 9. 风险

- **前端写入可能导致“记录 ≠ 实际执行”**：若某个链路绕过 `switchToExpert/switchToAuto` 改了 `config.expertAgentId`（例如 URL 深链锁定），记录不会同步。已通过“锁定不写”规避主要场景，其余链路需在实施时逐一核对。
- **首次打开旧会话会看到账号默认而非历史实际专家**，属已知取舍（历史消息的 `agent_name` 可能已被委派改写，不能反推）。
