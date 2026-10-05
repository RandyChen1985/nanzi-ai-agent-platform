# 🎉 NanZi AI Agent Platform v1.0.18.0 Release Notes

**GitHub Repository**: [RandyChen1985/nanzi-ai-agent-platform](https://github.com/RandyChen1985/nanzi-ai-agent-platform)

v1.0.18.0 版本是一次以 **全链路 AI 可观测性（Langfuse 链路追踪深度集成与直调链路统一埋点）、企业级账号与邮件安全闭环（自主注册申请审核 / 全平台邮件服务 / 忘记密码邮件找回 / 个人中心邮箱管理）** 为核心驱动，并全面推进 **AI 采样温度口径统一与真实上报、侧边栏 Modern Slate Navy 质感重构、仓库全量 LF 行尾治理（.gitattributes）及浏览器自动化交互优化** 的生产级加固版本。

本次变更范围自 `9ae58af6f00a723a80db46c24e720f46525742a2`（含）至 `75ed5d96696dbf586a117b189ff4c2c77df5098e`（含），共 **75 个提交**（其中非 Merge 提交 68 个），涉及 **276 个文件**、**53,576 行新增代码** 与 **12,919 行删除**。

> ⚠️ **升级注意**：
> 1. **数据库迁移**：本版本新增 4 组数据库迁移（MySQL `V161 ~ V164` / PostgreSQL `V62 ~ V65`），涉及 Langfuse 配置表、自主注册配置、用户邮箱字段（唯一索引）及平台邮件服务配置。升级时必须执行迁移脚本。
> 2. **跨平台换行符规范**：仓库已引入 `.gitattributes` 并统一按 LF 归一化。Windows 用户拉取后如遇脚本报错，建议执行 `git checkout-index --force --all` 刷新本地工作区。
> 3. **改密会话失效**：为保障账号安全，用户重置密码或修改密码后将自动失效该用户所有当前会话，需重新登录。

---

## 🚀 Key Features

### 1. 🔭 Langfuse 链路追踪与全链路 AI 可观测 (Langfuse Tracing Integration)
*   **编排轮次端到端追踪**：接入 Langfuse (v4.15.6)，对话轮次统一以 `chat.turn` 根 span 呈现；定制 `should_export_span` 放行平台作用域，确保链路完整上报。
*   **直调链路统一出口埋点**：在 `AgentScopeChatClient` 唯一出口拦截底层大模型直调（ChatBI、总结、意图识别等 50+ 处调用），打上 6 类业务来源标签，捕获首包延迟（TTFT）与精确 Token 用量。
*   **安全脱敏与无感热更新**：支持总开关、内容脱敏上报（仅留工具名与用量）及采样率分桶；密钥对称加密存储，配置变更异步热更新，完全不阻塞主事件循环。

### 2. 🔐 账号自主注册、审核看板与全平台邮件服务 (Auth, Registration & Email Services)
*   **自主注册与管理员审核闭环**：登录页支持自主申请账号，输入期防抖查重；注册后进入待审核状态；管理后台新增「待审核」选项卡与数量徽标，支持一键审核通过并分配角色。
*   **全平台统一邮件服务 (SMTP)**：系统配置新增「邮件服务」独立分组，整合底层发信实现；支持加密协议自适应与连通性自测，审核结果自动邮件触达。
*   **忘记密码（邮件找回）防枚举安全闭环**：公开端点重置密码，Redis 一次性原子核销（有效期 15 分钟）；严格实行防枚举设计（不存在/限流/成功返回一致文案）与三层风控防刷，改密成功吊销全量现有会话。
*   **个人中心与通知中心联动**：个人中心支持自主修改绑定邮箱；通知渠道状态与服务端能力真实对齐，邮件正文全面升级为 Markdown 富文本渲染。

### 3. 🎯 AI 采样温度统一口径与真实上报 (Unified Sampling Temperature)
*   **全链路口径统一**：确立「工具覆盖 > 会话覆盖 > 版本温度 > 全局默认」唯一解析规则。
*   **精确放行 0.0 有效温度**：彻底修正以布尔真假值判定导致「设为 0 度被回退为默认值」的问题；前端工具卡片与审计轨迹真实上报模型实际下发温度。
*   **启动优化**：跳过启动阶段本地向量全量同步，服务启动耗时显著降低。

### 4. 🎨 界面焕新与工程基线治理 (UI Redesign & Engineering Governance)
*   **侧边栏 Modern Slate Navy 重构**：深色模式全面升级为 Slate Navy 渐变质感，品牌名统一收敛为 NanZi Agent / NanZi AI Agent；版本号自适应展示（本地展示 dev，生产展示数字版本）。
*   **全站视觉与体验细节打磨**：全屏弹层遮罩统一挂载至 `body` 根节点，修复特定容器下遮罩遮不满的问题；嵌入式个人中心增加优雅骨架等待与宽限降级；优化 Token 统计与图表排版。
*   **仓库全量 LF 行尾规范化**：引入 `.gitattributes`，将代码与 Shell 脚本统一归一化为 LF，根治 WSL/Linux 跨平台换行符污染与脚本执行报错问题。

### 5. 🌐 浏览器沙箱与交互体验优化 (Browser & Interaction Polish)
*   **面板取帧与决策快照解耦**：前端面板抓帧走轻量独立通道，避免影响 Agent 决策快照槽位与验证码状态机；截图采用 WebP 压缩与会话级 8 帧环形自动回收，页面切后台自动暂停轮询。
*   **验证码可视化与人机接手**：支持缺口目标点与拟人轨迹可视化展示；AI 解算遇阻时平滑提示人工接手，人工操作瞬间自动停止 AI 解算，操作后自动追帧校验。

---

## 🐛 Bug Fixes

### AI 运行时 / 链路追踪
*   **Langfuse 默认过滤器丢弃根 span**：默认上报过滤器仅认自家插桩，重写 `should_export_span` 精确放行平台命名空间。
*   **Langfuse 重建 client 阻塞事件循环**：关闭旧 client 改为后台异步处理，配置无实质变动时跳过重建。
*   **直调 span 篡改外层 Trace 标识**：解耦 `identity_attributes()`，直调 span 仅绑定会话与用户，保留外层 Trace 名称。
*   **采样温度设为 0.0 被当作未设置**：修正真假值判定，数值型解析器正确放行 0.0 严谨温度。
*   **沙箱执行步骤日志被自身子步骤遮挡**：优化执行时间线日志收集结构，确保父子执行流分层透出。

### 账号认证 / 邮件通知 / 安全风控
*   **忘记密码接口存在邮箱枚举攻击面**：统一各分支返回一致的成功消息，发信操作异步执行消除耗时侧信道。
*   **重置密码核销失败却提前作废 Token**：调整为前置校验密码复杂度、保存时刻原子 `GETDEL` 一次性核销。
*   **改密后既有会话依旧有效**：重置密码或修改密码成功后全量吊销旧会话凭据，强制重新登录。
*   **邮件测试接口存在开放代发风险**：连通性测试邮件强制限定发给当前登录用户自身。
*   **自定义 SMTP 加密方式被硬编码为 SSL**：改为根据端口自适应（465 对应 SSL，其他对应 STARTTLS）并支持显式配置。
*   **收件人含有全角标点导致发信失败**：增加全角标点归一化清洗与格式校验。

### 前端交互 / 性能 / 跨平台
*   **全屏弹层遮罩受局部容器 overflow 限制**：统一改用 `Teleport to="body"`，确保遮罩覆盖率 100%。
*   **Token 统计卡片加载慢与图例遮挡**：指标请求改为并行聚合，压缩卡片高度；优化 ECharts 图例居中布局，消除与 X 轴刻度重叠。
*   **WSL/Linux 环境运行脚本提示 `\r` 错误**：通过引入 `.gitattributes` 与全量 LF 转换彻底清除所有 CR 符号。
*   **历史失败测试基线收敛**：清理过期用例，修复 Mock 漂移与跨用例上下文泄漏，大幅压降基线失败清单。

---

## 🗄️ Database Changes (数据库迁移清单)

本版本包含 **4 项** 核心表结构及配置迁移，MySQL 与 PostgreSQL 严格对齐：

| 变更序号 (MySQL / PG) | 涉及表 / 机制 | 变更核心说明 |
| :--- | :--- | :--- |
| **V161** / **V62** | `langfuse_config` / `system_configs` | 新增 Langfuse 链路追踪配置表及权限码 `element:system:langfuse_save` |
| **V162** / **V63** | `system_configs` | 新增自主注册总控开关配置项 `user_registration_enabled`（默认禁用） |
| **V163** / **V64** | `users` | `users` 表新增 `email` 字段并建立全局唯一索引 `uk_ai_agent_users_email` |
| **V164** / **V65** | `system_configs` | 新增全平台邮件服务（SMTP）核心配置组 |

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
docker load -i nanzi-ai-agent_1.0.18.0_linux-amd64_*.tar
cd docker && ./start-nanzi-ai-agent.sh
```

---

## 📋 Changelog (完整提交日志)

* `75ed5d96` docs: 更新微信交流群二维码图片
* `50f94815` fix(browser): 面板取帧与 Agent 快照分家，修复验证码状态机被反复重置、截图泄漏与后台空转
* `eb71947e` docs(checklist): 补充仓库行尾治理（.gitattributes + LF 规范化）的交付与验证记录
* `dc15e945` fix(git): 为 shell 脚本固定 eol=lf，修复在 WSL/Linux 下无法执行的问题
* `82a488f8` chore(git): 新增 .gitattributes 并统一历史遗留文件行尾为 LF
* `1e050a61` docs(readme): 技术栈徽标扩容至 15 个并修掉「空行当成换行」的段落断层
* `ace3f667` fix(knowledge): 运营分析 403 修复（鉴权口径对齐菜单权限）+ README 在线体验入口
* `22de394e` perf(token-stats): 取数并行化、失败清空并提示，压缩统计卡高度
* `eba202ea` perf(chat-logs): 补请求竞态保护与渲染缓存，修详情面板底部空白
* `3f105422` refactor(ui): 组件调试台「发送 INIT_CONFIG」改为「发送登录请求」
* `59693d11` feat(embed): 等待态改为「骨架 + 半透明遮罩 + 用户语言」，并加 10 秒宽限降级
* `35af5db5` chore: 忽略浏览器截图目录 browser-screenshots/
* `d7a3f640` fix(ui): 全屏弹层遮罩统一 Teleport 到 body，修「点 ? 遮罩遮不满」
* `6b274d32` feat(auth): 重置链接改为 15 分钟，并新增进页面预校验避免用户白填密码
* `7e309f0d` feat(ui): 重置密码页顶部加循环文案，打字机逐字打出后退格循环
* `6cb4c011` feat(email): 邮件正文支持富文本，Markdown 渲染为 HTML 并保留纯文本兜底
* `22bb1e69` fix(notification): 个人中心通知开关不再说谎
* `9aca521f` fix(notification): 任务通知渠道就绪判定下沉后端，修掉全局邮件勾不上
* `d61c4735` style(notification): 邮件测试成功不再重复弹 toast，只留卡片提示
* `ac9e522e` feat(notification): send_email 收件人缺省读用户配置，绝不伪造
* `5fdb0e4b` feat(email): 个人中心「修改邮箱」边输边查可用性
* `41061cc8` feat(email): 未配置 SMTP 时「测试连通性」置灰
* `cfb2a005` feat(prompt): 用户画像扩展 5 个只读字段；extra_data 脱敏改为两路共用
* `36fe4ae9` fix(personal): 普通用户不再被误报「平台未启用邮件服务」
* `381cbdd1` style(notification): 邮件测试按钮不再居中，与其它渠道卡片右对齐
* `5c7723b7` feat(auth): 个人中心测试发信，仅全局邮件服务可用时显示
* `498bbeb4` feat(auth): 注册申请通知管理员，随全局邮件服务启用
* `9cd449bd` style(login): 忘记密码与申请账号合并为一排，消除「孤块」排版
* `4944bc74` feat(auth): 忘记密码（邮件找回），含防枚举、三层风控与改密吊销全部会话
* `a64a86c8` fix(users): 用户列表不再显示待审核账号
* `ac97116c` fix(notifications): 邮件连通性测试只发给自己，防止借平台全局 SMTP 任意发信
* `cbe572c9` feat: 用户邮箱字段、全平台邮件服务与审核结果邮件通知
* `ef009cc6` feat: 账号自主注册申请与管理员审核闭环
* `ab423c08` fix(browser): 操作过程时间线覆盖 AI 与人工动作，并修复人工操作后画面不刷新
* `3fa15f98` fix(api): 全局 HTTPException 响应恢复 detail 与自定义响应头，浏览器安装引导不再丢失
* `e2af9385` fix(browser): 验证码拖动基准修正与人工接管优先，消除无谓会话冲突
* `2f110cd4` fix(browser): 人工接管残留不再锁死会话，错误提示与 HUD 遮挡一并治理
* `c4d859b5` fix(browser): 人工接管即刻中止自动解算，避免抢滑块与残留识别提示
* `e509f4ff` feat(browser): 验证码解算过程可视化与人工接手闭环，优化关闭会话与终止反馈
* `07ed283f` feat(browser): 验证码解算重试与可见性治理、触发式验证码支持及排队终止入口
* `d65d2028` feat(frontend): 优化侧边栏底部版本展示布局并修复测试套件断言对齐
* `12ca936f` test(ai): 修复 mock 与实现漂移及路由变更导致的失败，清理失效用例（清单 94→79）
* `8df3f7c3` test(ai): 修复请求上下文跨用例泄漏导致的隐式失败，并同步图表高度契约
* `a1575f1a` test(chart): 同步图表容器高度契约断言 h-64 -> h-72
* `3e7ee91d` test(ai): 既有失败测试基线治理机制与清单，129 条降至 94 条
* `a5bd8152` fix(ai): 修复验证码求解 langchain 残留与工具推送门禁恒假
* `edb08925` fix(chart): 修复 AI 消息中 ECharts 图例遮挡 X 轴刻度及标题居中对齐优化
* `a9a58df4` perf: 启动阶段跳过自动触发本地向量全量同步
* `fc8b22a6` style: 优化左侧菜单 MCP 工具集与 MCP 服务台的区分图标
* `ae09d739` feat(ai): Langfuse 直调 span 补齐 6 类业务来源标签
* `646269e2` chore(brand): 默认品牌名统一为 NanZi AI Agent
* `bde1ba65` docs(ai): 三期直调链路埋点的设计、计划与交付清单
* `0948c21a` feat(ai): Langfuse 直调链路统一出口埋点与业务来源标签
* `a943d295` docs(ai): 补齐自检设计/计划并更正跳转入口结论
* `ef6620d1` feat(ai): Langfuse 链路端到端自检与死配置清理
* `e7bbafb4` fix(ui): 侧边栏版本号精简与防截断优化，本地展示 dev 生产保留数字版本
* `aca95532` chore(brand): 默认品牌名统一为 NanZi Agent，侧边栏品牌区改单行右对齐
* `90eb309a` fix(ai): Langfuse 重建 client 不再阻塞事件循环，旧 client 改为后台关闭
* `7226440e` test(ui): 主题切换按钮契约放宽 padding，跟上侧边栏紧凑化改版
* `19953bbe` style(ui): 侧边栏品牌区紧凑化，通知铃铛与下拉面板同步收窄
* `71011496` feat(ai): 接入 Langfuse 链路追踪，智能体整轮对话可观测
* `589080bb` fix(ai): RAGFlow 超时不再吞掉真实错误，检索/删除超时校准并统一降级为 503
* `cbc69f29` test(ui): 品牌区契约测试改用语义断言，修复侧边栏主题改版后的误报
* `0acf3dd8` fix(ui): 修复工具卡片挂载沙箱准备子步骤时自身命令与输出日志被隐藏的问题
* `f6f91352` docs: 更新微信交流群二维码图片
* `1b064e5c` feat(ui): 优化执行过程时间线 Header 总耗时与动作摘要，支持一键展开明细
* `9d01d4f4` style(ui): 侧边栏深色主题改为 Modern Slate Navy 并重做品牌区
* `9ae58af6` fix(ai): 采样温度统一口径，工具卡片与审计轨迹上报真实下发温度

---

## 👥 Contributors & Acknowledgements

特别鸣谢所有参与 NanZi AI Agent Platform v1.0.18.0 版本需求研讨、代码贡献、架构设计及测试验证的团队成员与社区开发者！我们将持续以高标准打磨生产级 AI 平台能力，为企业与个人开发者提供极致、稳健、开箱即用的智能体中枢。
