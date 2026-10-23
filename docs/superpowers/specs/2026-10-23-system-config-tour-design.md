# 系统配置功能引导（System Config Tour）设计

## 目标

给 `/dashboard/system`（系统配置与诊断）加一套**系统级、全局共用**的分步引导：新平台第一次部署好、管理员进来配置时自动播一遍，聚光灯逐个高亮「参数配置」里必须关注的配置项和模型管理入口。状态存 Redis，**全平台只有一份**——第一个人播完（或主动结束），所有人都不再自动播；想看的人可以在 tab 栏右侧点「引导」自己重播。

## 与 EmbedChat 引导的关键差异

| 维度 | EmbedChat Tour | System Config Tour |
|---|---|---|
| 状态归属 | 账号级 `agent:portal_prefs:{user_id}` | 全局单键 `agent:tour:system_config` |
| 自动播门槛 | 所有进入者（桌面端） | 仅 `element:system:config_save`（要配置的人） |
| 断点 | 账号级，跨设备续播 | 仅 sessionStorage，刷新续播、关标签页重来 |
| 跳过语义 | 不标记完成（下次还会播） | **标记完成**（"任何人都能结束播放"） |
| 跨 tab | 无（只有一次弹层展开） | 需要切主 tab + 切配置分组，再等锚点渲染 |
| 重播 | 设置里，账号级 | tab 栏右侧，只给自己播，不改全局 |

## 已确认的产品决策

| 决策点 | 结论 |
|---|---|
| 存储 | Redis 单键，无 TTL；不落库，无迁移 |
| 谁能自动播 | 有 `element:system:config_save` | 
| 谁能结束播放 | 任何能进入页面的人（`menu:system:config`），结束即写全局完成态 |
| 新管理员 | 永不再自动播；感兴趣自己点「引导」 |
| 重播语义 | 只重播给自己看，**不动全局完成态** |
| 移动端 | 不自动播；重播按钮保留 |
| 引导点范围 | tab 概览 1 步 + 模型管理 2 步 + 参数配置 10 步（其中 3 步标题带「（可选）」），共 13 步 |
| 弹窗内字段 | 不讲「添加模型」弹窗内部（遮罩会压住弹窗，且字段有占位提示） |
| 保存后推进 | 不自动推进，用户自己点「下一步」 |
| 老环境升级 | 接受升级后自动播一次（可跳过，跳过即全局完成） |
| 版本化 | `SYSTEM_CONFIG_TOUR_VERSION` +1 即全员重播，不提供「重置全员」按钮 |

不做的事：不做「一键配置向导」批量改值；不做引导中自动打开添加模型弹窗；不做多语言/视频；不改参数配置页现有布局与交互语义。

## 组件与文件

| 文件 | 职责 |
|---|---|
| `frontend/src/components/embed/ChatTour.vue` | 复用；新增可选 `missingGraceMs`（默认 450，本页传 1500），其余不动 |
| `frontend/src/components/system/systemConfigTourSteps.ts` | 13 步定义：`data-tour` 选择器、标题、正文、`tab` / `group` 前置、版本常量 |
| `frontend/src/composables/system/useSystemConfigTour.ts` | 状态机：全局状态读取、自动播判定、sessionStorage 断点、完成/跳过上报、重播 |
| `frontend/src/views/SystemConfig.vue` | 挂载 `ChatTour`；tab 栏加 `data-tour="sys-tabs"`；每个配置项行加 `:data-tour="'config-' + item.key"`；tab 栏右侧重播按钮；步骤前置（切 tab / 切分组 / 等锚点） |
| `frontend/src/components/system/ModelRegistry.vue` | 给「+ 添加模型」按钮与列表行操作区加 `data-tour` |
| `app/api/portal/endpoints/system.py` | 新增 `GET /tour`、`PUT /tour` |

## 步骤定义（13 步）

