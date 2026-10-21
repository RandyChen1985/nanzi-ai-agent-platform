# 个人 MCP 点对点分享设计

- 状态：已实现（待用户执行迁移 V181 / V82 后生效）
- 关联：`docs/superpowers/specs/2026-10-21-personal-skill-sharing-design.md`（技能分享，本方案的模板）
- 迁移：`db-prod/V181-create_mcp_server_shares.sql`、`db-prod-pg/V82-create_mcp_server_shares.sql`

## 1. 背景

个人技能分享上线后，用户提出「我的 MCP 也一样，支持分享，接受后复制到他的『我的 MCP』」。
个人 MCP 的痛点比技能更强的多：一条私有 MCP 的价值几乎全在**配置**里（SSE 地址、Authorization、
固定 Token、用户身份断言私钥），同事想复用只能靠截图 + 手抄，抄错一个字符就连不上。

## 2. 目标

1. 「我的 MCP」增加二级菜单：**我的 MCP / 收到的分享 / 我的分享**，交互与「我的技能」完全同构。
2. 个人 MCP 卡片上有「分享」入口，选人、可附言，一次最多 50 人。
3. 接收人「接受」后，在**自己的**「我的 MCP」中得到一份完整可用的副本（配置 + 工具清单）。
4. 默认携带认证凭证，但分享人可显式关闭；两种选择都必须在弹窗里说清楚后果。

## 3. 非目标

- 不分享平台 MCP（`scope=global`）：那是公共资产，没有「分享」语义。
- 不做公开链接 / 分享码。
- 不接受后自动同步：副本是独立快照，源服务改动不会回流（与技能一致）。
- 不做「分享给部门」：本期只支持指定人，部门维度留待后续。

## 4. 与技能分享的差异（本方案的关键决策）

| 维度 | 技能分享 | MCP 分享 | 原因 |
| --- | --- | --- | --- |
| 载体 | 技能目录整体打 zip 快照，落到共享存储 | 配置 + 工具清单序列化成 JSON，存 `payload` 列 | MCP 没有可执行目录，只有一行配置 + N 行工具缓存 |
| 敏感信息 | 无 | Authorization / 固定 Token / 断言私钥 | 不带凭证的 MCP 对接收人是**连不上的空壳** |
| 名称冲突 | 目录名加 `-2` | `server_name` 加 `-2` | `sys_mcp_servers.server_name` 是**全库唯一**（V108 大小写不敏感唯一索引），不解决就是 500 |
| 接受后的副作用 | 解压目录 + 写技能行 | 写服务行 + 复制工具缓存 + 保留 audience | 工具缓存不复制的话，对方要等一次「同步工具」才能用 |
| 终态清理 | 删共享目录 | `payload = NULL` | 只在 PENDING 期间需要快照 |

## 5. 数据模型

`mcp_server_shares`（无物理外键，与 `skill_shares` 保持一致：删用户由服务层清理，避免 `delete_user` 被外键挡住报 500）：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | VARCHAR(36) PK | uuid |
| `source_user_id` | INT | 分享人 |
| `source_server_id` | VARCHAR(36) | 源服务 id（源被删后仅作追溯） |
| `server_name` / `remark` / `sse_url` | VARCHAR | 冗余展示字段，列表页不必 join 源服务 |
| `payload` | LONGTEXT NULL | 快照 JSON；终态清空 |
| `include_credentials` | TINYINT(1) | 是否携带凭证，前端要显示「未携带凭证」标签 |
| `target_user_id` | INT | 接收人 |
| `status` | VARCHAR(20) | PENDING / ACCEPTED / REJECTED / WITHDRAWN |
| `message` | VARCHAR(200) | 附言 |
| `accepted_server_id` | VARCHAR(36) NULL | 接受后副本的 id（「去我的 MCP 看看」用） |
| `created_at` / `responded_at` | DATETIME | |

索引：`idx_mcp_shares_source(source_user_id, source_server_id, status)`、`idx_mcp_shares_target(target_user_id, status, created_at)`。

**凭证为什么不做外层再加密**：`auth_headers` / `fixed_token_encrypted` / `user_assertion_private_key_encrypted`
在 `sys_mcp_servers` 里本来就是 `enc:v1:` 密文，快照只是把它们原样搬进 JSON。再加一层只会让
「接受后写回」多一次解密-加密往返，而威胁模型没有变化（能读到表的人同样能读到两处密文）。

