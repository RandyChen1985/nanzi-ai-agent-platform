# Redis 键设计与使用文档

本文档总结了当前系统 (`yovole-nanzi-ai-agent-platform`) 中 Redis 的键设计、数据结构、过期策略及业务用途。

## 1. 核心系统 (System Core)

### 1.1 系统配置缓存
*   **业务场景**: 缓存 `system_configs` 表中的系统配置参数，减少数据库查询频率。
*   **Key 模式**: `sys_config:{config_key}`
*   **数据结构**: `String` (Value)
*   **过期时间 (TTL)**: 5 分钟 (300s)
*   **代码位置**: `app/services/config_service.py`
*   **说明**: 
    *   读取策略: 先查 Redis，未命中查 DB 并写入 Redis。
    *   更新策略: 更新 DB 后同步更新 Redis。
    *   删除策略: 无显式删除，依赖 TTL 或更新覆盖。

## 2. 认证与权限 (Auth & Permissions)

### 2.1 API Key 认证信息的缓存
*   **业务场景**: 缓存 API Key 对应的用户信息，用于快速校验接口请求的合法性。
*   **Key 模式**: `auth:api_key:{hashed_key}`
*   **数据结构**: `Hash` (User Info Map)
*   **过期时间 (TTL)**: 1 小时 (3600s)
*   **代码位置**: `app/services/auth_service.py`
*   **说明**: 
    *   缓存内容包含 user_id, user_name, role, status 等核心校验信息。
    *   用户重置 API Key 或退出登录时会主动清理缓存。

### 2.2 用户权限集缓存
*   **业务场景**: 缓存用户的所有资源权限（Agent, Dataset, API, Metadata），用于鉴权。
*   **Key 模式**: `sys:auth:permissions:v3:user:{user_id}`
*   **数据结构**: `String` (JSON Serialized `UserPermissionsResponse`)
*   **过期时间 (TTL)**: 1 小时 (3600s)
*   **代码位置**: `app/services/permission_service.py`
*   **说明**: 
    *   由于权限计算（聚合用户角色、业务角色、直接权限）代价较高，因此进行整存整取。
    *   更新用户权限或角色时，会自动失效该缓存。

## 3. AI 智能体模块 (AI Agent)

### 3.1 对话历史 (Memory)
*   **业务场景**: 存储 AI 与用户的对话上下文历史，用于 LLM 多轮对话。
*   **Key 模式**: `conversation:{user_id}:{conversation_id}:history`
*   **数据结构**: `List` (JSON Strings)
*   **过期时间 (TTL)**: 7 天 (604800s)
*   **代码位置**: `app/services/ai/memory_service.py`
*   **说明**: 
    *   使用 Redis List 结构，推入新消息时自动刷新过期时间。
    *   通常会维持一个最大窗口（如 50-100 轮），通过 `LTRIM` 自动截断旧消息。
    *   **安全变更**: 引入 `{user_id}` 字段以隔离不同用户的对话上下文，防止越权访问。

### 3.2 数据集菜单缓存 (Dataset Menu)
*   **业务场景**: 缓存所有可用数据集及其 Schema 的文本描述，作为 Context 提供给 Agent，帮助其选择数据集。
*   **Key 模式**: `agent:dataset_menu:{user_id_or_admin_or_anon}`
*   **数据结构**: `String` (Plain Text)
*   **过期时间 (TTL)**: 10 分钟 (600s)
*   **代码位置**: `app/services/ai/config.py`
*   **说明**: 
    *   构建该菜单需要关联查询多张表（Dataset + Tables），成本较高。
    *   数据集变更时会主动调用刷新方法更新缓存。
    *   已按用户/Admin角色隔离缓存，防止越权数据泄露。

---

## 总结概览

| 模块 | Key Prefix | TTL | 数据类型 | 用途 |
| :--- | :--- | :--- | :--- | :--- |
| **System** | `sys_config:*` | 5 mins | String | 系统配置参数 |
| **Auth** | `auth:api_key:*` | 1 hour | Hash | API Key 校验缓存 |
| **Auth** | `sys:auth:permissions:v3:user:*` | 1 hour | String (JSON) | 完整权限集 |
| **AI** | `conversation:*:history` | 7 days | List | 对话上下文 |
| **AI** | `agent:dataset_menu:*` | 10 mins | String | 数据集 Schema 描述 |

