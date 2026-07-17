# Docker 部署指南

本目录包含了南孜智能体平台 (NanZi AI Agent Platform) 的容器化部署配置文件。

## 文件说明

| 文件名 | 用途 |
| :--- | :--- |
| `Dockerfile` | API 服务的镜像构建定义 (基于 Python 3.10-slim)。包含前后端构建流程。 |
| `docker-compose.yml` | **全栈启动配置**。包含 API 服务及其依赖组件 (Redis) 的编排。 |
| `docker-compose.ai-agent.yml` | **独立启动配置**。仅包含 API 服务,需通过环境变量连接外部的数据库和缓存。 |
| `build_linux_x86.sh` | 构建 **x86_64** 服务器镜像 (`linux/amd64`)，需传入版本号参数，Mac / Linux 均可执行。 |
| `build_linux_arm.sh` | 构建 **ARM64** 服务器镜像 (`linux/arm64`)，需传入版本号参数。 |
| `build_native.sh` | 按本机 CPU 架构构建，需传入版本号参数，适合本地 `docker run` 调试。 |
| `install-buildx.sh` | 修复 Homebrew docker + Colima 下 buildx 不可用的问题。 |
| `_build_common.sh` | 内部公共逻辑，请勿直接调用。 |
| `README_EN.md` | 英文部署说明。 |
| `start-nanzi-ai-agent.sh` | 一键启动脚本。自动检查配置并启动 API 容器。 |
| `stop-nanzi-ai-agent.sh` | 一键停止脚本。停止并移除 API 容器。 |
| `.env` | 环境变量配置文件（需从 `../env.example` 复制并修改）。 |

## 快速开始

### 1. 准备环境配置

首次部署需要配置环境变量文件：

```bash
# 复制环境变量模板到 docker 目录
cp ../env.example .env

# 编辑 .env 文件，修改关键配置
vim .env
```

**重要配置项说明：**

```bash
# 服务配置
API_SERVICE_ENV=prod                    # 环境: dev/prod
API_SERVICE_LOG_LEVEL=INFO              # 日志级别

# 数据库连接 (使用宿主机 IP 而非 localhost)
MYSQL_HOST=192.168.x.x                  # 宿主机 IP 地址
MYSQL_PORT=3306
MYSQL_DB=nanzi_ai_agent_platform
MYSQL_USER=root
MYSQL_PASSWORD=your_password



# Redis 连接
REDIS_HOST=192.168.x.x                  # 宿主机 IP 地址
REDIS_PORT=6379
REDIS_PASSWORD=your_password
REDIS_ENABLE=true

# 安全配置 (必须配置)
API_SERVICE_API_KEY_HASH_ALGORITHM=sha256
ENCRYPTION_KEY=your-fernet-key-here  # 使用 Fernet.generate_key() 生成
```

> **注意**: 
> - 容器内无法使用 `localhost` 访问宿主机服务
> - 使用 `ipconfig getifaddr en0` 获取 Mac 本机 IP
> - 或在配置文件中使用 `host.docker.internal`（Mac/Windows）

### 2. 构建镜像

首次运行或代码更新后,请先构建镜像：

```bash
cd docker

# 部署到常见 x86 Linux 服务器（推荐）
./build_linux_x86.sh 1.2.0

# 部署到 ARM64 Linux（鲲鹏 / Ampere 等）
./build_linux_arm.sh 1.2.0

# 仅在本机试跑（架构随 Mac/PC 而定）
./build_native.sh 1.0.0
```

产物固定输出到 **`docker/release/`** 目录，文件名带版本号与架构后缀，例如：

- `docker/release/nanzi-ai-agent_1.2.0_linux-amd64_20260529.tar`
- `docker/release/nanzi-ai-agent_1.2.0_linux-arm64_20260529.tar`

> **Mac 打 x86 包**：请用 `build_linux_x86.sh`（内部 `docker buildx --platform linux/amd64`），不要用 `build_native.sh`，否则 M 芯片会打出 arm64 镜像，无法在 x86 服务器运行。

**若提示 buildx 不可用**（Homebrew docker + Colima 常见）：`cli-plugins` 里可能仍指向已卸载的 Docker Desktop。执行：

```bash
./install-buildx.sh
```

然后重新 `./build_linux_x86.sh`。

**若 vite build 报 `Killed` / `cannot allocate memory`**（Mac 跨平台打 x86 包常见）：构建脚本会自动在**宿主机**预构建前端再打包；请确保本机已安装 Node.js。也可手动增大 Docker 内存（Docker Desktop → Settings → Resources，建议 ≥ 8GB）。

构建过程包括：
- 前端构建（Vue 3 + Vite）
- 后端打包（Python 依赖安装）
- 镜像导出（可用于离线部署）

### 3. 启动服务

#### 方式一：使用一键启动脚本(推荐)

```bash
# 在 docker 目录下执行
./start-nanzi-ai-agent.sh
```

启动脚本会自动：
- ✅ 检查 .env 文件是否存在
- ✅ 检查镜像是否已构建
- ✅ 停止并删除旧容器
- ✅ 启动新容器
- ✅ 检查启动状态

#### 方式二：手动启动

**场景 A: 仅部署 API（推荐用于生产环境）**

适用于已有独立的 MySQL、ClickHouse、Redis 服务的场景：

