# 凭据来源：从「绑死一条」到「按规则定位」设计文档

- 日期：2026-11-10
- 状态：设计待确认（四项关键决策已由用户拍板，见 §4）
- 原型：[`prototypes/2026-11-10-dynamic-credential-locator.html`](../../../prototypes/2026-11-10-dynamic-credential-locator.html)
- 前置：[`2026-11-09-user-credential-center-design.md`](./2026-11-09-user-credential-center-design.md)（凭据中心的绑定、授权、策略、审计四道闸门，本设计**全部沿用，不改一道**）
- 迁移：MySQL `V211`、PG `V111`（双库各一份，互不混用）

## 1. 要解决的问题

用户诉求（原文，两轮）：「现在使用凭证的地方都是固定凭证（模型填值 / 固定凭证）。但固定凭证有时候不方便，我们希望基于某个字段或规则动态获取凭证，或让模型来推断应该取哪个凭证。」「按规则匹配的信息来源应该是 mcp 或 api 工具的**入参的字段的值**吧。比如我个人配置了 A、B、C 三个人的凭证，凭证 id 写的就是 A、B、C**C**。如果我让 AI 给 A、B、C 三个人发邮件：按规则匹配，凭证来源字段 `user_name`，它会根据 A、B、C 那个名字找到 A、B，因为 C 我写的是 CC，可能就匹配不上，但是模型推断可以基于 id、备注等信息，推断出 CC 就是 C 的凭证——我是要这样的。」

这段说明把全部要害都讲完了，拆成四条：

1. **键 = 入参的值**。这次调用 `to="A"`，就去凭据里找 `A`；`to="B"` 找 `B`。不涉及「谁在调用」，只涉及「这次调用的参数是什么」。
2. **规则是精确匹配，必然有认不出的那一格**。凭据写成了 `CC`，入参是 `C`，字符串对不上——这不是配置错误，是现实（历史命名不规范、录入笔误）。
3. **认不出的那一格交给模型推断**。模型同时看得到入参值（`C`）和候选的 id / key / **备注**（`c3 / CC / 「C 的邮箱账号」`），能判断该用哪条。**「精确匹配」与「语义对齐」各管一段，这是整个设计的分工。**
4. **个人也要能用**，且这不是「管理员给某个业务开的特例」：
   - 键可以是任意入参，也可以是身份字段（后者是补充，不是主场）。
   - 配规则的不限于管理员：个人给自己绑的槽位同样有这需求。谁做的绑定谁决定策略，个人与管理员**共用同一套规则编辑器**。
   - 因此本次设计的是**机制**。凡是把「邮箱」「某个具体业务字段」写进实现的代码，都算实现偏差。

### 1.1 必须澄清的现状：缺的不是「动态」，是「按内容」

读代码确认（不是推测）：

- 一个槽位 = 一条绑定：`sys_secret_bindings(consumer_type, consumer_id, slot) → secret_id`。
- **「按人动态」本来就有**：管理员插全局绑定（`bound_by_user_id = ''`），用户自己再绑一条就覆盖它；`resolver._pick_binding` 里的 `order_by(...)` 明确让个人绑定优先。张三、李梅各自绑一次，今天就能各用各的。
- 真正表达不出来的只有**「按内容」**：配置里没法写「按调用者邮箱去决定用哪条」。

**结论：本次不改「按人」这条路，只补「按内容」。** 否则会重复实现一个已有能力。

## 2. 关键事实（实现必须踩准的六处）

1. **取值只有一个入口**：`resolver.resolve(user_id, scene, consumer_type, consumer_id, slot, ...)`。绑定、可见范围、grant、策略、解密、流水全在里面收口。**注入层不需要改**——
   `injection.apply_mcp_bindings` / `apply_mcp_arg_bindings` / `apply_script_env_bindings` 都只是「列槽位 → 逐个 `resolve(slot=...)`」，它们不关心凭据是被绑死的还是按规则算出来的。
2. **`list_bound_slots` 只读 `slot` 与 `bound_by_user_id`**，不读 `secret_id`。规则绑定作为同一张表里的一行，天然会被列出来。
3. **`GET /api/portal/secrets/bindings` 用的是 `JOIN UserSecret ON UserSecret.id == SecretBinding.secret_id`**。规则绑定的 `secret_id` 为空，**这个 JOIN 必须改成 LEFT JOIN 并补 `strategy` 字段**，否则「配了规则，绑定列表里却什么都没有」。
4. **唯一索引 `uk_binding_slot(consumer_type, consumer_id, slot, bound_by_user_id)` 限制同一槽位只能有一条全局绑定**。要让「全局规则 + 全局固定（回退目标）」共存，必须放宽（见 §6）。加上 `strategy` 后，个人层同样可以「规则 + 固定」各一条。
5. **个人侧已有绑定入口**：`frontend/src/components/mcp/PlatformMcpCredentialPanel.vue`（「我的 MCP → 平台服务」，逐槽位绑定 + `CredentialPicker`）、`frontend/src/components/system/ToolRegistry.vue` 的绑定逻辑。个人配规则就落在这两处，**不需要新页面**。
6. **`POST /bindings` 已区分「全局绑定」与「个人绑定」**（非管理员写全局会 403/400）。个人可以配规则的前提是这条判据保持不变，只是放开 `strategy=rule`（见 §5.2）。
7. **`secret_key` 入库时被小写化**（只允许 `[a-z0-9._-]`，因为要拼进 `${secret:<key>}`）。用户配 `A`/`CC`，库里是 `a`/`cc`——这是规则默认用 `exact_ci` 的直接原因（见 §5.0）。
8. **系统级参数的来源判定必须用 `!= "literal"`，不能写 `== "credential"`。** 加了 `rule` 之后，有两处漏改会静默出错：`system_params.PARAM_SOURCES` 少一个值 → 前端存得进、后端解析报错；`apply_system_params` 少一处 → `rule` 参数被填成空串发出去，`unresolved_params` 检测不到，用户只收到远端 401 而不知道是凭据没配上。两处都在 HTTP 端到端测试里被抓到（见 §12）。
9. **API 工具的参数级凭据已经具备，但声明方式与 MCP 不同**：MCP 用 `sys_mcp_tools.credential_params` 列；API 工具用 `parameter_schema` 里的 `x-nanzi-system-params` 标注 `source: credential`（`app/services/ai/tools/system_params.py` 读取），槽位由 HTTP 语义推导（`resolve_api_tool_slot`：GET→`query:<名>`，其它→`body:<名>`，path 参数也覆盖）。**两条链路最终都落到 `secret_resolver.resolve(slot=...)`**，所以 API 工具接规则是零额外后端改造，只需在 `ToolRegistry.vue` 的参数定义编辑器里加「按规则匹配」选项。
8. **平台已有「目录类工具」的成熟范式**：`app/services/ai/tools/resource_catalog_tools.py` 的 `list_accessible_datasets` / `list_available_agents` / `list_accessible_directories`。若要做「让模型知道当前用户有哪些凭据」，照这个形状做即可（见 §8.5）。

