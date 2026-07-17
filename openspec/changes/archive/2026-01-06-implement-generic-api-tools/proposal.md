# Proposal: 实现通用 HTTP 工具配置与管理 (Generic API Tools)

## 1. 背景与目标 (Background & Goals)
目前南孜智能体平台（NanZi AI Agent Platform）的工具集（Tools）主要依赖开发者手动编写 Python 代码（如 `get_dataset_schema`, `execute_sql_query`）。随着中台对接的业务系统增多（如运维操作、外部知识库搜索等），这种“硬编码”方式存在以下问题：
1.  **扩展成本高**：每新增一个 API 都需要发布新版本代码。
2.  **维护困难**：Token、URL 等配置分散，难以统一管理。
3.  **缺乏灵活性**：运营人员无法自助配置简单的查询工具。

本提案旨在实现一套 **“配置驱动的通用 HTTP 工具” (Generic API Tools)** 机制，并配套可视化管理界面。

**核心目标**：
1.  **通用执行器**：实现一个通用的 `GenericApiTool`，支持通过配置定义 URL、Method、Headers 和参数 Schema。
2.  **可视化管理**：在“系统设置”中新增“工具管理”模块，允许管理员 CRUD 工具配置。
3.  **动态加载**：Agent 启动或运行时能动态加载并注册这些配置化工具。

## 2. 核心变更 (Core Changes)

### 2.1 后端 (Backend)
- **数据库**：新增 `sys_api_tools` 表，存储工具的元数据（名称、描述、API 配置、启用状态等）。
- **工具工厂**：实现 `GenericApiTool` 类，负责解析配置并生成 LangChain `StructuredTool` 对象。
- **动态注册**：改造 `ToolRegistry` 或 `AgentManager`，支持从数据库加载工具。
- **管理接口**：新增 CRUD API (`POST /tools`, `GET /tools`, etc.)。

### 2.2 前端 (Frontend)
- **系统设置**：在“系统设置”页面新增“工具管理 (Tool Management)” Tab（位于“模型管理”之后）。
- **配置表单**：提供友好的表单，支持配置：
    - 基础信息（名称、描述）
    - 请求配置（URL Template, Method, Headers）
    - 参数定义（基于 JSON Schema 的参数名、类型、描述、必填项）
- **测试面板**：(可选) 提供一个简单的“测试运行”按钮，验证配置是否正确。

## 3. 交付物 (Deliverables)
- SQL 脚本：创建 `sys_api_tools` 表。
- 后端代码：`app/models/tool.py`, `app/services/ai/tools/generic_api.py`, `app/api/v1/tools.py`。
- 前端代码：`frontend/src/views/settings/ToolManagement.vue` 及相关路由/Store。
- 文档：更新 `AGENTS.md` 说明如何配置新工具。
