# MCP Client 使用统计设计

## 背景与目标

在外部 Client 卡片的“更多操作”菜单中增加“使用统计”。点击后打开统计弹框，分析指定 Client 在时间范围内的 Platform MCP 调用情况，包括每日趋势、方法分布、结果状态、认证类型、用户排行和资源关联排行。

统计只基于现有 `sys_mcp_inbound_audit_logs` 审计记录，不新增表、不改变调用链、不暴露 Token、Secret、请求头、IP 摘要或请求正文。

## 已确认的产品决策

- 默认统计周期为近 30 天，可切换近 7 天和近 90 天。
- 管理员查看指定 Client 的全部用户调用。
- 普通用户只查看自己实际发起的调用，即使该 Client 是全员共享 Client。
- 统计弹框独立于审计列表页，不改变审计页已有筛选条件。
- 所有图表同时展示文字数值、占比或空状态，不依赖 hover 才能理解。
- 窄屏下图表单列堆叠，弹框内容可滚动。

## 方案与边界

采用后端聚合接口加前端轻量 CSS/SVG 可视化的方案。后端负责权限过滤、时间桶补全、分组统计和延迟指标计算；前端只负责请求、加载状态和展示。避免在浏览器拉取原始审计记录，也不引入新的图表依赖。

聚合接口复用 `element:mcp_service:audit:read` 权限。服务端始终追加 `client_id` 条件；非管理员追加当前用户 `user_id` 条件。不存在、已删除或不可见 Client 的行为沿用现有 Client 读取边界，不通过统计接口泄露资源存在性。

## 后端数据契约

新增接口：

```text
GET /api/portal/mcp-service/clients/{client_id}/usage?range=7d|30d|90d
```

默认 `range=30d`。响应结构：

```json
{
  "range": "30d",
  "start_at": "2026-08-05T00:00:00",
  "end_at": "2026-09-03T23:59:59",
  "summary": {
    "total_calls": 0,
    "completed_calls": 0,
    "success_rate": 0,
    "failed_calls": 0,
    "denied_calls": 0,
    "average_latency_ms": null,
    "p95_latency_ms": null,
    "active_user_count": 0
  },
  "daily_trend": [
    { "date": "2026-08-05", "total": 0, "completed": 0, "failed": 0, "denied": 0 }
  ],
  "method_distribution": [
    { "name": "agent_invoke", "total": 0, "success_rate": 0 }
  ],
  "status_distribution": [{ "name": "completed", "total": 0 }],
  "auth_distribution": [{ "name": "user_delegated", "total": 0 }],
  "user_distribution": [{ "user_id": "user-1", "total": 0 }],
  "resource_distribution": [
    { "type": "agent", "name": "agent-1", "total": 0 }
  ]
}
```

实现要求：

- `daily_trend` 覆盖完整自然日范围，无调用日期返回 0；7/30/90 天均按日聚合。
- `method_distribution`、`status_distribution`、`auth_distribution`、`user_distribution` 均按调用次数降序返回，并保留并列项的稳定排序。
- 方法分布额外返回成功率；状态分布、认证分布和用户分布返回总次数。
- 资源分布按 `agent_id`、`conversation_id`、`dataset_id` 分成三类；同一条记录的多个非空资源字段分别计入对应维度；三个字段均为空的记录归入“其他”。
- `summary.completed_calls` 直接表示成功调用数，前端不得通过总量减失败/拒绝推断成功；未知结果状态仍保留在状态分布中。
- 平均耗时只统计 `latency_ms` 非空记录；P95 与现有审计汇总口径一致；没有延迟数据时返回 `null`。
- `active_user_count` 按可见记录中的非空 `user_id` 去重；普通用户因此最多为 1。
- 统计响应只返回上述聚合字段，不返回原始审计记录。
- 聚合查询使用 SQLAlchemy 可跨 MySQL/PostgreSQL 的表达方式；不新增或修改迁移文件。

## 前端交互与布局

修改 `frontend/src/views/McpServiceDesk.vue`：

1. 增加当前统计 Client、统计周期、统计数据、加载状态和错误状态。
2. 在每个 Client 的“更多操作”菜单中增加“使用统计”，置于“编辑 Scope”之前；点击时关闭菜单并打开弹框。
3. 弹框顶部显示 Client 名称、Client ID、权限口径提示和关闭按钮。
4. 周期选择提供“近 7 天”“近 30 天”“近 90 天”，默认近 30 天；切换周期只刷新弹框数据。
5. KPI 区显示调用总量、成功率、失败数、拒绝数、平均耗时、P95 耗时和活跃用户数。
6. 图表区显示每日调用趋势、方法调用分布、结果状态分布、认证类型分布、用户调用排行和资源维度排行。
7. 每个维度显示名称、次数和必要的占比；空数据显示“当前周期暂无调用数据”；加载时显示骨架或加载提示。
8. 请求失败时保留弹框，显示错误文案和“重新加载”按钮，不影响 Client 列表、Token 管理和审计页。
9. 使用现有 Tailwind/CSS 与内联 SVG/比例条完成图表；不增加 ECharts 等运行时依赖。

## 测试策略

### 后端

在现有 MCP Service Desk API 测试中增加使用统计契约，覆盖：

- 管理员按 Client 汇总全部用户调用；普通用户只能汇总自己的调用。
- Client 条件始终生效，不能被其他 Client 的记录污染。
- 7/30/90 天范围、自然日补零和空数据响应。
- 成功、失败、拒绝、方法、认证类型、用户和资源分组。
- 平均耗时、P95 和活跃用户数。
- 无审计读取权限、不可见 Client 和非法周期参数。

### 前端

在现有 `tests/frontend/test_frontend_mcp_service_desk_contract.py` 增加源码契约，覆盖：

- “更多操作”菜单存在“使用统计”入口并传入当前 Client。
- 新统计接口、默认 30 天和 7/30/90 天切换存在。
- KPI、每日趋势、方法/状态/认证/用户/资源维度和空/错状态存在。
- 弹框关闭、重新加载和窄屏可滚动布局存在。

完成后运行相关 pytest、`./node_modules/.bin/vue-tsc --noEmit`（工作目录为 `frontend`）和 `git diff --check`。不启动 `./dev.sh`，不执行真实数据库迁移或生产服务验收。

## 非目标

- 不改造全局审计页为通用报表系统。
- 不增加导出统计报表、实时刷新、定时推送或告警配置。
- 不修改 Client 权限、Token 生命周期、OAuth 授权流程和 MCP 调用鉴权逻辑。
- 不将用户排行扩展为用户姓名、邮箱等额外身份信息。
