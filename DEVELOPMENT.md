# 南孜智能体平台 - 开发指南

> 本文档面向开发人员，提供环境搭建、开发规范、测试、部署等完整指引。

## 📋 目录

- [快速开始](#快速开始)
- [开发环境搭建](#开发环境搭建)
- [项目结构](#项目结构)
- [开发工作流](#开发工作流)
- [代码规范](#代码规范)
- [测试指南](#测试指南)
- [本地调试](#本地调试)
- [常见问题](#常见问题)
- [部署指南](#部署指南)

---

## 快速开始

### 前置要求

- **Python**: 3.10+
- **Node.js**: 18+
- **MySQL**: 8.0+
- **Redis**: `redis/redis-stack:latest` (因系统需要使用 RediSearch 模块，必须使用 Redis Stack 镜像)
- **RAGFlow**: (可选，若使用 ChatBI 和知识库功能，系统需依赖此外部服务的 API 接口进行文档解析与向量检索)

### Docker 一键启动（推荐）

> [!IMPORTANT]
> **运行前置说明**：
>
> 1. **服务依赖**：需提前准备好 MySQL 服务（并手动创建数据库，如 `nanzi_ai_agent_platform`）与 Redis 服务（因包含向量检索等高级功能，必须使用支持 RediSearch 的 `redis/redis-stack:latest` 版本服务）。
> 2. **镜像构建**：一键启动脚本底层依赖本地的 `nanzi-ai-agent` 镜像，运行前需先执行构建。

```bash
# 1. 克隆仓库并进入目录
git clone <repository-url>
cd nanzi-ai-agent-platform

# 2. 本地构建 Docker 镜像（以本机 CPU 架构调试为例）
cd docker
./build_native.sh

# 3. 准备环境变量配置文件（修改其中的 MYSQL_HOST、REDIS_HOST 等连接信息）
cp ../env.example .env
vim .env

# 4. 运行一键启动脚本
./start-nanzi-ai-agent.sh
```

访问：http://localhost:8001

---

## 开发环境搭建

### 1. 后端依赖安装与初始化

```bash
# 1. 创建虚拟环境并激活
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 准备环境变量配置文件
cp env.example .env
# 编辑 .env 文件，配置数据库连接等信息

# 4. 初始化数据库（请先确保已手动创建数据库，如 `nanzi_ai_agent_platform`）
# 执行交互式部署脚本，详见 db-prod/README.md 指南
./db-prod/apply-sql.sh
```

### 2. 前端依赖安装

```bash
# 进入前端目录并安装依赖
cd frontend
npm install
```

### 3. 启动开发服务器

#### 方式一：使用一键开发启动脚本（推荐）

在开发阶段，推荐直接在项目根目录下使用一键开发启动脚本，它会自动处理编译和进程清理：

```bash
./dev.sh
```

该脚本会自动依次执行：

1. **清理旧进程**：检查并停止占用 `8001` 端口的旧后端服务。
2. **编译前端**：进入 `frontend` 目录并运行 `npx vite build` 编译前端静态资源。
3. **启动后端**：以热重载模式（`--reload`）前台启动后端 `uvicorn` 服务，并在终端前台显示日志，极大地方便了编译与启动日志排查。

**运行输出示例**：

```text
$ ./dev.sh
==================================================
       南孜智能体平台 · 本地开发启动工具       
==================================================

🛑 [1/3] 正在检查并停止旧服务 (Port 8001)...
✅ 端口 8001 空闲，无需停止

🚀 [2/3] 正在编译前端 (Building Frontend)...
vite v7.3.0 building client environment for production...
transforming (6423) node_modules/zrender/lib/Element.js
...
✅ 前端编译成功！

🔥 [3/3] 正在启动后端服务 (Starting Backend in Foreground)...
提示：您将在此看到实时运行日志，按 Ctrl+C 可停止服务。
------------------------------------------------
INFO:     Will watch for changes in these directories: ['/Users/chenxiaolong/资料/有孚网络/1南孜中台/yovole-nanzi-ai-agent-platform']
INFO:     Uvicorn running on http://0.0.0.0:8001 (Press CTRL+C to quit)
INFO:     Started reloader process [45012] using StatReload
INFO:     Started server process [45014]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

#### 方式二：手动分别启动（可选）

如果您在开发中需要分别调试前后端，也可以手动启动它们：

**手动启动后端**：

```bash
source venv/bin/activate
uvicorn app.main:app --reload --port 8001
```

**手动启动前端**：

```bash
cd frontend
npm run dev
```

前端开发服务器启动后，默认可通过 http://localhost:5173 进行访问（Vite 默认使用代理转发 `/api` 请求，无需额外配置）。

### IDE 配置

#### VS Code

推荐安装以下扩展：

```json
{
  "recommendations": [
    "ms-python.python",
    "ms-python.vscode-pylance",
    "esbenp.prettier-vscode",
    "dbaeumer.vscode-eslint",
    "bradlc.vscode-tailwindcss",
    "vue.volar",
    "vue.vscode-typescript-vue-plugin"
  ]
}
```

#### PyCharm

1. 安装 Python 插件
2. 配置代码风格（PEP 8）
3. 启用类型检查（mypy）

---

## 项目结构

```
yovole-nanzi-ai-agent-platform/
├── app/                          # 后端核心代码
│   ├── api/                      # API 路由层
│   │   ├── portal/               # 管理后台接口
│   │   └── v1/                   # 外部集成接口
│   ├── core/                     # 核心组件
│   │   ├── config.py             # 配置管理
│   │   ├── database.py           # 数据库连接
│   │   ├── redis.py              # Redis 连接
│   │   ├── middleware.py         # 中间件
│   │   └── errors.py             # 错误码定义
│   ├── services/                 # 业务逻辑层
│   │   ├── ai/                   # AI 编排核心
│   │   │   ├── executors/        # 执行器
│   │   │   ├── tools/            # 工具注册表
│   │   │   ├── agent_service.py  # 智能体服务
│   │   │   └── router_service.py # 路由服务
│   │   ├── auth_service.py       # 认证服务
│   │   ├── metadata_service.py   # 元数据服务
│   │   └── audit_service.py      # 审计服务
│   ├── models/                   # ORM 数据模型
│   ├── schemas/                  # Pydantic 数据模式
│   └── utils/                    # 工具函数
├── frontend/                     # 前端工程
│   ├── src/
│   │   ├── api/                  # API 客户端
│   │   ├── components/           # 组件
│   │   ├── views/                # 页面视图
│   │   ├── router/               # 路由配置
│   │   ├── utils/                # 工具函数
│   │   └── composables/          # 组合式函数
│   ├── public/                   # 静态资源
│   └── package.json
├── db-prod/                      # 数据库 Schema 与变更 SQL 脚本存放目录
├── tests/                        # 测试代码
│   ├── api/
│   ├── services/
│   ├── core/
│   ├── CHECKLIST.md              # 自动化测试清单（每次实现新功能/接口必须同步更新）
│   └── run_tests.sh              # 一键自动化测试运行脚本
├── architech/                    # 架构设计文档
│   ├── prompts/                  # 系统提示词 (Prompts) 存储与版本控制
│   ├── meta-schemal/             # 元数据规范
│   └── api-schemal/              # API 规范
├── openspec/                     # 接口变更追踪
│   ├── changes/                  # 功能变更记录
│   └── specs/                    # 接口规格文档
├── docker/                       # Docker 配置
├── scripts/                      # 脚本工具
├── dev.sh                        # 本地开发一键前台编译重启服务脚本
├── requirements.txt              # Python 依赖
├── env.example                   # 环境变量示例
├── README.md                     # 项目说明
└── DEVELOPMENT.md                # 本文档

```

---

## 开发工作流

### 分支管理

```bash
main              # 主分支（生产环境）
├── develop       # 开发分支（预发布环境）
└── feature/*     # 功能分支
    ├── feature/chatbi-self-healing
    ├── feature/mcp-integration
    └── feature/rag-optimization
```

**分支命名规范**:

- 功能分支: `feature/<功能描述>`
- 修复分支: `fix/<问题描述>`
- 优化分支: `refactor/<优化内容>`

### Git 提交规范

使用 Conventional Commits 格式（必须使用中文）：

```bash
feat: 添加 ChatBI SQL 自愈机制
fix: 修复 MCP 连接超时问题
docs: 更新 API 接口文档
style: 统一代码格式
refactor: 重构 Agent 调度器
perf: 优化数据库查询性能
test: 增加单元测试覆盖
chore: 更新依赖版本
```

**提交示例**:

```bash
git add .
git commit -m "feat: 添加 MCP 工具实时测试功能

- 支持在线测试 MCP 服务器工具调用
- 增加请求/响应详情查看
- 添加错误日志追踪"
```

### Code Review 流程

1. 创建 Pull Request
2. 填写 PR 模板（功能描述、测试情况、影响范围）
3. 至少一位开发者 Review
4. 通过 CI 检查
5. 合并到 develop 分支

### 数据库变更规范

凡涉及数据库 Schema 或数据的变更，必须严格遵守以下规范：

1. **执行边界**：开发人员与 AI 助手**仅负责**在 [db-prod/](file:///Users/chenxiaolong/%E8%B5%84%E6%96%99/%E6%9C%89%E5%AD%9A%E7%BD%91%E7%BB%9C/1%E4%BA%91%E6%9E%A2%E4%B8%AD%E5%8F%B0/yovole-nanzi-ai-agent-platform/db-prod/) 目录下创建 SQL 脚本。**严禁**自动执行 SQL 语句或通过 Python 脚本直接修改本地/线上数据库。
2. **命名规范**：使用 `V` 开头 + 自增序号 + 描述。例如 `V60-create_scheduler_job_store.sql`。在创建前必须检查目录下的当前最大序号并自增 +1。

### 系统提示词变更规范

凡涉及系统提示词 (Prompts) 的新增或更新，必须在 [architech/prompts/](file:///Users/chenxiaolong/%E8%B5%84%E6%96%99/%E6%9C%89%E5%AD%9A%E7%BD%91%E7%BB%9C/1%E4%BA%91%E6%9E%A2%E4%B8%AD%E5%8F%B0/yovole-nanzi-ai-agent-platform/architech/prompts/) 目录下进行并纳入 Git 版本控制。

> [!NOTE]
> 开发人员或 AI 助手仅负责创建/更新对应的 Prompt 文件，不负责自动同步到系统内部运行环境中。

### 自动化测试清单更新规范

为确保代码逻辑和接口的稳定性，在实现新功能或接口时，必须自动更新 [tests/CHECKLIST.md](file:///Users/chenxiaolong/%E8%B5%84%E6%96%99/%E6%9C%89%E5%AD%9A%E7%BD%91%E7%BB%9C/1%E4%BA%91%E6%9E%A2%E4%B8%AD%E5%8F%B0/yovole-nanzi-ai-agent-platform/tests/CHECKLIST.md) 中的自动化测试清单，记录测试用例。

### 代码已合本地同步与重启流程

当代码合并至主分支后，在本地开发环境依次执行以下流程，以保证本地和远程的分支基准对齐并稳定运行：

1. **同步主分支**：切换到主分支并同步拉取最新更改：
   ```bash
   git checkout main && git pull origin main
   ```
2. **变基开发分支**：切换回开发分支并进行变基：
   ```bash
   git checkout dev && git rebase main
   ```
3. **强推同步**：将干净的开发分支强推同步至远程：
   ```bash
   git push origin dev --force-with-lease
   ```
4. **编译与重启**：执行本地开发环境的前台编译与重启服务：
   ```bash
   ./dev.sh
   ```

---

## 代码规范

### Python 代码规范

#### 遵循 PEP 8 标准

**安装 Black 和 isort**:

```bash
pip install black isort flake8 mypy
```

**自动格式化**:

```bash
# 格式化代码
black app/ tests/

# 整理 import
isort app/ tests/
```

**类型检查**:

```bash
mypy app/
```

**代码检查**:

```bash
flake8 app/ --max-line-length=120
```

#### 命名规范

```python
# 类名：大驼峰
class AgentDispatcher:
    pass

# 函数/方法：小写加下划线
async def execute_query(self):
    pass

# 常量：全大写加下划线
MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30

# 变量：小写加下划线
user_id = 123
is_active = True
```

#### 类型注解

```python
from typing import List, Dict, Any, Optional

async def get_user(
    user_id: int,
    include_details: bool = False
) -> Optional[Dict[str, Any]]:
    """获取用户信息

    Args:
        user_id: 用户 ID
        include_details: 是否包含详细信息

    Returns:
        用户字典，如果不存在返回 None
    """
    pass
```

#### 异步编程规范

```python
# ✅ 推荐：使用 async/await
async def process_data():
    result = await fetch_data()
    return result

# ❌ 避免：混合使用异步和同步
async def bad_example():
    result = sync_fetch()  # 阻塞事件循环
    return result
```

### Vue/TypeScript 代码规范

#### 组件结构

```vue
<template>
  <!-- 模板内容 -->
</template>

<script setup lang="ts">
// 1. 导入
import { ref, computed } from 'vue'
import type { User } from '@/api/common'

// 2. Props 定义
interface Props {
  userId: number
  details?: boolean
}
const props = withDefaults(defineProps<Props>(), {
  details: false
})

// 3. Emits 定义
const emit = defineEmits<{
  update: [userId: number]
  error: [message: string]
}>()

// 4. 响应式数据
const loading = ref(false)
const user = ref<User | null>(null)

// 5. 计算属性
const displayName = computed(() => user.value?.name ?? '未命名')

// 6. 方法
async function fetchUser() {
  loading.value = true
  try {
    // 实现
  } finally {
    loading.value = false
  }
}

// 7. 生命周期
onMounted(() => {
  fetchUser()
})
</script>

<style scoped>
/* 样式 */
</style>
```

#### 命名规范

```typescript
// 组件：大驼峰（PascalCase）
export default defineComponent({
  name: 'DataTable'
})

// 函数/变量：小驼峰（camelCase）
const userName = '张三'
function getUserInfo() { }

// 常量：全大写加下划线
const MAX_ITEMS = 100
```

#### 代码格式化

```bash
# 格式化代码
npm run format

# 检查代码格式
npm run lint
```

### API 接口规范

#### 统一响应格式

```python
from app.core.errors import ErrorCode

# 成功响应
{
    "code": 200,
    "message": "success",
    "data": { ... },
    "timestamp": "2025-01-01T12:00:00+08:00",
    "trace_id": "uuid-xxx"
}

# 错误响应
{
    "code": ErrorCode.INVALID_PARAMETER,
    "message": "参数校验失败",
    "detail": "user_id 不能为空",
    "data": None,
    "timestamp": "2025-01-01T12:00:00+08:00",
    "trace_id": "uuid-xxx"
}
```

#### API 端点示例

```python
from fastapi import APIRouter, Depends, HTTPException
from app.schemas.user import UserResponse
from app.services.user_service import UserService

router = APIRouter()

@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    current_user: dict = Depends(get_current_user)
):
    """获取用户信息"""
    user = await UserService.get_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=404,
            detail="用户不存在"
        )
    return user
```

---

## 测试指南

本项目使用 `pytest` + `httpx` (AsyncClient) 构建自动化集成测试框架。更详细的测试方法与环境配置说明，请查阅 [tests/README.md](file:///Users/chenxiaolong/资料/有孚网络/1南孜中台/yovole-nanzi-ai-agent-platform/tests/README.md)。

### 后端测试

测试直接针对 FastAPI 应用实例运行，**无需**预先启动 `uvicorn` 服务。

#### 运行测试

**方式一：使用自动化测试脚本（推荐）**

在项目根目录下，运行封装好的自动化测试脚本：

```bash
./tests/run_tests.sh
```

该脚本会自动配置 `PYTHONPATH` 环境变量并检索依赖，自动执行所有集成测试。脚本源码细节详见 [tests/run_tests.sh](file:///Users/chenxiaolong/资料/有孚网络/1南孜中台/yovole-nanzi-ai-agent-platform/tests/run_tests.sh)。

**方式二：手动执行 pytest**
在项目根目录下执行：

```bash
# 运行所有测试
export PYTHONPATH=$PYTHONPATH:. 
python3 -m pytest tests/

# 运行特定测试文件
python3 -m pytest tests/services/test_user_service.py

# 运行特定测试用例
python3 -m pytest tests/services/test_user_service.py::test_get_user

# 显示详细输出
python3 -m pytest tests/ -v

# 显示测试覆盖率
python3 -m pytest tests/ --cov=app
```

> [!NOTE]
> **关于 `RuntimeError: Event loop is closed`**
> 这是 `pytest-asyncio` 在 Python 3.13 环境下清理异步资源（如 Redis 连接池）时的一个已知副作用。如果测试显示 `N passed`（例如 `10 passed`），说明业务逻辑测试是**成功**的。该错误发生在测试结束后的清理阶段，不影响代码功能的正确性验证。

#### 测试示例

```python
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.mark.asyncio
async def test_create_user():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/api/portal/users",
            json={
                "user_name": "test_user",
                "role": "user"
            },
            headers={"X-API-Key": "test_api_key"}
        )
    assert response.status_code == 200
    assert response.json()["code"] == 200
```

### 前端测试

```bash
# 运行测试（需要配置）
npm run test

# 运行测试覆盖率
npm run test:coverage
```

---

## 本地调试

### 后端调试

#### VS Code 调试配置

`.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Python: FastAPI",
      "type": "debugpy",
      "request": "launch",
      "module": "uvicorn",
      "args": [
        "app.main:app",
        "--reload",
        "--port",
        "8001"
      ],
      "cwd": "${workspaceFolder}",
      "console": "integratedTerminal",
      "envFile": "${workspaceFolder}/.env"
    }
  ]
}
```

#### 日志调试

```python
import logging

logger = logging.getLogger(__name__)

def process_data(data):
    logger.info(f"Processing data: {data}")
    logger.debug(f"Data details: {data.to_dict()}")
    try:
        # 业务逻辑
        pass
    except Exception as e:
        logger.error(f"Processing failed: {e}", exc_info=True)
        raise
```

### 前端调试

#### 浏览器开发工具

1. 使用 Vue DevTools 扩展
2. 查看 Pinia 状态
3. 监控网络请求

#### API 调试

访问 Swagger 文档：http://localhost:8001/docs

---

## 常见问题

### 后端

**Q: 数据库连接失败**

A: 检查 `app.core.config.py` 配置和 `.env` 文件：

```bash
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_DB=nanzi_ai
MYSQL_USER=root
MYSQL_PASSWORD=your_password
```

**Q: Redis 连接失败**

A: 确保 Redis 服务运行，配置正确的 `REDIS_HOST` 和 `REDIS_PORT`

**Q: API 返回 401 未授权**

A: 检查请求头是否包含 `X-API-Key`:

```bash
curl -H "X-API-Key: your_api_key" http://localhost:8001/health
```

### 前端

**Q: npm install 失败**

A: 清除缓存重试：

```bash
rm -rf node_modules package-lock.json
npm install
```

**Q: 请求跨域问题**

A: 检查 `vite.config.ts` 代理配置：

```typescript
export default defineConfig({
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true
      }
    }
  }
})
```

**Q: TypeScript 类型错误**

A: 确保 `tsconfig.json` 配置正确，运行：

```bash
npm run type-check
```

### 开发环境

**Q: 如何重置开发数据库**

```bash
# 1. 登录 MySQL 并重建数据库
mysql -u root -p -e "DROP DATABASE IF EXISTS nanzi_ai_agent_platform; CREATE DATABASE nanzi_ai_agent_platform CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;"

