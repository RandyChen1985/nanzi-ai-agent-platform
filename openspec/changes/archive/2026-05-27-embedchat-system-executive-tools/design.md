## Context

在目前的南孜智能体平台（EmbedChat）中，大部分已注册的静态工具为轻量级的应用级接口（如 JIRA 对接、用户认证、日志查询等）。当智能体面对需要进行深度文件分析、系统诊断、临时 SQL 沙箱建模、网页渲染抓取以及代码合规检查等复杂的“高级系统工程师/数据分析师”场景时，缺乏底层的执行工具支撑。

此外，智能体目前的上下文记忆主要局限于“短期会话上下文”，大模型在开启新会话或 Token 截断后，无法维持对用户偏好、习惯及核心事实的长期个性化认知。

由于南孜智能体平台本身天然运行在 Docker 隔离容器中，容器边界构成了完美的物理安全防线。这使得我们可以直接在容器内部授权高权限工具（如执行 shell 命令、读写容器内文件等），而无需过度担忧宿主机的物理安全。本方案旨在为智能体扩充 8 个容器自治的系统与高阶辅助工具，以及 2 个长期记忆（LTM）工具，并建立无感知的 Redis 记忆注入管道。

## Goals / Non-Goals

**Goals:**
- **容器自治工具集**: 扩展 4 个系统自治工具（读、写、命令执行、进程管理）与 4 个高阶进阶工具（SQLite 沙箱、目录检索、网页无头渲染、代码 Lint），提供健壮的防穿越与超时拦截机制。
- **SQLite 临时沙箱**: 实现会话级 SQLite 文件自动初始化及读写机制，使智能体能够导入 JSON 临时数据并执行 SQL 多维分析，保证对生产库零污染。
- **Playwright 双模态分析**: 提供基于 Playwright 的无头网页渲染及截图功能，向智能体返回“网页截图媒体路径 + 结构化 Markdown 文本”，支持 Vision 视觉交互。
- **Redis 长期记忆 (LTM) 引擎**: 基于 Redis Hash 数据结构，实现长期记忆的异步写入、查询。
- **无感记忆注入管道**: 在智能体对话生命周期入口，自动并发读取 Redis LTM 并将其注入 System Prompt 的 `[Memory Profile]` 块，在对对话调度流零侵入的前提下，赋予智能体长期个性化认知。

**Non-Goals:**
- **外部安全沙箱**: 不在本容器内另外实现虚拟机级内核沙箱（如 gVisor/Kata），以充分利用 Docker 本身的隔离屏障，达到极速开发部署效果。
- **短期会话记忆替代**: 本方案仅聚焦于 LTM，不废除或替代原有的短期会话存储机制（如 Redis List 形式的滚动 STM ），两者在 Prompt 层并存。

## Decisions

### 1. 系统自治工具设计与安全网关

为防止路径穿越以及由于智能体误操作引发容器崩溃，对以下工具进行严格边界防御：
- **`read_local_file` & `write_local_file`**:
  - 强制使用 `os.path.abspath` 对输入路径进行规范化，校验其前缀是否被局限在合法数据目录（如 `/app/data/uploads`、`/app/data/skills`）中。
  - 读取时，限制单次返回的最大字节数（默认 `max_bytes=1024*512` 即 512KB），并提供 `offset` 参数支持分页读取；支持 `tail` 参数以直接读取日志尾部。
- **`execute_system_command`**:
  - 底层基于 Python `subprocess.Popen` 执行，强制设定默认 `timeout=30` 秒以拦截挂死进程。
  - 对 stdout/stderr 的输出限制在 `100KB`，防止因大量控制台日志爆仓导致大模型 Token 撑破。
- **`manage_system_process`**:
  - 通过 `psutil` 列出进程，明确标识当前 Web 主服务进程 PID（通过 `os.getpid()` 捕获）以及守护进程。
  - 在终止进程操作时，校验目标 PID 是否在受保护的核心主进程列表中，若是则予以拒绝，防止智能体误杀 Web Server。

### 2. SQLite 会话级临时沙箱 (`sqlite_scratchpad`)

- **沙箱数据库生命周期**:
  - 每一个新对话会话，在 `/app/data/sandbox/` 下动态建立一个独立的 SQLite 数据库文件 `sess_{session_id}.db`。
  - 该数据库在对话创建时按需建立，并在会话清理或通过定期 Cron 任务进行无感物理删除。