## 6. 快照内容

```json
{
  "version": 1,
  "include_credentials": true,
  "config": {
    "server_name": "...", "remark": "...", "sse_url": "...",
    "credential_mode": "static", "user_assertion_header": "X-Nanzi-User-Assertion",
    "enabled_status": 1,
    "auth_headers": "enc:v1:...", "fixed_token_encrypted": "enc:v1:...",
    "user_assertion_enabled": true, "user_assertion_audience": "mcp:<源服务id>",
    "user_assertion_key_id": "...", "user_assertion_issuer": "...",
    "user_assertion_private_key_encrypted": "enc:v1:..."
  },
  "tools": [{ "tool_name": "...", "tool_description": "...", "parameter_schema": "{}", "is_published": true, "is_available": true }]
}
```

`include_credentials=false` 时：

- `auth_headers` 经 `without_authorization_headers()` 去掉 `Authorization`（大小写不敏感），保留 `X-Tenant` 之类的非敏感头；
- `fixed_token_encrypted = null`；
- `user_assertion_enabled = false`，audience / key_id / issuer / 私钥全部置空——**不能只清私钥**，否则对方拿到一个「开着签名却没有钥匙」的坏配置，报错信息还指向断言逻辑，很难查。

`user_assertion_audience` 保持源服务的 `mcp:<源id>` 原值：目标是让副本**可用**，而 MCP 服务端校验
audience 时认的是「这个服务」，改成 `mcp:<副本id>` 会让所有断言请求被对方服务拒绝。

## 7. 状态机

```
PENDING ──accept──> ACCEPTED
   │ └──reject──> REJECTED
   └──withdraw（分享人）──> WITHDRAWN
```

- 同一（源服务, 接收人）重复分享：**刷新同一条 PENDING 行**（换最新快照、重发通知），不堆历史；
- 终态不可再操作，重复 accept 返回 409；
- 终态一律 `payload = NULL`；PENDING 行的 `payload` 是唯一数据源，接受时读完即清。

## 7.1 凭证边界（「不携带凭证」到底挡住了什么）

勾掉「携带认证凭证」时，快照会清掉：

| 字段 | 处理 |
| --- | --- |
| `auth_headers` | 剔除**全部疑似凭据头**（`Authorization`、`X-Api-Key`、`X-Auth-Token`、`Cookie`、`Signature` 等按关键字识别），保留 `Accept` / `Content-Type` / `X-Tenant-Id` 这类协议头 |
| `fixed_token_encrypted` | 置空 |
| `user_assertion_*` | 开关关闭，audience / key_id / issuer / 私钥全部置空 |

**挡不住的情况**：凭据直接编进地址时（ModelScope 这类托管网关把 key 放在路径里，
如 `.../e260f2cad7f443/mcp`），`sse_url` 是必传配置，取消勾选并不能隐藏它。
分享弹窗会检测路径中的长 hex 段与 query 里的 `token` / `key` / `sig` 参数并给出
醒目提示（只提示、不阻止），由分享人判断是否值得发给对方。

## 8. 接受即复制

1. **名字换成接收人的前缀**（`mcp-private-<接收人>-<业务别名>`）：直接用源名会让副本看起来像
   分享人的资产（admin 分享出去的还叫 `mcp-private-admin-*`），既误导接收人，也让「谁建的」这条
   命名约定失效。别名来自 `shared_name_suffix()`——优先按 payload 里记下的分享人账号精确切掉
   `mcp-private-<分享人>-`（账号可能含 `-`，正则只切到第一个 `-` 会切歪），没有前缀的裸名原样沿用；
2. 再交给 `resolve_accepted_server_name()`：`server_name` 全库唯一，冲突则 `-2` / `-3`…，
   按列宽（100）截断，避免「截断后撞上一个真实存在的名字」；
3. 新建 `McpServer`：新 uuid、`scope='personal'`、`user_id=接收人`，其余字段全部来自快照；
4. 复制 `McpToolCache`：保留 `is_published` / `is_available`，让对方**刷新即用**，不必先手动同步；
5. 回写分享行：`status=ACCEPTED`、`accepted_server_id`、`responded_at`、`payload=NULL`；
6. 通知分享人「已被接受」。

