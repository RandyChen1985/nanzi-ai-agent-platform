# sub_agent_call 调度白名单 · 设计

日期：2026-11-03
状态：已确认（用户批准方案 A + 四条语义）

## 目标

给「多智能体协同与调度」的两个工具（`sub_agent_call` / `sub_agent_batch_call`）增加一个可选的智能体名单：
配置后只允许调度名单内的智能体，未配置则维持现状。

## 数据模型

`app/schemas/agent.py` 的 `ToolConfigItem` 新增：

```python
delegable_agent_ids: Optional[List[str]] = None
```

- 存在智能体版本的 `tools` JSON 里，随发布版本生效（与 `metadata_dataset_ids` 同构）。
- 只对 `sub_agent_call` / `sub_agent_batch_call` 有意义，其它工具上设置会被忽略。

## 语义（两套不对称规则）

| 智能体类型 | 未配置（`None`） | 已配置（非空列表） | 已配置（空列表 `[]`） |
|---|---|---|---|
| 系统级 | 全部「有 `agent:<id>` 授权」的 | 名单 ∩ 授权 ∩ 就绪 | 空 |
| 用户自建 | **不可委派**（保持现状） | 名单 ∩ 本人创建 ∩ 启用 ∩ 就绪 | 空 |

- 白名单对系统级是**收窄**，对自建是**开通**（唯一开通方式）。
- `[]` 表示显式禁止全部委派；`None` 表示未配置。二者必须区分。

## 运行时解析

1. 从当前智能体的**生效版本** `tools` 里找 `sub_agent_call` 项（`ChatConfig.tools`）。
2. `sub_agent_batch_call` 自身没配时，回落到 `sub_agent_call` 的名单。
3. 三重过滤，顺序固定：**白名单 → 权限 → 就绪**。
   - 任意一步不满足即**静默排除**（不进通讯录、运行时不可达），不报错。
4. 主助手隐式挂载：只有 `tools` 里真实存在该项才认白名单；没有即按「未配置」处理。

## 三处消费方必须同源

`resolve_effective_delegable_agents()` 是唯一入口，以下三处都调用它：

1. `sub_agent_call` / `sub_agent_batch_call` 的运行时校验
2. `build_sub_agents_context()` 注入的「可委派子智能体清单」
3. `list_available_agents` 工具

否则会出现「通讯录列了 A → 模型调 A → 被白名单挡掉」的事故。

## 错误分类

替代现在统一的 `agent_not_found`：

| 原因 | error_code | 文案要点 |
|---|---|---|
| 不在白名单 | `agent_not_in_allowlist` | 未被加入本智能体的委派名单 |
| 在白名单但无授权 | `agent_permission_denied` | 当前用户无权使用该智能体 |
| 有权限但未就绪 | `agent_not_ready` | 缺少发布版本或资源绑定 |

## 前端

- 工具卡片（`sub_agent_call`）齿轮旁新增「🎯 调度范围 · N 个」按钮。
- 弹窗 `DelegationScopeModal`：搜索 / 只看已选 / 系统分组 / 我的分组（橙色提示默认不可委派）/ 失效项禁用 / 清空二次确认。
- 未授权的系统智能体显示但禁用（排障友好）。
- `sub_agent_batch_call` 显示「沿用 sub_agent_call」，可单独配置。

## 不做

- 不通配符、不按分组/标签选择，只支持显式 ID 列表。
- 不为白名单破例做「立即生效」，仍随版本发布。
- 不自动清理失效项（保留配置以便回滚）。
