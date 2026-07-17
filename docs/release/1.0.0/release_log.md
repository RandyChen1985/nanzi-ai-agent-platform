# 🎉 NanZi AI Agent Platform v1.0.0 Release Notes

Welcome to the **first official release** of NanZi AI Agent Platform (南孜智能体平台)! 🚀

**GitHub Repository**: [RandyChen1985/nanzi-ai-agent-platform](https://github.com/RandyChen1985/nanzi-ai-agent-platform)

南孜 AI Agent 平台（`yovole-nanzi-ai-agent-platform`）是一个面向企业级的高性能、高安全、强审计的 **多智能体编排与数据智能运营中枢**。v1.0.0 正式版提供了多智能体并行调度、物理数据库直连分流、全链路多维记忆引擎、容器自治沙箱执行及企业级 Token 级审计看板等核心基础能力。

---

## 🚀 Key Features

### 1. 🤖 Multi-Agent Orchestration & CoT Routing

* **智能路由 (Router V2/CoT)**：基于思维链（CoT）与指代消解算法，自动提取上下文中的实体指代，并模糊/忽略大小写匹配分发至对应的专家智能体。
* **多智能体并行执行**：支持并发调度多个子智能体（Secondary Agents）并行计算，并在 Trace 步骤中进行流式聚合，最终由主智能体进行 Synthesis 汇总输出。
* **设备与屏幕自适应**：智能感知桌面宽屏/移动竖屏，自适应下发最适合当前屏幕排版的指令提示（如移动窄屏下强制禁用宽表格改用卡片或列表格式）。

### 2. 🔀 ChatBI & Raw SQL Routing

* **多物理数据库直连 Adapter**：原生支持 MySQL、ClickHouse 和 Oracle（适配 Thin 与 Thick 驱动），统一集成 `standardize_items` 字段类型标准化处理。
* **连接池健康自愈**：引入连接池状态指纹自愈检测，保障数据库连接闪断下的服务持久可用。
* **数据查询安全干跑与导出**：内置只读 SQL 校验、LIMIT 范围强制拦截以及 Dry-run 试运行评估。支持一键将 Trace 里的 SQL 结果导出为 CSV/Excel（带 UTF-8 BOM）文件提供下载。

### 3. 🧠 Unified Memory Engine

* **长期偏好记忆 (LTM)**：构建基于 Redis HASH 的个性偏好与事实事实画像异步抽取和并发注入管道（前置 200ms 短超时保护）。
* **无感前置记忆回忆 (Active Memory Ingest)**：大模型无需多消耗一轮 Tool-call 的往返延迟，系统会在首轮对话消息装配时智能捕获用户最新 query。若匹配相对时间或回顾词，后台静默调阅当日每日摘要、会话记录或最近 3 条会话摘要，直接以 `[System Preloaded Memories]` 动态灌入 Prompt。
* **记忆检索时间优化**：优化 `memory_search` 工具，使其支持对“今天”、“昨天”、“X天前”等中文相对时间词和具体日期的智能提取，将每日摘要与当日会话列表进行去重整合并分级呈现。
* **“我的记忆”隐私自主中心**：在前端个人中心新增“我的记忆”面板，支持会话级记忆过滤与物理清除，以及个人长期事实偏好（LTM）的 CRUD 自主修剪。后端强绑定当前用户 ID 彻底杜绝水平越权（IDOR）隐患。

### 4. 🧳 System Executive & Combo Web Search

* **受控本地沙盒执行**：提供安全的 `execute_system_command`、`read_local_file` 和 `write_local_file` 工具，严格拦截路径穿越并阻断高危命令。内置 `sqlite_scratchpad` 工具为大模型提供轻量 SQLite 演算沙盒。
* **百度深度网页检索 (Combo Web Search)**：重构 `web_search_baidu` 工具。在检索出百度列表后，后台自动高并发对前 2 个加密跳转链接使用 `httpx`（设置 `follow_redirects=True`）跟踪重定向获取真实源网页，调用 `BeautifulSoup` 脱水清洗噪点，切片前 600 字正文一并返回，彻底克服了大模型因懒于二次调用而脑补回答的通病。
* **极速静态网页抓取**：提供 `fetch_static_web_url` 网页抓取通道，防范内网 SSRF。
* **智能体技能自动生成**：大模型可在运行期间通过 `create_skills` 物理生成规范的 `.agent/skills/` 技能文件并即时挂载。

### 5. 🛡️ Enterprise Security & Audit

* **Token 消耗多维统计看板**：在 Trace 阶段自动求和 Input/Output Token，后端提供 trends 走势、Agent 占比排行及用户配额账单；前端新增 Token 消耗专项统计看板。
* **审计日志字段脱敏**：敏感操作字段（如密码、API Key 等）在存入 MySQL/ClickHouse 审计表前被深度脱敏；支持 TraceStep 多维高级过滤与执行链路追踪。
* **SSO 单点登录胶囊控制开关**：实现与 Yovole SSO 单点登录的一键联通。系统参数设置页提供“SSO 控制开关”胶囊，关闭时自动隐藏登录页 SSO Tab 及用户页“同步用户”按钮，保护后端端点。

---

## 🛠 Improvements & Stability

* **防浏览器强刷机制**：强制注入 `Cache-Control: no-cache` 策略，解决前端编译发版后用户浏览器无法热更新的缓存问题。
* **IME 中文输入法拦截优化**：修复了在聊天输入框中文拼音输入组合期间按下 Enter 键会误触发发送空消息的 Bug。
* **Docker 编译内存优化**：采用宿主机极速预构建前端静态资源再拷入容器 of Vite 编译机制，避免了在低配置 VPS 内直接通过 Vite 编译导致 OOM 崩溃。
* **一键 SQL 迁移及配置工具**：提供纯 Bash 版的原生导入工具 `apply-sql-native.sh`，支持目标数据库不存在时自动创建，并默认随附灌入初始化管理员，实现“开箱即用”。

---

## 📦 Quick Start

请参考项目根目录下的 [HOW_TO_INSTALL.md](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/HOW_TO_INSTALL.md) 进行部署。

### 本地快速开发模式启动：

```bash
# 1. 准备并配置本地 python venv 虚拟环境
python3 -m venv venv
source venv/bin/activate

# 2. 安装 Python 及前端依赖
pip install -r requirements.txt
cd frontend && npm install && cd ..

# 3. 运行初始化 SQL 脚本
./db-prod/apply-sql-native.sh

# 4. 执行前台编译与服务启动
./dev.sh
```

---

## 💾 Downloads / Assets

本项目发布版本关联的源码、Docker 镜像资产归档包及配置文件如下：

* 📦 **Source Code (zip)**: `nanzi-ai-agent-platform-1.0.0.zip`
* 📦 **Source Code (tar.gz)**: `nanzi-ai-agent-platform-1.0.0.tar.gz`
* 🐳 **Docker Image for Linux amd64 (x86_64)**: `nanzi-ai-agent_1.0.0_linux-amd64_20260529.tar` (适用于大部分云服务器及物理服务器)
* 🐳 **Docker Image for Linux arm64 (aarch64)**: `nanzi-ai-agent_1.0.0_linux-arm64_20260529.tar` (适用于 Apple Silicon Mac/鲲鹏/飞腾等 ARM 架构)
* ⚙️ **Docker Compose YAML file**: `docker-compose.yml` (Docker 一键部署配置文件)

🔗 **下载地址**: [GitHub Releases v1.0.0](https://github.com/RandyChen1985/nanzi-ai-agent-platform/releases/tag/1.0.0)

### 🐳 如何在离线/内网环境加载 Docker 镜像归档：

下载对应的 `.tar` 镜像包后，在终端执行以下指令快速载入并启动容器：

```bash
# 1. 加载本地镜像归档
docker load -i nanzi-ai-agent_1.0.0_linux-amd64_20260529.tar

# 2. 检查是否加载成功
docker images | grep nanzi-ai-agent

# 3. 利用 docker-compose 快速拉起服务
docker-compose up -d
```

---

## 🤝 Contributors

Special thanks to all developers who contributed to this release!

For complete changes and test files check out [CHECKLIST.md](https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/tests/CHECKLIST.md).