## 8.1 安装向导（自定义安装）

一键「接受并安装」按快照原样落地，适合「别人给什么就用什么」。但接收人常有自己
的诉求：名字想换个后缀、备注想写清用途、分享方地址要走内网网关。因此卡片上并列
两个入口：

| 入口 | 行为 |
| --- | --- |
| 接受并安装 | `POST .../accept` 不带 body，全部沿用快照 |
| 自定义安装 | 打开向导弹层，可改服务名后缀 / 备注 / SSE 地址，其余仍沿用快照 |

**可改的只有三项**（有意收敛）：

- 服务名前缀固定为 `mcp-private-<我的账号>-`，只让改后缀，避免用户把命名约定改坏；
- 备注视作可清空（传空串即置空）；
- SSE 地址必须 `http(s)://` 开头，前端与后端各校验一次。

**凭证不在可改范围**：分享决定「带不带凭证」，安装时不提供明文查看或替换入口；
若快照未携带凭证，向导只提示「安装后需到我的 MCP 补 Token」。

**名字冲突策略分两种**，这是与「自动加 `-2`」的关键区别：

- 未指定名字（一键安装 / 向导未改后缀）→ 默认名冲突自动 `-2` / `-3`；
- 用户显式输入名字 → 冲突 **409 直接报错**，让用户自己改，绝不悄悄加后缀。

后端接口：`accept_share(..., server_name=None, remark=None, sse_url=None)`，端点
`McpShareAcceptRequest` 三个可选字段；未传（`None`）表示沿用快照，传空串表示清空。
收到列表额外返回 `suggested_server_name`（已按接收人账号算好的默认落地名），
供向导预填。

## 9. 后端接口

全部挂在 `app/api/portal/endpoints/mcp.py`，`require_api_key` + 属主校验（只允许操作 `scope=personal`
且 `user_id=自己` 的服务）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/portal/mcp/share-targets?keyword=` | 复用 `skill_share_service.search_share_targets`（纯用户检索） |
| POST | `/api/portal/mcp/servers/{id}/shares` | body `{target_user_ids, message, include_credentials}` |
| GET | `/api/portal/mcp/shares/summary` | 三个角标：`received_pending` / `sent_pending` / `sent_total` |
| GET | `/api/portal/mcp/shares/sent?status=` | 我发出的，附 `status_counts` |
| GET | `/api/portal/mcp/shares/received?status=` | 我收到的，默认 `PENDING` |
| POST | `/api/portal/mcp/shares/received/{id}/accept` | 复制安装 |
| POST | `/api/portal/mcp/shares/received/{id}/reject` | 拒绝 |
| DELETE | `/api/portal/mcp/shares/sent/{id}` | 撤回 |

`GET /api/portal/mcp/servers?scope=personal` 的响应补充两个字段：

- `shared_from`：这条服务是从谁的分享安装来的（`share_id` / 来源人姓名 / 时间）；
- `share_count`：我把它分享出去过几次（PENDING + ACCEPTED）。

域异常映射：`McpShareNotFoundError → 404`、`McpSharePermissionError → 403`、`McpShareConflictError → 409`。

### 9.1 服务属主的调用概览

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/portal/mcp/outbound-summary/mine?range=24h\|7d\|30d` | 登录用户 | 只聚合本人 `scope=personal` 服务的调用日志 |
| GET | `/api/portal/mcp/outbound-logs` | 仅 admin | 全平台审计明细 |
| GET | `/api/portal/mcp/servers/{id}/outbound-logs` | 属主或 admin | 单服务调用明细 |

概览返回 `summary`（次数 / 成功率 / 失败 / 平均耗时）、`servers`（Top 5 服务）、
`tools`（Top 5 工具）、`daily`（每日调用量）。**安全边界**：日志表本身没有归属列，
必须先取「本人 `scope=personal` 的 server_id 白名单」再 `server_id IN (...)` 聚合，
否则一次疏忽就变成全平台数据。前端把摘要放在「我的 MCP」列表上方（`McpCallSummary.vue`），
不额外开菜单项。

## 10. 前端

- `McpManagement.vue`：`activeScope === 'personal'` 时渲染二级菜单 + 三块内容；顶部
  「平台 MCP / 我的 MCP / 审计日志」保持不变；接受分享后 `focusServer(id)` 直接选中新装的副本。