`step` 为 0 基索引，`step === 13` 表示已完成。每个 step 额外带 `tab` / `group`（进入该步前要先切过去）。"可选"直接写进标题文字，不引入 `optional` 字段，也不改共享组件 `ChatTour.vue` 的气泡模板。

| # | `data-tour` | 所在位置 | 标题 | 正文 |
|---|---|---|---|---|
| 0 | `sys-tabs` | 顶部 tab 栏 | 七个模块 | 系统配置分七块。本次只细讲「模型管理」和「参数配置」，其余几块等用到再看。 | |
| 1 | `model-add` | 模型管理 tab | 先配模型 | 新部署第一件事：点这里添加至少一个模型并**启用**，否则对话和 Embed 都没法用。 | |
| 2 | `model-row-actions` | 模型管理 tab | 测试连接 | 保存后点「测试连接」，跑通再启用。密钥或地址错了，这里会显示完整请求过程。 | |
| 3 | `config-toolbar` | 参数配置 tab | 改完要保存 | 所有参数改完点这里保存（⌘S / Ctrl+S）。左侧分组上的黄点表示该组有未保存的改动。 | |
| 4 | `config-app_public_url` | 参数配置 / general | 对外访问地址 | 填平台的公网地址（协议+域名+前缀）。下载链接、重置密码邮件、MCP 公网地址、报表深链都由它拼出来。 | |
| 5 | `config-agent_context_max_tokens` | 参数配置 / agent_context | 上下文预算 | 发给模型的上下文上限，默认 64k。超了就压缩早期对话或截断，按你的模型窗口调。 | |
| 6 | `config-llm_model_name` | 参数配置 / agent | 默认模型 | 全局默认模型。选一个已启用、且在你的网络环境里能连通的。 | |
| 7 | `config-multimodal_model_name` | 参数配置 / agent | 多模态模型（可选） | 当前模型不支持识图时，用这个模型把图片解析成文字。不配也能用，只是传图片会提示不支持。 |
| 8 | `config-embed_api_url` | 参数配置 / agent | 向量服务地址 | 元数据描述与记忆摘要的向量计算都走这里。可点上方「从模型管理加载」自动填。 | |
| 9 | `config-metadata_provider` | 参数配置 / metadata | 元数据来源 | local 用本地手工字典；ragflow 走语义检索自动取描述。只有用了 RAGFlow 才选后者。 | |
| 10 | `config-knowledge_base_enabled` | 参数配置 / knowledge | 知识库总开关（可选） | 用知识库才打开。关掉后下面的 RAGFlow 参数会隐藏，知识库管理与检索工具也一起禁用。 |
| 11 | `config-sandbox_policy` | 参数配置 / sandbox | 沙箱策略 | local 直接在宿主机跑；docker / k8s 隔离执行；ssh 连远程机；e2b 走云端。按你的部署形态选，下面的参数会跟着变。 | |
| 12 | `config-email_service_enabled` | 参数配置 / email | 系统邮件（可选） | 打开后配置 SMTP，用来发审核通知、重置密码等系统邮件。不配不影响主流程。 |

顺序刻意按**左侧分组导航的顺序**走（general → agent_context → agent → metadata → knowledge → sandbox → email），让引导本身就在教"左边这列是分组导航"。

**条件跳过**：第 2 步（`model-row-actions`）只在模型列表非空时有锚点；锚点等待超时则跳过该步。连续 3 步都找不到锚点时直接结束引导（防止数据异常时长跑）。

## 播放流程

**自动播**（页面 `onMounted` 且 `fetchConfigs()` 完成后）：

```
hasPermission('element:system:config_save')
  && window.innerWidth >= 768
  && location 无 ?tour=0 / ?tour=off
  && configGroups 非空（GET /configs 成功返回，否则锚点必然缺失）
  && (全局 completed_at 为空 || 全局 version !== SYSTEM_CONFIG_TOUR_VERSION)
→ 起播
```

起播前把 `activeTab` 切到 `models`，因为第 0 步讲 tab 栏时应当高亮"模型管理"。

