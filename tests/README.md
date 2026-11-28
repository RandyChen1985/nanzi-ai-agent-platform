# 自动化测试指南 (Automated Testing Guide)

本项目使用 `pytest` + `httpx` (AsyncClient) 构建自动化集成测试框架。测试直接针对 FastAPI 应用实例运行，**无需**预先启动 `uvicorn` 服务。

## 1. 快速开始

### 方式一：使用测试脚本（推荐）

```bash
# 在项目根目录执行
./tests/run_tests.sh

# 或者在 tests 目录中执行
cd tests && ./run_tests.sh
```

测试脚本会自动：
- 设置正确的 PYTHONPATH
- 检测 pytest 是否安装
- 运行所有测试

### 方式二：手动执行 pytest

在项目根目录下执行以下命令：

```bash
# 运行所有测试（后端 + 前端契约，约 16884 条）
export PYTHONPATH=$PYTHONPATH:. 
.venv/bin/python -m pytest tests/

# 只跑后端（跳过前端契约测试，前端那部分纯静态断言，可单独快速跑）
.venv/bin/python -m pytest tests/ --ignore=tests/frontend

# 只跑前端契约测试：不加载 tests/conftest.py，因此不初始化数据库/Redis，约 3 分钟
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend

# 运行特定模块的测试
.venv/bin/python -m pytest tests/api/v1/test_resources_donghuan.py

# 显示详细输出
.venv/bin/python -m pytest tests/ -v

# 显示测试覆盖率
.venv/bin/python -m pytest tests/ --cov=app
```

> **为什么 `pytest tests/` 现在能一次跑完**：`tests/` 及其所有子目录都是完整的 Python 包（各有 `__init__.py`），用例模块按 `tests.<path>.<name>` 全限定导入。此前只有 `tests/api/portal/` 有 `__init__.py`，pytest 的 prepend 导入模式会按文件名给模块命名，于是 `tests/api/v1/test_chat_home_recommendations_contract.py` 与 `tests/frontend/test_chat_home_recommendations_contract.py` 被判成同一个模块，收集阶段直接 `import file mismatch` 中断，全量只能拆两段跑。**新增测试文件时请保持包结构**（新目录记得放 `__init__.py`），目录内也不要再用无点的兄弟模块导入——写成 `from tests.<path>.<module> import ...`。


## 2. 环境要求

- 确保依赖已安装。推荐开发环境先执行 `./dev.sh`；如果只准备测试环境而不启动服务，需先确保 `uv` 已安装，再执行：
  ```bash
  uv python install 3.11
  uv venv --python 3.11 .venv
  uv pip install --python .venv/bin/python \
    --default-index https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt
  ```
- 确保本地数据库（ClickHouse, MySQL, Redis）已启动且配置正确（参考 `.env` 文件）。

## 3. 目录结构

```text
tests/
├── __init__.py          # 每个测试目录都有：保证模块按 tests.<path>.<name> 全限定导入
├── conftest.py          # 测试固件配置 (Fixtures)
│   ├── client           # 异步 HTTP 客户端 (httpx.AsyncClient)
│   └── valid_api_key    # 测试用的合法 API Key
├── known_failures.py    # 已知失败基线的解析与校验
├── known_failures.txt   # 已知失败清单（命中即 xfail，清单外失败照常报红）
├── run_tests.sh         # 一键测试脚本（自动配置环境，参数可透传）
├── ai/                  # AgentScope 运行时、工具体系与流水线
├── api/
│   ├── portal/          # Portal 管理接口测试
│   └── v1/              # V1 数据接口测试
├── core/                # 核心能力（数据库、Redis、密钥、权限）
├── frontend/            # 前端契约测试 + Node 探针（*.mjs / *.cjs）
├── services/            # 业务服务层
└── README.md            # 本指南
```

## 4. 常见问题

### Q: 为什么会出现 `RuntimeError: Event loop is closed`?
**A**: 这是 `pytest-asyncio` 在 Python 3.13 环境下清理异步资源（如 Redis 连接池）时的一个已知副作用。
如果你的测试摘要显示 `N passed`（例如 `2 passed`），说明业务逻辑测试是**成功**的。该错误发生在测试结束后的清理阶段，不影响代码功能的正确性验证。

### Q: 需要先启动 API 服务吗？
**A**: **不需要**。
测试框架使用 `ASGITransport` 直接与 FastAPI 应用实例交互。测试运行时会自动触发应用的生命周期事件（如初始化数据库连接池），测试结束后自动关闭。

### Q: 跑全量时报 `import file mismatch` 怎么办？
**A**: 这是**新建测试目录漏了 `__init__.py`**，或目录内写了无点的兄弟模块导入（`from foo import bar`）。pytest 的 prepend 导入模式按文件名给模块命名，两个不同目录下的同名 `test_*.py` 会互相覆盖。修法只有两步：

1. 新目录放一个 `__init__.py`（内容一行说明即可）；
2. 兄弟模块导入写成全限定路径 `from tests.<path>.<module> import ...`，不要依赖 pytest 往 `sys.path` 里插测试目录。

改完用 `pytest tests/ --collect-only -q` 确认能收集到全部用例（当前基线 16884 条），再跑全量。

### Q: 前端契约测试为什么要单独跑？
**A**: 不是为了绕开冲突（包化后 `pytest tests/` 已能一次跑完），而是因为 `tests/frontend` 里全是源码级静态断言，不需要数据库和 Redis。用 `--confcutdir=tests/frontend` 跳过 `tests/conftest.py` 后，约 3 分钟就能跑完 5650 条，改完前端顺手跑一遍比等 15 分钟的全量划算。

