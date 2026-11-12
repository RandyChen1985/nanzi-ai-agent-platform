# 智能体中心：业务分组页签设计说明

日期：2026-11-12
原型：[`prototypes/2026-11-12-agent-group-tabs.html`](../../prototypes/2026-11-12-agent-group-tabs.html)
关联：工具注册表 / 元数据管理 / 技能工作台的同类能力，见
[`2026-11-12-tool-group-tabs-design.md`](./2026-11-12-tool-group-tabs-design.md)、
[`2026-11-12-metadata-dataset-group-design.md`](./2026-11-12-metadata-dataset-group-design.md)、
[`2026-11-12-skill-group-design.md`](./2026-11-12-skill-group-design.md)

## 一、需求与结论

用户诉求：「智能体中心，全部智能体、我的智能体，也加一下分组的功能呢，万一智能体很多可不好找啊」。
原型确认后用户回复「按你建议来，新增吧」，即：

1. **新增单值 `ai_agents.group_name`**（不复用表里已有的三个「像分组」的字段）；
2. 页签**只做「全部智能体 / 我的智能体」**两个浏览视图；
3. 页签数字**算上除分组之外的所有条件**；
4. 本期**单条**设置（编辑弹窗 + 卡片菜单），批量留后续。

## 二、为什么不能复用已有的三个字段

表里本来就有三个长得像分组的字段，但语义都不是这个：

| 字段 | 真实语义 | 谁在读 | 结论 |
| --- | --- | --- | --- |
| `owner_group` VARCHAR(50) | V5 建表注释「所属组 (预留)」/ PG 注释「**所属用户组**」——组织架构上的用户组 | 分享服务、系统级发布服务都在元数据里传递它；有索引 `idx_owner_created(owner_group, created_at)` | ✗ 这是权限/组织维度。拿它当业务分组，以后「按用户组授权智能体」就无路可走 |
| `capabilities` JSON | 模型注释原文「**Tags for routing** e.g. ["data","coding"]」，多值 | 路由与调度时读，影响「这条请求派给谁」 | ✗ 多值 → 一个智能体出现在多个页签、各页签计数相加大于总数；还会把「导航口径」与「路由口径」绑死 |
| `agent_type` | 枚举式机制类型（GENERAL / CHATBI / KNOWLEDGE_BASE） | 筛选、卡片文字、部分运行时分支 | ✗ 这是机制类型不是业务域，且已是独立筛选维度 |

所以新增一列，与 `meta_datasets.group_name`、`tools.group_name` 同构：

- `group_name` = 这个智能体属于哪个业务域（单值）→ 页面导航 / 筛选
- `capabilities` = 它能接哪类请求（多值）→ AI 路由

## 三、数据与接口

### 迁移（本次需要，与技能分组不同）

- MySQL：`db-prod/V214-add-agent-group-name.sql`
- PostgreSQL：`db-prod-pg/V114-add-agent-group-name.sql`

`VARCHAR(64) NULL`，无默认值。存量行落 `NULL`，前端按「未分组」统计，**不需要回填**，
也不影响任何既有查询与运行期判定。两侧文件里都写了为什么不复用那三个字段，
否则下一个人还会再想一遍。

> 与技能分组的差别：技能是**文件**（分组必须写进 `SKILL.md`，否则复制/分享会丢），
> 智能体是**数据库实体**，所以这里就老实加一列。

### 归一口径

抽到 `app/utils/group_name.py`（`normalize_group_name` / `GROUP_NAME_MAX_LEN = 64`），
工具、数据集、智能体、技能四处共用同一个答案：去首尾空白、空白即未分组、换行折成空格、
超长截断到 64。`app/schemas/tool.py` 改为从这里导入，老写法
`from app.schemas.tool import normalize_group_name` 继续可用。

### 接口

| 端点 | 说明 |
| --- | --- |
| `PUT /api/portal/agents/{agent_id}/group` | **只改 group_name 一列**，权限 `element:agent:edit`；空白/null 表示清空 |

**为什么不复用 `PUT /{agent_id}`**：那条路为「元数据整表单保存」服务，为了安全加了一串
与分组无关的前置校验——待审冻结（`assert_not_frozen`）、「是否系统级不能改」、「引擎创建后
不可修改」。用户只是想把智能体归个类，不该因为「正在审核中」被拒；整表单 PUT 还要求前端
回传全部字段，少传一个就是覆盖。

**不检查「待审冻结」**：分组是纯导航字段，不改变审核方要评的内容（提示词、工具、模型）；
管理员审核期间所有者整理自己的分组是合理需求。

### 整表单保存时的行为

`AIAgentBase` 也加了 `group_name`（编辑弹窗要用），但服务层按
`if "group_name" in data.model_fields_set` 落库——与 `team_profile` 同一套「局部更新」逻辑。
无条件赋值会让版本配置 / 专家团 / 排版样式这些**不携带分组**的表单把已设好的分组悄悄清空。

### 复制

`duplicate_agent` 带上 `group_name=source.group_name`：复制通常是「照着这个再做一个同类」，
落到未分组反而要再归一次。

## 四、前端