## 3. 五条不可让步的结论

1. **规则只描述「怎么找」，不描述「找到谁」**。`rule_json` 里不出现 `secret_id`，所以「改一条规则」永远成不了「读别人凭据」的手段。
2. **定位结果必须再过四道闸门**：可见范围（自己的 或 已授权）→ grant → 策略（状态/有效期/场景/域名）→ 解密+流水。locator 只加在「找到候选」这一步，**任何一道闸门都不放宽**。
3. **模型只能看到 `编号 + secret_key + 名称 + 备注`，回传编号**。清单里不出现值、不出现 `secret_id`；模型给的任何标识都只在**当前调用者可见集合**里查，猜中 key 也拿不到别人的凭据。
4. **命中失败默认严格失败，绝不静默换人**。既不退回「让模型自己填」（等于把凭据泄漏进对话），也不偷偷用另一条。
5. **结果必须可试算、可解释**。「配完不知道会命中谁」的动态规则，运维不敢开，等于没做。

## 4. 已拍板决策

| # | 决策 | 结论 | 理由要点 |
|---|---|---|---|
| Q1 | 模型挑选的位置 | **规则为主，模型做「命中失败」的兜底**（不做并列的第四种来源） | 规则命中时确定、零额外 token、可解释；模型的选择只在规则没命中时才被采用，同一参数的行为依然可预测 |
| Q2 | 匹配键来源 | **工具入参的值（主场景）+ 任意身份字段（次）** | 真实场景要的是「这封信发给谁，就用谁的凭据」——值的来源天然是入参。身份字段保留作为补充，但不能当主角 |
| Q3 | 未命中行为 | **默认严格失败**；回退固定凭据是显式开关 | 严格失败让配置错误立刻暴露；迁移期允许回退，但回退命中必须在流水里可分辨 |
| Q4 | 首批范围 | **只做「工具参数」：MCP 工具参数 + API 工具参数级**。Header（MCP 其他 Header / API 工具 Headers）**只保留固定凭据**，脚本 env 放第二批 | 用户拍板：「head 只要固定凭证即可，不需要这个动态的」。实现上也站得住：``apply_mcp_bindings`` / ``apply_api_tool_bindings`` 在出站请求上就要算值，那时没有「这次调用的入参」，``rule_args`` 无从传起，规则只能按身份字段匹配，价值有限。API 工具的参数级凭据**已经存在**，只是声明方式不同（见 §2 第 7 条），接上 locator 是零额外后端改造 |
| Q5 | 谁能配规则 | **个人和管理员都能配，同一套规则编辑器** | 「按某个值动态取凭据」不是管理员专属策略：个人同样有「我绑了几条凭据，按场景该用哪条」的需求。谁做的绑定谁决定策略，不需要管理员代劳 |

### 4.1 Q6 已定：模型推断与「入参当键」必须能组合

用户在第二轮评审里把主场景说清楚了，也纠正了我上一版的一个错误限制：

> 我个人配了 A、B、C 三个人的凭证，凭据 id 写的是 `A`、`B`、`CC`。让 AI 给这三人发邮件：按规则匹配能认出 A 和 B，认不出 C（因为我把 C 写成了 CC）；这时候模型推断基于 id、备注等信息，能推断出 CC 就是 C 的凭证。

上一版我写过「键来自入参时模型兜底不可用，因为提前算不出命中与否」，**这条已作废**。理由是原来把「预判命中」当成了注入候选的前提，而这个前提本身不必要：

- **候选清单一律在构造工具 schema 时注入**（只要该槽位配了 `on_miss=model`），不需要预判规则命中。
- 模型在**同一次调用**里既填 `to="C"`，也填 `__secret_choice="c3"`——它看得到候选的编号 / key / 备注，本来就能拿入参值去对照。
- 运行期**规则先跑**：命中就用规则的，模型的选择被忽略（只记一条「规则判断与模型选择不一致」，用于发现规则该更新）；未命中才采用模型的编号，并限定在本可见集合内校验。

