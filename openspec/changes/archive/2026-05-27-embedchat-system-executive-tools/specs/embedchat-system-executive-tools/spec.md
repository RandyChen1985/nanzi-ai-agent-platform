## ADDED Requirements

### Requirement: read_local_file 读取本地文件
系统必须（MUST）提供 `read_local_file` 工具以分页、截断、倒序（Tail）的方式读取容器内的文件。系统必须限制单次读取的最大字节数以防止 Token 暴涨，并防止路径穿越漏洞（限制在指定根目录或合法工作目录下）。

#### Scenario: 成功分页读取本地大文件
- **WHEN** 智能体请求读取路径为 `/app/data/uploads/logs.txt` 且偏移量为 `1024`，限制长度为 `2048` 字节的文件
- **THEN** 系统返回该文件的指定切片字节流，并指示是否到达文件末尾（EOF）

#### Scenario: 路径穿越安全防护拦截
- **WHEN** 智能体请求读取路径为 `/app/data/uploads/../../../etc/passwd` 的文件
- **THEN** 系统拦截此请求，并返回包含“路径越界”的安全错误提示，确保文件读取始终被限制在安全沙箱目录内

---

### Requirement: write_local_file 写入本地文件
系统必须（MUST）提供 `write_local_file` 工具，以允许智能体直接写入或覆盖容器内的指定文件。系统必须在写入前自动创建缺失的父级目录。

#### Scenario: 成功写入或覆盖文件
- **WHEN** 智能体提供文件路径为 `/app/data/skills/my_skill.py` 以及 Python 源码内容
- **THEN** 系统自动创建 `/app/data/skills` 目录（如果不存在），将源码完整写入指定路径，并返回写入成功确认和文件大小

---

### Requirement: execute_system_command 运行系统命令
系统必须（MUST）提供 `execute_system_command` 工具以允许智能体在容器内部执行指定的 shell 命令。系统必须对命令执行施加默认 30 秒的强制超时限制，并限制返回的标准输出和错误流的最大长度，防止命令挂死或消耗过多资源。

#### Scenario: 成功执行命令并在超时前返回
- **WHEN** 智能体请求在 shell 中执行 `python -m unittest tests/test_math.py`
- **THEN** 系统在 30 秒内完成执行并返回 stdout 和 stderr 的组合内容

#### Scenario: 命令执行超时保护拦截
- **WHEN** 智能体请求执行一个阻塞性命令如 `sleep 100`
- **THEN** 系统在到达默认的 30 秒超时上限时强制终止该命令进程，并返回超时警告及已捕获的阶段性输出

---

### Requirement: manage_system_process 系统进程管理
系统必须（MUST）提供 `manage_system_process` 工具，允许智能体查看当前容器内的运行进程列表，并在获得必要参数时终止指定的非核心进程。系统必须（MUST）内置核心主进程的安全防护，防止智能体误杀大本营进程（如应用后端主 PID 和开发构建守护进程）导致系统崩溃。

#### Scenario: 成功列出当前进程并安全过滤
- **WHEN** 智能体请求获取容器内的进程列表
- **THEN** 系统返回当前运行的进程信息（包括 PID、Name、CPU/Memory 占用），并明确标识哪些是系统受保护的核心主进程（无法被终止）

#### Scenario: 拒绝终止受保护的核心主进程
- **WHEN** 智能体企图终止 PID 为 FastAPI 服务核心主进程的 PID
- **THEN** 系统拒绝执行终止操作，返回“权限不足或受保护的核心进程”的安全拦截提示

---

### Requirement: sqlite_scratchpad 会话级 SQLite 临时沙箱
系统必须（MUST）为每个会话动态创建并挂载一个独立的、轻量的 SQLite 沙箱数据库文件。系统必须（MUST）提供 `sqlite_scratchpad` 工具以允许智能体在沙箱中导入结构化数据，执行 SQL 多维联查、分组与聚合，确保对生产 ClickHouse/MySQL 数据库零污染。

#### Scenario: 会话级 SQLite 数据清洗与复杂分析
- **WHEN** 智能体提供多份临时 JSON 结构数据，并请求在会话沙箱中将其导入临时表，随后发起 `GROUP BY` SQL 联查
- **THEN** 系统在临时 SQLite 中执行并成功返回标准的结构化联查结果集

---

### Requirement: directory_tree_navigator 目录树深度检索导航
系统必须（MUST）提供 `directory_tree_navigator` 工具，以便智能体递归或分层检索容器内目标目录下的结构，支持按文件后缀过滤和关键字检索，辅助智能体快速定位代码文件或配置文件。

