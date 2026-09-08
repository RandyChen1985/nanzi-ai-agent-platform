# NanZi 开源智能体平台部署安装指南 (HOW TO INSTALL)

本指南旨在指导开发和运维人员在本地开发环境或生产环境下完成 **NanZi 开源智能体平台 (NanZi AI Agent Platform)** 的安装部署、数据库表结构初始化以及常见问题的快速排查。

---

## 1. 概要 (Overview)

NanZi 开源智能体平台是企业级的多智能体编排与数据智能洞察（ChatBI）系统。为了适应不同用户的环境，平台主要提供两套部署安装方案：
*   **Docker 容器化部署（生产首选，支持离线）**：通过位置参数指定版本号构建 Docker 归档包，可在隔离容器环境下运行并一键部署。
*   **本地源码开发调试部署（开发首选）**：使用 `dev.sh` 自动准备 Python 3.11 运行环境和后端依赖，配合 Node.js 宿主机环境完成前后端热重载实时开发联调。

无论采用何种部署方案，服务拉起前均需要完成平台主库结构与初始管理员账号的初始化。

> **重要：平台主库必须二选一。**
>
> 请根据实际环境选择 **MySQL** 或 **PostgreSQL** 其中一种：
> - 选择 MySQL：配置 `DATABASE_TYPE=mysql`（不配置时默认使用 MySQL），执行 `db-prod/` 初始化脚本。
> - 选择 PostgreSQL：配置 `DATABASE_TYPE=postgresql`，执行 `db-prod-pg/` 初始化脚本。
>
> 同一个平台运行环境不要同时初始化两套主库，也不要交叉执行两套迁移脚本。两套数据库都可以作为外部数据源被平台连接，但平台自身的主库运行时只使用其中一种。

---

## 2. 前置环境 (Prerequisites)

在开始部署平台之前，请确保当前环境满足以下各项依赖条件：

### 💻 基础工具依赖
*   **Docker**（建议 v20.10+） 与 **Docker Compose**（建议 v2.0.0+）
*   **Python**（项目运行基线为 v3.11；本地源码启动时 `dev.sh` 会自动安装/准备。若在启动前使用 Python 版数据库导入工具，需按数据库章节通过 uv 准备 `.venv`。如果直接使用已打好的 Docker 镜像包部署，则无需安装）
*   **uv**（本地源码启动时 `dev.sh` 会自动安装；手动执行数据库 Python 导入工具时，建议使用 uv 创建项目 `.venv`）
*   **Node.js**（建议 v18+ & npm，仅用于本地开发联调或宿主机前端预构建。如果不自行构建镜像且不进行本地源码调试，则无需安装）

### 🔌 数据库与外部依赖服务
*   **平台主库（二选一）**：
    *   **MySQL**（建议 v8.0+）：必须支持 `utf8mb4` 字符集，用以存放平台系统级配置、角色权限、审计日志及智能体元数据。
    *   **PostgreSQL**（建议 v14+）：作为 MySQL 的替代主库；初始化脚本使用 PostgreSQL 原生方言和幂等迁移。