代价说明白：配了 `on_miss=model` 的槽位，每次调用都会多带一份候选清单（20 条以内，约几百 token）。所以它只该开在规则覆盖不到的地方——这是配置者的选择，不是平台的默认。

## 5. 概念模型：locator

把「绑定一条凭据」升级为「绑定一条选择器」：

```
binding = strategy + (secret_id | rule_json)
strategy = fixed  → 就是今天的行为，secret_id 必填
strategy = rule   → 按 rule_json 在「调用者可见」的凭据里定位
```

**键的主要来源是这次调用的入参**，不是调用者身份。真实场景长这样：

> 我给 A、B、C 三个人配了三条邮件凭据，`secret_key` 是 `A` / `B` / `CC`（C 那条写错了名字）。让 AI 依次给他们发信：规则用入参 `to` 的值去匹配 `secret_key`，A 和 B 直接命中；`to="C"` 时会找不到 `C`，这时模型看得到 `CC` 的备注是「C 的邮箱账号」，据此推断出该用 `CC`。

所以键 = **`arg:<入参名>`**（主），身份字段（`user.email` 等）只是补充能力：有些场景确实要按「谁在调用」取凭据，但那是少数。值可以做变换、套多条模板，去精确匹配凭据的 `secret_key` / `name` / `category`。

同一条规则对个人和管理员都是同一种东西，只是**挂在谁的绑定上**不同（见 §5.2）。

### 5.0 规则表达

**默认 `match` 是 `exact_ci`（不区分大小写），不是 `exact`。** 这是端到端测试逼出来的：

`center.create_secret` 会把 `secret_key` 规范化为小写（只允许 `[a-z0-9._-]`，因为它要拼进 `${secret:<key>}`）。用户凭直觉配 `A` / `B` / `CC`，入库后是 `a` / `b` / `cc`；而入参值往往是 `A` / `C`。用 `exact` 的话，**用户的核心场景在默认配置下必然匹配不上**，而界面上完全看不出问题——规则显示已配置、试算却说未命中，排查要靠翻库。

`exact` 仍然保留给「确实要严格区分」的场景，但它不该是默认值。

（`rule_json`，v1）

```json
{
  "version": 1,
  "key_source": "arg:to",            // 主：arg:<入参名>；次：user.email / user.user_name / user.real_name / user.dept_code
  "transforms": ["trim"],            // 顺序执行：trim / strip_domain / lower
  "templates": ["{value}", "{value}-smtp"], // 按顺序尝试，最多 3 条
  "target": "secret_key",            // secret_key | name | category
  "match": "exact_ci",               // exact_ci（默认）| exact。默认不区分大小写，见 §5.1
  "on_miss": "model"                 // fail | fallback_fixed | model
}
```

- `key_source=arg:<入参名>`：入参必须是该消费者（MCP 工具 / API 工具）**模型可见的普通参数**，不能是另一个凭据参数（否则形成依赖环）。
- `transforms` 按顺序执行，`strip_domain` 对不含 `@` 的值是**空操作**（不报错），这样同一套规则可以同时挂在邮箱键与其他键上。
- `templates` 按顺序尝试，**第一条命中的即生效**；全部未命中才进入 `on_miss`。
- `target` 首批支持 `secret_key`（最常用，命名规范就落在 key 上）、`name`、`category`；都是**精确匹配**，不做模糊/包含。

### 5.1 解析顺序（`_pick_binding` 之后新增的一层）

**层间**：个人绑定优先于全局绑定（今天的行为，不变）。**层内**：规则优先于固定——固定在这一层里是「规则没命中时的回退目标」，而不是规则的上位。

| 序号 | 情况 | 行为 |
|---|---|---|
| 1 | 存在**个人规则绑定**（`bound_by_user_id = 我, strategy = rule`） | 执行规则：命中 → 用它；未命中 → 看 `on_miss` |
| 2 | 个人规则未命中且 `on_miss = fallback_fixed`，且存在**个人固定绑定** | 落到个人固定，流水标「回退命中」 |
| 3 | 个人规则未命中且 `on_miss = fail` | 失败。**不继续往下走到全局**——个人规则是本人显式声明的策略，越过去等于替他做主 |
| 4 | 无个人规则，存在**个人固定绑定** | 直接用。这是今天的行为，最常见的一条路 |
| 5 | 无个人绑定，存在**全局规则绑定** | 执行规则，命中/未命中语义同上，回退目标是全局固定 |
| 6 | 无全局规则，存在**全局固定绑定** | 直接用（今天的行为） |
| 7 | 都没有 | `DENY_BINDING`，与今天一致 |

**关键：`on_miss = fail` 时绝不继续往下取固定绑定**——无论同层还是跨层。否则「回退」就成了默认行为，配置错误永远发现不了。

### 5.2 规则挂在谁的绑定上

规则不区分身份，**谁做的绑定，谁决定这一层的策略**：

| 绑定归属 | 谁能配 | 对谁生效 | 匹配范围（可见集合） |
|---|---|---|---|
| 个人绑定（`bound_by_user_id = 本人`） | 该用户自己，在「我的 MCP / 我的工具」入口里配 | 只有他自己调用时 | 他自己可见的凭据（本人的 + 被授权的系统级） |
| 全局绑定（`bound_by_user_id = ''`） | 管理员，在服务配置里配 | 所有**没有个人绑定**的调用者 | 每个调用者各自的可见集合（**不是管理员的**） |

三条必须写死的规则：

