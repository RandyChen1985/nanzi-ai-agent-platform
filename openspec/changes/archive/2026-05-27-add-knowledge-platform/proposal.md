## Why

当前平台已经通过 RAGFlow 支持知识库检索、知识库列表选择和工具调用，但知识库创建、文档上传、解析管理仍需要管理员登录 RAGFlow 控制台完成，导致 ChatBI 开发平台与知识库开发流程割裂。

本变更将 RAGFlow 的知识库管理与检索验证能力集成到南孜智能体平台内，降低运维与业务人员维护知识库的成本，并为 Agent 关联知识库、检索调参和问题排查提供统一入口。

## What Changes

- 新增「知识库开发平台」菜单分组，位于「CHATBI 开发平台」分组之后。
- 新增「知识库管理」模块，用于管理 RAGFlow 知识库与知识库内文档。
- 新增「检索测试」模块，用于选择知识库、输入查询并查看 RAGFlow 检索命中结果。
- 扩展后端 RAGFlow Portal API，代理知识库创建、删除、文档列表、文档上传、文档删除、文档解析与检索测试。
- 新增知识库开发平台相关菜单权限与操作权限，纳入角色权限配置。
- 新增本地知识库元数据表，用于维护 RAGFlow 元数据之外的业务属性、归属信息、展示信息和扩展配置。
- 知识库创建、删除、文档上传、文档删除、文档解析、检索测试等操作写入审计日志。

## Capabilities

### New Capabilities

- `knowledge-platform`: 覆盖知识库开发平台的导航、权限、RAGFlow 知识库管理、文档管理和检索测试能力。

### Modified Capabilities

- `permissions`: 增加知识库开发平台菜单权限和知识库管理/检索测试操作权限。
- `audit-logging`: 增加知识库管理与检索测试操作审计要求。

## Impact

- 前端：
  - 新增知识库开发平台菜单分组。
  - 新增 `KnowledgeBaseManagement.vue` 与 `KnowledgeRetrievalTest.vue` 页面。
  - 扩展路由与权限常量。
  - 复用或扩展 `RagFlowResourceSelector`、Toast、ConfirmModal 等现有 UI 组件。
- 后端：
  - 扩展 `app/api/portal/endpoints/ragflow.py` 管理接口。
  - 复用并补齐 `app/services/ai/ragflow_client.py` 中的 RAGFlow Dataset、Document、Retrieval API 封装。
  - 增加请求/响应 DTO 与错误处理。
  - 新增本地知识库元数据模型、数据库迁移与服务层。
- 权限与安全：
  - 需要为知识库菜单和操作按钮增加权限点。
  - 后端接口必须校验当前用户权限，避免未授权用户创建、删除或上传知识库内容。
  - 知识库管理相关写操作和检索测试操作必须记录审计日志。
- 外部依赖：
  - 依赖系统配置中的 `ragflow_api_url` 与 `ragflow_api_key`。
  - 依赖 RAGFlow OpenAPI 的 datasets、documents、chunks、retrieval 接口。
