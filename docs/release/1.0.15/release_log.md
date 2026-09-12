# 🎉 NanZi AI Agent Platform v1.0.15.0 Release Notes

**GitHub Repository**: [RandyChen1985/nanzi-ai-agent-platform](https://github.com/RandyChen1985/nanzi-ai-agent-platform)

v1.0.15.0 版本是一次以 **Kubernetes 原生安全 Pod 沙箱策略与运维工具链全量交付、Docker/K8s 双沙箱按需惰性拉起（Lazy Sandbox）与毫秒级首调推流、执行时间线全层级跳秒动效与 500ms 响应式时钟、稳定前缀 KV Cache 优化与真实事实防编造加固系统提示词** 为核心驱动，并全面推进 **元数据物理结构巡检与缺失表整表下线、Schema 漂移智能校准与全链路变更日志 Changelog、ChatBI 完整明细全量导出（10万行直连）、等保三级密码合规与 2FA 双因素认证、飞书群机器人通知与卡片 Schema 2.0 接入、Main 主专家系统锁定与委派时间线合二为一、我的工作台 UI/UX 全面升级与常用助手置顶 Pin** 的重量级企业级版本。

在本次更新中，平台对沙箱运行体系进行了历史性的架构重构：正式推出生产级 **Kubernetes 原生安全 Pod 沙箱**，提供对等 `kubectl exec` 的 Pod Web 终端与一站式运维治理脚本体系；同时将 Docker 与 K8s 运行环境重构为**按需惰性代理机制（Lazy Sandbox）**，未触发 Bash 命令行工具时容器/Pod 绝不拉起，彻底杜绝服务器资源浪费；执行时间线补齐 500ms 动态跳秒、滑动微动效条与全层级三阶段安抚文案，彻底移除挤占输入框的横条 Banner，带来轻盈聚焦的对话体验。在模型认知防线方面，完成提示词结构化装配架构升级与稳定前缀 KV Cache 优化，并落地「真实结果优先」提示词强约束，彻底杜绝可验证指标的凭空编造；在数据治理领域，推出物理表缺失探测、整表安全下线、类型不匹配智能同步、全链路数据集变更日志 Diff 审计与定时巡检告警闭环体系；在安全合规层面，上线等保三级密码合规、密码有效期多触点提醒、2FA 双因素认证（TOTP）及 API Key 重置保障；此外，还接入了飞书群机器人 Schema 2.0 卡片、10 万行级 ChatBI 数据明细直连导出、工作台助手置顶及深色模式全量适配。

本次变更范围自 `4f004dfff6dc308e74e557f86dc88b99f6b59bd4`（不含，为 v1.0.14.0 发布提交）至 `40098a61b2249b6e754de12641bbce11b2d5fe5e`（含），共 **118 个提交**（其中非 Merge 提交 105 个），涉及 **268 个文件**、**34,004 行新增代码**与 **6,148 行删除**。

---

## 🚀 Key Features

### 1. ☸️ Kubernetes 原生 Pod 安全沙箱策略与企业级自动化运维体系 (Native K8s Sandbox & Operations Suite)
*   **Kubernetes 原生 Pod 安全沙箱运行时**：平台在既有 Docker 沙箱基础上，重磅支持生产级 Kubernetes 原生 Pod 隔离策略（`k8s_workspace`）。智能体运行 Bash 命令行任务时，在专用 Kubernetes 命名空间内动态拉起独立隔离 Pod，实现进程、网络、存储及计算资源的强边界物理级隔离。
*   **平台 ServiceAccount 绑定沙箱 RBAC 权限**：采用 Kustomize 原生纳管与自动化部署，平台 ServiceAccount 严格遵循最小权限原则绑定 Pod 创建、状态监听、日志读取与容器内 Exec 权限，支持跨集群多命名空间隔离与企业多租户资源配额配额（Quota）管控。
*   **对等 `kubectl exec` 的「进入 Pod」Web 终端**：前端工作区状态浮标内集成专属 Web 终端（支持 Docker 与 K8s 双协议），直接基于 Kubernetes CoreV1 API 的 WebSocket SPDY 子协议实现双向交互，开发者与管理员可一键直接进入 Pod 内部排查系统依赖、运行状态与调试命令。
*   **全生命周期运维与一键体检脚本体系 (`k8s_deploy/nanzi-k8s.sh` / `k8s_deploy/install.sh`)**：
    *   新增 `k8s_deploy/nanzi-k8s.sh health` 一键体检命令，自动诊断集群节点、Pod 存活、RBAC 授权、PVC 存储及沙箱镜像就绪状态；
    *   新增 `nanzi-k8s.sh restart` 二次安全确认机制与 `restart-pod-force` 强制重启功能；
    *   优化 `install.sh` 支持显式子命令（`-i/--install`）、交互意图选择支持 `q` 安全退出，修复升级向导内嵌 ANSI 颜色乱码问题；
    *   支持非 K3s 纯原生 Kubernetes/containerd 环境并提供 `ctr -n k8s.io import` 离线镜像导入与手动检查工具包（`--images/--import`）；
    *   系统配置页提供 `sandbox_k8s_image` 预置网关镜像一键构建引导弹窗（标准 containerd 与 K3s 双环境 Tab 自由切换）。

### 2. ⚡ 沙箱架构革新：按需惰性拉起（Lazy Sandbox）、后台自动预热加载与执行时间线全层级跳秒动效 (On-Demand Lazy Sandbox, Auto-Warm & 500ms Tick Timeline)
*   **按需惰性沙箱代理机制（Lazy Sandbox）**：彻底颠覆过去进入会话盲目启动沙箱容器的高开销模式，重构为 `LazySandboxWorkspaceProxy` 与 `LazySandboxBashNativeTool` 惰性代理：
    *   当用户进行普通对话、业务咨询、ChatBI 查数、知识库检索或使用宿主侧文件工具（Read/Write/Edit/Glob/Grep）时，**沙箱容器 / Pod 绝不拉起，实现服务器零容器开销**；
    *   仅在智能体实际决策调用 Bash 命令行工具（`exec_command`）时，毫秒级按需触发沙箱真实拉起，彻底杜绝服务器资源的无效占用；
    *   补齐 AgentScope 原生工具协议元数据（`is_external_tool`, `is_state_injected` 等）与只读权限降级防护。
*   **会话静默自动预热加载与空闲安全回收双轮驱动（Auto-Warm & Idle Reclaim）**：
    *   **会话自动预热加载**：系统配置新增 `sandbox_auto_warm` 开关（`V153` / `V54`，Docker 与 K8s 通用）。用户新建或打开会话后，系统自动在后台异步静默拉起预热沙箱；在用户阅读历史或打字构思期间，容器 / Pod 已经提前就绪，调用 Bash 时**直接命中存活沙箱秒级执行，零冷启动等待**；
    *   **空闲超时自动回收**：新增 `sandbox_idle_time` 配置项，沙箱闲置超过指定周期自动销毁释放资源，完美兼得极致响应速度与算力成本节约。
*   **Bash 卡片节点动态挂载与 SSE 进度推流**：
    *   引入 `ContextVar` 与 `BashSandboxParentLinkMiddleware`，将 Bash 工具触发的沙箱拉起进度（pending / success / error）通过独立 ID `workspace:sandbox:<bash_id>` 精准挂载至当前 Bash 卡片下方，避免进度漂移至准备节点；
    *   在沙箱冷启动拉起时推流 `status: "pending"` 并弹出「正在拉起沙箱运行环境…」Info Toast；容器就绪后推流 `status: "success"` 并弹出「沙箱环境已就绪，正在执行命令…」Success Toast；失败实时推送错误原因。
*   **执行时间线 500ms 响应式时钟与全层级动态跳秒动效**：
    *   **根除 computed 缓存冻结缺陷**：彻底定位并解决因 `void tickNow.value` 被置于布尔值计算属性内部导致已等待时间永远卡死在 0s 的缺陷，建立正确的 500ms 动态响应式刷新链；
    *   **全层级覆盖**：无论处于顶级主步骤、折叠卡片子步骤（`subStep` in `child.children`）还是更深层递归（`nestedStep`），统一在单行右侧精致渲染滑动微动效条（`workspace-prewarm-bar`）与动态文案（`已等待 Ns · 阶段安抚文案`）；
    *   **三阶段安抚文案贴心优化**：
        1. 阶段一（0s ~ 4s）：`已等待 0s · 首次创建沙箱，正在申请隔离资源配置…`（标明首次冷启动，消除用户困惑）；
        2. 阶段二（4s ~ 10s）：`已等待 4s · 正在初始化沙箱工作区（拉取镜像与启动运行环境）…`（通用化表述，剔除仅特指 K8s 的 Pod 术语，适配 Docker 与 K8s 双沙箱）；
        3. 阶段三（10s 以上）：`已等待 10s · 创建工作区耗时较长，请稍候（最长约 60 秒）…`。
*   **彻底移除输入框上方挤占横条 Banner**：彻底清理历史遗留的 `DockerWorkspaceBanner.vue`，消除页面布局上下跳动（Layout Shift）与视线干扰；仅在输入框左下角保留轻量状态指示浮标，支持沙箱启停、重启、终端进入与详细信息查看。

### 3. 🧠 提示词工程跃迁：稳定前缀 KV Cache 架构优化与真实结果优先防编造体系 (Prompt Cache Architecture & Real-Result Guard)
*   **分层确定性工程化装配架构（Deterministic Layered Assembler）**：
    *   **三层生命周期解耦**：彻底摒弃传统 AI 应用中散落无序的字符串拼接（String Concatenation），构建基于生命周期与频次分层的结构化装配体系：
        1. **静态全局准则层（Static Layer）**：平台全局行为准则、格式规范、反编造准则（真实结果优先）；
        2. **智能体版本基线层（Agent Profile Layer）**：智能体角色设定（System Prompt）、工具集元数据（Tools Spec 与参数约束）；
        3. **动态瞬态上下文层（Dynamic Layer）**：多轮历史记忆、短期上下文窗口、用户实时输入与瞬态状态。
    *   **严格稳定前缀隔离法则**：新增 `prompt_cache_layout` 结构化布局配置项（`V151` / `V52`）。**在前缀区域严禁插入任何易变动态变量（如动态时间戳、轮次 ID、随机数或会话变量）**，确保前缀 Token 序列在多轮对话、跨会话乃至跨用户之间形成 100% 确定性的稳定哈希前缀。
    *   **双轨灰度与全链路可观测性（Observability & Gate Control）**：各大 Runner 严格对齐 `PromptCache` 灰度门控（`legacy` 传统路径与 `observe` 观测路径），后端全量采集并落库真实缓存命中量（`cached_tokens` / 命中率），让性能提升与算力成本账目清晰可见、可审计。
*   **KV Cache 缓存命中带来三大断崖式收益**：
    *   💰 **输入 Token 成本暴降 80% ~ 90%**：全面对齐主流大模型推理服务商（DeepSeek V3/R1、Anthropic Claude 3.5 Sonnet、OpenAI GPT-4o 等）的上下文缓存（Prompt Caching）阶梯计费机制；命中的前缀 Token 费用仅为普通输入费用的 10%~20%，在企业级 Agent 多轮对话与长工具调用循环中，API 成本直接形成断崖式下降；
    *   ⚡ **首字延迟（TTFT）大幅缩短 70%+（毫秒级爆发直出）**：跳过数千甚至数万 Token 的 GPU Prefill（预填充）重复计算，显存中的注意力矩阵直接命中复用，首字呈现延迟从数秒骤降至几百毫秒；
    *   🚀 **推理吞吐翻倍与显存减负**：极大缓解 GPU 显存带宽压力，单节点并发承载会话数成倍释放。
*   **真实结果优先系统提示词加固（泛化反编造防御）**：
    *   **新增固定提示词专节「真实结果优先：禁止凭空给出可验证数值」**：明确当任务需要获取可验证的外部或运行时事实（URL/网站连通性与 HTTP 状态码、请求/连接/DNS 耗时、传输/文件字节数、服务或端口在线状态、CPU/内存/磁盘/负载、进程列表、日志内容、软件或依赖版本、实时业务指标等），且本轮已绑定相应工具时，**必须先真正调用工具获取真实结果再回答，不得凭常识、记忆或推测替代执行**；
    *   **严禁编造可验证数值**：严禁输出只有真实执行才能得到的度量数值（如「HTTP 200」「耗时 5.7ms」「版本 3.11」），未执行或失败时须如实说明「未能真实获取」，一切以工具真实返回为准；
    *   动态系统连通性与系统状态规则同步升级，从模型执行倾向层补足工具已绑定却不调用直接脑补的缺陷。
*   **宿主文件工具优先与平台公共文档专读强约束**：
    *   明确文件读写与搜索一律走宿主侧工具（Read/Write/Edit/Glob/Grep），严禁在沙箱 Bash 中通过 `cat`、`head`、`echo >`、`sed` 等读写常规文件；
    *   **平台公共文档（`data/docs/`、`FAQ.md`）绝对路径强引导**：明确公共文档仅宿主侧可读，沙箱 Bash 不可见；强制使用宿主侧绝对路径读取（Docker 为 `/app/data/docs/...`，本地开发为项目根 `data/docs/...`），切勿把 `data/docs` 当作个人工作区相对路径；
    *   目录发现工具强引导：明确告知模型 `list_accessible_directories` 属于平台内置、始终可用工具，路径不确定时直接调用，杜绝反复试错。

### 4. 🏢 元数据物理结构巡检、整表下线与 Schema 漂移闭环治理体系 (Metadata Physical Drift Inspection & Table Drop HITL Console)
*   **物理表缺失（`table_missing_in_db`）巡检与整表安全下线**：
    *   **现存表集合快速探测**：巡检逐表扫描前，优先通过数据源适配器比对物理库中实时存活的全部物理表集合；若平台纳管表在物理库中已被 DROP，精准标记为「物理表缺失（整表已删除）」，并跳过无效列探测；
    *   **整表安全下线处置（`drop_table`）**：后端支持将物理缺失表从元数据集中安全下线，ORM 自动级联清理下属列定义，**并自动将该表关联的所有历史待处理告警一并流转为已处置（`status=1`）**，彻底消除孤儿告警；
    *   **一键批量下线**：抽屉控制台提供【🏢 一键下线全部缺失表 (N)】快捷通道与二次确认防护。
*   **物理类型不匹配（`type_mismatch`）智能校准体系**：
    *   支持单项处置【同步物理类型】与顶部【🔄 一键同步全部类型差异 (N)】，告别过去只能忽略的痛点；
    *   双重提取策略：处置时优先通过数据源连接池实时探测物理库真实数据类型；若外部数据源离线，自动通过正则从告警异常样本中提取物理类型智能兜底；
    *   字段类型校准后自动级联同步本地 Redis 向量知识库。
*   **全链路打通数据集变更日志 (Changelog) 与 Diff 审计**：
    *   对下线整表、下线字段、录入新增字段及同步物理类型等治理操作，全量调用 `ChangelogService` 记录操作人（User ID / Name）与结构化处置原因；
    *   数据集详情页【变更日志】Tab 提供高辨识度的时间线节点与字段级红绿 Diff 对比，实现数据治理全生命周期的精确溯源。
*   **全量定时物理一致性巡检体系**：
    *   元数据管理右上角菜单收拢【⏱️ 定时巡检】配置面板（仅 Admin 可见），支持常用预设周期与 5 位标准 Cron 灵活设定；
    *   **调度引擎系统任务旁路执行（零 Token 消耗）**：通过 `metadata_inspection` 系统任务直连物理库扫描比对，不经过大模型与 Agent 编排；生成专属 Trace ID，向执行历史与审计日志落库；
    *   **异常告警通知按需派发**：支持站内信（强制勾选）与外部机器人（钉钉/企微/飞书/邮件），修复表缺失指标未汇聚导致跳过告警通知的缺陷；严格遵守「仅在检出漂移异常或执行错误时通知」，正常一致时不产生打扰；
    *   **仅巡检开启数据集防护（`active_only=True`）**：自动过滤维护期与禁用状态的数据集；任务中心专属系统徽标展示与配置按钮保护。
*   **ChatBI 物理报错驱动的过时 Schema 字段自动剔除与一次性重查纠正（方案 A）**：
    *   当 SQL 执行触发真实物理报错（`unknown column` / `does not exist`）时，严格对照当前已声明 Schema。仅对 Schema 中已声明但数据库报不存在的列判定为「真实过时列」予以剔除，未声明列走常规纠错；
    *   支持单表多失效列安全剔除与 `table.column` 表前缀精准消歧；
    *   实时重构内存态 Schema 并更新本地 Preflight 防护网关，配置 `stale_column_corrected` 修复策略与 1 次重试熔断控制，杜绝死循环。

### 5. 📈 ChatBI 完整明细导出通道与经验库检索诊断模拟器 (Full Detail Data Export & Experience Diagnostic Simulator)
*   **绕过 LLM 令牌层的完整明细导出通道**：
    *   新增 `POST /api/portal/chatbi-export/result` 专属导出接口，以当前用户身份鉴权并直连物理数据源，绕过大模型 Context 长度限制；
    *   **三格式全量导出**：支持 Excel（openpyxl）、CSV（utf-8-sig，杜绝 Excel 中文乱码）以及 Markdown 表格；
    *   单次导出上限高达 `MAX_EXPORT_SQL_ROWS=100000` 行，生成带签名校验的时效性下载直链；
    *   支持安全防护：禁止非只读 SQL 导出，跨数据集联邦查询友好提示。
*   **经验库检索未命中诊断增强与测试模拟器**：
    *   提供经验案例集检索测试模拟器，支持直观调整匹配阈值、权重与语义相似度并实时查看召回得分；
    *   将经验库检索工具 `search_qa_examples` 统一纳入运行时只读白名单（`read` 权限），彻底消除工具调用时误弹出的人工审批弹窗。

### 6. 🔐 企业级身份安全与合规防护：等保三级密码合规与 2FA 双因素认证 (Security Compliance, Level-3 Password & 2FA MFA)
*   **等保三级强密码合规与周期性修改提醒**：
    *   引入符合网络安全等级保护（等保三级）标准的密码复杂度校验（必须同时包含大写字母、小写字母、数字及特殊符号，长度不得低于 8 位）；
    *   用户表新增 `password_updated_at` 字段（`V148` / `V49`），支持配置密码有效期天数（如 90 天）；
    *   提供多触点过期预警机制：临期 15 天内登录显示友好警告，过期后强制引导至修改密码流程。
*   **2FA 双因素身份验证全链路接入（TOTP）**：
    *   新增 `two_factor_secret` 与 `two_factor_enabled` 字段（`V146` / `V47`），基于标准 TOTP 协议（支持 Google Authenticator、Microsoft Authenticator 等应用）；
    *   个人中心提供 2FA 绑定与解绑向导，包括专属二维码扫描、安全密钥备份及 6 位动态验证码校验；
    *   登录环节支持密码 + 动态验证码双重挑战；用户管理列表展示 2FA 安全锁徽标，并支持管理员应急强制重置/关闭二次验证。
*   **API Key 重置与用户上次登录时间精确记录**：
    *   支持用户自主在个人中心重置 API Key 凭证（`V147` / `V48`），旧凭证即时作废；
    *   新增 `last_login_at` 字段（`V149` / `V50`），在用户管理列表与个人中心直观呈现上次登录精确时间与 IP 来源。

### 7. 🕊️ 飞书群机器人通知全链路接入与消息卡片 Schema 2.0 升级 (Feishu Webhook Notification & Card Schema 2.0)
*   **飞书群自定义机器人通知渠道全链路贯通**：
    *   在系统告警与定时任务通知渠道中正式接入飞书（Feishu）机器人支持，数据库新增对应配置与系统工具（`V150` / `V51`）；
    *   支持配置 Webhook 地址与可选的加签签名密钥（Sign Secret），保障网络通信安全。
*   **卡片 Schema 2.0 排版升级**：
    *   全面拥抱飞书开放平台最新的「卡片 Schema 2.0」协议规范，告别旧版纯文本/简单富文本交互；
    *   根据告警级别智能渲染卡片主题色（普通任务为优雅浅灰蓝，异常/漂移告警为警示琥珀红），提供结构化多列布局与平台一键直达跳转按钮；
    *   个人中心提供完整的飞书机器人配置分步图文指引，并在智能体编排中支持将其作为原生通知工具调用。

### 8. 🛡️ 系统智能体委派体系规范化与 Main 兜底专家双端锁定 (System Agent Delegation Governance & Main Agent Lock)
*   **系统智能体配置行卡片化与用途透出**：
    *   重构新建/编辑智能体抽屉中的「系统智能体」设置，由角落微型开关升级为独立的高亮配置行卡片，配专属盾牌微图标与权限徽章；
    *   明确用途说明：「只有系统智能体才会加入智能委派候选列表由主助手自动委派，普通自定义智能体不参与自动委派且仅供手动直接对话；如果只是自己用，也不必设置为系统智能体」，彻底消除用户认知盲区。
*   **Main 兜底主专家双端锁定保护**：
    *   前端识别 `isMainAgent` 时强制将系统开关禁用（展示小锁图标与「主专家固定锁定」状态标签），保存时兜底写入 `is_system = true`；
    *   后端 `AgentManager.update_agent` 加入底层拦截防线，判定为 Main 主智能体时强制保持 `is_system = True`，杜绝任何接口篡改。
*   **时间线委派智能体二合一合并与心跳降噪**：
    *   消除原先「委派智能体」工具调用与「调用子代理」生命周期事件割裂为两条记录的缺陷，合为单个清晰父级卡片 `委派智能体 · {智能体名称}`，并直接外显子智能体实时状态与完成耗时；
    *   在执行时间线中自动过滤 `✨ 开始生成回复` 内部无信息量心跳流式日志，保持步骤清晰紧凑。

### 9. 🖥️ 我的工作台全新改版：常用助手置顶 Pin、资产立体微图标与深色模式全量适配 (Personal Workbench UI/UX Overhaul)
*   **常用智能体置顶收藏 (Pin Agents)**：
    *   在工作台助手卡片中提供一键 Pin/Unpin 图钉交互，基于本地浏览器记忆实现智能体在列表中置顶优先排列，附带专属微徽标。
*   **个人资产立体微图标与视觉层次升级**：
    *   为记忆、Token、数据、技能、MCP、任务分别配置专属高辨识度微图标背景色与卡片 hover 悬浮微动效（`-translate-y-0.5 shadow-md`）。
*   **全量深色模式 (Dark Mode) 深度适配**：
    *   对工作台主视图及全部子组件（待处理、最近会话、最近产出、最近任务、下次调度、运行中等）全面补齐暗色模式类名，夜间观感温和细腻。

### 10. 🌐 分布式系统吞吐加固、公共目录管理与全局体验打磨 (Distributed Redis Audit Queue & UI/UX Polish)
*   **审计日志迁移 Redis 共享异步队列**：
    *   将高频审计日志写入由同步落库改为推入 Redis 共享队列异步消费，极大缓解高并发场景下主数据库的写事务压力。
*   **系统定时任务补齐分布式锁**：
    *   在多节点分布式部署环境下，为底层定时调度任务统一注入 Redis 分布式锁，防止多实例集群并发导致同一任务被重复触发执行。
*   **管理员对平台公共目录（docs/、skills/）安全运维赋能**：
    *   文件系统接口支持管理员对平台公共资源目录进行文件的新增、修改、重命名及永久删除，非管理员严格保持只读。
*   **数据库迁移脚本 Warning 优雅格式化与前置建库检查**：
    *   在 `db-prod/apply_sql.py` 中重载告警输出，剥离底层 `aiomysql` 内部代码调用行与文件路径暴露，消除虚假崩溃堆栈误报；
    *   增加数据库存在性前置探测，彻底消除每个 SQL 文件执行时重复打印 `Can't create database; database exists` 刷屏的痛点。
*   **工具调用确认卡片 UI/UX 轻量化重构**：
    *   移除冗余的高占位大方格与套话，空间占用压缩 70%；终端代码块等宽直显支持一键复制；决断后背景与边框自动降级为低调浅灰。
*   **系统配置页重构**：参数配置重构为左侧分组导航 + 右侧单面板布局，并移除右侧冗余折叠按钮。

---

## 🐛 Bug Fixes

### 沙箱运行时 / 容器隔离 / 调度运维
*   **沙箱权限预检 NoneType 崩溃修复**：修复在按需沙箱模式下 `LazySandboxBashNativeTool` 与 `WorkspaceFileToolProxy` 权限预检偶发返回 `None` 导致 `'NoneType' object has no attribute 'behavior'` 的严重报错，补齐工具链空安全兜底。
*   **K8s 空 PVC 自动补建公共 docs**：修复在 Kubernetes 环境首次挂载空 PVC 时，因缺少 `data/docs` 目录导致 Grep 工具抛出 `Directory not found` 的问题，启动时自动递归补建初始目录结构。
*   **K8s 沙箱网关工具链依赖缺失**：补齐 Kubernetes 沙箱网关内 AgentScope 运行依赖，修复 Bash 执行偶发 HTTP 500 的故障。
*   **沙箱初始化超时兜底**：为沙箱环境初始化添加超时守卫（预热 60s / 执行 90s），针对 uv 虚拟环境创建增加 `UV_VENV_CLEAR` 幂等清理。
*   **沙箱初始化失败优雅降级**：当 Docker 守护进程未启动或 Kubernetes Pod 创建失败时，一律优雅降级为宿主安全模式保障普通聊天不中断，并明确向用户透传降级说明。
*   **Pod Terminating 状态展示**：在沙箱详情面板中将处于 Terminating 终止过程中的 Pod 状态正确显示为“终止中”，避免前端误显为运行中。

### 聊天流 / 时间线动效 / 前端交互
*   **执行时间线时钟缓存穿透修复**：解决 Vue 3 computed 属性缓存优化导致沙箱预热跳秒永久停留在 0s 的缺陷，恢复 500ms 动态响应式刷新。
*   **时间线折叠子节点动效漏渲染修复**：补齐在「1 个工具调用」折叠卡片内部 Bash 子步骤（`subStep` 与 `nestedStep`）漏渲染预热跳秒动效与阶段文案的问题。
*   **输入框间距与空悬容器消除**：优化聊天消息列表与输入框的边距，彻底清除沙箱 Banner 移除后残留的空悬容器与 Markdown 尾部多余留白。
*   **执行卡片总耗时提前冻结修复**：修复执行卡片总耗时在工具尚未完全返回前被错误提前冻结并记录不准的问题。
*   **会话中断恢复二次审批状态防覆盖**：优化中断会话恢复时的状态流转，防止 `awaiting_permission` 工具审批状态被后续事件意外覆盖。
*   **浏览器自动化参数容错与自动滚屏**：修复 `browser_wait_for` 参数不匹配与超时报错，将上限提升至 30s 并支持快照优雅降级；服务端浏览器安装日志支持实时滚屏触底。
*   **下载链接防误转预览**：内部接口地址与大模型生成文件下载直链隐藏内置打开按钮，防止被误识别为本地预览。

### 元数据 / 定时巡检 / 数据治理
*   **定时巡检遗漏物理表缺失告警修复**：修复在元数据定时巡检中未正确汇聚 `missing_tables_count` 指标，导致物理表缺失时被误判为“结构完全一致”而漏发告警通知的问题。
*   **通知双标题冗余修复**：定位并剔除告警通知在正文首行冗余拼接 Markdown 标题的逻辑，彻底根除站内信与群机器人卡片中标题重复展示两遍的缺陷。
*   **过时 RAGFlow 提示纠正**：彻底清除元数据治理操作后遗留的“请随后手动同步至 RAGFlow”的误导文案，统一校准为“（已自动同步向量知识库）”。
*   **ChatBI 经验库未命中排查**：增强经验库检索未命中时的结构化诊断日志，便于快速排查为什么大模型未命中案例。

### 认证 / 安全 / 权限管控
*   **未成对孤儿 User 消息回滚**：当后端接口返回非 2xx 异常（如超额、鉴权失败）时，自动回滚前端已发送的孤儿用户消息气泡并透出精确错误原因。
*   **内部记忆检索泛时间词识别**：优化记忆检索对“昨天”、“上周”、“前几天”等相对泛时间词的语义消歧与匹配兜底。
*   **2FA 管理员强制解绑**：当用户丢失验证器时，管理员可在后台一键安全解绑其 2FA，并生成审计日志。

---

## 🗄️ Database Migrations (表结构与配置变更清单)

本次版本包含 **9 组双数据库（MySQL / PostgreSQL）同步迁移脚本**，涵盖用户 2FA 认证、密码合规、飞书工具、提示词缓存、Kubernetes 沙箱及元数据漂移告警表：

| 版本号 (MySQL / PG) | 变更说明 | 涉及数据表 / 配置项 |
| :--- | :--- | :--- |
| `V146` / `V47` | 新增用户 2FA 双因素认证支持 | `sys_users.two_factor_secret`, `sys_users.two_factor_enabled` |
| `V147` / `V48` | 新增隐藏登录 API Key 配置 | `system_configs` 增加 `hide_login_apikey` 配置项 |
| `V148` / `V49` | 新增密码最后修改时间与过期合规策略 | `sys_users.password_updated_at`, `system_configs` 增加密码有效期配置 |
| `V149` / `V50` | 新增用户最后登录时间记录 | `sys_users.last_login_at` |
| `V150` / `V51` | 系统工具集注册飞书群自定义机器人通知工具 | 注册 `feishu_group_notification` 系统工具定义 |
| `V151` / `V52` | 新增提示词缓存装配布局与观测配置项 | `system_configs` 增加 `prompt_cache_layout`, `prompt_cache_mode` |
| `V152` / `V53` | 新增 Kubernetes 沙箱环境核心配置项 | `system_configs` 增加 `sandbox_k8s_image`, `sandbox_k8s_namespace` 等 |
| `V153` / `V54` | 新增沙箱自动化与生命周期管理配置项 | `system_configs` 增加 `sandbox_auto_warm`, `sandbox_idle_time` |
| `V154` / `V55` | 创建元数据 Schema 漂移异常告警持久化表 | 新建 `meta_schema_drift_alerts` 表（存储物理表/列缺失、类型不匹配告警及处理状态） |

> **升级提示**：启动前请确保执行平台数据库迁移脚本。MySQL 环境执行 `bash db-prod/apply-sql.sh`，PostgreSQL 环境执行 `bash db-prod-pg/apply-sql.sh`，脚本具备完整的幂等性检查与优雅日志格式化。

## 📖 Upgrade Guide (平滑升级指引)

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

   # PostgreSQL 用户执行：
   bash db-prod-pg/apply-sql.sh
   ```

3. **依赖更新与启动本地服务**：
   ```bash
   # 后端依赖检查（Python 3.11）
   poetry install --no-root

   # 前端构建或启动本地开发
   ./dev.sh
   ```

---

### 方式二：Docker 容器化升级（生产环境推荐）

#### 1. 导入官方 Release 镜像归档（推荐）
从 GitHub Releases 下载对应服务器架构的预编译 Docker 镜像归档包：
```bash
# 1. 执行数据库结构平滑迁移
bash db-prod/apply-sql.sh  # PG 用户执行: bash db-prod-pg/apply-sql.sh

# 2. 导入镜像归档（按架构选择）
# x86_64 服务器
docker load -i nanzi-ai-agent_1.0.15.0_linux-amd64_*.tar

# ARM64 服务器（鲲鹏 / Ampere / Apple Silicon 等）
docker load -i nanzi-ai-agent_1.0.15.0_linux-arm64_*.tar

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
./build_linux_x86.sh 1.0.15.0   # ARM64 服务器执行: ./build_linux_arm.sh 1.0.15.0

# 3. 启动并验证容器
./start-nanzi-ai-agent.sh
```

---

### 方式三：Kubernetes 沙箱环境初始化（若启用 k8s 沙箱策略）

```bash
# 1. 检查集群就绪状态与命名空间 RBAC 权限
bash k8s_deploy/nanzi-k8s.sh health

# 2. 预置/构建沙箱网关运行时镜像
bash k8s_deploy/build-k8s-sandbox-image.sh
```

---

## 👥 Contributors & Acknowledgements

特别鸣谢所有参与 NanZi AI Agent Platform v1.0.15.0 版本需求讨论、代码贡献、架构演进及线上验证的团队成员与社区开发者！平台将持续践行极客、稳定、高性能的架构演进，助力企业打造最值得信赖的自主智能体生产力中枢。