锚点挂载位置（都要落在稳定容器上，不绑元素 id/class）：

- `sys-tabs`：7 个 tab 所在的那层 `inline-flex` 滚动容器
- `config-toolbar`：参数配置右侧已有 `data-config-toolbar` 的 sticky 工具条，在它上面**再加** `data-tour="config-toolbar"`
- `config-<key>`：配置项 `v-for` 的根节点。原来挂在左侧列上的 `id` 提到这个根节点（`config-key-` → `config-item-`），让高亮、搜索滚动、引导锚点三者指向同一个盒子

**进入任意一步**（`@enter`）：

1. step 声明了 `tab` → `activeTab = step.tab`
2. step 声明了 `group` 且与当前分组不同 → `selectConfigCategory(group, false)`（`false` = 不做"滚到分组顶部"，交给 ChatTour 的 `scrollIntoView` 精确落点；同组连续步骤不重复切换）
3. `await waitForAnchor(selector, 1200)`（60ms 轮询；参数配置切分组后是整块重渲染，实测 1 帧~200ms）
4. 成功 → `tourRef.refresh()`；失败 → 跳过该步，并在 `missingSkips` 上计数

**跳过为什么会重复推进**：ChatTour 在锚点缺失时也会在 `missingGraceMs`（1500ms）后 `emit('missing-target')`。页面侧必须在两个入口做同一步去重，否则会一次跳两步：

- `@enter` 里 `waitForAnchor` 超时 → 记一个 `skipGuardIndex = index` 再 `goNext()`
- `@missing-target` 回调里若 `index === skipGuardIndex` 直接返回，否则才 `goNext()`
- 同一 index 只允许跳过一次；连续 3 个 index 被跳过 → `finish()`

**结束**：

- 看完 / 点「完成」→ `PUT {version, completed_at: now, skipped: false}`
- 点「跳过引导」/ `Esc` → `PUT {version, completed_at: now, skipped: true}`（跳过也算结束，这是"任何人都能结束播放"的语义）

写完成态时后端从登录态取 `completed_by`（`user_info["user_id"]`）与 `completed_by_name`（`user_info["real_name"]`，回退 `user_name`），便于在 Redis 里查"是谁关掉的"。

## 重播入口

位置：顶部 tab 栏最右端，与 7 个 tab 同一行。DOM 由

```html
<div class="-mx-1 overflow-x-auto px-1">
  <div class="inline-flex min-w-full gap-1 rounded-lg bg-gray-100 p-1 sm:min-w-0">…7 个 tab…</div>
</div>
```

改为外层 `flex items-center gap-2`，tab 滚动容器 `min-w-0 flex-1`，按钮 `shrink-0`，窄屏横向滚动时按钮固定可达。

按钮：

- 显示条件：`completed_at !== '' && version === SYSTEM_CONFIG_TOUR_VERSION`（**播完才出现**；版本升号后自动隐藏，因为那时会重新自动播）
- 规格：`inline-flex h-7 items-center gap-1 rounded-lg border border-transparent px-2 text-[11px] font-bold text-gray-400 transition-colors hover:border-primary/20 hover:bg-primary/5 hover:text-primary dark:text-gray-500`
- 内容：14px 循环箭头 + 文字「引导」
- tooltip：`重看系统配置引导（只影响自己，不改动其他管理员的状态）`
- 点击：`restart()` —— 切 `models` tab、从第 0 步播、**不写全局状态**

## 持久化与接口

Redis 单键 `agent:tour:system_config`，无 TTL，值为 JSON：

```json
{
  "version": 1,
  "completed_at": "2026-10-23T15:02:11+08:00",
  "completed_by": 7,
  "completed_by_name": "admin",
  "skipped": false
}
```

端点（挂在现有 `/api/portal/system` 下，与 `/configs` 同文件）：

