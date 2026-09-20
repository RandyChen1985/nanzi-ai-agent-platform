# 🎉 NanZi AI Agent Platform v1.0.16.0 Release Notes

**GitHub Repository**: [RandyChen1985/nanzi-ai-agent-platform](https://github.com/RandyChen1985/nanzi-ai-agent-platform)

v1.0.16.0 版本是一次以 **门户会话令牌化与凭据全域收敛（浏览器彻底不再持有长期 API Key）、会话可吊销与审计脱敏加固、嵌入凭据实例级隔离、数据集质量治理分体系与元数据漂移 AI 语义治理、K8s 沙箱零配置共享用户工作区、双数据库迁移工具链全能力升级** 为核心驱动，并全面推进 **登录防暴破锁定与安全响应头、上下文管理 token 口径统一、Agent Debug 执行详情抽屉、内嵌对话与模型选择器交互精修、Prompt Cache 多厂商指标归一化** 的重量级安全与治理版本。

在本次更新中，平台完成了自建库以来最彻底的一次**身份凭据体系重构**：登录后浏览器只持有不可猜测、可即时吊销的不透明会话令牌 `sess_<random>`，真实 API Key 不再出现在任何一种响应体、`localStorage` 或前端注入代码中；配套补齐了会话按用户成批复核吊销、审计日志凭据脱敏、嵌入票据来源校验失败不消耗票据、同一页面多嵌入实例身份互不串号等一系列纵深防御；同时为 API Key 引入 `nzi_` 可辨识前缀，让凭据在日志与抓包中一眼可辨。在数据治理领域，推出**数据集质量治理分**（类型一致性 30 + 结构覆盖 25 + 备注完备度 25 + 表完整性 20）与**元数据漂移 AI 语义治理**闭环，覆盖新增字段语义推断、备注缺失巡检、动作与漂移类型白名单校验、单条 SAVEPOINT 隔离批量处置；在沙箱与运维侧，Kubernetes 沙箱实现**零配置自动探测并共享平台数据卷**、命名空间默认对齐平台、运维脚本按正向标签彻底划分平台与沙箱视角；此外，双库迁移脚本升级为一套带交互式确认、断点续跑与原生客户端模式的工程化工具链。

本次变更范围自 `f9a651715000a009820046a78617c048abfdfdf8`（含）至 `6ead40170b3e0ded59885ac2b2ed978c5cc86a10`（含），共 **94 个提交**（其中非 Merge 提交 85 个），涉及 **219 个文件**、**21,591 行新增代码**与 **3,299 行删除**。

> ⚠️ **破坏性变更提示**：本次门户会话 Cookie 由 `admin_token` 正式更名为 `portal_session`（遵循「只认新名、不加兼容分支」的既定迁移风格），**升级后所有已登录用户需重新登录一次**；同时「代客签发嵌入凭证」由借用「获取用户画像」API 权限改为独立的 `POST:/api/v1/embed/tickets` 权限码，**此前依赖旧权限的角色需管理员在后台重新授权**。详见下方 Upgrade Guide。

---

## 🚀 Key Features

### 1. 🔐 门户会话令牌化与凭据全域收敛：浏览器彻底告别长期 API Key (Opaque Portal Session & Credential Convergence)
*   **不透明会话令牌 `sess_<random>` 全链路落地**：
    *   登录成功后由服务端签发可吊销的不透明随机令牌，写入既有 `auth:api_key:{sha256(token)}` 键空间——**真实 API Key 不再下发到前端**，令牌落入 Redis 哈希（含 `user_id` / `role` / `status` / `session_type` / `verified_at`）并设 24 小时 TTL，活跃请求自动滑动续期；
    *   `PORTAL_SESSION_TOKEN_ENABLED` 开关（**默认开启**）支持秒级回退：置 `false` 即恢复旧行为（Cookie 直存真实 Key），仅在 Redis 不可用等异常场景需要；
    *   登录下发点统一为 `_issue_portal_session_cookie`（SSO / 密码 / API Key / 2FA / 重置共 5 处），并以契约测试锁定调用点数量，避免新增登录路径时遗漏；Redis 不可用时 Fail-Open 回退原凭据，保证登录永不因缓存层故障而不可用。
*   **登录响应体不再回传真实 API Key（P3 实施）**：
    *   `POST /auth/login`、`/auth/sso/login`、`/auth/login/2fa` 三处响应体移除 `api_key` 字段，凭据只经 HttpOnly Cookie 下发，彻底消除「任何能调用登录接口的人都能拿到长期密钥」以及凭据在日志、代理、抓包中留存的隐患；
    *   保留 `GET /api/v1/users/profile`、`POST /auth/api-key/reset`、`GET /management/api-key/{id}` 三处按业务必需的显式取 Key 通道，并以 6 项源码级契约测试锁定边界。
*   **API Key 引入 `nzi_` 可辨识前缀**：
    *   原先 Key 由 `secrets.token_urlsafe(32)` 直出、无任何前缀，在日志与抓包中无法与 `sess_` / `emb_ses_` / `emt_` 等凭据区分；现统一加 `nzi_` 前缀（随机体仍为 43 字符 / 256 bit，**不减熵**，总长 43→47）；
    *   `verify_api_key` 不解析格式、只做 SHA256 查库，**历史无前缀 Key 继续有效**；刻意不引入「前缀不符即拒绝」的前置校验，避免失效外部集成中已配置的 Key；
    *   重置 API Key 弹窗同步加宽（`max-w-md`→`max-w-lg`）以容纳变长的等宽文本，保留 `break-all` 作为超长兜底。
*   **前端凭据去本地化（Credential De-localization）**：
    *   移除全局与 19 个文件共 **27 处页面内凭据注入**，鉴权统一交由同源 HttpOnly Cookie；停止写入 `localStorage.api_key`，登录态改以 `/auth/me` 响应为准确认；
    *   新增 `userSession.ts` 收敛 `persistUserInfo` / `clearUserSession` 单一职责，常态化清理旧版残留凭据，统一全站登出清理路径；
    *   嵌入场景新增 `EmbedService.issue_session_from_user`：由已鉴权用户签发 `emb_ses_` 短期令牌，**不再透传 `user["api_key"]`**，长期 Key 用后即弃。
*   **凭据改造对外文档全量同步**：ChatBI HTTP API、嵌入集成指南、FAQ 等活文档统一对齐「Cookie 不承载真实 API Key、登录不回传凭据、外部集成仍走 X-API-Key / Authorization」的凭据形态说明。

### 2. 🛡️ 会话生命周期治理：成批吊销、审计脱敏与嵌入凭据实例级隔离 (Session Revocation, Audit Masking & Embed Isolation)
*   **会话令牌可吊销（高危缺陷修复）**：
    *   会话缓存在 `auth:api_key:{sha256(token)}`，单向哈希无法由 `user_id` 反查，原 `invalidate_user_auth_cache` 只删真实 Key 缓存键，对已签发会话完全无效；叠加 24h 滑动续期，禁用 / 删号 / 改角色 / 重置 Key 后旧会话仍长期可用；
    *   现于签发时同步登记 `auth:user_sessions:{user_id}` 索引集合，`revoke_sessions_for_user` 按索引 `SMEMBERS` 成批 `DEL` 并清索引，`revoke_portal_session` 同时接受 `sess_` 与 `emb_ses_`；
    *   新增 `verified_at` 字段与 **60 秒回源复核**：会话缓存命中后按间隔复核 `status` / `role` 并刷新缓存与 TTL，作为吊销索引之外的兜底（防有入口直接改库）；查库异常时 Fail-Open 沿用缓存，避免把在线用户整批踢下线。
*   **审计日志凭据脱敏（高危加固）**：
    *   审计中间件捕获全部响应体写入 `ai_agent_access_logs` 且详情接口原样回显，而脱敏名单缺 `session_token`，导致令牌明文入库、拿到即可冒用；
    *   键名补 `session_token` / `admin_token` / `embed_session` / `ticket` 及 camelCase 变体（`ticket` 仅精确匹配，避免误伤 `ticket_id`），并新增**值级兜底正则**覆盖 `emb_ses_` / `sess_` / `emt_` / `nzi_`，可拦截 query 串与自由文本中的裸令牌。
*   **嵌入凭据实例级隔离，消除身份串号**：
    *   修复「同一 tab 内多个同源 iframe 共享 `sessionStorage`」导致的覆盖问题：实例 A 刷新后读到实例 B 的令牌，以他人身份进入会话历史；
    *   存储键按 `instance_id` 分桶（`nzi_embed_session_token:<instanceId>`），与既有 `conversationStorageKey` 隔离口径一致；**读取时刻意不回退**无实例维度的旧键——回退正是串号来源，宁可视为无凭据；
    *   `logout` 补齐 `embed_session` 清理：此前只认并只删门户 Cookie，嵌入会话服务端不吊销且 Cookie 残留可被认证层回落使用，造成身份残留。
*   **票据兑换安全性与可用性双修**：
    *   来源校验失败不再消耗票据：由「先 `GETDEL` 核销 → 再校验 `allowed_origins`」改为「`GET` 只读校验 → 通过后 `GETDEL` 核销并比对内容」，一次域名白名单没配对不再把票白白吃掉、掩盖真实原因；`GETDEL` 原子性 + 内容比对保证防重放强度不削弱；
    *   前端按状态码分类失败文案：`403` 归为 `origin_not_allowed`（提示核对 `allowed_origins` 与访问地址，需含协议与端口），其余归为 `ticket_invalid`，真实原因经 `resolveTicketFailureReason()` 一并透传宿主 `INIT_FAILURE.reason`，便于宿主自动重签；
    *   前端增加 in-flight 与已消费去重，消除 URL 与宿主 `postMessage` 同票并发兑换时必有一方误报「凭证已失效」的问题。
*   **内嵌宿主握手态语义拆分**：区分「等待宿主下发凭据」与「凭据失效」——严格模式下的中性蓝色等待态不再误渲染为红色「登录状态已失效」，并消除 `INIT_CONFIG` 切换瞬间因 `hasPermission` 尚未就绪而闪出一帧红遮罩的缺陷；顺带修复 `initConfigReceived` 非响应式（普通 `let` 导致 computed 永不追踪）的潜在缺陷。

### 3. 🔒 认证韧性加固、安全响应头与权限治理规范化 (Auth Resilience, Security Headers & Permission Governance)
*   **登录与会话防暴破锁定**：引入基于 Redis 的 **5 次 / 15 分钟**失败锁定，具备 Fail-Open 容灾与 TTL 兜底防永久锁死机制。
*   **纯 ASGI 安全响应头中间件**：在 `http.response.start` 阶段注入 `X-Content-Type-Options: nosniff`、CSP 与 **30 天 HSTS**，流式响应零损耗，并放行 `/embed/` 路由以避免干扰跨站嵌入。
*   **反向代理自适应 Cookie Secure**：优先解析 `X-Forwarded-Proto` 首跳，兼容 TLS 终止在入口网关的部署形态，同时保持纯 HTTP 环境可正常登录。
*   **全局 CORS 配置卫生**：检测到通配符 `*` 与 `allow_credentials` 冲突时在启动日志中显式告警，`env.example` 新增 `ALLOWED_ORIGINS` 配置说明与示例。
*   **代客签发权限码规范化**：
    *   「代他人签发嵌入凭证」原借用「获取用户画像」API 权限判定，语义无关且管理员无法直观理解；先迁移为 `element:agent:embed_ticket_issue`，最终归位为**专用 API 权限码 `POST:/api/v1/embed/tickets`**（分组「V1 嵌入服务」），与「获取用户信息」并列；
    *   遵循「只认新权限码、不加兼容分支、不加迁移 SQL」策略，旧的 API 权限回归「获取用户信息」本义；
    *   `GET:/api/v1/users/profile` 显示名由「获取用户画像」更正为「获取用户信息」（权限 ID 不变，已分配权限不失效）；
    *   角色管理与编辑用户两处权限卡片新增**请求方法彩色徽章与 mono 字体接口路径行**（仅 API 资源显示，带 `v-if` 守卫）。
*   **前端会话抗抖动退避**：Dashboard 会话校验区分「认证失败」与「网络 / 5xx 故障」，避免服务重启期间被误踢登出。
*   **门户内嵌 chat 加载态修复**：凭据收敛后 `Chat.vue` 不再向 iframe 传递 token，而 `EmbedChat` 在无 token 分支仍是 `warn + return`，导致页面永久卡在骨架屏；现非 strict 模式照常下发初始化配置并回落同源 Cookie 校验，strict 调试模式仍显式失败，不允许静默回落。

### 4. 🏢 数据集质量治理分体系与元数据漂移 AI 语义治理 (Dataset Quality Score & AI Semantic Drift Governance)
*   **数据集质量治理分（0-100 分，可解释）**：
    *   新增纯函数评分服务 `compute_quality_score`，按 **类型一致性 30 + 结构覆盖 25 + 备注完备度 25 + 表完整性 20** 计算总分与可解释维度明细；
    *   `meta_datasets` 新增 `quality_score` / `quality_breakdown` / `quality_scored_at` 三字段（`V155` / `V56`），单数据集巡检与全库巡检结算时写入；`GET /datasets` 自动带出，前端卡片与列表展示徽标并**支持按质量分排序**；
    *   **降级比对不再给出满分（重要修正）**：物理列读取失败（权限 / 网络异常）时不再因 mismatch/new/stale 全 0 而判定 100 分「优秀」，新增 `unreadable_tables_count` 统计与 `degraded` / `degraded_reason` 标记，前端以 ⚠ 徽标与悬停说明提示「分数基于不完整比对」；
    *   **评分口径统一**：单数据集对 0 张纳管表的分支同样结算并提交分数，与全库路径一致，消除「同一数据集分数取决于入口」的矛盾；全库巡检对每个数据集独立 `try/except`，单数据集异常不再回滚整轮告警与分数；
    *   前端展示补齐「评分时间」（此前后端已写但从未展示，无法判断新鲜度），维度问题数由「问题 X/Y」改为「问题 X 处」，消除 `problem_count > total` 时「问题 12/5」的自相矛盾展示。
*   **元数据漂移 AI 语义治理（人机协同）**：
    *   **新增字段（`new_in_db`）AI 语义分析**：提供可编辑的收录预览，物理库注释优先、LLM 推断中文术语 / 描述 / 同义词；批量收录时对物理库无注释字段走 `Semaphore(4)` 有界并发 AI 补全，失败降级为物理名兜底；
    *   **新增备注缺失（`missing_comment`）巡检类型**：支持物理注释免 LLM 回填、AI 补充分析、单字段与批量补备注入口；
    *   **字段语义 AI 推荐**：元数据表编辑字段新增「AI 推荐语义」，支持源表真实数据采样推断与一键回填表单，弹窗经 `Teleport` 挂载至 body 根节点确保绝对置顶不被底层弹窗遮挡；
    *   **物理类型归一化**：统一为 6 大通用类型（`String` / `Int64` / `Float64` / `DateTime` / `Boolean` / `JSON`），改用前缀令牌匹配，修复 `longtext` / `point` 等误判。
*   **治理动作安全边界加固**：
    *   **动作 ↔ 漂移类型白名单**：新增 `ACTION_DRIFT_TYPE_MAP` 与 `_is_action_allowed` 校验，杜绝「对整表缺失告警执行 `add_column` 从而写入 `physical_name='*'` 垃圾字段并把真实告警标记为已解决」的跨类型误处置；单项不匹配时抛 `ValueError`，批量处置跳过并计入 `skipped_count`，未知漂移类型放行以兼容历史数据；
    *   **批量处置按条使用 SAVEPOINT**：新增 `_resolve_one_atomically`，在 `db.begin_nested()` 内处置单条并 `flush`，把数据库级错误（如 `uk_table_col` 唯一约束冲突）就地限制在该条并回滚 savepoint，彻底修复「`processed_count` 已计成功但最终整批回滚、向量同步也不执行」与「单条失败仍继续」语义矛盾的问题；
    *   **采样 SQL 标识符安全引用**：新增 `quote_identifier` / `build_sample_sql`，拒绝含双引号 / 反引号 / 分号 / 空字节 / 换行的名称（可突破标识符边界），放行中文等合法名称，不可安全引用时降级为无采样；4 处采样（含 `recommend_column_semantic` 端点）统一收口，顺带修复原先「中文列名被静默跳过采样、AI 语义质量下降且无任何提示」的过严校验。
*   **测试基础设施加固**：新增 `_make_async_db_mock()` 对齐 `AsyncSession` 语义（`add` / `begin_nested` 为同步方法），统一替换 25 处 mock 构造，严格模式下 `RuntimeWarning` / `Unraisable` 零告警；修正多处**弱断言**（`analyze_update_comment_ai` 的空断言此前因 mock 未接入被测代码而恒真、去重测试删除 `WHERE` 仍会通过等）；补齐质量分排序、徽标、降级提示等此前零覆盖的前端契约测试。

### 5. ☸️ K8s 沙箱零配置共享用户工作区与运维视角划分 (Zero-Config K8s Workspace Sharing & Ops View Separation)
*   **工作区挂载对齐 Docker，subPath 指向整个用户工作区根**：
    *   K8s 沙箱 `/workspace` 原先只挂载 `agent_workspaces/{user_key}/sandbox` 子目录，沙箱内看不到用户工作区的其余内容（`sessions/`、`docs/`、历史落盘文件），与 Docker 沙箱 bind 整个用户工作区的体验不一致；现改为挂载整个用户工作区根，并对应预建目录结构。
*   **命名空间默认对齐平台（共享 PVC 的前置条件）**：
    *   Kubernetes 的 PVC 是命名空间级资源，历史默认值 `agent-sandboxes` 与平台主 PVC（位于 `nanzi-ai-agent`）分属不同命名空间，导致共享挂载配置静默失效（Pod Pending 或退化为独立空卷）；
    *   新增 `resolve_platform_namespace()`（环境变量 → in-cluster ServiceAccount 命名空间文件 → `nanzi-ai-agent` 回退）与 `resolve_sandbox_namespace()`（留空或历史默认值自动跟随平台），并统一 5 处硬编码默认值；迁移 `V156` / `V57` **仅把仍为历史默认值或为空的配置对齐**，绝不覆盖管理员显式自定义的命名空间。
*   **零配置自动探测并共享平台数据卷**：
    *   新增 `detect_platform_data_pvc()`：定位自身 Pod → 读取 spec → 提取挂载 `/app/data` 的卷所引用的 `claimName`，带 300 秒 TTL 缓存与全路径 best-effort 兜底（任何失败均返回 `None` 且永不抛异常）；
    *   配置语义收敛为三态：**留空 = auto（自动探测共享，推荐零配置）**、`none` = isolated（强制独立空卷，沙箱内看不到用户工作区）、具体卷名 = configured（显式共享，须同命名空间）；迁移 `V157` / `V58` 同步说明文案；
    *   保留「显式配置共享 PVC 必须具备已认证用户身份」的安全守卫；管理员连通性测试等无身份路径不挂共享卷。
*   **挂载配置校验告警与排障可观测**：新增 `evaluate_k8s_workspace_mount_config()`，对「`existing_pvc` 未配置」与「命名空间不一致却配置了共享 PVC」两类**静默失效配置**产生显式告警（管理员显式隔离不再告警）；`check_k8s_rbac_status` 响应新增 `warnings` 与 `workspace_mount` 字段便于排障。
*   **运维脚本按正向标签彻底划分平台与沙箱视角**：
    *   沙箱默认与平台同命名空间后，原先按命名空间整表列举会导致两侧资源互相混显；现引入 `SANDBOX_LABEL`（`app.kubernetes.io/managed-by=agentscope`，沙箱 Pod 与动态 PVC 均带该标签）与 `PLATFORM_APP_LABEL` 形成两侧对称的正向标签；
    *   平台侧 5 处列举与沙箱侧 3 处列举统一按标签过滤，同命名空间时 `events` 合并为单个「命名空间事件」小节避免重复输出；
    *   新增 `print_shared_workspace_volume()`：由平台 Pod 的 jsonpath 读出挂载在 `/app/data` 的 PVC 并展示 subPath 复用说明，三级降级任一失败都不中断脚本。
*   **前端配置说明与契约防漂移**：`SystemConfig` 两处独立文案块（【参数作用】长说明与 `💡 参数作用与是否必填` 引导卡片）与输入框 placeholder 全量同步新语义，并新增契约测试要求两处**均提及「完整工作区」**，防止再次只改其一；`K8sTerminalModal` 默认命名空间文案同步。

### 6. 🧰 沙箱构建与运维 CLI 工程化：目录归集与全链路环境自适应 (Sandbox Toolchain Engineering)
*   **沙箱运维资产规范化归集至 `sandbox/`**：Docker 与 K8s 两侧构建脚本、Python 实现与说明文档统一收敛到 `sandbox/docker/`、`sandbox/k8s/`，脚本命名结构完全对齐（`build-docker-sandbox-image.sh` ↔ `build-k8s-sandbox-image.sh`），并同步更新 README / README_EN / HOW_TO_INSTALL / FAQ 引用与断言。
*   **入口环境预检与场景化引导**：Docker 与 K8s 构建脚本入口新增前置环境预检，区分「未安装 CLI」「服务未运行」「权限不足」三类失败并给出精确引导命令；检测通过时自动格式化打印 Docker Client/Server 版本、宿主 OS/架构、当前 Context、Host 端点及资源规格。
*   **预置镜像补全常用排障工具链**：沙箱网关镜像生成模板动态注入 `git`、`tree`、`telnet`、`net-tools`（`netstat`）、`iputils-ping`、`dnsutils`（`dig`）、`iproute2`、`procps` 等排障与系统工具，开箱即可自查网络与进程状态。
*   **镜像就绪状态核验 `--list`**：K8s 构建脚本新增 `-l/--list/list/--check`，双层探测本地 Docker daemon 与 K8s/K3s 节点容器运行时（K3s containerd、系统 containerd、`crictl`）中已就绪的沙箱镜像清单，并重构 `resolve_node_container_tool` 统一节点运行时检测与调用（支持普通用户下探测 `sudo` 执行权限）。
*   **终端 UI/UX 结构化升级**：Docker 与 K8s 构建终端全面采用 GitHub 风格颜色编码与结构化卡片排版，进度、警告与错误分区呈现，显著降低排障阅读成本。
*   **沙箱虚拟环境激活脚本**：新增 `sandbox/activate-env.sh`，构建脚本缺包时提示先激活虚拟环境；兼容 Linux 环境（改用 `python3` 打印版本信息）；AgentScope 补丁失败时**拦截构建流程**并提示激活环境，避免产出残缺镜像。
*   **启动时自动初始化三个公共只读目录**：平台启动阶段自动递归创建 `data/docs`、`data/skills`、`data/branding`，彻底消除 K8s 空 PVC 首次挂载时因缺少公共目录导致 Grep 抛 `Directory not found` 的问题；随启动自动初始化落地，仓库中用于占位的 `data/skills/.gitkeep` 一并移除。
*   **系统保护目录的写入防线**：工作区浏览器（`WorkspaceBrowserDrawer`）禁止对系统保护目录（`docs` / `skills` / `branding` / `agent_workspaces`）执行重命名与删除，并禁止普通用户对自己的主目录执行重命名或删除。

### 7. 🗄️ 双数据库迁移工具链全能力升级与 PG 幂等修复 (Migration Toolkit Overhaul)
*   **CLI 能力全面对齐**：MySQL 与 PostgreSQL 迁移脚本全面支持 `--help`、`--all`、`--spec` 范围指定、`--last` 断点续跑。
*   **PostgreSQL 原生客户端模式**：新增 `apply-sql-native.sh`，免 Python 依赖、直接使用系统 `psql`，适配精简容器与受限环境。
*   **高颜值终端交互**：控制台全面采用 ANSI 配色、边框卡片排版、执行进度方块与结构化迁移统计看板；无参数运行时展示交互式模式选择，执行前严格要求输入 `YES` 确认，防误触。
*   **运行环境前置探测与引导安装**：启动时自动探测 Python / 虚拟环境与核心依赖，通过时打印高亮就绪徽标；依赖缺失时在交互式终端下提供一键自动安装引导。
*   **PostgreSQL 强类型与 DDL 幂等性修复**：修复 `V48` 与 `V52` 中 `system_configs.is_secret` 误写为整型 `0` 的问题（纠正为布尔值 `FALSE`）；为 `V25`（`tags` 字段）与 `V33`（`toolcall_timeout_seconds` 字段）补齐 `IF NOT EXISTS`，保障无损重跑幂等性。
*   **测试守卫与断点防护**：新增 `tests/test_db_prod_pg_apply_sql.py`（双库 49 项单测全绿）；`.gitignore` 忽略双库断点记录文件 `.last_applied_sql`；安装文档补充 `all` / `spec` / `last` 等模式使用说明。

### 8. 🧠 上下文管理复核修复：token 口径统一与预算契约收敛 (Context Management Review & Token Accounting)
*   **token 估算口径对齐 AgentScope**：`estimate_text_tokens` 由 `cjk*1.5 + other/4` 改为 **UTF-8 字节数 / 4**，与 AgentScope 的 `count_tokens` 对齐——旧口径对中文高估约 2 倍，实测纯中文会话在 64k 模型上只用到 35% 就被判定超窗并压缩，而纯英文反而低估、可能真正超窗。
*   **手动压缩快照失效机制**：补上全仓此前**缺失的快照删除点**，修复「编辑重发 / 清空会话」把已放弃分支的摘录重新拼回新历史（用户在历史里看不到、模型却看得到）的问题；并在读路径增加 `revision` 校验作第二道防线（无 `revision` 字段的历史快照一律弃用）。
*   **seq counter 续期修复**：原先只在 `add_message` 续期，长期静默后 `INCR` 会从 1 重来、低于历史中保留的旧 seq，导致快照合并把新消息当作旧消息丢弃；现随 `reset_context_state` 一并续期。
*   **REST 上下文接口口径修复**：改复用 `_runtime_context_metadata`，修复原先手搓字典传 `max_output_tokens` 而消费方读 `completion_reserve_tokens` 导致**输出预留恒为 0**、水位线各偏大一个 `max_output_tokens` 并与调用统计弹框自相矛盾的缺陷。
*   **压缩卡片去重与阶段标记**：pre-route 压缩只是为路由准备不超窗上下文的中间态（其窗口随后会被路由后阶段按目标模型真实窗口重算，永非最终状态），但两处都发卡、都落记录；现为 SSE 事件显式打 `stage`，前端统一经 `isUserVisibleContextCompaction` 排除 `pre_route`，记录仍保留供排障。
*   **LLM 摘要 transcript 加上限**：默认 24000 字符、单条上限为其 1/8，超出时优先保留离当前最近的部分并插入省略说明——原先会把整段被丢弃历史原样拼入摘要请求，让这个「用来解决超窗」的请求自己超窗、然后静默退回确定性摘录。
*   **输出钳制补下限与显式告警**：`available` 落在 1..127 时钳完只够吐几个 token，表现为空回复或半截话（比供应商直接拒绝更难排查），现补 128 下限；输入已超窗时同样留下显式告警。
*   **口径收敛与常量下沉**：两套断轮清理口径统一为 `drop_unfinished_turns`（原先预算路径与进模型路径各写一份，注释声称一致、实测三种场景均不一致）；`agent_context_overhead_headroom_tokens` 与 `agent_context_llm_digest_transcript_max_chars` 下沉为代码常量（前者取决于工具绑定数与系统提示长度、后者只是内部护栏，管理员无法凭经验估出正确值，做成配置只会多一个填错反而更糟的旋钮）；删除会摧毁 digest 防覆盖护栏的死代码 `set_digest`；`build_overflow_digest` 跳过带 `COMPACTION_MARKER` 的系统消息，消除摘录自我抄写。
*   **测试基础设施修正**：修正 `test_execution_observability.py` 的 `no_infrastructure` 标记误用——整文件标记会让 `init_infrastructure` 跳过「先 `close_db()` 再 `init_db()`」这一把 SQLAlchemy 全局引擎连接池重新绑定到当前事件循环的关键步骤，导致用例取到上一事件循环遗留连接并报 `Future attached to a different loop`，呈现为随测试顺序波动的假 flaky 失败。

### 9. 🖥️ Agent Debug 执行详情抽屉与沙箱操作打通 (Agent Debug Execution Drawer)
*   **执行详情抽屉（`ExecutionDebugDrawer`）**：新增右侧抽屉整合三个标签页——**执行步骤**（按 `trace_id` 拉取 `/chat/logs`，折叠展示完整 `tool_input` / `tool_output` JSON，识别截断标记与 raw 解包）、**组装 Prompt**、**运行时上下文**（`agentContext` 快照）；AgentDebug 消息头按钮收敛为「执行详情」唯一入口，移除与之功能重复的快捷按钮。
*   **组装 Prompt 交互增强**：每条消息头部新增一键复制（成功后短暂显示「已复制」）与折叠切换（默认展开首条全量系统提示词），并显示 `≈N tok` token 估算（复用 PromptStudio 相同口径：中文 ×0.8 + 其余 ×0.25）；后端 `assemble_step` 的 `raw_prompt` debug 事件补 `system_prompt` 字段，暴露组装完成的完整系统提示词。
*   **沙箱操作跨页面复用**：抽取 `useSandboxWorkspace` 组合式函数（原 EmbedChat 内联 350+ 行沙箱生命周期逻辑），AgentDebug 接入沙箱启动 / 刷新 / 停止 / 重启 / 终端并透传状态 Props；同时对齐底部 ChatInput 的专家 / 智能委派胶囊，移除顶部冗余模式选择栏，清理 `AgentLogicFlowModal`、`clearHistory` 等死代码，并修复组合式函数在 AgentDebug 中的 TDZ 崩溃（`isProcessing` 声明提前）导致的页面空白。

### 10. 💬 内嵌对话、模型选择器与全局前端体验精修 (Embed Chat & Frontend Polish)
*   **内嵌对话顶栏与会话历史侧边栏全面升级**：
    *   顶栏左右功能按钮全量接入**非原生深色磨砂 Tooltip**（移除滞后的原生 `title`），历史会话开关图标升级为纯净分栏面板 SVG，并对齐 Dashboard 与 EmbedChat 顶层折叠按钮的 X 轴基线；
    *   侧边栏移除顶部多余新建占位块，支持**当前会话活动态高亮与左侧垂直指示条**、搜索框 300ms 防抖与一键清空、Markdown 导出、会话操作菜单与防误触确认、**日期分组折叠（Accordion）与骨架屏加载过渡**；
    *   顶栏文字层级精致化（主标题 `text-[13px] font-semibold`），智能体状态胶囊由高饱和蓝底重构为现代中性微质感灰底 + 主题色微指示圆点。
*   **模型选择下拉菜单**：新增完整键盘导航（`activeModelIndex`、循环切换、回车确认、Esc 关闭、`scrollIntoView({ block: 'nearest' })` 保持激活项可视），下拉选项升级为**双行卡片**（名称 + 勾选 / 能力元信息），触发器移除大号相框图标、改为模型名后的「视觉」微胶囊；并将多模态 / 深度思考等状态药丸由紫色系统一替换为中性灰与主题色阶，重塑色彩层级、消除紫色视觉干扰。
*   **交互菜单键盘导航滚动跟随**：`@` 专家提及改用 `data-mention-index` 精准定位（修复分割线占位导致的 `scrollIntoView` 索引错位），高亮升级为 ring + border 双层并补 `↵` 激活徽标；快捷指令菜单新增容器引用与 `scrollActiveCommandIntoView`，方向键切换时滚动跟随并补齐 Enter 选择 / Esc 关闭提示。
*   **AI 内容底部操作栏重构**：点赞 / 点踩移至重新生成之后以优化聚合动线，复制与重新生成按钮纯图标化并统一为 28×28 按钮 / 14×14 图标规范，全量移除原生 `title` 改用向下弹出自定义深色磨砂 Tooltip 防遮挡，Token 用量紧凑化展示（圆柱数据库图标 + `XX.XK tok`）。
*   **继续分析按钮移动端响应式**：移动端自动隐藏文字与箭头、仅保留 28×28 纯图标，释放 55px+ 横向空间避免小屏溢出；桌面端保持图标 + 文字 + 下拉箭头完整形态。
*   **网页预览面板（Web Preview）**：新增 `KNOWN_RESTRICTED_DOMAINS` 主机名预判，命中防嵌套站点时展示受限兜底卡片（一键新标签页打开 / 仍尝试内嵌加载）；**在新窗口打开后自动关闭面板**（三处入口统一绑定 `handleOpenInNewTab`，同时刻意不重置 `pinned` 跨会话偏好）。
*   **HTML 交互应用与在线预览链路**：支持代码块优先渲染、后端 `inline` 模式与前端拦截预览，配套补充生成文件下载与 MessageRenderer 契约测试。
*   **AI 主动提问交互卡**：`UserQuestionCard` 与 `BusinessConfirmationCard` 移入统一的 Agent Message 气泡容器内，消除宽度与底色不对齐；缩小单选 / 多选内边距、细化指示器；后端 `tool_nudge_policy` 新增 `looks_like_decision_request` 与决策收集模式弱提示（`_resolve_decision_request_nudge`），在用户表达选择 / 犹豫时引导模型调用 `ask_user_question`，但**不强 force 首轮调用**，允许模型直接给出推荐。
*   **时间线日志图标治理**：标题剥离范围由「历史遗留的 ✨」泛化为任意图形字符类（`Extended_Pictographic`），避免与卡片图标重复；上下文压缩类条目统一使用折叠图标，并将「准备知识资源范围」从压缩条目中拆出。

### 11. 📊 Prompt Cache 指标归一化与提示词工程精修 (Prompt Cache Metrics & Prompt Engineering)
*   **多厂商 Prompt Cache 用量解析归一化**：兼容解析 OpenAI、Anthropic、聚合网关等多种 `usage` 缓存字段，杜绝缓存命中漏计；后端中间件与数据模型新增 `uncached_input_tokens`（向后兼容老数据反序列化），前端模型调用明细弹窗新增「未缓存输入」与「缓存读取」拆分卡片与单条胶囊标签，并完善 Prompt Caching 命中率计算公式与老数据自动兜底。
*   **`read_image` 确定性触发规则优化**：隔离通用字面相关度匹配与证据兜底强推，专设独立实体词与动作解析器，避免普通文本问答引发路径幻觉；为 `png` / `gif` / `chart` 等英文实体词增加正则**全词边界校验**，根治 `gift` / `charter` 等日常词汇的子串误伤；剔除视觉 / 图谱等抽象概念词，增加工具用法解释类问答防御。
*   **移除当前模型前置直通拦截**：从 RouteStep 移除当前模型身份直通拦截与截断，废弃宽泛正则匹配，消除模型测速、评价与普通问答被误判截断的缺陷；询问当前模型身份时直接调用 `get_current_model` 运行时工具且严禁检索文档。
*   **收窄公共文档检索触发**：ToolNudge 仅在用户明确询问「平台使用手册 / 怎么部署 / 报错排查」时才触发公共文档 Grep 强推，运行时状态询问与普通闲聊绝不触发；同步精简全局守则中冗长的公共文档路径与防盲猜说明，避免每轮重复注入。
*   **历史助手表格裁剪压缩策略优化**：深度保护最近一轮 Assistant 回复（放宽至 100 行表格明细，避免紧随其后的追问修改丢失数据），常规历史表格门槛提高至 30 行且超出 ≥10 行才折叠以消除负收益截断；折叠提示改造为**独立的外部系统引用块**，防止大模型在后续轮次将其作为表格数据照抄回显。

### 12. 🔔 通知连通性测试增强与平台公共目录治理 (Notification Diagnostics & Common Directory Governance)
*   **连通性测试消息带触发用户与时间**：原先钉钉 / 企业微信 / 飞书 / 邮件四处各自硬编码完全固定的文案，用户在多渠道、多群收到时既认不出是谁触发、也对应不上自己刚点的那次操作；现收敛为 `TEST_MESSAGE_TITLE` / `TEST_MESSAGE_BODY_TEMPLATE` / `TEST_CHANNEL_LABELS` 单一来源，注入「姓名（登录名）」身份行（`real_name` 与 `user_name` 相同时自动去重）与跟随平台时区的触发时间（复用 `platform_now()`，避免服务器 UTC 与用户本地差 8 小时）；区分 Markdown（钉钉 / 企微 / 飞书卡片）与纯文本（邮件正文）两种呈现形态；`test_connection` 新增可选 `actor` 参数，未传时退化为不含身份行以保持向后兼容；**凭据不外溢**——只透传 `user_name` / `real_name` 两个展示字段，不把内含明文 `api_key` 的 `user_info` 整体带入消息链路。顺带修掉钉钉与企微「您的AI」漏掉的空格，两渠道措辞自此一致。
*   **Git 仓库卫生**：`.gitignore` 由逐条列举 `data/` 子目录（易漏配而被误提交）改为忽略整个 `data/` 运行时目录，与既有注释「Project Specific Runtime Data & Cache (DO NOT COMMIT)」的意图一致。
*   **文档与社区**：README 顶部新增「疑难解答与常见问题手册 (FAQ)」入口（并修正引用块内 soft break 导致两行连成一行的 CommonMark 渲染问题）、新增 DeepSeek V4.1 Flash SGLang / vLLM 部署实践技术文、更新社区微信交流群二维码、完善 FAQ 7.3.1 沙箱选型对比与安装文档迁移脚本使用说明。

---

## 🐛 Bug Fixes

### 认证 / 会话 / 凭据安全
*   **会话令牌无法吊销（高危）**：`invalidate_user_auth_cache` 因单向哈希无法由 `user_id` 反查，对已签发会话完全无效，叠加 24h 滑动续期使得禁用 / 删号 / 改角色 / 重置 Key 后旧会话长期可用；已补签发索引、成批吊销与 60s 回源复核兜底。
*   **审计日志明文落库（高危）**：脱敏名单缺 `session_token`，令牌随响应体明文入库且详情接口原样回显；已补键名与值级正则双重脱敏。
*   **跨站 iframe 刷新失效（高危回归）**：`session_cookie_issued` 仅代表服务端写了 `Set-Cookie`，而 `embed_session` 为 `SameSite=lax` 在跨站第三方 iframe 中不会发送，原「见到该标记即清 URL」使刷新后既无 URL 凭据、Cookie 也不发送；现以本 tab 短期令牌作为刷新依据，长期 Key 仍用后即弃。
*   **`logout` 未清理 `embed_session`**：门户登出后请求会回落到嵌入身份造成身份串号；现两个 Cookie 都读、都删，并按会话前缀走服务端吊销。
*   **登录响应体泄露长期密钥**：三处登录接口不再回传 `api_key`，凭据只经 HttpOnly Cookie 下发。
*   **门户内嵌 chat 永久卡在加载态**：无 token 的 `INIT_CONFIG` 不再静默 `return`，非 strict 模式照常回落同源 Cookie 校验。
*   **组件调试台闪红**：区分「等待宿主下发凭据」与「凭据失效」，消除 `INIT_CONFIG` 切换瞬间因 `hasPermission` 未就绪而闪出的一帧红色遮罩。
*   **票据被来源校验白白消耗**：改为「只读校验 → 原子核销并比对内容」，域名白名单未配对不再掩盖为「票已被使用」。
*   **嵌入令牌并发兑换误报**：前端增加 in-flight 与已消费去重，URL 与宿主 `postMessage` 同票并发时不再必有一方报「凭证已失效」。
*   **多嵌入实例身份串号**：`sessionStorage` 隔离边界是 tab + origin 而非 iframe，固定键会被同页多实例互相覆盖；现按 `instance_id` 分桶且读取不回退旧键。
*   **用户信息接口中文名不符**：「获取用户画像」更正为「获取用户信息」（仅显示名，权限 ID 不变）。

### 元数据 / 巡检 / 数据治理
*   **巡检降级误判满分**：物理列读取失败时 mismatch/new/stale 全 0 反而判定 100 分「优秀」，把「读不到」当成「结构一致」；现标记 `unreadable_tables_count` 与 `degraded` 并在前端以 ⚠ 提示。
*   **质量分入口不一致**：单数据集对 0 张纳管表直接 `return` 不写分数，而全库巡检会写 100 分；现两条路径统一结算。
*   **全库巡检单数据集异常拖垮整轮**：`_scan_dataset_tables` 未被 `try` 包裹，任一异常会跳过循环外唯一 `commit`，导致整轮告警与分数全部回滚；现逐数据集独立隔离并计入 `failed_datasets_count`。
*   **跨类型误处置**：对整表缺失告警执行 `add_column` 会写入 `physical_name='*'` 垃圾字段并把真实告警标记为已解决；已引入动作 ↔ 漂移类型白名单校验。
*   **批量处置语义矛盾**：循环外单次 `commit` + `autoflush=False` 使数据库级错误延迟到最终提交才抛出，`processed_count` 已计成功但实际整批回滚、向量同步不执行；现按条 SAVEPOINT 隔离。
*   **采样 SQL 注入面与误杀**：4 处采样中 3 处为字符串拼接直通请求体，而唯一的白名单校验又过严会静默跳过中文列名采样；现统一经 `build_sample_sql` 安全引用。
*   **告警去重吞没**：去重条件补 `drift_type`，避免 `type_mismatch` 吞没 `missing_comment`。
*   **物理类型误判**：统一归一化为 6 大通用类型并改用前缀令牌匹配，修复 `longtext` / `point` 等误判。
*   **AI 推荐语义弹窗被遮挡**：改用 `Teleport` 挂载至 body 根节点并置顶显示。
*   **模型注释与实际取值不符**：`MetaColumn` / `MetaSchemaDriftAlert` 的 `drift_type` 注释补齐实际已支持的 `table_missing_in_db` 与 `missing_comment` 两个取值。
*   **数据表卡片操作按钮藏得过深**：移除 `opacity-0 group-hover:opacity-100`，编辑 / 删除 / 智能推荐按钮默认常显。

### 上下文管理 / 提示词 / AI 工具
*   **中文上下文被高估约 2 倍**：token 估算口径改为 UTF-8 字节 / 4，与 AgentScope `count_tokens` 对齐，避免中文会话在 64k 模型上仅用到 35% 就被压缩。
*   **手动压缩快照未失效**：「编辑重发 / 清空会话」会把已放弃分支的摘录重新拼回新历史；已补快照删除点与 `revision` 校验。
*   **seq counter 静默后重置**：长期静默后 `INCR` 从 1 重来导致快照合并丢弃新消息；现随 `reset_context_state` 续期。
*   **输出预留恒为 0**：REST 上下文接口手搓字典传 `max_output_tokens` 而消费方读 `completion_reserve_tokens`，导致水位线偏差与统计弹框自相矛盾。
*   **压缩卡片重复与计数翻倍**：为 SSE 事件打 `stage`，前端排除 pre-route 中间态。
*   **摘要请求自身超窗**：为 LLM 摘要 transcript 加上限并优先保留最近部分。
*   **空回复 / 半截话难以排查**：输出钳制补 128 下限与显式告警。
*   **断轮清理口径不一致**：两套实现统一为 `drop_unfinished_turns`。
*   **摘录自我抄写**：`build_overflow_digest` 跳过带 `COMPACTION_MARKER` 的系统消息；`_contains_compaction` 支持纯字符串 `content`（原先 `_safe_getattr(str, "text")` 永远取不到值导致漏检）。
*   **`read_image` 词边界误伤**：`gift` / `charter` 等日常词汇不再误触发。
*   **模型身份询问被误判截断**：移除 RouteStep 的当前模型前置直通拦截。
*   **上一轮表格数据丢失**：深度保护最近一轮 Assistant 回复至 100 行，折叠提示改为外部系统引用块防止被照抄回显。
*   **执行观测测试假 flaky**：修正 `no_infrastructure` 标记误用导致的连接池事件循环错配。
*   **会话历史路由错位**：`/conversation/{conversation_id}` 装饰器从误挂的辅助函数移回 `get_conversation_history`，修复 400 Bad Request，并新增路由契约测试。

### 沙箱 / K8s 运维 / 构建脚本
*   **K8s 沙箱看不到用户工作区**：subPath 由 `.../sandbox` 改为整个用户工作区根，并对齐 Docker 行为。
*   **共享 PVC 静默失效**：PVC 为命名空间级资源，沙箱与平台分属不同命名空间时共享挂载无法生效；已对齐默认命名空间并新增挂载配置告警。
*   **沙箱运维脚本资源混显**：平台视角混入沙箱 Pod、`events` 重复打印、看不到沙箱实际复用的平台数据卷；现按正向标签划分并补显共享数据卷。
*   **预构建标记写库失败**：修复 `docker_prebuild.py` 中预构建标记的 `ConfigService.set_config` 调用。
*   **构建脚本缺包无提示**：新增 `activate-env.sh` 并在 AgentScope 补丁失败时拦截构建流程。
*   **空 PVC 缺公共目录**：启动时自动初始化 `docs` / `skills` / `branding` 三个公共只读目录。
*   **系统保护目录可被误删**：前端禁止对保护目录与用户主目录执行重命名 / 删除。

### 前端交互 / 视觉 / 预览
*   **网页预览面板跳转后残留**：在新窗口 / 新标签页打开后自动关闭面板（三处入口统一绑定）。
*   **快捷指令标签多余 Emoji**：智能体调试页面标签清理冗余 emoji；时间线标题剥离任意图形字符类前缀。
*   **图标契约退化为排版快照**：契约断言改为先做空白归一化再匹配，只约束行为、不约束缩进与换行。
*   **默认大模型下拉不含多模态模型**：`llm_model_name` 下拉改为同时放行 `llm` 与多模态两类，并抽取 `MULTIMODAL_MODEL_TYPES` 常量作为类型判定单一来源，选项文本对多模态追加「· 多模态」标注。
*   **兼容模式提示文案指向不清**：压至 38 字并给出终端用户唯一可执行的动作「联系开发人员」，同时以契约测试禁止正文再变长或泄露接口细节。

---

## 🗄️ Database Migrations (表结构与配置变更清单)

本次版本包含 **3 组双数据库（MySQL / PostgreSQL）同步迁移脚本**，另含 PostgreSQL 侧 4 处历史脚本的幂等与类型兼容修复：

| 版本号 (MySQL / PG) | 变更说明 | 涉及数据表 / 配置项 |
| :--- | :--- | :--- |
| `V155` / `V56` | 数据集质量治理分（巡检结算时写入，仅新增列，不改动既有数据） | `meta_datasets.quality_score`, `meta_datasets.quality_breakdown`, `meta_datasets.quality_scored_at` |
| `V156` / `V57` | K8s 沙箱命名空间默认值对齐平台命名空间（共享用户工作区 PVC 的前置条件；仅当仍为历史默认值 `agent-sandboxes` 或为空时对齐，绝不覆盖自定义值） | `system_configs` 更新 `sandbox_k8s_namespace` |
| `V157` / `V58` | 更新共享 PVC 配置说明文案（留空即自动探测共享、`none` 为强隔离；仅更新 `description`，不改动 `value`） | `system_configs` 更新 `sandbox_k8s_existing_pvc` 说明 |
| `V25` / `V33` | PostgreSQL 侧补齐 `IF NOT EXISTS`（非新增版本，无损重跑幂等性修复） | `tags`、`toolcall_timeout_seconds` 字段 |
| `V48` / `V52` | PostgreSQL 侧修正 `is_secret` 由整型 `0` 误写为布尔 `FALSE`（非新增版本） | `system_configs.is_secret` |

**新增环境变量（`env.example`）**：

| 变量名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `PORTAL_SESSION_TOKEN_ENABLED` | `true` | 开启后浏览器只持有不透明随机会话令牌（`sess_...`），真实 API Key 不再下发前端；仅在 Redis 不可用等异常场景才需临时置 `false` 回退旧行为 |
| `ALLOWED_ORIGINS` | `["*"]` | CORS 允许来源。服务端启用 `allow_credentials`，浏览器不接受通配符与凭据共存，跨域部署请填写实际访问域名；未配置时回退 `["*"]` 并在启动日志告警 |

> **升级提示**：启动前请确保执行平台数据库迁移脚本。MySQL 环境执行 `bash db-prod/apply-sql.sh`，PostgreSQL 环境执行 `bash db-prod-pg/apply-sql.sh`（或免 Python 依赖的 `bash db-prod-pg/apply-sql-native.sh`），脚本具备完整的幂等性检查、断点续跑与优雅日志格式化。

## 📖 Upgrade Guide (平滑升级指引)

### ⚠️ 升级前必读：破坏性变更

1. **门户会话 Cookie 更名（需重新登录）**：`admin_token` → `portal_session`。本次遵循「只认新名、不加兼容分支」策略（与项目此前 `element` → API 权限码迁移风格一致），**部署后所有已登录用户需重新登录一次**。
2. **代客签发嵌入凭证权限码变更（需重新授权）**：「代他人签发嵌入凭证」改为独立的 API 权限码 `POST:/api/v1/embed/tickets`，此前依赖「获取用户画像」权限的角色**需管理员在后台【角色管理 → 智能体中心】重新分配**。
3. **Redis 成为会话硬依赖**：会话令牌化默认开启后，请确保 Redis 已开启持久化（RDB / AOF）。Redis 不可用时登录会自动回退到真实 API Key 以保可用，但会话吊销、在线状态等能力将退化。
4. **K8s 沙箱命名空间变更后的 RBAC 补授**：若沙箱 Pod 随迁移进入平台命名空间，需在该命名空间重新应用 `k8s_deploy/sandbox-rbac.example.yaml`，授予 Pod / PVC 管理权限；强隔离部署请显式指定独立命名空间并留空 `sandbox_k8s_existing_pvc`。

### 方式一：源码拉取升级（开发与虚机部署）

1. **拉取最新代码并同步主分支**：
   ```bash
   git fetch origin
   git checkout main
   git pull origin main
   ```

2. **数据库结构平滑升级**：
   ```bash
   # MySQL 用户执行：
   bash db-prod/apply-sql.sh

   # PostgreSQL 用户执行（Python 环境）：
   bash db-prod-pg/apply-sql.sh

   # PostgreSQL 用户执行（免 Python，使用系统 psql）：
   bash db-prod-pg/apply-sql-native.sh
   ```

3. **依赖更新与启动本地服务**：
   ```bash
   # 后端依赖检查（Python 3.11；本版本 requirements.txt 无新增依赖）
   poetry install --no-root

   # 前端构建或启动本地开发
   ./dev.sh
   ```

4. **环境变量检查**：确认 `.env` 中的 `PORTAL_SESSION_TOKEN_ENABLED`（建议保持 `true`）与 `ALLOWED_ORIGINS`（跨域部署需填实际域名）。

---

### 方式二：Docker 容器化升级（生产环境推荐）

#### 1. 导入官方 Release 镜像归档（推荐）
从 GitHub Releases 下载对应服务器架构的预编译 Docker 镜像归档包：
```bash
# 1. 执行数据库结构平滑迁移
bash db-prod/apply-sql.sh  # PG 用户执行: bash db-prod-pg/apply-sql.sh

# 2. 导入镜像归档（按架构选择）
# x86_64 服务器
docker load -i nanzi-ai-agent_1.0.16.0_linux-amd64_*.tar

# ARM64 服务器（鲲鹏 / Ampere / Apple Silicon 等）
docker load -i nanzi-ai-agent_1.0.16.0_linux-arm64_*.tar

# 3. 重启容器服务
cd docker && ./start-nanzi-ai-agent.sh
```

#### 2. 本地 / 生产自主构建镜像
```bash
# 1. 拉取最新代码并执行数据库迁移
git checkout main && git pull origin main
bash db-prod/apply-sql.sh  # PG: bash db-prod-pg/apply-sql.sh

# 2. 进入 docker 目录构建镜像
cd docker
./build_linux_x86.sh 1.0.16.0   # ARM64 服务器执行: ./build_linux_arm.sh 1.0.16.0

# 3. 启动并验证容器
./start-nanzi-ai-agent.sh
```

---

### 方式三：沙箱构建与运维 CLI（若启用 Docker / K8s 沙箱策略）

沙箱运维资产已统一归集至 `sandbox/` 目录，脚本与说明文档同步对齐：

```bash
# Docker 沙箱：环境预检 + 预置镜像构建（含 git/tree/ping/netstat 等排障工具链）
bash sandbox/docker/build-docker-sandbox-image.sh

# K8s 沙箱：集群就绪与 RBAC 权限体检
bash k8s_deploy/nanzi-k8s.sh health

# K8s 沙箱：构建并核验沙箱网关镜像（--list 双层探测本地与节点运行时）
bash k8s_deploy/build-k8s-sandbox-image.sh --list
bash k8s_deploy/build-k8s-sandbox-image.sh
```

> **沙箱共享工作区配置建议**：`sandbox_k8s_existing_pvc` 留空即自动探测并共享平台数据卷（推荐零配置）；需要强隔离时填 `none`，沙箱将使用每工作区独立创建的空 PVC（沙箱内看不到用户工作区）。

---

## 👥 Contributors & Acknowledgements

特别鸣谢所有参与 NanZi AI Agent Platform v1.0.16.0 版本需求讨论、代码贡献、架构演进及线上验证的团队成员与社区开发者！本次版本围绕「凭据不外泄、会话可吊销、治理有依据、沙箱零配置」四条主线完成了一次纵深式的安全与治理加固，平台将持续践行极客、稳定、高性能的架构演进，助力企业打造最值得信赖的自主智能体生产力中枢。