1. **全局规则对每个人的匹配范围是该调用者自己的可见集合**——同一条 `secret_key = {入参 to}` 的全局规则，张三调用时 `to=C`，他只能在自己的凭据里找 `C`；他没有 C 的凭据就命中不了，**绝不会**用到李梅那条 `CC`。要让他也能发，只有两条路：管理员授权给他的凭据，或他自己绑一条。
2. **个人规则可以覆盖全局规则**（层间优先级），这是「我的 MCP」里今天已有的心智：我自己配的，盖过服务默认。
3. **非管理员不能写全局绑定**（`bound_by_user_id = ''`）——今天 `POST /bindings` 就是这么判的，本设计沿用，不因为「个人也能配规则」而放宽。

## 6. 数据模型与迁移

### 6.1 MySQL `db-prod/V211-add-credential-locator.sql`

```sql
-- 规则绑定没有具体 secret：fixed 时必填，rule 时为空
ALTER TABLE `sys_secret_bindings`
  MODIFY COLUMN `secret_id` VARCHAR(36) NULL
    COMMENT 'strategy=fixed 时必填；strategy=rule 时为空';

ALTER TABLE `sys_secret_bindings`
  ADD COLUMN `strategy` VARCHAR(16) NOT NULL DEFAULT 'fixed'
    COMMENT 'fixed / rule：绑一条凭据，还是绑一条定位规则',
  ADD COLUMN `rule_json` TEXT NULL
    COMMENT 'strategy=rule 时的匹配规则（JSON，见设计文档 §5）';

-- 唯一索引加入 strategy：同一槽位允许「全局规则 + 全局固定（回退目标）」各一条。
-- 分两次执行：先加新索引（本文件第 2 段），确认后再删旧索引（第 3 段）——
-- 顺序反了会出现「旧索引已删、新索引未建」的裸窗口，并发写入可能插入重复槽位。
ALTER TABLE `sys_secret_bindings`
  ADD UNIQUE KEY `uk_binding_slot_strategy`
    (`consumer_type`, `consumer_id`, `slot`, `bound_by_user_id`, `strategy`);
```

```sql
-- 第 3 段（确认上一步生效后单独执行）：旧索引已被新索引覆盖
ALTER TABLE `sys_secret_bindings` DROP INDEX `uk_binding_slot`;
```

### 6.2 PG `db-prod-pg/V111-add-credential-locator.sql`

```sql
ALTER TABLE "sys_secret_bindings" ALTER COLUMN "secret_id" DROP NOT NULL;

ALTER TABLE "sys_secret_bindings"
  ADD COLUMN IF NOT EXISTS "strategy" VARCHAR(16) NOT NULL DEFAULT 'fixed',
  ADD COLUMN IF NOT EXISTS "rule_json" TEXT;

-- 与 MySQL 同序：先建新索引，确认后再删旧索引（零裸窗口）
CREATE UNIQUE INDEX IF NOT EXISTS "uk_binding_slot_strategy"
  ON "sys_secret_bindings" ("consumer_type", "consumer_id", "slot", "bound_by_user_id", "strategy");
```

```sql
-- 第 3 段（确认上一步生效后单独执行）
DROP INDEX IF EXISTS "uk_binding_slot";
```

### 6.3 迁移约束（按仓库既有口径）

- **两套执行器不同**：MySQL 侧执行器忽略 1060/1061/1091 等错误码，`ADD COLUMN`、重复建索引、删不存在索引重跑都安全；PG 侧**不吞任何错误**，所以 PG 必须靠 `IF NOT EXISTS` 自查幂等，两边的写法不可互换。
- **存量零数据变更**：`strategy` 默认 `'fixed'`，全部既有行自动归为固定绑定；`secret_id` 只放开 NOT NULL，不回填、不清空。
- **索引替换分两次执行（先加后删）**：先建 `uk_binding_slot_strategy`，确认生效后再删 `uk_binding_slot`。反过来（先删后建）会出现「旧索引已删、新索引未建」的裸窗口，并发写入可能插入重复槽位行。绑定写入虽是低频人工操作，但没有必要承担这个风险。

## 7. 解析链路改造

改动集中在 `app/services/secrets/resolver.py`，注入层零改动。

### 7.1 `_pick_binding` → `_locate`

- 查询放宽为「该槽位所有候选绑定」（删掉 `limit(1)`），按 `个人优先 → rule 先于 fixed` 排序。
- 新增 `_apply_rule(rule_json, context)` 纯函数：输入规则 + 当前调用者身份 + 入参，输出 `{matched_key, steps[]}`。**不查库、不解密**，便于单测与试算复用。
- `_locate` 再与「可见凭据集合」求交：只从 `list_visible_secrets` 的结果里精确匹配 `secret_key / name / category`。**这是越权防线：规则匹配的是「我已经能看见的集合」，不是全表。**

### 7.2 失败语义（两条链路不同，必须写清）

| 链路 | 未命中时的行为 | 原因 |
|---|---|---|
| **工具参数**（`arg:`） | **报错，本次调用失败** | 参数已从模型可见 schema 剔除，取不到值就没有任何替代来源 |
| **Header**（`header:`） | **跳过该 Header + 记流水，不阻断调用** | Header 的语义是「没人绑时的默认值」。规则命不中时，退回配置里的固定值（`fixed_token` 或其他 Header 原值），与服务此前行为一致 |

这条差异必须在 `resolve` 的返回值里可分辨（已有 `denial` 字段即可），不要为了「统一」把 Header 也改成报错——那会让今天能跑的服务突然调不通。

### 7.3 试算端点

`POST /api/portal/secrets/locate-preview`（管理员）