*   **Redis**：**必须使用支持向量检索的 Redis Stack 版本**（例如 `redis/redis-stack-server:latest`），用以支持平台内部高并发缓存、长期记忆（LTM）向量检索、向量搜索诊断以及任务执行期锁与状态缓存（APScheduler 配合 Redis）。
*   **RAGFlow 生态（若使用知识库和 ChatBI，则为必选）**：如需接入非结构化 SOP 知识库或使用 ChatBI 数据洞察功能，必须保证 RAGFlow 服务就绪并提供相应的 API URL 与 API Key。更多信息请参考 [RAGFlow 官网](https://ragflow.io/)。

---

## 3. 部署流程 (Deployment Flow)

### 3.1 主库选项 A：MySQL（二选一）

仅当平台主库选择 MySQL 时执行本节。若选择 PostgreSQL，请跳过本节，直接执行 [3.2 主库选项 B：PostgreSQL](#32-主库选项-bpostgresql二选一)。

平台采用版本化迁移管理（数据库脚本位于 `db-prod/` 目录下）。无参数执行导入脚本时，会按版本号依次执行全部 `V*.sql`。

> **目标库不存在时会自动创建。**  
> 导入器连接 MySQL 后，若你输入的目标库名尚不存在，会自动执行  
> `CREATE DATABASE IF NOT EXISTS \`...\` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci`，  
> 无需事先手工建库。每个版本文件开始前都会再次确保目标库存在（已存在时可能打印无害 Warning，可忽略）。

1.  **准备依赖（途径一需要）**：
    Python 工具导入需要 `aiomysql`。如果尚未准备项目 Python 环境，建议使用与
    `dev.sh` 一致的 Python 3.11 `.venv`：
    ```bash
    uv python install 3.11
    uv venv --python 3.11 .venv
    uv pip install --python .venv/bin/python \
      --default-index https://pypi.tuna.tsinghua.edu.cn/simple \
      -r requirements.txt
    source .venv/bin/activate
    ```
    如果之前已经成功执行过 `./dev.sh`，可直接激活已有环境后运行下面的导入脚本：
    ```bash
    source .venv/bin/activate
    ```
    MySQL 建议使用 **v8.0+**，字符集以 `utf8mb4` 为准。

2.  **执行结构自动初始化（提供以下两种途径）**：

    *   **途径一：使用 Python 工具导入（推荐）**
        ```bash
        # 推荐：在项目根目录执行
        chmod +x db-prod/apply-sql.sh
        ./db-prod/apply-sql.sh

        # 也可进入目录后执行
        cd db-prod
        ./apply-sql.sh
        ```
        脚本会依次询问 Host、Port、User、Password 和目标数据库，并要求输入 `YES` 确认。  
        无参数时会执行 `db-prod/V*.sql` 全部版本文件。

    *   **途径二：免 Python 依赖的纯 Shell 脚本导入**
        仅依赖系统已安装的 `mysql` 命令行客户端。具备与 Python 脚本等价的幂等性过滤机制（自动跳过重复建表、重复列等容错）：
        ```bash
        chmod +x db-prod/apply-sql-native.sh
        ./db-prod/apply-sql-native.sh
        ```
        *注：根据提示输入 Host、Port、User、Password 及数据库名，输入 `YES` 即可。目标库同样会在不存在时自动创建。*

3.  **交互导入示例**（首次部署，目标库可不预先创建）：

    以下示例将数据导入到本地库 `test222`（若不存在会自动创建）：

    ```text
    $ cd db-prod
    $ ./apply-sql.sh
    MySQL host [localhost]:                 # 回车使用默认 localhost
    MySQL port [3306]:                      # 回车使用默认 3306
    MySQL user: root
    MySQL password: ******
    Target database: test222                # 不存在则自动创建（utf8mb4）
    ---------------------------------------------------
    请确认本次 SQL 执行目标：
      Host     : localhost
      Port     : 3306
      User     : root
      Database : test222
      Password : ******
      SQL files: db-prod/V*.sql
    确认无误请输入 YES 继续执行：yes
    No arguments provided. Running all SQL files from db-prod/...
    ---------------------------------------------------
    🚀 Applying db-prod/V0-init_yunshu_ai_agent_metadata.sql...
    🔌 Connecting to MySQL server to ensure database 'test222' exists...
    🔌 Connecting to database 'test222'...
    ✅ SQL applied successfully.
    ---------------------------------------------------
    🚀 Applying db-prod/V1-create_system_configs.sql...
    🔌 Connecting to MySQL server to ensure database 'test222' exists...
    ...（后续 V2、V3 … 直至最新版本依次执行）
    ✅ SQL applied successfully.
    ```

    *说明：*
    *   生产环境请将 `Target database` 换成正式库名（如 `nanzi_ai_agent_platform`），并与 `.env` 中的 `MYSQL_DB` / `MYSQL_DATABASE` 保持一致。
    *   若库已存在，后续版本执行时可能出现 `Can't create database '...'; database exists` 的 Warning，属于幂等确保逻辑，可忽略。
    *   可选：仍可事先手工建库（字符集须为 `utf8mb4`），例如：
        ```sql
        CREATE DATABASE IF NOT EXISTS `nanzi_ai_agent_platform` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
        ```

4.  **导入默认管理员账号与预置 API Key（可选）**：
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

详细的库表结构说明，请参考：[db-prod/README.md](db-prod/README.md)。

> 💡 **小贴士：若使用 Docker 部署 MySQL，手动登录执行 SQL 避免中文丢失**  
> 如果您通过 `docker exec` 进入 MySQL 容器（如容器名为 `laplace-mysql`）手动执行或更新 SQL，容器终端默认语言环境可能非 UTF-8，容易导致写入的中文变成问号或丢失。请使用以下命令指定 UTF-8 语言与客户端字符集登录：
> ```bash
> docker exec -it -e LANG=C.UTF-8 -e LC_ALL=C.UTF-8 laplace-mysql mysql --default-character-set=utf8mb4 -u root -p
> ```

---

### 3.2 主库选项 B：PostgreSQL（二选一）

仅当平台主库选择 PostgreSQL 时执行本节。若选择 MySQL，请跳过本节。不要同时执行 `db-prod/` 和 `db-prod-pg/` 两套主库初始化脚本。

平台同时提供独立的 PostgreSQL 初始化入口，脚本位于 `db-prod-pg/`，不会修改现有
`db-prod/` MySQL 迁移链。新环境会按版本号自动执行 `V0-baseline.sql`、`V1-...sql`
等全部版本文件；重复执行是安全的，V1 配置对齐迁移不会覆盖已有环境配置值。

> **目标库不存在时会自动创建。**  
> 导入器连接 PostgreSQL 后，若你输入的目标库名尚不存在，会自动执行 `CREATE DATABASE ... WITH ENCODING 'UTF8'`，无需事先手工建库。  
> 但仍须**显式输入目标库名称**；禁止使用 `postgres`、`template0`、`template1` 作为应用库。

1.  **准备依赖**：
    Python 工具导入需要 `psycopg`。如果尚未准备项目 Python 环境，建议使用与
    `dev.sh` 一致的 Python 3.11 `.venv`：
    ```bash
    uv python install 3.11
    uv venv --python 3.11 .venv
    uv pip install --python .venv/bin/python \
      --default-index https://pypi.tuna.tsinghua.edu.cn/simple \
      -r requirements.txt
    source .venv/bin/activate
    ```
    如果之前已经成功执行过 `./dev.sh`，可直接激活已有环境后运行下面的导入脚本：
    ```bash
    source .venv/bin/activate
    ```
    PostgreSQL 目标实例建议使用 **v14+**。

2.  **执行 PostgreSQL 初始化（导入结构与种子数据）**：
    ```bash
    # 推荐：在项目根目录执行
    chmod +x db-prod-pg/apply-sql.sh
    ./db-prod-pg/apply-sql.sh

    # 也可进入目录后执行（兼容 sh）
    cd db-prod-pg
    sh apply-sql.sh
    ```
    无参数时，脚本会按版本号排序，依次执行当前目录下全部 `V*.sql`（当前为 `V0` ~ `V14`）。版本目录是实际迁移状态的唯一来源；新增版本后无需修改本段文字。
    脚本会依次询问 Host、Port、User、Password 和目标数据库，并要求输入 `YES` 确认。  
    全部 SQL 成功后，会询问是否顺带创建默认管理员 `admin` 并生成 API Key。

3.  **交互导入示例**（首次部署，目标库可不预先创建）：

    以下示例将数据导入到本地库 `test111`（若不存在会自动创建），并完成管理员初始化：

    ```text
    $ cd db-prod-pg
    $ sh apply-sql.sh
    PostgreSQL host [localhost]:            # 回车使用默认 localhost
    PostgreSQL port [5432]:                 # 回车使用默认 5432
    PostgreSQL user: postgres
    PostgreSQL password: ******
    Target database: test111                # 不存在则自动创建；勿填 postgres/template0/template1
    ---------------------------------------------------
    请确认本次 SQL 执行目标：
      Host     : localhost
      Port     : 5432
      User     : postgres
      Database : test111
      SQL files:
        - .../db-prod-pg/V0-baseline.sql
        - .../db-prod-pg/V1-align_system_config_seeds.sql
        - .../db-prod-pg/V2-update_memory_embedding_default_config.sql
        - .../db-prod-pg/V3-add_mcp_scope_and_user_id.sql
        - .../db-prod-pg/V4-add_category_to_chatbi_examples.sql
        - .../db-prod-pg/V5-register_example_search_tool.sql
        - .../db-prod-pg/V6-enforce_mcp_server_name_uniqueness.sql
        - .../db-prod-pg/V7-add_mcp_tool_availability.sql
        - .../db-prod-pg/V8-enforce_ai_model_id_uniqueness.sql
        - .../db-prod-pg/V9-add_ai_model_token_limits.sql
      Password : ******
    确认无误请输入 YES 继续执行：yes
    ---------------------------------------------------
    🚀 Applying .../V0-baseline.sql ...
    ✅ SQL applied successfully.
    ...（V1 ~ V9 依次执行，全部 ✅）
    ---------------------------------------------------
    ✅ 所有 PostgreSQL 版本 SQL 文件执行成功。
    ---------------------------------------------------
    是否需要顺带创建默认管理员 admin 并生成新的 API Key？ (推荐首次部署时创建) [Y/N]: y
    ✅ 管理员账号创建成功！
    🔑 管理员 API Key（请立即保存）
       用户名: admin
       API Key: <仅显示一次，请立即保存>
    ```

    *说明：*
    *   生产环境请将 `Target database` 换成正式库名（如 `nanzi_ai_agent_platform`），并与下文 `.env` 中的 `POSTGRES_DB` 保持一致。
    *   若只需升级单个版本，可显式传文件，例如：  
        `./db-prod-pg/apply-sql.sh db-prod-pg/V9-add_ai_model_token_limits.sql`

4.  **管理员初始化与凭证维护（若第 2 步已选 Y 可跳过）**：
    PostgreSQL 基线不写入固定管理员 API Key。初始化时选择 `Y` 会使用当前
    `ENCRYPTION_KEY` 生成新的管理员凭证；如果初始化时跳过，可在配置好运行环境后执行：
    ```bash
    ./db-prod-pg/create-admin-user.sh
    ./db-prod-pg/create-admin-key.sh
    ./db-prod-pg/reset-admin-password.sh
    ```
    API Key 只在终端显示一次，请立即保存。三个脚本分别用于创建管理员、重新生成 API Key
    和重置管理员密码。

5.  **配置平台运行时**：
    在项目根目录 `.env` 中选择 PostgreSQL 主库（`POSTGRES_DB` 须与导入时的目标库名一致）：
    ```dotenv
    DATABASE_TYPE=postgresql
    POSTGRES_HOST=localhost
    POSTGRES_PORT=5432
    POSTGRES_DB=test111
    POSTGRES_USER=postgres
    POSTGRES_PASSWORD=<password>
    ```
    不配置 `DATABASE_TYPE` 时默认仍使用 MySQL。应用 ORM 和 APScheduler 会根据该值选择
    PostgreSQL 或 MySQL 连接。

详细的 PostgreSQL 初始化、版本迁移和管理员脚本说明，请参考
[db-prod-pg/README.md](db-prod-pg/README.md)。

---

完成上面 MySQL 或 PostgreSQL 两个主库选项中的任意一个后，再根据部署方式选择以下方案。

### 3.3 方案 A：Docker 容器化部署 (推荐)
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
        # 编辑 .env 文件，先确认 HOST_DATA_DIR，再填写 MySQL/PostgreSQL、Redis、Oracle、Jira 配置
        vim .env
        ```
        *注：因容器是网络隔离的沙箱，主库 Host 和 `REDIS_HOST` 均严禁配置为 `localhost` 或 `127.0.0.1`。使用 MySQL 时填写 `MYSQL_HOST`，使用 PostgreSQL 时填写 `POSTGRES_HOST`，可设置为宿主机局域网 IP 或 `host.docker.internal`。*
        *   **`HOST_DATA_DIR`（DooD 必填）**：填写 Docker daemon 所在宿主机上的数据目录绝对路径，例如 `HOST_DATA_DIR=/data/yunshu-aiagent/data`。该目录会挂载到平台容器的 `/app/data`，平台再通过 Docker Socket 创建用户沙箱时，会把用户工作区映射为宿主机的 `/data/yunshu-aiagent/data/agent_workspaces/{user_key}`。只配置 Compose 的 `/app/data` 挂载而不配置此变量，会导致沙箱挂载到错误的宿主机路径，表现为 `/workspace/sessions` 为空。*
        *   **`DATABASE_TYPE`（默认 `mysql`）**：选择平台主库类型。设置为 `mysql` 时使用 `MYSQL_*`，设置为 `postgresql` 时使用 `POSTGRES_*`。
        *   **`TASK_SCHEDULER_ENABLED`（默认 `true`）**：控制当前容器是否启动定时任务调度器。单节点保持 `true`；多节点只给一个节点 `true`，其他 API 节点 `false`。所有节点必须共享同一主库和 Redis，修改后重启对应容器；滚动发布时避免两个节点短时间同时为 `true`。
        *   **`ENCRYPTION_KEY`（必填）**：用户 API Key 的 Fernet 对称加密密钥（用于加密入库 / 后台解密查看）。MySQL 的 `db-prod/INIT-USER-ADMIN.sql` 固定管理员 Key 只在保持 `env.example` 默认密钥时有效；PostgreSQL 不写入固定管理员凭证，会在初始化或手动创建时使用当前密钥生成管理员。生成新密钥示例：
            ```bash
            python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
            ```

        ##### 如何创建 / 重建 admin 
        *   **MySQL**：`.env` 中 `ENCRYPTION_KEY` 仍为 `env.example` 默认值时，可导入预置脚本：
            ```bash
            ./db-prod/apply-sql-native.sh db-prod/INIT-USER-ADMIN.sql
            # 或：./db-prod/apply-sql.sh db-prod/INIT-USER-ADMIN.sql
            ```
            登录使用文档「首次登录指引」中的默认 API Key。
        *   **MySQL（已修改 ENCRYPTION_KEY）**：用当前 `.env` 里的密钥现场生成管理员：
            ```bash
            # 若库中已有旧 admin，先删除再创建
            # mysql ... -e "DELETE FROM ai_agent_users WHERE user_name = 'admin';"

            source .venv/bin/activate
            export PYTHONPATH=.
            python scripts/create_admin_user.py
            # 也可指定用户名：python scripts/create_admin_key.py <username>
            ```
            终端会打印**仅此一次**的 API Key，请立即保存并用该 Key 登录。
        *   **PostgreSQL**：不使用 MySQL 的 `INIT-USER-ADMIN.sql`，执行以下脚本：
            ```bash
            ./db-prod-pg/create-admin-user.sh
            ./db-prod-pg/create-admin-key.sh
            ./db-prod-pg/reset-admin-password.sh
            ```

    *   **检查与修改 Docker Compose 编排文件（[docker-compose.ai-agent.yml](file:///Users/chenxiaolong/资料/有孚网络/1南孜中台/yovole-nanzi-ai-agent-platform/docker/docker-compose.ai-agent.yml)）**：
        在启动前，您可以根据实际运行环境修改该配置文件：
        1.  **镜像版本校准**：YAML 中默认使用 `image: nanzi-ai-agent:latest`。如果您下载或编译出来的镜像是带具体版本号的（如 `nanzi-ai-agent:1.0.0`），您需要将 YAML 中 `image:` 指向对应标签；或直接在终端为该镜像重新打上 `latest` 标签，即可免除文件修改：
            ```bash
            docker tag nanzi-ai-agent:1.0.0 nanzi-ai-agent:latest
            ```
        2.  **Oracle 客户端挂载卷调整（仅当需要直连 Oracle 数据库时）**：请根据宿主机上 Oracle Instant Client 实际物理存放路径，将 `volumes` 下的 `/app/nanzi-aiagent/lib/instantclient_19_30` 替换为宿主机对应的真实目录。
            *   *低版本 Oracle 数据库兼容提示*：若需要连接低版本 Oracle（如 Oracle 11g 或更低版本），Python 的默认 Thin 模式无法兼容，**必须启用 Thick 模式**（即在 `.env` 中设置 `USE_ORACLE_THICK_MODE=1`），并在此处正确挂载兼容该低版本 Oracle 的物理客户端目录。如果您的智能体不需要操作 Oracle 数据库，此挂载卷配置可直接保留默认或予以注释。
        3.  **DooD 沙箱挂载配置（启用 `docker` 策略时必查）**：Compose 中必须同时存在以下两项，并且两处使用同一个宿主机绝对路径：
            ```yaml
            volumes:
              - /data/yunshu-aiagent/data:/app/data
              - /var/run/docker.sock:/var/run/docker.sock
            environment:
              - HOST_DATA_DIR=/data/yunshu-aiagent/data
            ```
            `HOST_DATA_DIR` 不是平台容器内的 `/app/data`，而是宿主机 Docker daemon 能访问的真实目录。修改 `.env` 或 Compose 后必须重新创建平台容器，旧的用户沙箱也需要停止并重新创建才能获得新的挂载。
4.  **一键启动与停止服务**：
    平台封装了高内聚的容器启动管理脚本，能自动完成冲突校验与状态检测：
    ```bash
    # 启动 API 容器
    ./start-nanzi-ai-agent.sh
    
    # 停止并移除容器
    ./stop-nanzi-ai-agent.sh
    ```

详细的 Docker 编排配置，请参考：[docker/README.md](file:///Users/chenxiaolong/资料/有孚网络/1南孜中台/yovole-nanzi-ai-agent-platform/docker/README.md)。

Docker 打包与启动的完整执行日志，请参考：[start-logs.md](start-logs.md)。

---

### 3.4 方案 B：本地源码开发调试部署
适合日常编写业务逻辑、开发调试新功能时采用。

#### 1. 环境准备 (Python 3.11)
平台运行基线为 **Python 3.11**。本地源码开发无需提前安装 Python 或 uv，首次执行
`./dev.sh` 时脚本会自动检测/安装 uv、准备 Python 3.11、创建项目根目录下的 `.venv`，
并按 `requirements.txt` 安装后端依赖。

启动前仍需准备以下内容：

```bash
# 复制并编辑环境变量文件
cp env.example .env
# 编辑 .env：选择 MySQL 或 PostgreSQL，并配置 Redis、ENCRYPTION_KEY、TASK_SCHEDULER_ENABLED 等

# 确保数据库结构已经按上面的 MySQL/PostgreSQL 章节初始化
# 确保本机已安装 Node.js/npm；dev.sh 会自动安装前端 npm 依赖
```

源码多节点部署同样遵守调度器节点约束：`TASK_SCHEDULER_ENABLED` 未配置时默认为
`true`；只保留一个源码进程为 `true`，其他 API 进程设置为 `false`。环境变量修改后需要重启对应进程，开启节点会约每 30 秒从共享任务库同步任务定义。

首次运行需要联网下载 uv、Python 3.11 和 Python 依赖。若需要调整 PyPI 镜像，可设置
`PYPI_INDEX_URL`；默认使用清华 PyPI 镜像。

#### 2. 一键编译与启动 (`./dev.sh`)

平台提供了高度集成的启动工具 [`dev.sh`](dev.sh)，内置**Python 3.11 环境与后端依赖自动准备**、
**前端依赖自动感知安装**、**动态读取 `.env` 端口**与**旧进程自动清理**：

```bash
# 用法 1：前台调试启动（实时查看前端构建与后端日志，按 Ctrl+C 退出）
./dev.sh

# 用法 2：后台常驻启动（推荐）
./dev.sh -d
```

**启动输出示例 (`./dev.sh`，后续启动且依赖未变化)：**
```text
==================================================
       NanZi AI 开源智能体平台 · 本地开发启动工具         
       用法: ./dev.sh (前台调试) | ./dev.sh -d (后台常驻) 
==================================================

启动环境信息
       ➜ uv: uv <当前版本>
       ➜ Python 目标版本: 3.11
       ➜ 虚拟环境: .venv
       ➜ PyPI 镜像: https://pypi.tuna.tsinghua.edu.cn/simple
       ➜ DATABASE_TYPE: mysql (effective: mysql)
       ➜ 数据库地址: localhost:3306/nanzi_ai_agent_platform
       ➜ Redis 地址: localhost:6379/2

🧰 [1/4] 正在准备 uv、Python 3.11 和后端依赖...
✅ 后端依赖未变化，跳过安装

🛑 [2/4] 正在检查并停止旧服务 (Port 8001)...
✅ 端口 8001 空闲，无需停止

🚀 [3/4] 正在编译前端 (Building Frontend)...
✓ built in 55.83s
✅ 前端编译成功！

🔥 [4/4] 正在启动后端服务 (Starting Backend in Foreground)...
提示：您将在此看到实时运行日志，按 Ctrl+C 可停止服务。
INFO:     Uvicorn running on http://0.0.0.0:8001 (Press CTRL+C to quit)
```

顶部的数据库和 Redis 信息来自当前 shell/`.env` 配置，仅用于确认脚本将采用的连接类型和
地址，不执行数据库或 Redis 连通性测试；数据库和 Redis 密码不会单独打印。

源码启动、源码升级后启动以及 Docker 打包启动的完整日志，请参考：[start-logs.md](start-logs.md)。

---

## 4. 登录使用 (Getting Started)

当服务启动成功后，您可以通过浏览器访问管理后台：
*   **管理后台地址**：`http://localhost:8001/`
*   **Swagger 接口文档**：`http://localhost:8001/docs`

### 🔑 首次登录指引
1.  南孜系统后台默认采用 **仅 API Key 认证** 的安全规则。
2.  **MySQL**：若您在初始化阶段执行过 `db-prod/INIT-USER-ADMIN.sql`，且 `.env` 中的 `ENCRYPTION_KEY` **仍为 `env.example` 默认值**，平台会预置以下默认管理员凭证：
    *   **默认用户名**：`admin`
    *   **默认管理员 API Key**：`5BYfsKWhU_Cfx83cuo8E0kd4AtEhlUHDVlKwwR2kN-c`
    *   若您已修改 `ENCRYPTION_KEY`，请**不要**使用上述默认 Key，应重新创建管理员并使用新生成的 API Key。
3.  **PostgreSQL**：没有固定预置 API Key。请使用 `db-prod-pg/apply-sql.sh` 初始化时自动生成的凭证，或执行 `create-admin-user.sh` / `create-admin-key.sh` 后使用终端输出的新 Key 登录。
4.  在登录框中粘贴对应数据库初始化流程生成的 API Key 即可登录后台。
5.  **安全提示**：首次登录成功后，请务必前往**【用户管理】**或【个人中心】，为 `admin` 用户设置登录密码，并妥善保存或轮换 API Key。

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
*   **原因**：在配置 `docker/.env` 文件时，选中的主库 Host 或 `REDIS_HOST` 写了 `localhost` 或 `127.0.0.1`。因为 Docker 容器属于网络隔离沙箱，在容器内部，`localhost` 永远指向容器自身。
*   **解决**：使用 MySQL 时检查 `MYSQL_HOST`，使用 PostgreSQL 时检查 `POSTGRES_HOST`，将其改为宿主机局域网 IP；在 Mac/Windows 的 Docker Desktop 环境下也可使用 `host.docker.internal`。

### Q3: 运行表初始化脚本时报错缺少 `aiomysql` 或 `psycopg`
*   **原因**：未激活 Python 虚拟环境，或未在此虚拟环境下正确运行依赖安装。
*   **解决**：如果之前已经成功执行过 `./dev.sh`，执行 `source .venv/bin/activate` 后再运行数据库脚本；
    仅初始化数据库、不希望启动服务时，可手动执行以下命令准备环境：
    ```bash
    uv python install 3.11
    uv venv --python 3.11 .venv
    uv pip install --python .venv/bin/python \
      --default-index https://pypi.tuna.tsinghua.edu.cn/simple \
      -r requirements.txt
    source .venv/bin/activate
    ```
    然后再执行数据库脚本。MySQL 导入需要 `aiomysql`，PostgreSQL 导入需要 `psycopg`。

### Q4: 改了 `ENCRYPTION_KEY` 后，默认 admin API Key 登录失败 / 后台解不开 Key
*   **原因**：MySQL 的 `env.example` 默认 `ENCRYPTION_KEY` 与 `INIT-USER-ADMIN.sql` 里预置的 admin 密文是一对的。改了密钥后，预置密文无法再用新密钥解密；PostgreSQL 本身不使用固定预置 Key，但已经创建的凭证同样依赖当时的密钥。
*   **解决**：MySQL 不要再依赖 `INIT-USER-ADMIN.sql` 中的默认 Key，PostgreSQL 则直接使用对应目录的管理员脚本按当前配置重新生成：
    ```bash
    source .venv/bin/activate
    export PYTHONPATH=.
    python scripts/create_admin_user.py
    # PostgreSQL 也可以直接使用：
    # ./db-prod-pg/create-admin-user.sh
    # ./db-prod-pg/create-admin-key.sh
    # ./db-prod-pg/reset-admin-password.sh
    ```
    保存终端输出的新 API Key 后登录。若希望继续用初始化脚本里的默认 admin，请将 `ENCRYPTION_KEY` 保持为 `env.example` 中的默认值。

### Q5: 多节点部署时如何避免定时任务重复执行？失败重试在哪里配置？
*   **调度器节点**：`TASK_SCHEDULER_ENABLED` 未配置时默认为 `true`。多节点部署时只保留一个节点为 `true`，其他 API 节点设置为 `false`；关闭节点仍可提供 API、任务管理和「立即执行」，但不会启动 Cron 定时器。修改 Docker Compose、Kubernetes ConfigMap 或 `.env` 后必须重启对应服务。
*   **重试配置**：进入 **任务中心 → 编辑定时任务 → 执行失败策略**，设置最大重试次数（`0–3`，默认 `0`）和重试间隔（`1–60` 分钟，默认 `5` 分钟）。该策略只对定时触发失败生效，「立即执行」不会自动重试。
*   **排查顺序**：确认只有一个节点为 `true`、所有节点连接同一主库和 Redis，再检查任务是否启用、Cron 表达式和平台时区；任务定义修改后通常等待约 30 秒同步。

### Q6: 使用 Docker 运行 MySQL 容器，手动登录执行 SQL 时中文内容丢失或变为问号
*   **原因**：通过 `docker exec -it <container> mysql ...` 进入容器时，容器终端默认的环境变量（`LANG` / `LC_ALL`）通常为 POSIX/C，且 `mysql` 命令行客户端在没有显式指定 `--default-character-set` 时容易回退到 `latin1` 字符集，导致在终端粘贴或执行含中文的 SQL（如系统配置描述 `system_configs`、工具说明 `sys_api_tools` 等）时，中文字符被错误转码为问号 `???` 或直接丢失。
*   **解决**：登录 MySQL 容器时显式声明 UTF-8 环境变量与客户端字符集：
    ```bash
    docker exec -it -e LANG=C.UTF-8 -e LC_ALL=C.UTF-8 laplace-mysql mysql --default-character-set=utf8mb4 -u root -p
    ```
    *（注：将 `laplace-mysql` 替换为您实际运行的 MySQL 容器名）*
    以此方式登录后，容器环境及客户端连接均强制锁定为 `utf8mb4`，再次执行或贴入含中文的 SQL 语句就不会丢失中文了。

