# sub_agent_call 调度白名单 · 设计

日期：2026-11-03（2026-11-04 修订：名单合并为一份，batch 不再单独配置）
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
- **名单只有一份**：事实源是 `sub_agent_call` 的配置项。`sub_agent_batch_call` 是同一个
  动作的并行形态，跟随 `sub_agent_call`，不再单独配置。
- 存量数据里只配在 `sub_agent_batch_call` 上的字段仍会被兜底读取（不丢配置），
  前端保存时清理掉，避免两份并存再次分叉。

## 工具开关：一个入口，两个工具

页面只显示 `sub_agent_call` 一张工具卡，勾选它 = 同时拥有串行与并行委派：

- 前端勾选/取消都成对处理（`toggleTool`），全选/取消全选同样成对；
- 提交前 `normalizeDelegationToolsForSubmit` 补齐，保存后 `tools` 里两个工具同时存在；
- 后端 `ensure_delegation_tool_pair` 在**保存落库**（`_normalize_tools_for_db`）和
  **运行时工具解析**（`ToolRegistry.get_tools` / `get_runtime_tools`）两处兜底补齐。

只存 `sub_agent_call` 而没有 `sub_agent_batch_call`，模型就看不到并行委派入口，
「并发汇总」场景直接不可用；反过来只存 batch 则串行委派消失。历史已发布版本不
需要重新发布——运行时解析会补齐。

能力表（提示词里的工具清单）同样成对补齐：只勾 `sub_agent_call` 的版本若不补
batch，「需要同时处理多个独立任务」那行提示永远不出现。

## 语义（两套不对称规则）

| 智能体类型 | 未配置（`None`） | 已配置（非空列表） | 已配置（空列表 `[]`） |
|---|---|---|---|
| 系统级 | 全部「有 `agent:<id>` 授权」的 | 名单 ∩ 授权 ∩ 就绪 | 空 |
| 用户自建 | **不可委派**（保持现状） | 名单 ∩ 本人创建 ∩ 启用 ∩ 就绪 | 空 |

- 白名单对系统级是**收窄**，对自建是**开通**（唯一开通方式）。
- `[]` 表示显式禁止全部委派；`None` 表示未配置。二者必须区分。

## 运行时解析

1. 从当前智能体的**生效版本** `tools` 里找 `sub_agent_call` 项（`ChatConfig.tools`）。
2. 两个委派工具共用这一份名单：`sub_agent_batch_call` 跟随 `sub_agent_call`；
   batch 上的历史字段仅作兜底。**不允许**出现「只配一个、另一个按未配置走全量」——
   那会让自建子专家在一条路径上消失，正是「团里配了成员却委派不了」的根因。
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

- 工具卡片只有 `sub_agent_call` 一张：齿轮旁「🎯 调度范围 · N 个」按钮。
- `sub_agent_batch_call` 不出卡片（`displayAvailableTools` 过滤），由 `sub_agent_call` 自动带上；
  「已选 X/Y」的分母与计数都按一个算，历史版本里两个都存了也不会显示成 2 个。
- 存量只存了 batch 或只存了 single 的版本：选中态按「已启用委派」显示，保存时补成对。
- 弹窗 `DelegationScopeModal`：搜索 / 只看已选 / 系统分组 / 我的分组（橙色提示默认不可委派）/ 失效项禁用 / 清空二次确认。
- 未授权的系统智能体显示但禁用（排障友好）。
- 保存时写 `sub_agent_call` 并清掉 batch 上的历史字段；计数与后端一样支持存量兜底。

## 不做

- 不通配符、不按分组/标签选择，只支持显式 ID 列表。
- 不为白名单破例做「立即生效」，仍随版本发布。
- 不自动清理失效项（保留配置以便回滚）。
