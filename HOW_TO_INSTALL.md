# 南孜智能体平台部署安装指南 (HOW TO INSTALL)

本指南旨在指导开发和运维人员在本地开发环境或生产环境下完成 **南孜智能体平台 (NanZi AI Agent Platform)** 的安装部署、数据库表结构初始化以及常见问题的快速排查。

---

## 1. 概要 (Overview)

南孜智能体平台是企业级的多智能体编排与数据智能洞察（ChatBI）系统。为了适应不同用户的环境，平台主要提供两套部署安装方案：
*   **Docker 容器化部署（生产首选，支持离线）**：通过位置参数指定版本号构建 Docker 归档包，可在隔离容器环境下运行并一键部署。
*   **本地源码开发调试部署（开发首选）**：使用 Python 虚拟环境与 Node.js 宿主机环境，支持前后端热重载实时开发联调。

无论采用何种部署方案，服务拉起前均需要完成 **MySQL 数据库结构与初始管理员账号数据**的导入。

---

## 2. 前置环境 (Prerequisites)

在开始部署平台之前，请确保当前环境满足以下各项依赖条件：

### 💻 基础工具依赖
*   **Docker**（建议 v20.10+） 与 **Docker Compose**（建议 v2.0.0+）
*   **Python**（建议 v3.10+，仅用于本地源码调试或运行 SQL 导入工具。如果直接使用已打好的 Docker 镜像包部署，则无需安装）
*   **Node.js**（建议 v18+ & npm，仅用于本地开发联调或宿主机前端预构建。如果不自行构建镜像且不进行本地源码调试，则无需安装）