```
入参：{ consumer_type, consumer_id, slot, rule_json, sample_user_id, sample_args?: {name: value} }
出参：{ ok, strategy, secret_key, secret_name, steps: [{step, input, output}], candidate_count, miss_reason }
```

- **身份固定为当前登录人**（已定）：试算用的是「你」的可见范围，不支持代某个用户试算。所以管理员配全局规则时，试算结果只代表自己的可见集合；其他人能被授权到哪些凭据可能不同，结果可能不一样。界面文案必须写清这一点，避免把试算当成「所有人都能这样」的保证。
- 纯计算：不写流水、不解密、不发出站请求。
- `steps` 就是 §7.1 里 `_apply_rule` 的步骤轨迹，前端直接渲染成原型里那张「① 取匹配键 ② 变换 ③ 套模板 ④ 精确匹配 ⑤ 闸门」。
- 供配置页「试算」按钮使用；试算通过 ≠ 一定可用（grant/策略仍可能在真实调用时拦截），文案要写明这一点。

### 7.4 模型兜底（编号字段 + 候选走 `list_my_credentials`）

**实现时的修正（推翻原设计）**：原方案是「只要槽位配了 `on_miss=model`，就把候选清单写进参数描述，并加一个 `__secret_choice:<参数名>` 隐藏参数」。写代码时发现这条走不通：

> **工具定义是跨用户缓存的**（`registry.py:536` 把 `McpToolFactory.create_tool` 的产物按 `cache_key` 缓存），而候选清单是按**当前用户的可见范围**算的。一旦把候选写死进 schema，A 用户看到的 `c3` 和 B 用户看到的 `c3` 就会串号——模型回传 `c3`，平台找到的是别人的凭据。更糟的是这类错误不报错，只是「悄悄用了不对的那条」。

改成本更低的方案，正好把 P3 的两个部件接起来：

1. **schema 里只加一个固定的编号字段**：对每个凭据参数加 `secret_choice_<参数名>`（`Optional[str]`，默认空）。它不随用户变化，工具定义仍可缓存。
   - 字段名不能以 `_` 开头：Pydantic 会把 `_foo` 当私有属性直接报错（`Fields must not use names with leading underscores`），所以用 `secret_choice_` 而不是 `__secret_choice_`。
   - 远端参数名可能含 `-`、`.`，两侧统一用 `locator.choice_field()` 做安全化，否则「字段写进去、运行时读不到」。
2. **候选清单由 `list_my_credentials` 提供**（§8.5）：模型想知道有哪些候选就先调它，两边用**同一套编号**（排序后的序号，`locator.catalog_sort_key` = `secret_key` → `id`，全序）。
3. **运行期** `apply_mcp_arg_bindings`：把 `secret_choice_<name>` 的值经 `locator.parse_choice` 解析成序号，再用同一排序重建候选、取 `secret_id`，作为 `preferred_secret_id` 传给 `resolve()`。
   - 序号越界、查库失败、凭据已失效 → 一律当作「模型没选」，**绝不猜**（猜错会把值注入到错误的凭据上）。
   - 只有规则**未命中**且 `on_miss=model` 时才采纳；规则命中时忽略并记「判断不一致」日志。
4. **出站前必须剔除** `secret_choice_*`：远端收到未知参数会报错。剔除点在 `apply_mcp_arg_bindings` **之后**（注入层要从这里读模型的选择）。
5. 编号上限沿用 `list_my_credentials` 的 20 条默认 / 50 条上限，超出的用 `keyword` 收窄——不再有「拒绝保存」这条约束（候选不再写进配置，没有截断问题）。

代价对比：原方案每次调用都多带候选清单（几百 token，即使规则命中），新方案只在模型主动 `list_my_credentials` 时才带。可靠性略降（依赖模型主动查），但换来正确性——**串号是不可接受的，多一次工具调用是可接受的**。

## 8. 模型兜底的边界

### 8.1 候选清单长什么样

- 只含 `编号（c1/c2…）+ secret_key + name + remark`，**不含值、不含 `secret_id`**。
- 必须有 `remark`：这是模型能推断的唯一依据（例：key 写错了，但备注写着「C 的邮箱账号」，模型才救得回来）。配置页要提示「备注写清楚是谁的凭证」。
- 按调用者可见范围裁剪：未授权的系统级凭据**连名称都不出现**（与 `list_visible_secrets` 同口径）。

### 8.2 模型推断的依据是什么

模型同时看到两样东西：**这次调用的入参值**（`to="C"`）和**候选清单**（`c3 / CC / 「C 的邮箱账号」`）。它做的是语义对齐，不是字符串匹配——所以 `CC` 与 `C` 对不上也能选对。这也是它和规则的分工：**规则负责确定、快速、零成本；模型负责「名字没规范起来」的那一格。**

### 8.3 模型选错或选不出来

按 `on_miss` 处理：`fail` / 选不出 → 明确失败，报错写「规则未命中，可见候选 N 条」，**不退回让模型自己填密码**；`fallback_fixed` → 落地固定绑定并在流水标记回退。**不做二次追问问模型**（那会引入不可控的往返次数）。

### 8.5 已定：加 `list_my_credentials` 工具（默认开启）

用户提出：*「是不是要加一个工具，类似数据集 list 工具一样，让模型知道当前用户有哪些凭证列表」*，并明确：*「这个暴露应该没有关系吧，因为只有元数据，没有凭证的敏感的内容值啊」*。

**结论：做，默认开启。** 元数据不是凭据值，默认关是我过度保守；关掉反而让「我有几个邮箱账号」这种正常问题在对话里答不了。

