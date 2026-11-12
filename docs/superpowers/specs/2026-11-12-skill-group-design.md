# 技能工作台：业务分组页签与筛选设计说明

日期：2026-11-12
原型：[`prototypes/2026-11-12-skill-group-tabs.html`](../../prototypes/2026-11-12-skill-group-tabs.html)
关联：工具注册表 / 元数据管理两处同类能力见
[`2026-11-12-tool-group-tabs-design.md`](./2026-11-12-tool-group-tabs-design.md)、
[`2026-11-12-metadata-dataset-group-design.md`](./2026-11-12-metadata-dataset-group-design.md)

## 一、需求与结论

用户诉求：「平台技能、我的技能这块，怎么也加这个 tab 分组的功能，不然太多了看不过来。」
原型确认后用户回复「那你建议来吧，开搞」，即按原型里的建议方案落地：

1. 分组**写进 `SKILL.md` 的 frontmatter**（不建表）；
2. 页签只放**右侧内容区**（与「我的技能」的左侧二级菜单并列，不叠加第三层）；
3. 分组名**可选已有 + 允许新建**；
4. 本次只做**单条**设置，批量后续再说。

## 二、为什么分组存文件而不是建表

技能和工具、数据集**不是一类东西**：技能没有主表，列表由扫物理目录 + 解析 `SKILL.md`
得出（平台在 `data/skills/<id>/`，个人在 `agent_workspaces/<user>/skills/<id>/`）。

| | 甲：写进 SKILL.md frontmatter（**采用**） | 乙：新建 `skill_groups` 表 |
| --- | --- | --- |
| 迁移 | **零**（解析器是通用键值，新增 `group:` 立即可读） | 每个库一份，且要分 scope |
| 复制 / 分享 / 发布技能 | 分组**跟着文件走** | 不跟，必须额外做同步 |
| 直接改文件、导入 Zip/Tar | 一致 | **DB 与文件双源必然漂移** |
| 改分组的成本 | 改文件一行（已有 `enabled` 的现成套路） | 改库 |
| 分组改名 / 合并 / 排序 | 要扫目录 | 天然好做 |
| 分组承担权限 | 不合适 | 合适（唯一选乙的理由） |

技能能被直接编辑、导入导出、分享给同事、提交发布——这些路径都在**搬运文件**，不会顺带
更新数据库。分组一旦落在库里，搬运一次就丢一次，用户只会觉得「分组自己没了」。所以除非
以后要让分组承担权限或排序，否则不建表。

## 三、数据与接口

### 存储

`SKILL.md` 顶部 frontmatter 增加一行：

```yaml
---
name: 天气查询与出行建议
description: 查询指定城市实时天气…
enabled: true
group: 天气出行      # ← 本次新增，可缺省
---
```

键名 `group`，与 `meta_datasets.group_name`、`tools.group_name` 的**语义**一致（单值业务
分组），只是 frontmatter 里用短名。归一口径统一在
`app/utils/skill_metadata.py` 的 `SKILL_GROUP_KEY` / `normalize_skill_group`：

- 去首尾空白；
- **换行折成空格**（换行会写坏 frontmatter：逐行 `key: value` 解析，半行会变成新的键）；
- 超长截断到 `SKILL_GROUP_MAX_LEN = 64`（与 `meta_datasets.group_name` 的列宽对齐）；
- 空串 / 空白 → `None`（= 未分组，且**不写** `group:` 空行）。

### 解析与输出

- `parse_skill_frontmatter_text`：本来就是通用键值解析，**无需改动**即可读出 `group`；
- `skills.parse_skill_metadata`：输出字段是白名单，补一个 `"group": get_skill_group(meta)`。
  平台接口与个人接口**共用这一个函数**，所以两边同时就有了；
- 缺省 / 空值一律是 `None`（JSON `null`），前端按 `?? ''` 归一。

### 写入

新增 `set_skill_group_in_file(skill_md_path, group) -> bool`，与既有的
`toggle_skill_enabled_in_file` 同一套做法：**只动 frontmatter 里那一行，正文一个字节不碰**。

- 已有该行 → 替换；没有 → 追加；传空 → 删除该行；
- 没有 frontmatter 的纯 Markdown 技能 → 补一份最小 frontmatter，正文原样保留；
- 容忍 BOM、容忍闭合 `---` 位于文件末尾且无尾换行（手写 SKILL.md 的常见形态）。

**为什么单开端点而不是复用「编辑 SKILL.md 文件」接口**：后者要求把整份正文回传，而技能
列表页只持有元数据。按那条路走就得「先读全文再覆盖」，中间任何时序差（有人正好在网页
编辑器里改过正文）都会把正文冲掉。

| 端点 | 鉴权 | 说明 |
| --- | --- | --- |
| `PUT /api/portal/skills/{skill_id}/group` | `element:skills:admin` | 平台技能 |
| `PUT /api/portal/skills/personal/{skill_id}/group` | API Key + 属主校验 | 个人技能 |