- **数据分析流**:
  - 智能体可调用工具在沙箱中创建表，将 JSON 数据流解析并导入。
  - 智能体调用该工具执行复杂的 `GROUP BY`、`JOIN` 等 SQL 分析，系统返回 Markdown 表格结果，保证分析计算在完全无污染的主库外环境下进行。

### 3. 基于 Playwright 的无头渲染器 (`web_renderer_and_snapshot`)

- **双模态输出**:
  - 工具接收外部 URL，并在后台调用 Playwright 无头浏览器渲染页面。
  - 异步捕获页面快照，以 PNG 格式保存至媒体工件目录 `/app/data/uploads/media/` 中，将物理路径以 Vision Mode 支持的方式返回给智能体。
  - 使用 HTML 解析库去除冗余标签，转换成干净的可读 Markdown 文本一并返回，即使在非 Vision 模型下，智能体也能“读懂”网页。

### 4. 长期记忆 (LTM) 数据结构与无感注入管道

- **Redis 双轨哈希存储结构**:
  - 长期记忆 key 设计为 `nanzi:agent:ltm:{user_id}`。
  - 采用 Redis `Hash` 数据类型，字段规划为：
    - `user_preferences`: 结构化存储用户的代码风格、语言偏好、系统偏好（JSON）。
    - `core_facts`: 记录关于用户角色的核心事实、历史遗留背景（JSON）。
    - `summary_profile`: 记录用户过往对话周期的画像摘要。
- **两级读写模式**:
  - **显式管理**: 智能体在识别到重要长期偏好时，调用 `update_user_preference` 对 Hash 结构进行异步写入更新；需要时调用 `fetch_user_long_term_memory`。
  - **隐式无感注入**:
    - 在 `app/services/chat_service.py` 触发 LLM 推理组装 Message 前，系统使用 `asyncio.gather` 并发读取 Redis LTM（由于 LTM 极小，配合极短的超时控制如 200ms 以防止 Redis 抖动拖慢对话响应）。
    - 将读取到的 LTM 格式化拼接至 System Prompt 的头部：
      ```text
      [Memory Profile]
      - User Preferences: {preferences_json}
      - Core Facts: {core_facts_json}
      ```
    - 组装后的完整 Prompt 发送给 LLM，实现用户无感感知、智能体在首轮对话即具备长期记忆。

### 5. 前端工具能力集卡片化与分组化 UI 设计

- **双列网格卡片布局**：
  - 将原本单列的工具列表修改为双列卡片布局（`grid grid-cols-2 gap-3`），并在滚动区域（`max-h-[450px]`）做自适应响应式排版。
  - 增加 Hover 微动画与过渡效果（`transition-all hover:border-primary/50 hover:bg-gray-50/50 hover:scale-[1.01] duration-150`），选中卡片呈现柔和阴影与深蓝底色。
- **智能过滤与分组机制**：
  - 在前端通过定义 `groupedTools` 的 `computed` 属性，通过工具名称正则匹配，将工具分类为“📊 ChatBI 数据分析”、“📖 知识库检索 (RAG)”、“💻 容器系统自治工具”、“💬 办公协作与消息通知”、“🧠 长期事实与记忆引擎”、“🔧 其他扩展工具”等组，以精美胶囊小标题分隔呈现。


## Risks / Trade-offs

- **[Risk] Playwright 在 Docker 镜像中缺少 Linux 底层库依赖**
  - *Mitigation*: 在 `dev.sh` 编译和 Docker 构建脚本中，增加 `playwright install-deps` 及 `playwright install chromium` 操作，确保依赖库在初始化时完整可用。
- **[Risk] 频繁读写 Redis 带来的并发网络开销**
  - *Mitigation*: 读操作在对话会话层级进行并发（与短期上下文及 Prompt 模板组装并发执行），写入使用异步非阻塞线程，极大地减小时延影响。
- **[Risk] 智能体写入破坏性脚本运行**
  - *Mitigation*: 因为运行在天然的 Docker Container 中，即使崩溃也只是容器受损，只需自动拉起新容器实例，极大降低了安全风险；同时依然做好目录穿越拦截。
