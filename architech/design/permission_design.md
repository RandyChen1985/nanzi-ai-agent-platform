# 云枢智能体平台：权限体系设计文档

## 1. 概述
本文档详细描述了云枢智能体平台的权限管理体系。系统采用 **RBAC (Role-Based Access Control)** 模型，结合系统内置角色与自定义业务角色，实现对管理后台、智能体应用、知识库等资源的细粒度访问控制。

## 2. 权限模型
系统权限分为三个层级：

### 2.1 系统管理权限 (System Level)
*   **控制目标**：控制用户是否能进入管理后台。
*   **实现方式**：基于 `ai_agent_users.role` 字段。
*   **角色定义**：
    *   `admin`: 超级管理员，拥有系统所有操作权限，跳过所有资源校验逻辑。
    *   `user`: 普通用户，仅能访问被授权的资源。

### 2.2 资源访问权限 (Resource Level - RBAC)
*   **控制目标**：控制用户能访问哪些具体的资源对象。
*   **涉及资源类型**：
    *   `agent`: 智能体应用
    *   `dataset`: RagFlow 知识库数据集
    *   `api`: 对外服务接口 (如 V1 Chat, V1 Schema)
    *   `metadata`: 元数据数据集
*   **权限来源**：
    *   **直接授权**：直接在用户管理界面为用户勾选的资源。
    *   **角色继承**：用户通过关联“业务角色”，继承该角色所拥有的资源。
*   **计算公式**：
    `有效权限 = (系统角色 == admin) OR (用户直接权限) OR (所属所有业务角色的权限并集)`

### 2.3 API 调用权限 (External API Level)
*   **控制目标**：第三方系统通过 API Key 调用 V1 接口时的安全性。
*   **校验点**：在 `/api/v1/chat/completions` 等接口入口处，强制校验 API Key 的持有者是否拥有该智能体（Model ID）的访问权。

## 3. 数据库设计
主要涉及以下表格：

| 表名 | 说明 | 核心字段 |
| :--- | :--- | :--- |
| `ai_agent_users` | 用户主表 | `id`, `role` (admin/user), `status` |
| `ai_agent_roles` | 业务角色定义表 | `id`, `code`, `name` |
| `ai_agent_user_role_relations` | 用户-角色关联表 | `user_id`, `role_id` |
| `ai_agent_resource_permissions` | 资源权限表 | `user_id`, `role_id`, `resource_type`, `resource_id` |

*注：`ai_agent_resource_permissions` 表中，如果 `user_id` 有值则为直接授权；如果 `role_id` 有值则为角色授权。*

## 4. 核心组件
### 4.1 后端校验服务 (`PermissionService`)
*   `get_user_permissions(user_id)`: 聚合计算用户的所有有效权限，并缓存至 Redis。
*   `check_permission(user_id, resource_type, resource_id)`: 统一权限检查入口。
*   `update_user_roles(user_id, role_ids)`: 更新用户所属的业务角色。

### 4.2 接口依赖 (FastAPI Dependencies)
*   `require_admin`: 拦截非管理员用户的后台操作。
*   `require_api_key`: 基础认证拦截。
*   `verify_v1_api_access`: V1 接口专属的资源权限拦截器。

## 5. 缓存策略
*   **缓存位置**：Redis (`sys:auth:permissions:v3:user:{user_id}`)
*   **失效触发**：
    *   修改用户角色关联时。
    *   修改用户直接权限时。
    *   修改角色包含的资源时（失效该角色下所有用户的缓存）。
*   **有效期**：默认 1 小时。

## 6. 管理流程
1.  **创建角色**：在【角色管理】中创建业务角色（如“产品部”），并分配 Agent 和知识库。
2.  **分配用户**：在【用户管理】中为用户勾选所属角色。
3.  **细粒度补丁**：如有特殊需求，在【用户管理】->【权限配置】中为该用户单独追加特定资源。