请求体 `{ "group": "天气出行" | null }`，返回落盘后的实际值
`{ "status": "success", "group": "天气出行" }`（前端据此就地更新卡片，不整页重拉）。

### 新建技能

`SkillCreateRequest` 增加 `group: Optional[str] = None`；两处创建接口改用
`build_default_skill_md(name, description, group)`（此前各拼一份逐字相同的字符串）。
**分组为空时输出与历史模板逐字一致**，存量技能不需要回填 —— 这一点由测试钉住。

## 四、前端

文件：`frontend/src/views/SkillsManagement.vue`

复用已有的共享模块，不另写一套：

- `utils/toolGroupTabs.ts`：`computeGroupTabs` / `matchesToolFilters` / `toolGroupName`；
- `components/system/ToolGroupTabs.vue`：页签条（含「更多分组」溢出出口）。

三处适配（都只在页面侧做一次）：

| 技能的形状 | 共享模块要的形状 |
| --- | --- |
| `enabled` 是 frontmatter **字符串**（`"true"` / `"False"`） | `is_active: boolean`，经 `isSkillEnabled` 映射 |
| `group: string \| null` | `group_name: string`，`?? ''` |
| 技能主键是字符串 ID，用户习惯直接搜 ID | `searchFields = ['id','name','description']` |

`ToolSearchField` / `GroupableTool` 因此扩展了可选 `id`（类型 `string | number`，数据集是
数字主键）。这是本次唯一的共享模块改动，向后兼容。

行为：

- **页签位置**：搜索行下方、卡片网格上方。不放进「我的技能」的左侧二级菜单——那会让页面
  变成「一级 Tab + 左侧二级菜单 + 分组页签」三层混排；
- **共用状态**：页签与卡片分组徽标共用同一个 `skillGroupFilter`；锁定某组时卡片不再逐条
  显示分组徽标（每条都一样，纯噪音）；
- **计数口径**：计数**不含**分组条件，只含关键词——否则未选中的组永远显示完整数量，点过去
  才发现被搜索词筛空；
- **重置**：切一级视图 / 切二级菜单回「全部」；选中分组被搜空或分组名被改掉后回「全部」，
  但用 `currentScopeSkills.length > 0` 跳过首屏加载期（否则刚点的分组会在数据回来前被清掉）；
- **两个设置入口**：新建技能表单的「业务分组」；已有技能的「⋯ → 设置分组」弹窗。两处共用
  同一个 `<datalist id="skill-group-options">`（跨平台 + 我的两个列表聚合），避免同一业务域
  被写成两个名字；
- **弹窗必须 Teleport 到 body**：卡片 hover 带 `-translate-y`（即祖先有 transform），
  否则 fixed 遮罩只盖住卡片而不是视口。

## 五、边界与不做的事

- 分组**不参与权限**、**不进 AI 提示词**、不影响技能是否可挂载；`enabled` 与禁用策略完全
  不受影响；
- 「待审核 / 已发布」两个视图**不做**分组页签（那是待审版本与上线版本队列，不是浏览场景）；
- 列表视图（表格）本次不加分组列，分组通过页签筛选；
- **批量设置分组**本次不做，等分组真用起来、确实嫌一条条点烦了再加；
- 分组改名不提供「批量重命名」：直接改某个技能的 `group` 即可，旧组名会因没有成员而自动
  从页签消失（`computeGroupTabs` 只统计存在的分组）。

## 六、测试

`tests/frontend/test_skill_group_tabs_contract.py`（26 条）：

- **真跑探针** `tests/frontend/skill_group_tabs_probe.cjs`：用真实 `toolGroupTabs.ts` 验证
  页签顺序与计数、`enabled` 字符串映射链（含 `enabled: False` 首字母大写）、按 ID 搜索命中
  且去掉 `id` 字段后落空、空白/null/缺失归入未分组、页签计数与逐条过滤一致、平台与个人
  各算各的；
- **源码契约**：复用共享模块、映射只写一处、`searchFields` 含 `id`、页签与徽标共用状态、
  锁定时不显示徽标、切视图重置且跳过首屏、两个设置入口 + 共用 datalist、改分组走专用端点；
- **后端契约 + 纯函数真跑**：模板逐字一致（无分组时）、写入 → 覆盖 → 清除的往返、正文与
  其它字段不丢、无 frontmatter 补最小块、闭合符无尾换行、换行折空格与 64 截断。

## 七、落地步骤

1. 无需迁移（分组存文件）；
2. 重启后端（`./dev.sh`，由用户在控制台执行）；
3. 给几个技能用「⋯ → 设置分组」归组，确认页签出现、计数正确；
4. 新建一个带分组的技能，确认 `SKILL.md` 里出现 `group:` 行；
5. 复制 / 导出该技能，确认分组跟着走（这是选「存文件」方案的核心收益）。