它不能替代 `__secret_choice`（凭据参数已从模型可见 schema 移除，模型 list 出来也没有字段可指定），两者是配合关系：

| | 谁触发 | 返回什么 | 干什么用 |
|---|---|---|---|
| `list_my_credentials` | 模型主动调用 | 编号 + key + 名称 + 备注 + 分类 + 状态 | 发现清单、回答「我有哪些凭据」 |
| `__secret_choice` | 平台注入 schema | 无（让模型填编号） | 这一次用哪条 |

**编号必须是同一套**（`c1/c2/c3`，会话内稳定），否则模型会拿 list 的编号去填一个对不上的地方。

四条实现约束：

1. **只给元数据**：编号 + `secret_key` + `name` + `remark` + `category` + `status`。不含值、不含 `secret_id`、不含归属人、不含授权关系（后者是结构信息，泄露组织结构）。
2. **只列「调用者自己可见的」**：与 `list_visible_secrets` 同口径；未授权的系统级凭据连名字都不出现。
3. **只在与凭据有关的会话里暴露**：本次可用工具里若没有任何 `source: credential` / `credential_params` 声明，就不把这个工具给模型——减少无关对话里的清单暴露与 prompt injection 枚举面。

**它读不到值，这一点与 `__secret_choice` 同源**：两条路径都只做「定位」，值只在注入阶段由平台解密、直接写进出站请求，既不进模型上下文，也不进工具出参。这是 §3 第 3 条红线的组成部分，不靠这个工具自己守。

**已知并接受**（不做特例）：出参按普通工具落 `ai_agent_execution_traces.tool_output`，而 trace 详情端点支持 `cross_user`，所以有审计权限的角色**能看到某个用户的凭据清单元数据（编号、key、名称、备注）**。这里不含值，仅是「他有哪些凭据」这一层信息。选择不做特例的理由：少一条专门分支，也不必解释「为什么这个工具特殊」；若将来合规要求收紧，在审计视图侧对凭据类工具统一脱敏更合适，而不是在工具侧打补丁。

## 9. 前端改造

**规则编辑器抽成一个共享组件**（`components/secrets/SecretLocatorEditor.vue`：来源切换 + 规则面板 + 试算），个人与管理员用的是同一个东西，只是落库时的 `bound_by_user_id` 不同。四处挂载点如下。

### 9.1 参数设置弹框 · 管理员（`McpServerRegistry.vue`）

「值的来源」由两选一改为三选一：**模型填写 / 固定凭据 / 按规则匹配**。选「按规则匹配」展开规则面板。左侧参数名列宽度已在上一提交调到 30%，够放。

规则面板就四个控件（与原型一致）：

1. **用到哪个入参**：下拉列出该工具**模型可见的普通参数**（`to` / `subject` / `body` …），默认选第一个字符串参数；末尾另有一项「我自己的账号名（身份字段）」作为补充。
2. **取值变换**：去首尾空格 / 转小写 / 去掉 @ 及之后（勾选式，可组合）。
3. **匹配模板**：默认 `{value}`，可加多条按顺序尝试。
4. **匹配不上**：`直接报错` / `让模型从候选里推断（会额外消耗候选清单 token）` / `回退到固定凭据`。

- 选「让模型推断」时面板下方展示一句成本提示与候选预览（「本次将带 3 条候选：c1 A / c2 B / c3 CC」），让配置者看到代价。
- 规则面板底部给「试算」：填一组入参（如 `to=C`）即可看到会命中哪条、命中不了会怎样（调用 §7.3）。

### 9.2 API 工具参数级凭据（`ToolRegistry.vue`，首批新增）

API 工具的参数级凭据声明在 `parameter_schema` 的 `x-nanzi-system-params` 里（`source: credential`），编辑入口是 **API 工具注册 → 参数定义**。本次改造：

- 每个参数的「数据来源」由「模型填写 / 来自凭据」扩为「模型填写 / 固定凭据 / 按规则匹配」，规则编辑器与 MCP 侧同一个组件；
- 槽位由既有 `resolve_api_tool_slot` 推导（GET→`query:<名>`，其它→`body:<名>`，path 参数同样覆盖），**规则配置界面不展示槽位**（与 MCP 侧一致，hover 才给排查用）；
- 后端零改造：`apply_api_tool_param_bindings` 已经走 `secret_resolver.resolve`，locator 落地后自动获得规则能力。

### 9.3 其他 Header · 管理员（`McpServerRegistry.vue` / `ToolHeadersEditor.vue`）—— 不做规则

**已定（用户拍板）**：Header 只保留「固定值 / 固定凭据」两种来源，不加「按规则匹配」。理由有两层：

1. 产品上：Header 大多是固定值（`Content-Type`、`X-Tenant` 一个环境一个值），按入参动态选 key 的场景很少；
2. 实现上：`apply_mcp_bindings` / `apply_api_tool_bindings` 在出站请求上就要把 Header 算出来，那时没有「这次调用的入参」，`rule_args` 无从传起——规则只能按身份字段（`user.*`）匹配，表达力有限。

所以这两处**代码一行不改**。将来若要支持，得先把「工具入参」从调用链传到 Header 注入，那是一次独立的链路改造。

### 9.4 个人侧入口（`PlatformMcpCredentialPanel.vue`、`ToolRegistry.vue`）

这是本次因「通用」而必须补的一半——个人同样有「按某个值动态取凭据」的需求：

