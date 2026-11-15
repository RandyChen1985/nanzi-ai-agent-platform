# 管理员站内信群发（2026-11-14）

## 背景

站内信原本只有两条产生路径：

- **业务事件自动发**：任务结果/告警、技能分享与发布、资源访问审批、智能体系统通知，收件人由事件决定；
- **智能体工具 `send_portal_notification`**：入参只有 `title / content / level`，收件人恒等于当前对话的登录用户（`agent_ctx.user_id` 来自 API Key 校验结果），**结构上不可能发给别人**。

平台缺少「管理员主动给一批人发通知」的能力：没有群发接口、没有后台页面，`portal_notifications` 的写入口分散在各业务服务里。

## 目标

管理员在「系统配置与诊断 → 系统诊断」二级栏里，按 **指定用户 / 部门（含下级）/ 角色** 选定范围，发一条站内信给命中的启用账号。

## 非目标（明确不做）

| 不做的 | 原因 |
|---|---|
| 定时发送 / 草稿 / 撤回 | 广播动作本身低频，先保证「发对人、发对人数的预期一致」 |
| 同步投递到钉钉 / 邮件等外部渠道 | 外部渠道属于个人配置，群发公告走平台内更可控 |
| 给智能体新增「管理员群发工具」 | 模型触发高危广播，误发不可撤回；入口只留人工操作的管理页 |
| 新权限码 | 需求方明确「写死仅 admin 角色」，先用 `require_admin` |

> 发送记录表是**第二轮需求**加进来的（用户要「历史发送列表 + 一键重发」），最初版本按
> 「只用请求日志」实现，后来因为请求日志正文会截断、无法按群发聚合，改为落
> `portal_broadcast_records`，详见下方「历史发送记录与重发」。

## 架构

```
前端 SystemConfig.vue（diagSection 增加 'broadcast'，仅 admin 可见）
  └─ SystemBroadcastPanel.vue
       ├─ POST /api/portal/broadcast/preview   算命中人数 + 抽样名单（不落库）
       └─ POST /api/portal/broadcast/send      解析 → 批量写 portal_notifications
后端
  app/api/portal/endpoints/broadcast.py       两个端点，均 require_admin
  app/services/portal_broadcast_service.py    范围解析 + 批量插入
  app/schemas/portal_broadcast.py             入参清洗与长度上限
复用（未改动）
  DepartmentService.subtree_like_prefix       部门子树前缀匹配
  portal_notifications 表与铃铛/消息中心前端    无需迁移与新增页面
```

### 收件人解析

三个维度取**并集**后去重（勾「研发部 + 值班经理角色」= 两拨人都要收到），再统一收口：

1. 只保留 `users.status = 1`（停用/待审核账号不占命中人数，也不落库）；
2. 部门按物化路径 `path LIKE '/1/7/%'` 取整棵子树（`subtree_like_prefix` 保证不误匹配 `/1/70/`）；
3. 结果按 `id` 升序，保证预览与发送看到的是同一份名单。

### 防误发的两条闸门

1. **`confirm_total`**：发送请求必须带上预览时的命中人数；服务端重新解析后不一致直接 409，前端提示重新预览。预览和发送之间可能有人入职/离职/调部门，静默按新人数发出去是最危险的处理。
2. **前端预览失效**：范围或正文任何改动都会清空 `preview`，没重新预览就不允许点发送。

### 落库

- `category = "system_broadcast"`，铃铛里显示「公告」标签；
- `metadata = {source: "admin_broadcast", sender_*, audience:{...}}`：用户侧详情能标出发件人，也能反查这是哪次群发（请求日志正文会被截断到 10KB，不能当唯一凭据）；
- `read_at = NULL`（未读红点）；
- 分片 `executemany`（每 200 行），比逐条 `create` 快且不会撑爆 `max_allowed_packet`。

## 历史发送记录与重发（第二轮需求）

### 为什么从「只用请求日志」改成建表

请求日志（`ai_agent_access_logs`）能满足「事后知道有人发过」，但满足不了管理页要的两件事：