### 🔌 数据库与外部依赖服务
*   **MySQL**（建议 v8.0+）：必须支持 `utf8mb4` 字符集，用以存放平台系统级配置、角色权限、审计日志及智能体元数据。
*   **Redis**：**必须使用支持向量检索的 Redis Stack 版本**（例如 `redis/redis-stack-server:latest`），用以支持平台内部高并发缓存、长期记忆（LTM）向量检索、向量搜索诊断以及分布式异步调度队列（APScheduler）。
*   **RAGFlow 生态（若使用知识库和 ChatBI，则为必选）**：如需接入非结构化 SOP 知识库或使用 ChatBI 数据洞察功能，必须保证 RAGFlow 服务就绪并提供相应的 API URL 与 API Key。更多信息请参考 [RAGFlow 官网](https://ragflow.io/)。

---

## 3. 部署流程 (Deployment Flow)

### 3.1 第一步：MySQL 数据库结构初始化
平台采用版本化迁移管理（数据库脚本位于 `db-prod/` 目录下）。导入方法如下：

1.  **手动创建数据库**：
    登录您的 MySQL 服务，手动创建一个干净的数据库，确保使用 `utf8mb4` 字符集以完美支持中文字符与 Emoji：
    ```sql
    CREATE DATABASE IF NOT EXISTS `nanzi_ai_agent_platform` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
    ```

2.  **执行结构自动初始化（提供以下两种途径）**：

    *   **途径一：使用 Python 工具导入（推荐）**
        需要在本地准备 Python 虚拟环境并安装 `aiomysql` 依赖：
        ```bash
        # 激活 Python 虚拟环境并安装依赖
        python3 -m venv venv
        source venv/bin/activate
        pip install -r requirements.txt
        
        # 授权并执行
        chmod +x db-prod/apply-sql.sh
        ./db-prod/apply-sql.sh
        ```
        *注：根据交互提示输入 Host、Port、User、Password 及数据库名，输入 `YES` 即可执行。*

    *   **途径二：免 Python 依赖的纯 Shell 脚本导入**
        仅依赖系统已安装的 `mysql` 命令行客户端。具备与 Python 脚本等价的幂等性过滤机制（自动跳过重复建表、重复列等容错）：
        ```bash
        # 授权并执行原生 Shell 导入脚本
        chmod +x db-prod/apply-sql-native.sh
        ./db-prod/apply-sql-native.sh
        ```
        *注：根据提示输入 Host、Port、User、Password 及数据库名，输入 `YES` 即可。*

3.  **导入默认管理员账号与预置 API Key（可选）**：
    若是首次部署，建议导入管理员数据以建立系统初始连接。
    *提示：在第 2 步执行结构初始化时，导入脚本在执行完毕后会**自动弹出询问一键级联导入该数据**，若您当时已选择导入，此步骤可跳过。若当时选择了跳过，也可通过以下命令随时**手动单独导入**：*
    *   **使用 Python 工具**：
        ```bash
        ./db-prod/apply-sql.sh db-prod/INIT-USER-ADMIN.sql
        ```
    *   **使用纯 Shell 脚本**：
        ```bash
        ./db-prod/apply-sql-native.sh db-prod/INIT-USER-ADMIN.sql
        ```

详细的库表结构说明，请参考：[db-prod/README.md](file:///Users/chenxiaolong/资料/有孚网络/1南孜中台/yovole-nanzi-ai-agent-platform/db-prod/README.md)。

---

### 3.2 方案 A：Docker 容器化部署 (推荐)
通过 Docker 容器化可以避免环境依赖缺失造成的各种意外错误。

1.  **获取离线镜像包（提供以下两种途径）**：

    *   **途径一：直接下载官方预编译镜像包（推荐，最便捷）**
        直接前往 [GitHub Releases](https://github.com/RandyChen1985/nanzi-ai-agent-platform/releases) 页面，下载对应版本以及适合您服务器 CPU 架构（如 `linux-amd64` / `linux-arm64`）的离线 Docker 镜像归档 tar 包。
        
    *   **途径二：本地自行编译并导出镜像包（适合二次开发与定制）**
        执行入口构建脚本时，**必须显式在第一位传入版本号参数**（如 `1.0.0`）：
        ```bash
        cd docker
        
        # 构建 x86_64 架构 Linux 镜像
        ./build_linux_x86.sh 1.0.0
        
        # 构建 ARM64 架构 Linux 镜像
        ./build_linux_arm.sh 1.0.0
        
        # 仅在本机试跑调试（原生架构）
        ./build_native.sh 1.0.0
        ```
        构建完成后，带版本号与平台架构后缀的镜像 tar 归档包将固定生成在 `docker/release/` 目录下（例如 `nanzi-ai-agent_1.0.0_linux-amd64_YYYYMMDD.tar`）。

2.  **载入离线镜像包**：
    将下载或自行编译生成的镜像 tar 包拷贝到目标运行服务器上，执行以下命令载入镜像：
    ```bash
    docker load -i nanzi-ai-agent_1.0.0_linux-amd64_YYYYMMDD.tar
    ```
3.  **准备容器环境变量配置文件及 Docker Compose 编排调整**：

    *   **配置环境变量**：
        ```bash
        cd docker
        cp env.example .env
        # 编辑 .env 文件，填入 MySQL、Redis、Oracle 以及 Jira 相关的真实配置信息
        vim .env
        ```
        *注：因容器是网络隔离的沙箱，`MYSQL_HOST` 与 `REDIS_HOST` 严禁配置为 `localhost` 或 `127.0.0.1`，必须设置为宿主机的局域网 IP（在 Mac 系统上可通过 `ipconfig getifaddr en0` 查询，或使用 `host.docker.internal`）。*

    *   **检查与修改 Docker Compose 编排文件（[docker-compose.ai-agent.yml](file:///Users/chenxiaolong/资料/有孚网络/1南孜中台/yovole-nanzi-ai-agent-platform/docker/docker-compose.ai-agent.yml)）**：
        在启动前，您可以根据实际运行环境修改该配置文件：
        1.  **镜像版本校准**：YAML 中默认使用 `image: nanzi-ai-agent:latest`。如果您下载或编译出来的镜像是带具体版本号的（如 `nanzi-ai-agent:1.0.0`），您需要将 YAML 中 `image:` 指向对应标签；或直接在终端为该镜像重新打上 `latest` 标签，即可免除文件修改：
            ```bash
            docker tag nanzi-ai-agent:1.0.0 nanzi-ai-agent:latest
            ```
        2.  **Oracle 客户端挂载卷调整（仅当需要直连 Oracle 数据库时）**：请根据宿主机上 Oracle Instant Client 实际物理存放路径，将 `volumes` 下的 `/app/nanzi-aiagent/lib/instantclient_19_30` 替换为宿主机对应的真实目录。
            *   *低版本 Oracle 数据库兼容提示*：若需要连接低版本 Oracle（如 Oracle 11g 或更低版本），Python 的默认 Thin 模式无法兼容，**必须启用 Thick 模式**（即在 `.env` 中设置 `USE_ORACLE_THICK_MODE=1`），并在此处正确挂载兼容该低版本 Oracle 的物理客户端目录。如果您的智能体不需要操作 Oracle 数据库，此挂载卷配置可直接保留默认或予以注释。
4.  **一键启动与停止服务**：
    平台封装了高内聚的容器启动管理脚本，能自动完成冲突校验与状态检测：
    ```bash
    # 启动 API 容器
    ./start-nanzi-ai-agent.sh
    
    # 停止并移除容器
    ./stop-nanzi-ai-agent.sh
    ```

详细的 Docker 编排配置，请参考：[docker/README.md](file:///Users/chenxiaolong/资料/有孚网络/1南孜中台/yovole-nanzi-ai-agent-platform/docker/README.md)。

---

### 3.3 方案 B：本地源码开发调试部署
适合日常编写业务逻辑、开发调试新功能时采用。

1.  **后端启动 (FastAPI)**：
    ```bash
    # 激活 Python 环境并安装依赖
    source venv/bin/activate
    pip install -r requirements.txt
    
    # 在项目根目录下，拷贝并配置本地 .env 文件
    cp env.example .env
    
    # 启动后端 Uvicorn 调试服务
    uvicorn app.main:app --reload --port 8001
    ```
2.  **前端启动 (Vue 3 + Vite)**：
    ```bash
    cd frontend
    npm install
    npm run dev
    ```
    *注：推荐在开发联调时，直接运行项目根目录下的 `./dev.sh` 集成开发脚本，能以前台交互形式一键编译前端并拉起后端，极为高效。*

---

## 4. 登录使用 (Getting Started)

当服务启动成功后，您可以通过浏览器访问管理后台：
*   **管理后台地址**：`http://localhost:8001/`
*   **Swagger 接口文档**：`http://localhost:8001/docs`

### 🔑 首次登录指引
1.  南孜系统后台默认采用 **仅 API Key 认证** 的安全规则。
2.  若您在初始化阶段执行过 `db-prod/INIT-USER-ADMIN.sql` 脚本，平台会预置以下默认管理员凭证：
    *   **默认用户名**：`admin`
    *   **默认管理员 API Key**：`5BYfsKWhU_Cfx83cuo8E0kd4AtEhlUHDVlKwwR2kN-c`
3.  在登录框中粘贴上述 API Key 即可登录后台。
4.  **安全提示**：由于默认管理员 API Key 是固定的，首次登录成功后，请务必立即前往**【用户管理】**或【个人中心】，为 `admin` 用户**自主设置登录密码**。系统此后将全面启用安全级别的身份校验。

---

## 5. 相关配置初始化 (Configuration Initialization)

首次成功登录系统后，必须前往**【系统配置】**页面完成平台核心配置项的初始化，以保证智能体及各项组件能够正常工作：

### 5.1 大模型配置 (LLM Providers)
*   在【大模型配置】选项卡下，选择或添加您所使用的模型厂商（如 OpenAI, DeepSeek, 阿里百炼/DashScope 等）。
*   配置各厂商对应的 **API Key**、**API Base URL**（如果使用代理或国内镜像端点）以及默认使用的 Model 标识符，供平台各个智能体工作流作为推理底座。

### 5.2 RAGFlow 配置 (RAGFlow Integration)
*   **API 地址 (API URL)**：在【RAGFlow 配置】处，输入您已部署好的 RAGFlow 服务接口地址（注意在 Docker 部署下请写宿主机或局域网 IP，避免使用 localhost）。
*   **接口密钥 (API Key)**：填入在 RAGFlow 控制台中为知识库应用生成的 API Token，使得南孜平台能正常唤醒、同步非结构化知识库并为 ChatBI 数据分析提供文档参考。

### 5.3 数据源管理 (Data Sources)
*   若需使用 ChatBI 智能数据问答与图表可视化，请在【数据源管理】中添加您的业务数据库连接（支持 MySQL、ClickHouse、Oracle 等）。
*   输入连接信息后点击“连通性测试”确保连接无误，智能体将基于此数据源结构进行 SQL 生成与业务指标诊断。

---

## 6. FAQ (常见问题解答)

### Q1: 运行 `build_linux_arm.sh` 或 `build_linux_x86.sh` 编译前端时报 `Killed / cannot allocate memory` 错误
*   **原因**：这是因为在 Mac M 芯片或某些轻量级虚拟机上，使用 Qemu 模拟异构架构（如 `linux/amd64`）执行 Node.js 的 Vite 生产编译时，非常容易产生 CPU 暴涨和内存溢出 (OOM) 导致进程被系统强杀。
*   **解决**：我们已经在构建系统中集成了 **宿主机预构建机制**。在构建异构镜像时，脚本会自动检测宿主机环境，并直接在宿主机进行极速 vite build 编译，跳过容器内模拟编译，彻底避免内存不足错误。请确保您的宿主机已提前安装了 Node.js（`node` 和 `npm` 可执行）。如果想强行在容器内编译，请手动将 Docker Desktop 的可用内存调大至 **8GB 或更大**。

### Q2: 容器启动后自动退出，通过 `docker logs` 查看报错 `Database health check failed`
*   **原因**：在配置 `docker/.env` 文件时，`MYSQL_HOST` 或 `REDIS_HOST` 写了 `localhost` 或 `127.0.0.1`。因为 Docker 容器属于网络隔离沙箱，在容器内部，`localhost` 永远指向容器自身，从而连不上您电脑上的 MySQL 服务。
*   **解决**：将 Host 配置修改为宿主机的局域网 IP（在 Mac 系统上可在终端执行 `ipconfig getifaddr en0` 快速查看）；或在 Mac/Windows 的 Docker Desktop 环境下修改为 `host.docker.internal` 即可。

### Q3: 运行表初始化脚本时报错 `ModuleNotFoundError: No module named 'aiomysql'`
*   **原因**：未激活 Python 虚拟环境，或未在此虚拟环境下正确运行依赖安装。
*   **解决**：必须先在根目录下激活虚拟环境（`source venv/bin/activate`），运行 `pip install -r requirements.txt`，确保依赖库齐备后再执行部署脚本。