文件：[`AgentManagement.vue`](../../frontend/src/views/AgentManagement.vue)，
复用 `utils/toolGroupTabs.ts` + `components/system/ToolGroupTabs.vue`。

### 关键改动：过滤拆成两段

这一页有十来个筛选条件（状态 / 类型 / 来源 / 引擎 / 就绪度 / 权属人 / 团队三项 + 搜索）。
为了让「页签数字 = 点进去看到的条数」，原 `filteredAgents` 拆成：

| computed | 内容 |
| --- | --- |
| `agentsMatchingFilters` | 当前视图 + 关键词 + **全部筛选**，不含分组 |
| `filteredAgents` | 上一段结果再套分组，最后排序 |

页签数据来自前者（`computeGroupTabs(agentsMatchingFilters.value.map(toGroupableAgent), {}, …)`，
`filters` 传空）。如果反过来只把关键词交给纯函数，用户勾了「只看已启用」就会看到
「页签说 5、点进去 3」。

> 与技能页的口径差别：技能页没有其它筛选，两种写法等价；这一页必须按上面的做。
> 原则是一致的——**页签数字 = 点进去的条数**。

### 其它行为

- 页签只在 `workspaceTab === 'mine' | 'all'` 出现；
  「分享给我 / 我分享的 / 专家团」是待办与协作队列，不做分组；
- **可收起**：一级页签行右端一个「隐藏分组 / 显示分组」开关（`ml-auto` 靠右），
  选择存 `localStorage('agent-management:group-tabs-visible')`。分组页签是浏览辅助，
  整理型用户不需要它常占一行；收起后按钮仍在原位，不会「藏起来就找不回」；
- **没有分组时不出现**：`items.length > 2`（即除「全部」「未分组」外至少还有一个真实分组）
  才连行带开关一起渲染——一个分组都还没建时先教用户点一个没用的开关是负收益；
- 页签集合随当前视图变化：切到「我的智能体」只剩自己名下有的分组；
- 切视图、分组被筛空或改名后回「全部」，但首屏加载期跳过（否则刚点的分组会被清掉）；
- 卡片徽标放在**描述下方独立一行**（名称右侧已有 👥 专家团 / 子专家 / 🛡 三个徽标，再挤会溢出）；
  锁定某组时不再逐条显示；未分组显示虚线徽标；点徽标 = 只看该组；
- 两个设置入口：编辑弹窗「基本信息」tab（描述下方）+ 卡片「⋯ → 设置分组」小弹窗，
  共用同一个 `<datalist id="agent-group-options">`；
- 弹窗 Teleport 到 body（卡片 hover 带 `translate-y`，否则 fixed 遮罩只盖住卡片）。

## 五、边界与不做的事

- 分组**不参与权限**、**不进 AI 提示词**、**不影响路由**；`capabilities` 与 `owner_group`
  的职责完全不变；
- 分享视图、专家团视图不做分组；
- **批量设置分组**本次不做（卡片已有批量模式，将来要加就在那里多一个「设置分组」）；
- 列表（表格）视图不加分组列，分组通过页签筛选；表格行仍只有卡片菜单里的「设置分组」。

## 六、测试

`tests/frontend/test_agent_group_tabs_contract.py`（25 条）：

- **真跑探针** `tests/frontend/agent_group_tabs_probe.cjs`：用真实 `toolGroupTabs.ts` 验证
  页签顺序与计数、**筛完之后页签数字等于逐组条数**、`filters` 传空时禁用项也计入
  （与工具页的区别）、「我的智能体」只剩自己的分组、搜索把某组筛空后该页签消失、
  空白/null/缺失都归未分组；
- **源码契约**：复用共享模块、分组过滤必须在下游（`agentsMatchingFilters` 里不许出现
  `agentGroupFilter`）、映射只一处、页签只在浏览视图、切视图重置且跳过首屏、锁定时不显示
  徽标、两个入口共用 datalist、改分组走专用端点、弹窗在 Teleport 内；
- **后端契约**：两侧迁移文件（含「为什么不用 owner_group / capabilities」的说明）、模型列、
  专用请求体、整表单更新的 `model_fields_set` 局部更新、复制保留分组、
  `set_agent_group` 只改一列、归一口径四处共用。

既有契约更新：`test_agent_share_contract.test_admin_has_all_agents_tab` 里「归属过滤在
`filteredAgents` 里」改为指向 `agentsMatchingFilters`（过滤位置随拆分下移，行为未变）。

## 七、落地步骤

1. **先跑迁移**：MySQL `db-prod/V214-add-agent-group-name.sql` + PG `db-prod-pg/V114-...`；
   —— 代码先于迁移上线会让智能体列表 1054（这一列已映射进 ORM），**顺序不能反**；
2. 重启后端（`./dev.sh`，由用户在控制台执行）；
3. 给几个智能体设置分组（卡片「⋯ → 设置分组」或编辑弹窗），确认页签出现、数字正确；
4. 勾一个筛选看页签数字是否跟着变（这是本次最容易做错的口径）；
5. 切到「我的智能体」，确认页签只剩自己名下有的分组。