- 「我的 MCP → 平台服务」每个槽位今天只有一个「绑定」按钮（打开 `CredentialPicker` 选一条）。改为两个动作：**选凭据**（不变）/ **用规则**（打开 `SecretLocatorEditor`，键来源同样是「该工具/服务的入参」下拉 + 身份字段补充项，与管理员完全一致）。
- 槽位行展示：个人规则存在时显示 `我的规则：secret_key = {入参 to}`；同时展示（若有）`我的固定：A` 作为回退目标，并标注「规则未命中时用这条」。
- 个人规则写库时 `bound_by_user_id = 自己`，走与今天完全相同的 `POST /bindings`，只多一个 `strategy=rule`。
- 个人看不到、也改不了全局规则——面板里对全局规则只读展示一行「服务默认规则：…（管理员配置）」，避免用户以为「没绑就等于没配置」。

### 9.5 绑定列表（`/api/portal/secrets/bindings`）

- 后端改 LEFT JOIN，返回 `strategy`、`rule_summary`（人话摘要，如 `secret_key = {入参 to}`）、`bound_by_me`。
- 前端绑定列表、凭据「被绑到哪」视图都要能显示规则行，否则用户会以为规则没生效。

### 9.6 文案

- 「标为『来自凭据』的参数会从模型可见的参数里移除」保留。
- 规则面板顶部加一句：**「这里配的是规则，不是某一条凭据：匹配到什么值，就用哪条凭据。」**
- 个人与管理员视角的差异只说一句：个人配的规则只影响自己；管理员的规则是「服务默认」，个人配了会盖过它。

## 10. 审计与可观测

- **命中成功**：写入既有 `sys_secret_usage_logs`（`secret_id` / `secret_key_snapshot` / `slot` / `consumer_*` 全都有），无需新表。
- **定位过程**：`logger.info` 记结构化一行（`consumer` / `slot` / `strategy` / `key_source` / **脱敏键值** / 命中的模板 / 候选数 / 结果）。**不新增列、不新增表**。
- **键值脱敏**：邮箱记成 `z***@nanzi.com`，其余按 `redact.py` 既有规则。
- **回退命中**：流水 `result` 区分 `success_fallback`（回落固定绑定成功）与 `success`，否则「规则其实一直没命中、全靠回退」这种状态永远看不见。
- **模型兜底**：额外记 `choice=c3`，便于回答「模型为什么选了这条」。
- **规则与模型判断不一致**：规则命中但模型选了别的编号时，记一条 `logger.info`（`rule_key` / `model_choice`）。这不是错误，而是「规则配得可能过时了」的信号——没有这条记录，规则该更新时没人知道。
- **凭据目录调用**：`list_my_credentials` 每次调用记一条 usage log（`action=catalog`、返回条数），能回答「谁在什么时候让 AI 看过自己的凭据清单」；出参按普通工具落 trace（不含值，见 §8.5 的「已知并接受」）。
- **缺口统计**：「某槽位规则未命中的次数」是评估「该不该开模型兜底 / 该不该改命名」的唯一依据，按天聚合即可（从使用流水按 `deny` + `rule_miss` 计数），不额外建表。

## 11. 不做的事（YAGNI）

- 不做「规则表达式语言」（正则 / 逻辑组合）。首批只有「变换 + 模板 + 精确匹配」，覆盖命名规范场景；写正则的自由度换来的是没人看得懂配置。
- 不做「把个人规则共享给别人」：规则只对配置者自己生效，管理员想对所有人生效就配全局规则。
- 不做「跨用户候选池」。候选永远是调用者本人的可见集合。
- 不做规则继承 / 规则分组。
- 不改脚本 `env:` 与 API 工具系统参数（第二批，复用同一 locator）。

## 12. 测试计划

**后端（pytest）**

- `_apply_rule` 纯函数：各变换组合、多模板顺序回退、精确/忽略大小写、命中与未命中；键是任意入参名（**用例的主场景用 A/B/C 与 CC 这组数据**，防止实现写死某个业务字段）。
- **主场景三连**（与原型同一组数据）：凭据 `A` / `B` / `CC`，入参 `to` 依次为 `A` / `B` / `C` → 前两次规则命中 `A`、`B`；第三次规则未命中且 `on_miss=model` → 采用模型选的 `c3`（`CC`），调用成功；`on_miss=fail` → 失败且报错写明「可见候选 3 条」。
- `_locate` 顺序：个人规则 > 个人固定 > 全局规则 > 全局固定；`on_miss=fail` 时**不得**落到任何固定绑定（含跨层）（变异验证：把 fail 改成继续取 fixed，用例必须失败）。
- 个人规则覆盖全局规则；个人规则未命中且 `on_miss=fail` 时**不得**落到全局固定。
- 权限：普通用户 `POST /bindings` 写 `bound_by_user_id=''` 仍被拒；写自己的 `strategy=rule` 被允许。
- 越权防线：规则模板恰好等于别人系统级凭据的 key 时，**必须未命中**（不在可见集合内），不得解密、不得写 success 流水。
- Header 链路未命中不阻断（服务照常带固定值出站）；参数链路未命中必须报错。
- `get_bindings` 对规则行返回 `strategy=rule` 且不因 JOIN 丢行（变异验证：改回 INNER JOIN 必须失败）。
- 迁移契约：两份 SQL 的索引名、`secret_id` 可空、`strategy` 默认值；PG 侧必须用 `IF NOT EXISTS`。
- **端到端注入（真实 DB、不走 HTTP）**：`tests/secrets/test_locator_e2e.py` 用 echo MCP 的 `send_mail(username, mailkey)` 场景——建三条凭据（`A`/`B`/`CC`）+ 一条按 `arg:username` 定位的规则，断言：`username=A` 注入 A 的值、`username=B` 注入 B 的值、`username=C` 在 `on_miss=fail` 下报 `rule_miss`、`on_miss=model` + `secret_choice_mailkey=c3` 时注入 CC 的值、**规则命中时模型乱填的编号被忽略**、编号越界当「没选」、别人的私有凭据即便 key 撞上也不可用。
  这组用例的价值：它是唯一会同时跑到「查绑定 → 规则匹配 → 可见集合 → 授权 → 解密 → 加密值回填」的测试，mock 结构造不出这些协作问题。