#### Scenario: 递归检索指定目录下的特定文件
- **WHEN** 智能体发起对 `/app/services` 的深度导航请求，指定过滤后缀为 `.py`
- **THEN** 系统返回该目录下所有 Python 文件的相对路径、大小以及嵌套树级结构，帮助智能体精准定位目标文件

---

### Requirement: web_renderer_and_snapshot Playwright 网页渲染与截图
系统必须（MUST）集成 Playwright 组件并提供 `web_renderer_and_snapshot` 工具，以允许智能体在无头模式下渲染外部网页，并将网页以图片截图（PNG/JPEG）及可读 Markdown 格式返回。配合 Vision 大模型，使智能体能够获取并“视觉看懂”网页内容。

#### Scenario: 无头渲染外部网页并生成视觉截图
- **WHEN** 智能体传入一个合法的外部 URL 链接，要求对其进行渲染和截图
- **THEN** Playwright 在后台加载该网页，捕获页面视口截图保存为本地媒体工件，并提取该网页的可读文本，一并返回给智能体

---

### Requirement: code_syntax_linter 代码静态语法检测
系统必须（MUST）提供 `code_syntax_linter` 工具，允许智能体在写入代码或进行热重载前，对 Python 或 JavaScript 源码进行静态语法检查与合规性扫描，提前拦截语法错误和拼写异常。

#### Scenario: 代码语法检查拦截语法错误
- **WHEN** 智能体提供了一段带有明显语法错误的 Python 源码（如缺失冒号）进行 lint 检查
- **THEN** 系统检测出语法错误，并返回精确的错误行号及错误描述，阻止后续的写入或编译流程

---

### Requirement: update_user_preference 记忆自主更新
系统必须（MUST）提供 `update_user_preference` 工具，允许智能体在对话中捕捉到用户的个性偏好、行为习惯或长期事实时，异步将这些信息以键值对（Key-Value）形式持久化写入 Redis 长期记忆哈希（Hash）结构中。

#### Scenario: 自动捕捉并持久化用户偏好
- **WHEN** 用户说“我更偏好使用 dark mode 暗黑主题”，智能体调用该工具更新键值对 `theme: dark`
- **THEN** 系统成功在 Redis `nanzi:agent:ltm:{user_id}` 哈希中存入该偏好，并返回成功回执

---

### Requirement: fetch_user_long_term_memory 长期事实记忆查询
系统必须（MUST）提供 `fetch_user_long_term_memory` 工具，允许智能体在必要时主动按 Key 或字段模糊检索当前用户存储在 Redis 哈希中的所有核心事实和偏好记录。

#### Scenario: 主动检索用户的历史事实记录
- **WHEN** 智能体调用该工具查询当前用户的长期记忆
- **THEN** 系统从 Redis 快速读出所有已记录的用户偏好和核心事实，并以结构化 JSON 格式返回给智能体

---

### Requirement: 无感记忆加载与 System Prompt 注入管道
系统必须（MUST）在智能体与 LLM 交互的对话生命周期入口，自动并发、异步地从 Redis 读取 `nanzi:agent:ltm:{user_id}` 长期记忆内容。系统必须将这些内容格式化为 `Memory Profile`，并无感地注入到发送给大模型的 System Prompt 的头部，无需智能体在每轮对话中手动调用检索工具。

#### Scenario: 用户新对话自动注入长期事实偏好
- **WHEN** 用户新发起一轮对话，系统开始组装 System Prompt
- **THEN** 系统并发异步查询 Redis 并拉取该用户的长期偏好，将其无感渲染在 System Prompt 中，最终使大模型直接呈现出感知到用户偏好（如暗黑主题）的拟人化响应

---

### Requirement: 前端工具能力集卡片化与分组化重构
系统必须（MUST）对“版本详情”弹窗中的“工具能力集”列表进行交互与视觉重构。系统必须将原有的单列工具平铺列表重构为精美的双列卡片（Grid）布局。系统必须根据工具的命名及功能，实现全自动的智能分组，并使用带有气泡图标的胶囊标题区隔各组，提供顺滑的悬停（Hover）与选中微动画，提升高密数据的阅读与编辑体验。

#### Scenario: 成功加载双列网格且智能分组
- **WHEN** 管理员打开版本详情弹窗，且静态工具列表已成功加载
- **THEN** 界面将工具自动分为 “ChatBI 数据分析”、“知识库检索 (RAG)”、“容器系统自治工具”、“办公协作与消息通知” 等组，每一组下的工具呈双列卡片排列，并提供平滑的选中激活样式