- `McpPersonalMenu.vue`：宽屏左栏（`hidden md:block`，200px）、窄屏横向胶囊（`md:hidden`，可横滑）。
- `McpShareDialog.vue`：搜索 → 多选 chips → **「携带认证凭证」开关**（默认开，开时说明会交出
  Authorization / Token / 私钥，关时说明对方需自行补 Token）→ 附言 200 字。
- `ReceivedMcpSharesPanel.vue`：待处理 / 全部；卡片显示服务名、来源人、附言、「未携带凭证」标签、
  「接受并安装到我的 MCP」/「拒绝」；已接受显示「去我的 MCP 看看」。
- `SentMcpSharesPanel.vue`：按状态筛选 + 计数、撤回（仅 PENDING）。
- `McpServerRegistry.vue`：个人卡片增加分享按钮（`emit('share-server')`）、显示「来自 xx 的分享」
  与「已分享 N 次」。
- `PersonalCenter.vue`：`?tab=mcp&mcp_tab=received|sent` 深链直落对应二级页。
- `PortalNotificationBell.vue`：`mcp_share` 类通知跳 `/dashboard/personal?tab=mcp&mcp_tab=…`。

窄屏注意：四个顶部 tab 之前会在 `sm` 以下挤成竖排单字，本轮给 tab 加 `whitespace-nowrap` / 横滑，
二级菜单同样改为胶囊横滑——两级导航都不能靠换行排版。

## 11. 通知

| 场景 | category | 接收人 | metadata |
| --- | --- | --- | --- |
| 收到分享 | `mcp_share` | 接收人 | `{share_id, server_id, open: 'mcp_share', target_tab: 'received'}` |
| 分享被接受 | `mcp_share_result` | 分享人 | `{share_id, server_id, accepted_server_id, open, target_tab: 'sent'}` |

## 12. 边界与异常

| 场景 | 行为 |
| --- | --- |
| 分享平台 MCP | 403「只能分享『我的 MCP』中的个人服务」 |
| 分享别人的个人 MCP | 403 |
| 分享给自己 | 409 |
| 接收人不存在 / 已停用（`status != 1`） | 409「以下接收人不存在或已停用：…」，口径同技能分享 |
| 接收人 > 50 / 附言 > 200 | 409 |
| payload 丢失或 JSON 损坏 | 409「分享内容已损坏，请让分享人重新分享」 |
| 同名服务过多（>98 次） | 409 |
| 删除用户 | `delete_user` 删除两个方向的分享行（`payload` 只是 DB 字段，无需回收磁盘） |

## 13. 测试

- `tests/services/test_mcp_share_service.py`：凭证开关（携带 / 剥离 Authorization + 关断言）、工具快照、
  名字冲突 `-2`、接受复制（独立行 + 工具缓存 + payload 清空）、拒绝/撤回权限、发送前置校验、分页上限。
- `tests/api/portal/test_mcp_shares_endpoint.py`：8 条路由注册、`delete_user` 清理、通知 category。
- `tests/test_mcp_shares_migration_contract.py`：两套迁移的列/索引/无外键。
- `tests/frontend/test_mcp_share_contract.py`：菜单、弹窗（含凭证警告）、两个面板、深链、通知、暗黑模式。

## 14. 上线步骤

1. 执行 `db-prod/V181-create_mcp_server_shares.sql`（MySQL）或 `db-prod-pg/V82-create_mcp_server_shares.sql`（PG）；
2. 重启后端、重建前端（用户自行 `./dev.sh`）；
3. 验证：A 分享个人 MCP 给 B → B 的「收到的分享」出现角标 → 接受 → B 的「我的 MCP」出现副本（同名时带 `-2`）→ 工具可直接调用。

## 15. 风险与后续

- **凭证外泄风险**：默认携带是产品选择（否则功能不可用），已用弹窗强提示 + 可关闭缓解；后续可考虑
  「分享时二次确认」或「凭证有效期」。
- **源服务改名不回流**：快照语义决定副本不会跟随源改动，后续如需「跟随更新」要另做订阅机制。
- **部门维度分享**：`search_share_targets` 已返回部门信息，扩展成本低，等用户提出再做。