1. `request_params` 按 10KB 截断，长正文看不到全文，回填重发更不可能；
2. 日志按 endpoint 聚合，没有「哪一次群发」的身份，无法分页列出「发了哪些、发给了谁的范围、命中多少人」。

于是新增 `portal_broadcast_records`（MySQL `V218` / PostgreSQL `V118`）。

### 表结构取舍

| 决定 | 理由 |
|---|---|
| **只存范围，不存最终收件人名单** | 名单可由范围重新解析；存快照会在人员变动后与事实不符。重发时重新预览、把人数差异摆给操作者看，比「照旧名单再发一次」安全 |
| `sender_*` 三列存快照、不建外键 | 发送者账号可能停用/删除，历史记录仍须可读 |
| 正文存 `TEXT` 原文 | 回填重发要用原文，不能是预览 |
| 不做保留期清理 | 每次群发只有一行，体量远小于 `portal_notifications`（后者 30 天清理）；保留全量便于回溯 |
| 消息 `metadata` 带 `broadcast_record_id` | 从任意一条站内信能反查它是哪次群发发出的 |

记录与站内信写在**同一个事务**里：要么「有记录且发出去了」，要么都没有。失败时显式 `rollback`，避免失败事务挂在 session 上污染后续请求。

### 重发的语义：回填 + 自动预览，不直接发送

点「重发」后：

1. `GET /api/portal/broadcast/records/{id}` 取原文与范围；
2. 回填标题、正文、级别、用户/部门/角色三个范围维度，并展开收件人区域；
3. 立刻自动跑一次 `preview`；
4. 预览结果旁显示「历史命中 128 人 → 现在 130 人（人员有变动，请确认后再发）」；
5. 仍需点「发送站内信」并在二次确认里确认。

这样做的好处：**唯一的发送入口始终带 `confirm_total` 校验**，不存在绕过防护的「直接重发」通道。

实现细节：回填赋值会触发 `watch` 清空预览，因此用 `suppressPreviewReset` 标志位在回填期间抑制重置，`await nextTick()` 后恢复并手动预览。

### UI 变化

- **收件人区域可折叠**：折叠后只留一行范围摘要（`指定 N 人 + M 个部门 + K 个角色`），把版面让给正文与历史列表；
- **历史发送卡片**：时间、标题、范围摘要、命中人数、发送人，操作「查看」「重发」，10 条/页；
- **记录详情弹窗**：全文 + 范围 + 人数，弹窗内也可一键回填重发。

## 实现陷阱记录

**`meta_info` vs `metadata`**：模型里是 `meta_info = Column("metadata", JSON)`，列名是 `metadata`，但批量插入的参数键要用属性名 `meta_info`。写成 `metadata` 会抛 `CompileError`（未消费的列名）。因此行构造被抽成 `_build_rows()`，测试用 SQLite 内存库真跑一次插入来兜住它——假 session 发现不了这类编译期错误。

## 风险与运维

- 站内信按平台策略**保留 30 天**后由每日维护任务清理，群发公告同样会被清掉（页面已提示）；
- 一次群发上万账号时会在单个事务里写入，需留意 `innodb` 事务大小与前端请求超时；当前按 200 行分片，实测量级足够；
- 审计只依赖请求日志中间件（`ai_agent_access_logs`），正文超过 10KB 会被截断。

## 测试

| 文件 | 覆盖 |
|---|---|
| `tests/services/test_portal_broadcast_service.py` | 范围并集去重、非法 ID 清洗、部门子树 SQL、停用账号过滤、`confirm_total` 不一致拒绝、真实表可插入、端点必须带 `require_admin` |
| `tests/frontend/test_system_broadcast_contract.py` | 入口仅 admin、发送带 `confirm_total`、范围变更作废预览、409 处理、铃铛「公告」标签、收件人可折叠、重发走回填+预览、回填期间抑制预览重置 |
| 服务层（记录部分） | 摘要压平/截断、详情保留全文、空表短路、`portal_broadcast_records` 真实表可插入、`send` 同事务写记录并回填 `broadcast_record_id` |

