# 🎉 NanZi AI Agent Platform v1.0.19.0 Release Notes

**GitHub Repository**: [RandyChen1985/nanzi-ai-agent-platform](https://github.com/RandyChen1985/nanzi-ai-agent-platform)

v1.0.19.0 版本是一次以 **智能体级细粒度资源限定（用户/角色 × 智能体排除机制）、微信小程序微端生态（静默开户 / 内嵌 EmbedChat）、沙箱孤儿后台自动对账与技能生命周期治理、多会话并发防串台与死锁自愈机制、API 工具向导模式（cURL 导入与业务分组治理）、全站 File Viewer 预览与产物确定性签名下载** 为核心驱动，并全面完成 **全站 27 处悬浮遮罩规范化收敛、模型手动切换白名单管控、会话硬超时看门狗系统配置化及 ECharts 窄容器图例自适应** 的生产级里程碑版本。

本次变更范围自 `8edb8107e71fe10704fa709691a2441c04ac5488`（含）至 `eff02898f606157e4320db85fc2fa5e828d71801`（含），共 **143 个提交**（其中非 Merge 提交 120 个），涉及 **526 个文件**、**98,137 行新增代码** 与 **6,211 行删除**。

> ⚠️ **升级注意**：
> 1. **数据库平滑迁移**：本版本新增 8 组数据库迁移（MySQL `V165 ~ V172` / PostgreSQL `V66 ~ V73`），涵盖 API 工具业务分组、版本模型切换锁、资源权限字段扩列、智能体级权限作用域、沙箱孤儿对账配置、微信小程序身份绑定表、微端角色种子与开关、会话生产看门狗超时配置。升级时必须执行迁移脚本。
> 2. **沙箱孤儿对账说明**：新增 `sandbox_orphan_reconcile` 系统配置（默认启用），适用于单副本环境自动清理超过 30 分钟的遗留无主容器/Pod；在多副本/多 Worker 集群部署场景下，请先关闭该配置以避免副本间注册表差异误删在途沙箱。
> 3. **微信小程序端点接入**：如需接入小程序，请在系统配置中启用 `miniprogram_enabled`，并在管理后台「角色管理」中为预置的 `miniprogram_user` 角色分配允许访问的智能体清单。
> 4. **权限缓存增量失效**：优化了权限变更逻辑，管理端更新用户或角色的禁用资源后将直接刷新本地权限缓存，无须像旧版本一样强制注销用户已有登录会话。

---

## 🚀 Key Features

### 1. 🛡️ 智能体级细粒度资源限定与防御加固 (Fine-Grained Agent-Scoped Resource Permissions)
*   **智能体级排除机制（Agent-Scoped Exclusion）**：在既有全局禁用的基础上，新增支持「用户/角色 × 特定智能体」的作用域组合。管理员可灵活限定某个角色仅在指定智能体内禁用特定工具（系统工具、API 工具、MCP 工具）或公共技能，未配置作用域则作为全局兜底。
*   **「禁用资源」管理端全面升级**：权限分配 Tab 命名从「禁用工具与命令」统一改版为「禁用资源」，提供系统工具、API 工具、MCP 工具及公共技能四页签结构；分组卡片引入黑名单命中高亮（有禁用项即红色边框/背景提示），一眼定位被拉黑分组；弹窗增加中文描述、候选来源版本解析与分条高亮说明。
*   **底层执行策略收敛与防御旁路加固**：
    *   统一沙箱 MCP shell 与交互终端命令禁用的单一策略源，彻底封堵只读命令等绕过漏洞；
    *   拦截器同时匹配 MCP 运行时名与系统工具名，解决别名匹配误判与历史遗留问题；
    *   数据库表 `ai_agent_resource_permissions.resource_id` 扩宽至 `VARCHAR(255)`，消除超长 MCP 工具名和公共技能 ID 导致的 422 校验报错；
    *   被禁公共技能在技能清单入口直观置灰只读，并明确展示禁用原因。
*   **模型切换权限与白名单管控**：
    *   智能体版本配置新增「允许在输入框手动切换模型」开关（`allow_model_switch`，默认允许），关闭后输入框彻底隐藏模型切换入口，强制使用当前版本编排模型；
    *   新增角色/用户级「模型白名单」，精细限定各租户/用户在前端输入框允许自主切换的模型池；
    *   模型下拉选择框全面支持快速搜索过滤，覆盖版本配置、工具级覆盖和系统配置默认模型。

### 2. 📱 微信小程序微端生态与移动端体验进阶 (WeChat Mini-Program & Mobile Interaction)
*   **微信小程序微端开箱即用**：
    *   提供开箱即用的小程序微端集成支持，基于微信原生 `wx.login` / `code2Session` 实现无感静默登录与平台账号自动开户；
    *   新增 `ai_agent_wechat_bindings` 表保证 `(appid, openid)` 唯一性并关联平台账号，支持 `unionid` 跨应用关联；
    *   预置 `miniprogram_user` 角色种子与 `miniprogram_enabled` 系统配置开关，智能体可见性基于白名单安全隔离。
*   **移动端与 EmbedChat 交互深度打磨**：
    *   移动端头部菜单重构为紧凑弹层并无缝接入个人中心；
    *   个人中心返回浮标支持触摸拖动手势定位与边缘方向感知收起；
    *   移动端统一底部操作抽屉：数据调用统计与上传附件入口外露，四组分组排布，操作后自动收起；
    *   挂起文案多档分层安抚，避免用户长时间等待产生焦虑。

### 3. 📦 沙箱架构韧性、技能生命周期与孤儿自动对账 (Sandbox Architecture & Orphan Reconciliation)
*   **沙箱孤儿后台自动对账与回收机制**：新增系统配置项 `sandbox_orphan_reconcile`（默认启用），后台周期性按容器/Pod 的 `agentscope.workspace` 标签对账，自动回收未被当前进程注册表持有且物理创建超过 30 分钟的历史遗留沙箱，彻底根治服务重启后 `sleep infinity` 容器/Pod 泄漏问题。
*   **技能沙箱可见性与 .seed 模板隔离**：
    *   会话技能快照统一收敛至平台维护的 `.seed` 隐藏目录，彻底消除硬编码死路径；
    *   公共技能变更后自动按内容指纹失效并重新同步沙箱技能模板；
    *   个人技能源目录只读挂载防篡改；技能压缩包解压增加总大小（上限）与文件数硬限制，防止解压炸弹撑爆磁盘。
*   **K8s 与 Docker 沙箱环境同构对齐**：拉平 K8s 与 Docker 的技能挂载路径与治理口径，补齐 K8s 技能集合令牌透传、执行期心跳守护机制；沙箱空闲回收时长与后端回收器统一同源，UI 浮标不再硬编码；刷新按钮增加响应中反馈。

### 4. ⚡ 高并发多会话防串台、运行态看板与死锁自愈 (Concurrency Control & Session Resilience)
*   **多会话并行与防串台加固**：
    *   彻底治理多会话并行时的跨会话串台与切会话死锁问题；
    *   聊天端全角色会话自隔离，用户切换登录不再串台，审计改走独立审计端点；
    *   浏览器 viewer 连接与调试页会话指针严格绑定会话归属人。
*   **进行中的会话可视化看板与强制终止**：
    *   EmbedChat 顶部新增「进行中的会话」面板与数量徽标，实时识别进行中与疑似僵死轮次，支持一键切换或强制复位终止；
    *   历史会话列表点击当前会话不再重复重载，修复在途生成内容冻结；历史面板打开即拉取最新状态并标识「进行中/疑似僵死」。
*   **会话生产硬超时看门狗系统配置化**：新增系统配置 `agent_chat_producer_hard_timeout_seconds`（默认 600 秒，范围 60~3600 秒，支持 0 关闭），解决整轮超时看门狗与单工具超时的隐式冲突，运维可根据大任务场景自主调节。
*   **会话流程体验流畅度改进**：历史面板引入全新空状态指引组件；新会话创建免确认，瞬间开始；在途生成时新建会话自动中止旧任务释放会话锁；上下文浮标在消息流式生成中保持可点击查看。

### 5. 🪄 API 工具向导模式、分组治理与 Schema 净化 (API Tools Wizard & Grouping Governance)
*   **添加工具「向导模式」**：支持直接粘贴 cURL 命令，系统自动解析 URL、HTTP Method、Header 及 Query/Body 参数，自动生成标准工具 Schema；支持在向导中直接填写测试参数并联调「先试跑」，调通后再一键保存入库。
*   **API 工具业务分组治理**：数据库表 `sys_api_tools` 新增 `group_name` 业务分组字段；智能体编排「工具能力」步骤支持分组视图、批量展开/折叠与分组治理入口，告别所有自定义工具堆积在「其他扩展」的混乱局面。
*   **参数定义可视化编辑与坏 Schema 自动净化**：API 工具参数定义支持可视化表格编辑，长文本自动折行；工具 Schema 净化器自动补齐根层 `required` 等必要字段，修复坏 Schema 废掉整个大模型请求的问题。
*   **MCP 编辑态凭据复用与 SSE 状态保护**：编辑 MCP 工具时，连接测试自动复用已保存的敏感凭据（仅当服务地址变更时才需重新录入）；SSE 会话握手成功前不提前发布会话，提升长连接稳定性。

### 6. 📄 全站 File Viewer 预览、产物签名与弹层规范化 (File Viewer & Artifacts & UI Polish)
*   **全站统一接入 File Viewer**：覆盖工作空间、画布、知识库文档列表与 RAG 引用详情；代码与文档一键预览，保留源码模式自由切换；支持大文件分块流式上传与优雅进度浮层。
*   **产物（Artifacts）预览与确定性签名下载**：生成的代码、表格、文档等产物支持在线预览与下载；下载链接采用确定性签名 Token，修复裸文件名被误当域名的正则问题。
*   **全站弹层遮罩与 Modal 统一治理**：全面清理历史残留的 6 处悬浮 overlay 和 21 处原生弹窗，两份棘轮清单清零；统一通过 Vue Teleport 挂载到 body，消除容器 overflow 导致的遮罩裁剪与层级错乱；通用 Modal 新增全屏最大化能力。
*   **图表与时间线视觉优化**：深度思考步骤在折叠态展示摘要胶囊与字数统计；ECharts 图例优化为默认滚动布局并校准位置，彻底消除窄容器下多系列图例遮挡 X 轴刻度的问题；时间线入参展示重排，入参独立折叠并前置。

---

## 🐛 Bug Fixes

### AI 编排 / 权限 / 运行时
*   **MCP 工具调用名口径不一致**：统一全链路 MCP 工具调用名称解析，修复「未在本智能体注册」误拦截问题。
*   **工具坏 Schema 导致整轮请求崩溃**：增加工具 Schema 自动净化器，自动补齐根层 `required` 并聚合清理警告日志。
*   **模型调用失败无原始错误记录**：补全模型非 2xx 与流式中断时的原始错误留痕，便于精准排查上游供应商故障。
*   **跨用例 Redis 事件循环泄漏**：修复 runtime 与 executors 合跑时跨 event loop 导致的连接污染。
*   **RAG 引用详情刷新丢失**：知识库引用详情写入 Redis 旁路存储，页面刷新后仍可正常展开查看。
*   **知识库检索等待空档**：检索完成后实时转发中间过程事件，消除前后端事件同步空白期。
*   **委派场景引用信息丢失**：修复委派场景下引用详情被过滤白名单误丢的问题，双路径补全 citation 转发。
*   **任务约束指令冲突**：任务级约束指令自动注入 system prompt 末段，明确约束与平台通用规范的仲裁优先级。

### 会话并发 / 隔离 / 稳定性
*   **跨用户登录会话串台**：聊天端全面实现多角色会话自隔离，管理员审计改走独立端点，彻底杜绝会话指针混用。
*   **多会话切换导致死锁与冻结**：重构会话切换与响应信封判定状态机，点击当前历史会话不再重复重载，修复在途生成冻结。
*   **新会话输入框被旧生成锁定**：新建会话时主动停掉在途流式任务并释放会话锁，免去确认弹窗即刻进入输入态。
*   **看门狗与单工具超时隐式冲突**：将 producer 硬超时看门狗外化为系统配置，并消除二次范围判定的边界缺陷。
*   **SSE 会话状态提前发布**：修复 SSE 会话握手成功前提前发布导致的状态不同步。

### 沙箱环境 / 技能系统
*   **沙箱孤儿僵死容器累积**：实现容器/Pod 标签后台对账回收，自动清理脱离注册表的孤儿沙箱。
*   **沙箱技能硬编码路径失效**：重构为平台维护的 `.seed` 快照目录，公共技能变更后基于指纹自动重建模板。
*   **K8s 沙箱配置与 Docker 脱节**：拉平 K8s 技能集合令牌传递、挂载路径与执行期心跳守护。
*   **技能压缩包解压风险**：解压流程强制校验总文件大小上限与文件数上限，防御恶意解压炸弹。
*   **个人技能与公共技能覆写冲突**：增加路径与覆写安全护栏，打通创建覆写、审核预览与导出改名全链路。

### 前端交互 / 弹层 / 图表
*   **全站 27 处弹层遮罩受局部容器限制**：收敛 6 处 overlay 与 21 处原生弹窗，统一 Teleport 挂载到 body 铺满视口。
*   **ECharts 窄容器下底部图例遮挡 X 轴刻度**：默认启用滚动图例，校准 `legend.top="bottom"` 布局判定。
*   **雪花 ID 精度在前端丢失**：用户扩展数据字段中的长整型与雪花 ID 统一序列化为字符串处理。
*   **工作空间右键菜单翻转溢出**：增加右键菜单高度限制，超出视口高度时自动反向翻转展开。
*   **内嵌网页预览被名单误拦截**：内嵌可行性改为由服务端实测 HTTP 响应头（X-Frame-Options/CSP）判定，放行正常页面。

---

## 🗄️ Database Changes (数据库迁移清单)

本版本包含 **8 项** 核心表结构及配置迁移，MySQL 与 PostgreSQL 严格对齐：

| 变更序号 (MySQL / PG) | 涉及表 / 机制 | 变更核心说明 |
| :--- | :--- | :--- |
| **V165** / **V66** | `sys_api_tools` | 新增 `group_name` 业务分组字段，支持自定义 API 工具按业务域归组管理 |
| **V166** / **V67** | `ai_agent_versions` | 新增 `allow_model_switch` 开关，支持锁定模型并隐藏前端切换入口 |
| **V167** / **V68** | `ai_agent_resource_permissions` | `resource_id` 扩宽至 `VARCHAR(255)`，防止超长工具名/公共技能 ID 报 422 错误 |
| **V168** / **V69** | `ai_agent_resource_permissions` | 新增 `agent_id` 作用域列与复合索引，支持「用户/角色 × 智能体」排除机制 |
| **V169** / **V70** | `system_configs` | 新增 `sandbox_orphan_reconcile` 配置项（默认开启），支持沙箱孤儿自动对账回收 |
| **V170** / **V71** | `ai_agent_wechat_bindings` | 新建微信小程序身份绑定表，建立 `(appid, openid)` 与平台账号唯一映射 |
| **V171** / **V72** | `ai_agent_roles` / `system_configs` | 预置 `miniprogram_user` 角色种子与 `miniprogram_enabled` 微端开关配置 |
| **V172** / **V73** | `system_configs` | 新增 `agent_chat_producer_hard_timeout_seconds` 配置，将对话硬超时看门狗配置化 |

> 📌 **迁移执行**：MySQL 执行 `bash db-prod/apply-sql.sh`；PostgreSQL 执行 `bash db-prod-pg/apply-sql.sh`。

---

## 📖 Upgrade Guide (版本升级指南)

### 方式一：源码拉取升级（开发与虚机部署）

1. **拉取最新代码并同步**：
   ```bash
   git fetch origin && git checkout main && git pull origin main
   ```

2. **数据库平滑迁移**：
   ```bash
   # MySQL
   bash db-prod/apply-sql.sh

   # PostgreSQL
   bash db-prod-pg/apply-sql.sh
   ```

3. **依赖更新与启动**：
   ```bash
   poetry install --no-root
   ./dev.sh
   ```

---

### 方式二：Docker 容器化升级（生产环境推荐）

```bash
# 1. 执行数据库平滑迁移
bash db-prod/apply-sql.sh  # PG: bash db-prod-pg/apply-sql.sh

# 2. 导入 Release 镜像包并重启服务
docker load -i nanzi-ai-agent_1.0.19.0_linux-amd64_*.tar
cd docker && ./start-nanzi-ai-agent.sh
```

---

## 📋 Changelog (完整提交日志)

* `eff02898` feat(chat): 历史面板空状态引导、新会话免确认、上下文浮标生成中可点
* `d4a6bf5e` fix(chat): 点历史列表「当前会话」不再重载，修在途内容冻结
* `0ea8e72c` Merge pull request #239 from RandyChen1985/dev-agentscope
* `a7f1eb7b` fix(chat): 历史面板打开即取最新，并补「进行中 / 疑似僵死」标识（含移动端入口）
* `1377fdca` fix(chat): 修多会话并行的跨会话串台与切会话死锁，收敛会话级复位入口
* `f6334983` Merge pull request #238 from RandyChen1985/dev-agentscope
* `c112618b` docs(checklist): 补登本次 7 个提交的交付与测试闭环
* `b9b2e573` fix(chat): 新建会话时停掉在途生成，修复新会话输入框仍锁定
* `3eab5014` test(chat): 补齐「进行中的会话」点条目切换的契约断言
* `2e2148d4` feat(chat): 快捷指令栏生成中只锁会撞会话锁的指令
* `b6e1c3ee` feat(chat): EmbedChat 顶部「进行中的会话」入口与强制终止
* `0b61f0e7` fix(agent): 看门狗读取侧不再二次判定范围
* `4b46fd17` feat(agent): 会话恢复卡片、模型流空闲守卫与看门狗配置化
* `18d7d2ae` feat(skills): read_skill_instruction 支持按需读取技能包内资源并附清单
* `c0ec620c` Merge pull request #237 from RandyChen1985/dev-agentscope
* `353073d9` chore(assets): 更新微信交流群二维码图片
* `36424e77` feat(embed): EmbedPersonal 返回浮标支持方向感知收起与触摸拖动
* `295d7be0` fix(embed): 修 EmbedPersonal 返回浮标的尺寸、可读性与边框
* `5d31c567` feat(embed): 移动端头部菜单收纳为紧凑弹层，并接入个人中心
* `422806a5` docs(miniprogram): 修正集成指南的 403 文案合并缺陷，补齐页面侧错误翻译
* `870be27d` Merge pull request #236 from RandyChen1985/dev-agentscope
* `c3e46e01` docs(miniprogram): 补微信小程序微端集成指南（产品文档）
* `3d73f197` fix(system-config): 布尔配置补胶囊开关，并加契约测试防同类遗漏
* `959dcaa7` feat(miniprogram): 微信小程序微端——静默自动开户 + 内嵌 EmbedChat（平台侧）
* `ddb44341` Merge pull request #235 from RandyChen1985/dev-agentscope
* `6db0b419` feat(tools): 添加工具支持「向导模式」，粘贴 curl 即可配出工具并先试跑
* `7541bd11` test(known-failures): 移出已修好的 2 条基线，纠正 2 条污染项的分类
* `2a4f9f49` fix(sandbox): 刷新按钮补「刷新中」反馈，转圈不再只跟容器启停状态
* `865f7de1` Merge pull request #234 from RandyChen1985/dev-agentscope
* `a3e60e69` docs(checklist): 补记沙箱空闲时长同源显示一行
* `5c80c921` fix(chat): 沙箱空闲回收时长与回收器同源，浮标不再写死 30m
* `75f09e65` fix(skills): 沙箱技能治理第四轮审计修复（路径口径/指纹/只读入口/发布状态机）
* `31b2a89b` fix(web-preview): 内嵌可行性改由服务端实测响应头判定，修掉 baidu.com 被名单误拦
* `b8b80c1f` fix(sandbox): 修复 K8s 技能集合令牌漏传，补执行期心跳与孤儿沙箱对账
* `fac138e5` fix(sandbox): 沙箱物理身份对齐技能集合，权限变更即失效，并补技能下架能力
* `f0439c79` Merge pull request #233 from RandyChen1985/dev-agentscope
* `3d375f25` fix(chart): 默认滚动图例，消掉窄容器 + 多系列的图例换行遮挡
* `ffc514b0` fix(chart): 底部图例遮挡 X 轴刻度，修掉 legend.top="bottom" 被误判为顶部图例
* `9a7ba9ec` fix(ui): 收敛 6 处 overlay 与 21 处原生弹窗，两份棘轮清单清零
* `5eefb844` Merge pull request #232 from RandyChen1985/dev-agentscope
* `3b7e698f` fix(skills): 重命名改用输入弹窗，技能详情抽屉遮罩铺满视口
* `31017c23` fix(sandbox): 沙箱缓存键纳入技能集合，修掉与智能体白名单脱钩
* `fa019794` fix(frontend): 技能 enabled 判定统一到与后端同源的真值白名单
* `3488da06` fix(sandbox): 技能 seed 失效判据纳入个人技能库与禁用集合
* `b38de313` fix(skills): review 第三轮——补路径与覆盖护栏，打通创建覆写 / 审核预览 / 导出改名
* `0e1815cb` fix(skills): 压缩包解压补总量与文件数上限，避免高压缩比归档撑爆磁盘
* `cd05a621` fix(skills): 补齐个人技能 .seed 兼容的漏改路径，并按布局防护公共技能副本
* `cbecba20` fix(skills): 个人技能 .seed 布局兼容 + K8s 沙箱判断对齐，并修掉「已创建」横幅重复弹出
* `78f9a9b9` fix(sandbox): K8s 沙箱技能路径与 Docker 对齐（挂载 + 治理 + 口径）
* `5d226133` fix(skills): 目录清单与提示词点明公共技能在沙箱的隐藏 seed 子目录
* `477125b7` Merge pull request #230 from RandyChen1985/dev-agentscope
* `5c4f1ab3` fix(skills): 会话技能快照改由平台维护 .seed，删除技能清单死链路
* `4597428a` fix(skills): 公共技能变更后自动重建沙箱技能模板（指纹失效 + 分区同步）
* `184ab5f6` fix(skills): 技能沙箱可见性修复——提示去死路径 + 个人技能源目录只读挂载
* `078b4053` feat(timeline): 折叠态深度思考行展示摘要与字数胶囊
* `6001c193` feat(admin): 禁用资源页四页签化 + 功能限定弹框说明区改版
* `02434c66` Merge pull request #229 from RandyChen1985/dev-agentscope
* `56e11a1d` docs(checklist): 去掉本地开发环境特有数据，改为通用表述
* `3a776455` fix(permission): 禁用策略口径统一与安全旁路修复（三批对抗性审查）
* `28215bb7` fix(permission): 智能体级禁用加固——MCP 全名落库、跨库探测、写入校验
* `23657fc2` fix(skills): 技能清单 forbidden 标记接入智能体作用域，被限定技能直接置灰
* `2bfb6d41` fix(permission): 功能限定弹框个人技能改为置灰只读并标注原因
* `cab68bc9` test(tests): 测试数据用例级兜底清理，并修复既有失败与同名冲突
* `c090e5e7` fix(permission): 功能限定弹框修正 MCP 描述与分组名解析，顶部说明分条高亮
* `ccc7bf63` feat(permission): 功能限定弹框补中文描述与候选来源版本
* `a0c53e9c` feat(permission): 智能体级功能限定（用户/角色 × 智能体，排除机制）
* `020f9dc6` fix(auth): 清权限缓存不再吊销会话，管理端重置凭据补吊销，RAGFlow 上游 401 不再透传
* `5c77ea88` fix(permission): 命令禁用覆盖沙箱 MCP shell 与交互终端，收敛为单一策略来源
* `0c24f2df` fix(admin): 禁用资源页直配口径、徽标口径、命令分隔符与文案修正
* `e1eb11a7` fix(permission): 禁用资源审查修复，堵住命令只读绕过与被禁技能可读等旁路
* `2e1cb8a7` feat(skills): 被禁技能在技能入口置灰不可选，静默失效改为明确提示
* `476da521` feat(admin): 禁用资源支持禁用选定的公共技能，被禁用户全链路不可用
* `682307d4` feat(admin): 禁用资源分组卡片有选中即变红，一眼定位已拉黑的组
* `a97646c1` feat(admin): 分配权限 tab 统一徽标，「禁用工具与命令」改名「禁用资源」
* `f1eafc63` fix(tools): 禁用工具拦截同时匹配 MCP 运行时名，别名不再误判为历史遗留
* `449a871b` feat(tools): 禁用工具目录候选后端化，并改为三层分组折叠渲染
* `c8d1a0c2` Merge pull request #228 from RandyChen1985/dev-agentscope
* `9b020a4f` docs(checklist): 补充 MCP 编辑态凭据复用与上下文计量修正的测试清单
* `c0cf48f7` fix(chat): 上下文浮标改用模型实测校准值，窗口/预算换算统一 ÷1024
* `86e3f959` feat(mcp): 编辑态连接测试复用已保存凭据，地址变更后不复用
* `60e0f24d` Merge pull request #227 from RandyChen1985/dev-agentscope
* `8c6d49aa` feat(auth): 角色/用户模型白名单，限定输入框手动切换的模型范围
* `5e8c1553` feat(ui): 模型下拉支持搜索，覆盖版本配置 / 工具级覆盖 / 系统配置默认模型
* `979a9b2c` feat(agent): 版本级「允许在输入框手动切换模型」开关，关闭后输入框隐藏模型切换入口
* `a0d1ac87` Merge pull request #226 from RandyChen1985/dev-agentscope
* `ed2ca41a` chore(mcp): 去掉同步自同事改动里的来源标记，保留解释性注释
* `14e714bf` fix(mcp): SSE 会话状态防护与降级兜底：握手成功前不发布会话
* `9c95571c` fix(ui): 业务弹窗遮罩统一 Teleport 到 body，修「编辑角色」遮罩只盖内容区
* `d120ebb8` Merge pull request #224 from RandyChen1985/dev-agentscope
* `42285be7` fix(chat): 移动端「▤ 数据」补兜底：无调用统计时仍进抽屉，判据收敛为单一函数
* `1b754f20` fix(chat): 移动端操作入口两处收尾：数据按钮按徽标分派 + 继续分析组件在 EmbedChat 恒按桌面形态
* `11eb7303` Merge pull request #223 from RandyChen1985/dev-agentscope
* `300590cf` docs(task): 任务约束指令设计文档与实施计划，并登记 CHECKLIST
* `ffe231c3` feat(task): 任务约束指令表单字段、填写引导与列表标识
* `3f67bf85` feat(task): 任务约束指令注入 system 末段，任务级输出约束可与平台规范裁决
* `8b3614d7` feat(chat): 移动端操作栏重排 + 统一底部操作抽屉：数据/文件入口外露、四组分组、动作后自动收起
* `23cc4ab9` fix(chat): 时间线入参展示重排：入参独立折叠、前置到子步骤之前、去盒子改细竖线
* `8cb8f6e5` Merge pull request #222 from RandyChen1985/dev-agentscope
* `35c49ecb` fix(artifacts): 外部 review 三条建议的核实与处理（画布丢 documentMeta、产物链接正则、兜底时区）
* `e7ae6145` feat(artifacts): 产物预览/下载 + 下载 token 确定性签名 + 裸文件名不再被当成域名
* `4e2d2d78` test(chat): 会话采用链路补跨层回归（真实后端响应喂真实前端判定）
* `0cb643ac` fix(chat): 会话采用按真实响应信封判定，并放行「混着他人轮次但有自己的轮次」的会话
* `cdf6aace` fix(chat): 浏览器 viewer 连接绑定会话归属人，调试页会话指针按身份隔离
* `c6e9d554` fix(chat): 换用户登录不再串会话：聊天面全角色自隔离，admin 审计改走独立端点
* `4d9f1453` test(ai): 修复 runtime 与 executors 合跑时的 Redis 跨 event loop 泄漏
* `ddeb9fe3` fix(ai): 修正工具 schema 净化方向（补根层 required），并把补 required 的日志聚合为一条
* `09c97c1e` Merge pull request #221 from RandyChen1985/dev-agentscope
* `2a5e4149` feat(synthesis): 合成 prompt 增加引用标记保留契约（缓解，非根治）
* `2e1fb52f` chore(assets): 更新微信群二维码图片
* `9f2d7728` fix(delegate): 委派场景的引用详情被白名单丢弃，两条路径都补上 citation 转发
* `efe1e4ef` fix(ai): 工具 schema 预检与净化，坏 schema 不再废掉整轮请求
* `f2b664c6` Merge pull request #220 from RandyChen1985/dev-agentscope
* `a9c63d83` fix(ui): 个人中心两端都去掉 Dashboard 叠加的外层左右边距
* `38876503` fix(ai): 模型调用失败留痕，非 2xx 与流式中断都记录上游原始错误
* `3ee65faa` fix(chat): 挂起文案第三档不再猜原因，tool 第二档措辞拉开层次
* `7bee9ae1` Merge pull request #219 from RandyChen1985/dev-agentscope
* `b2b2ea22` feat(chat): 挂起步骤按类别给安抚文案，指示器与沙箱解耦
* `d22ad67a` fix(rag): 知识库检索完成后实时转发过程事件，消除等待空档
* `61c69025` feat(embed): /embed/personal 支持 ticket 自主兑换，跨站 iframe 可用
* `5cdaf541` Merge pull request #218 from RandyChen1985/dev-agentscope
* `f6b723da` fix(rag): 知识库引用详情落 Redis 旁路存储，刷新后仍可展开
* `0ea96df3` docs(readme): 文档索引补入嵌入集成指南，API 指南描述归位
* `eee41b16` fix(canvas): 画布让位方位跟随停靠侧，钉住默认值按打开来源区分
* `72ec02ea` Merge pull request #217 from RandyChen1985/dev-agentscope
* `032fcc4b` fix(tools): 隐式工具描述不再推荐未注册工具，拦截文案补出生效版本
* `8978f0be` feat(ui): 通用 Modal 新增 opt-in 的最大化能力，知识库预览接入
* `1ed84abf` feat(workspace): 上传队列浮层全部成功后自动淡出，失败与取消保留
* `4c11c989` feat(rag): 知识库文档选择列表的 PDF 改用 file-viewer 预览
* `37192220` feat(workspace): 上传进度与状态反馈 + 服务端分块流式落盘
* `4d5681dd` feat(canvas): 画布文本与代码格式默认走 file-viewer 预览，保留「源代码」标签页
* `6b8183d8` docs(checklist): 登记前端构建内存上限对齐的测试用例
* `6518ad98` fix(docker): 前端构建堆上限 3048 -> 4096，修复容器内 vite build OOM
* `25a61a1f` Merge pull request #216 from RandyChen1985/dev-agentscope
* `8a462b5b` fix(canvas): 修复从工作空间打开文本文件时被误判为文档预览
* `63164e17` fix(canvas): 修复文本格式在画布被误判为文档预览，并将操作按钮上移 header
* `a20d0d29` fix(frontend): 用户扩展数据值统一按字符串序列化，修复雪花 ID 精度丢失
* `a3e2510b` feat(frontend): 文档预览接入 file-viewer，覆盖画布、RAG 引用与知识库
* `83c76e1e` fix(frontend): 注册表列宽只留名称列自适应；工作空间右键菜单限高后再翻转
* `557cd5d3` feat: API 工具参数定义支持可视化编辑，注册表长文本折行
* `7365f792` feat: API 工具注册表新增分组视图与分组治理入口
* `5e613834` feat: 工具能力分组批量展开/折叠 + API 工具业务分组归类
* `878c2401` Merge pull request #215 from RandyChen1985/dev-agentscope
* `30ec4686` fix(frontend): 配置与发布抽屉遮罩铺满视口，补上契约的等价写法漏检
* `8edb8107` fix(ai): MCP 工具调用名口径统一，修复「未在本智能体注册」误拦

---

## 👥 Contributors & Acknowledgements

特别鸣谢所有参与 NanZi AI Agent Platform v1.0.19.0 版本架构重构、攻防审查、多端适配与测试验证的团队成员与社区开发者！我们将继续深耕生产级智能体运行时，为企业和开发者提供安全、稳定、高性能的现代化 AI Agent 基础设施。