```bash
# 使用独立配置 (docker-compose.ai-agent.yml)
docker-compose -f docker-compose.ai-agent.yml up -d
```

**场景 B: 全栈启动（适用于本地开发）**

一键启动 API 及所有依赖服务（Redis）：

```bash
# 使用全栈配置 (docker-compose.yml)
docker-compose up -d
```

> **注意**: 全栈模式不包含 MySQL，需要单独准备 MySQL 服务。

### 4. 验证服务

启动后，检查服务状态：

```bash
# 查看容器状态
docker ps | grep nanzi-ai-agent

# 查看启动日志
docker logs nanzi-ai-agent

# 实时查看日志
docker logs -f nanzi-ai-agent
```

正常启动后，日志应显示：
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8001 (Press CTRL+C to quit)
```

## 访问服务

服务启动成功后，可以通过以下地址访问：

| 服务 | 地址 | 说明 |
| :--- | :--- | :--- |
| **管理后台** | http://localhost:8001/ | 前端管理界面（用户管理、API Key 管理、审计日志等） |
| **API 文档** | http://localhost:8001/docs | Swagger UI 交互式 API 文档 |
| **API 接口** | http://localhost:8001/api/v1/* | 数据查询接口（需要 API Key 认证） |
| **健康检查** | http://localhost:8001/health | 服务健康状态检查 |

### 登录管理后台

**认证方式：仅使用 API Key**

1. 首次使用需要创建管理员 API Key：
   平台推荐通过运行 `db-prod/INIT-USER-ADMIN.sql` 来初始化默认管理员账号。您可以参考 [db-prod/README.md](file:///Users/chenxiaolong/资料/有孚网络/1南孜中台/yovole-nanzi-ai-agent-platform/db-prod/README.md) 的说明，在项目根目录下通过运行以下部署脚本来完成导入：
   ```bash
   ./db-prod/apply-sql.sh db-prod/INIT-USER-ADMIN.sql
   ```
   *注：默认管理员用户名为 `admin`，默认 API Key 为 `5BYfsKWhU_Cfx83cuo8E0kd4AtEhlUHDVlKwwR2kN-c`。首次使用此 API Key 登录后，请立即进入【用户管理】为其设置密码以保证安全。*

2. 在管理后台登录页面输入上述 API Key 即可登录。
3. 登录后可以：
   - 查看系统概览和统计数据
   - 管理用户和 API Key
   - 查看审计日志
   - 测试 API 接口

### 调用 API 接口

API 接口需要在请求头中携带 API Key：

```bash
curl -X GET "http://localhost:8001/api/v1/query/real-metrics" \
  -H "X-API-Key: your_api_key_here" \
  -H "Content-Type: application/json"
```

或访问 Swagger 文档进行交互式测试：http://localhost:8001/docs

## 常用命令

### 容器管理

```bash
# 启动服务
./start-nanzi-ai-agent.sh

# 停止服务
./stop-nanzi-ai-agent.sh

# 重启服务
docker restart nanzi-ai-agent

# 删除容器
docker rm -f nanzi-ai-agent

# 查看容器状态
docker ps -a | grep nanzi
```

### 日志查看

```bash
# 查看最近 100 行日志
docker logs nanzi-ai-agent --tail 100

# 实时查看日志
docker logs -f nanzi-ai-agent

# 查看错误日志
docker logs nanzi-ai-agent 2>&1 | grep -i error
```

### 进入容器调试

```bash
# 进入容器 Shell
docker exec -it nanzi-ai-agent bash

# 查看环境变量
docker exec nanzi-ai-agent env | grep MYSQL

# 测试数据库连接
docker exec nanzi-ai-agent python -c "from app.core.database import engine; print(engine)"
```

## 故障排查

### 问题 1: 容器启动后立即退出

**原因**: 数据库连接失败

**解决方法**:
1. 检查 `.env` 配置是否正确
2. 确认数据库 Host 使用的是宿主机 IP 而非 localhost
3. 检查数据库服务是否启动
4. 查看详细日志：`docker logs nanzi-ai-agent`

### 问题 2: 无法访问管理后台

**原因**: 端口被占用或容器未正常启动

**解决方法**:
1. 检查 8001 端口是否被占用：`lsof -ti:8001`
2. 检查容器状态：`docker ps | grep nanzi-ai-agent`
3. 查看容器日志确认启动状态

### 问题 3: API Key 认证失败

**原因**: API Key 加密密钥未配置或不匹配

**解决方法**:
1. 确认 `.env` 中配置了 `ENCRYPTION_KEY`
2. 确认 docker-compose 文件中传递了该环境变量
3. 重新生成 API Key：`python scripts/create_admin_key.py`

## 生产环境部署建议

1. **安全配置**
   - 修改所有默认密码
   - 使用强密码和安全的加密密钥
   - 限制数据库访问 IP 白名单

2. **性能优化**
   - 启用 Redis 缓存（`REDIS_ENABLE=true`）
   - 配置数据库连接池大小
   - 根据负载调整 uvicorn workers 数量

3. **监控告警**
   - 定期检查容器状态和日志
   - 配置健康检查端点监控
   - 设置磁盘空间告警

4. **数据备份**
   - 定期备份 MySQL 数据库

   - 保存环境配置文件