# 2. 重新运行脚本进行全量初始化
./db-prod/apply-sql.sh

# 3. （可选）重新导入默认管理员账号
./db-prod/apply-sql.sh db-prod/INIT-USER-ADMIN.sql
```

---

## 部署指南

### Docker 部署

#### 构建镜像

```bash
# 构建后端镜像
docker build -t nanzi-ai-backend:latest .

# 构建前端镜像
cd frontend
docker build -t nanzi-ai-frontend:latest .
```

#### 启动服务

```bash
cd docker
docker-compose up -d
```

#### 查看日志

```bash
docker-compose logs -f backend
docker-compose logs -f frontend
```

### 生产环境配置

#### 环境变量

```bash
# 生产环境配置
API_SERVICE_ENV=production
LOG_LEVEL=WARNING
ALLOWED_ORIGINS=https://your-domain.com

# 数据库连接池
MYSQL_POOL_SIZE=50
MYSQL_MAX_OVERFLOW=100
MYSQL_POOL_RECYCLE=3600
```

#### Gunicorn 配置

`gunicorn_config.py`:

```python
import multiprocessing

workers = multiprocessing.cpu_count() * 2 + 1
bind = "0.0.0.0:8001"
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
max_requests = 10000
max_requests_jitter = 1000
timeout = 120
keepalive = 5
preload_app = True
```

启动命令：

```bash
gunicorn app.main:app -c gunicorn_config.py
```

### 监控与日志

#### 日志收集

```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        }
    },
    'handlers': {
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': 'logs/app.log',
            'maxBytes': 104857600,  # 100MB
            'backupCount': 5,
            'formatter': 'standard'
        }
    },
    'root': {
        'handlers': ['file'],
        'level': 'INFO'
    }
}
```

#### 性能监控

建议集成以下工具：

- Prometheus: 指标采集
- Grafana: 可视化面板
- Sentry: 错误追踪

---

## 技术支持

- **文档**: `docs/` 目录
- **API 文档**: http://localhost:8001/docs
- **架构设计**: `architech/` 目录
- **问题反馈**: 创建 Issue

---

## 贡献指南

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'feat: 某个功能'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

---

## 许可证

本项目采用商业许可模式，详情联系 [南孜智能体](https://www.yovole.com)。

---

**最后更新**: 2025-03-18