- **HTTP 端到端（进程内 ASGI，不起服务）**：`tests/secrets/test_api_tool_http_e2e.py` 用内置的 `POST /api/test/echo`（`app/services/mcp/http_echo.py`，只在非 production 挂载）当目标，`httpx.ASGITransport` 把 `generic_api` 的客户端换成内存传输——请求头、query、body 的真实编码与传输都走到，只是不占端口。断言：固定凭据注入的 header 服务端收到明文（指纹对得上、回显里无明文）、按 `arg:username` 的规则让每次请求带上不同的 `mailkey`、未命中时**在发请求前**就报错并点出参数名。
  这层的必要性：注入函数的返回值对了 ≠ HTTP 报文里对。header 值编码、参数落位（query/body/path）、空串处理都只有真发一次才暴露。
- **回显端点只回掩码 + 指纹**：明文不进响应体、日志、trace。验收要回答的是「发出去的是哪一条」，指纹够用。
- `list_my_credentials`：只返回本人可见的（未授权的系统级凭据不出现）；返回体**不含值、不含 `secret_id`、不含归属人**（变异验证：把 `secret_id` 加进返回体，用例必须失败）；本次可用工具无任何凭据参数时**不暴露**该工具。

**前端（契约测试）**

- 参数弹框三种来源、规则面板四个控件齐全（用到哪个入参 / 变换 / 模板 / 匹配不上）；「用到哪个入参」的下拉**只列该工具模型可见的普通参数**，不含凭据参数自身（防依赖环）。
- 选「让模型推断」时展示候选预览与成本提示（「本次将带 3 条候选」），且**入参键与模型推断可以同时选**（不允许出现「选了入参就禁用模型推断」的实现）。
- 试算按钮调用 `locate-preview` 并渲染步骤。
- Header 徽章可切换、规则摘要可读；「命中失败退回固定值」的说明存在。
- 个人面板（`PlatformMcpCredentialPanel.vue`）存在「用规则」入口，且与管理员侧复用同一个规则编辑组件；个人面板对全局规则只读展示。
- 不可出现「规则里存 secret_id」的 UI 路径。

**回归**：`tests/frontend` 全量、`vue-tsc` 与基线逐条比对（只允许零新增）。

## 13. 风险

| 风险 | 处理 |
|---|---|
| 规则写错导致线上调用失败 | 默认严格失败 + 配置页试算 + 报错写明「规则未命中，可见候选 N 条」 |
| 迁移放宽唯一索引后出现重复槽位 | 分两步执行（先加新索引、再删旧索引），写入是低频人工操作 |
| 模型兜底把「我有哪些凭据」带进对话上下文 | 只给编号 + key + 名称 + 备注；这些本来就是调用者有权看到的；不给值、不给 `secret_id` |
| 入参值由模型生成，可能被诱导去「选」某条凭据 | 入参值再自由，候选也只在**本人可见集合**内；命中与否写进流水，审计可回放「这次为什么用了这条」 |
| 回退掩盖配置错误 | 回退是显式开关；`result=success_fallback` 可查 |

## 14. 实施顺序（三步，每步可独立交付）

| 步骤 | 内容 | 不依赖后续步骤即可验证 |
|---|---|---|
| **P1 规则内核** | 迁移 `V211`/`V111`；`rule_json` 解析纯函数 `_apply_rule`；`_locate` 顺序与 `on_miss`；`result=success_fallback`；`get_bindings` 改 LEFT JOIN；权限放开（个人可写 `strategy=rule`，全局仍需管理员）；单元测试与变异验证 | 用接口直接写一条规则绑定，即可验证「张三命中自己的、王强失败」；个人规则能覆盖全局 |
| **P2 配置面** | 共享组件 `SecretLocatorEditor.vue`；**三个入口**：MCP 工具参数（`McpServerRegistry.vue`）、API 工具参数（`ToolRegistry.vue` + `ToolSystemParamEditor.vue` + `toolSchema.ts`）、个人侧槽位（`PlatformMcpCredentialPanel.vue` + `platform-targets` 补 `rules`/`arg_options`）；`locate-preview` 试算；前端契约测试 | 管理员与普通用户都能在各自入口配规则、试算、看到命中结果，全程不碰 AI。Header 不做（见 §9.3） |
| **P3 模型推断** | `on_miss=model`：schema 无条件注入候选 + `__secret_choice`；运行期「规则先跑、未命中才采用模型选择」；「规则与模型判断不一致」日志；候选上限 20 条与超限拒绝保存；个人与管理员两种绑定都支持；`list_my_credentials` 工具（§8.5：默认开、共用编号、只给元数据）；`secret_choice_<参数名>` 编号字段与运行期采纳（§7.4） | 用户的原用例：`to=C` 而凭据写成 `CC`，模型据备注推断出 `c3`，这次调用成功 |

P1 是纯后端、可单独上线（旧 UI 不受影响，`strategy` 默认 `fixed`）；P2 不改后端语义；P3 只加一条兜底分支。任一步出问题，前面的步骤仍然成立。