```
GET /api/portal/system/tour
  → {"code":0,"data":{"version":0,"completed_at":"","completed_by":0,"completed_by_name":"","skipped":false}}
  权限：require_permission("menu", "menu:system:config")

PUT /api/portal/system/tour
  body: {"version":1,"completed_at":"2026-10-23T15:02:11+08:00","skipped":false}
  → 写入 completed_by / completed_by_name（取登录态），返回完整状态
  权限：require_permission("menu", "menu:system:config")（**不要求 config_save**）
```

行为约定：

- Redis 不可用 → `503`，前端静默忽略（本次照常播，不写状态，下次还会播）
- Redis 里没有该键 → 返回全默认值（视为未完成），不写入空键
- 不做分布式锁：多人同时完成就是后写覆盖前写，字段语义相同，无害
- 不提供删除/重置端点：全员重播靠版本号

## 前端状态机

`useSystemConfigTour` 暴露 `active / stepIndex / steps / currentStep / shouldAutoPlay / resumeOrStart / goTo / next / prev / finish / skip / restart`，内部：

- 全局状态只在起播前读一次（`GET /tour`），之后不再轮询
- 断点在 `sessionStorage['sysconfig_tour_progress'] = {"version":1,"step":5}`，`persist()` 每步写一次（同步写，无防抖——体积小、频率低，不需要 embedchat 那套 500ms 防抖）
- `finish()` / `skip()` 调 `PUT /tour`，失败不重试（下次进来仍是未完成，可接受）
- 不带 localStorage 兜底：全局语义下"本机记住"没有意义，宁可下次重播

## 顺手修掉的既有缺陷

`selectConfigSearchResult()` 用 `document.getElementById('config-item-' + key)` 定位，但模板渲染的是 `:id="'config-key-' + item.key"`，两个前缀不一致 → **参数搜索的"跳转并高亮"一直静默失效**（不报错、只是不滚动也不高亮）。引导依赖同一套定位逻辑，本设计一并修复：

- 把 `id` 从左侧列移到配置项 `v-for` 根节点，并统一为 `config-item-${item.key}`
- `selectConfigSearchResult()` 的选择器不动，正好命中
- 契约测试断言模板 `id` 前缀与 `getElementById` 前缀一致，防止再漂

## 测试策略

**后端** `tests/api/portal/test_system_config_tour.py`（`no_infrastructure`，FakeRedis）：

- 空 Redis → GET 返回全默认值
- PUT 写入 `version/completed_at/skipped`，并带上登录态里的 `user_id` / 用户名
- PUT 后再 GET 能读回
- Redis 不可用 → 503
- 端点权限依赖是 `menu:system:config`，且**没有** `config_save`

**前端契约** `tests/frontend/test_system_config_tour_contract.py`：

- 13 个步骤 id 与顺序固定
- 每个 selector 形如 `[data-tour="..."]`，且对应 `data-tour` 真的出现在 `SystemConfig.vue` / `ModelRegistry.vue` 源码里
- 自动播条件包含 `element:system:config_save`、桌面端宽度判断、`?tour=0`
- 重播按钮显示条件同时包含 `completed_at` 与版本比对
- 全局端点路径 `/api/portal/system/tour`、Redis 键 `agent:tour:system_config`
- 断点用 `sessionStorage`
- 配置项 `v-for` 根节点同时绑定 `data-tour` 与 `id`，两处的 key 前缀写法一致（模板 `id` 用 `config-item-`，搜索定位 `getElementById` 用同一前缀）

**清单**：`tests/CHECKLIST.md` 补该特性的测试与交付记录。

## 发布与兼容

- 无 DB 迁移、无新表；只新增一个 Redis 键和一个端点
- 已部署环境升级后，管理员首次进入系统配置会看到一次引导（已确认接受）
- 移动端不播、`?tour=0` 可关，出问题有立即止血手段
- 版本升号只改前端常量 `SYSTEM_CONFIG_TOUR_VERSION`，无需后端配合
