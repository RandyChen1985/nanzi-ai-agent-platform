# 开放平台 API 文档 (v1)

本文档描述了 `/v1` 路径下的对外开放接口信息。

## 1. 鉴权说明
所有接口均需要通过 HTTP Header 进行鉴权。

| Header 字段 | 说明 | 示例 |
| :--- | :--- | :--- |
| `X-API-Key` | **推荐**。直接传递 API Key。 | `sk-r8s7...` |
| `Authorization` | 兼容项。Bearer Token 格式。 | `Bearer sk-r8s7...` |

---

## 2. 智能体对话 (Chat)

### 2.1 获取欢迎语
获取系统动态生成的欢迎语。

*   **URL**: `/v1/chat/greeting`
*   **Method**: `GET`

#### 响应 (Response)
**Content-Type**: `application/json`

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `code` | int | 状态码 (200=成功) |
| `message` | string | 响应消息 ("success") |
| `data` | object | 业务数据 |
| `data.greeting` | string | 欢迎语内容 |

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "greeting": "您好！我是您的智能助手..."
  }
}
```

### 2.2 获取对话历史 (Redis)
从服务端内存（Redis）中获取指定会话的历史记录。

*   **URL**: `/v1/chat/conversation/{conversation_id}`
*   **Method**: `GET`

#### 参数 (Parameters)

| 参数名 | 位置 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| `conversation_id` | Path | string | 是 | 会话 ID |
| `limit` | Query | int | 否 | 返回的消息数量限制，默认 50 |

#### 响应 (Response)

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `data.conversation_id` | string | 会话 ID |
| `data.messages` | list | 消息列表 |
| `data.messages[].role` | string | 角色 (user/assistant/system) |
| `data.messages[].content` | string | 消息内容 |

```json
{
  "code": 200,
  "data": {
    "conversation_id": "uuid...",
    "messages": [
      { "role": "user", "content": "hello" }
    ]
  }
}
```

### 2.3 发起对话 (Stream/Non-Stream)
统一的对话接口，支持流式和非流式响应。系统会自动根据历史记录进行智能体路由，或者通过 `agent_id` 指定智能体。

*   **URL**: `/v1/chat/completions`
*   **Method**: `POST`

#### 请求体 (Body)

| 参数名 | 类型 | 必填 | 说明 | 示例 |
| :--- | :--- | :--- | :--- | :--- |
| `messages` | List[Object] | **是** | 消息列表 | `[{"role": "user", "content": "查销量"}]` |
| `stream` | bool | 否 | 是否流式返回 (SSE) | `false` |
| `model` | string | 否 | 指定模型名称 | `gpt-4` |
| `agent_id` | string | 否 | 指定智能体 ID | `agent_sales` |
| `conversation_id` | string | 否 | 服务端会话 ID | `uuid...` |
| `debug_options` | object | 否 | 调试选项 | `{"trace": true}` |

#### 响应 (非流式 Non-Stream)

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `data.content` | string | 对话回复内容 |
| `data.intent` | string | 识别的意图 |
| `data.confidence` | float | 置信度 |
| `data.reasoning` | string | 推理过程 |

```json
{
  "code": 200,
  "data": {
    "content": "销量为 100",
    "intent": "query_sales",
    "confidence": 0.95,
    "reasoning": "Standard Response"
  }
}
```

#### 响应 (流式 Stream)
**Content-Type**: `text/event-stream`

返回 SSE (Server-Sent Events) 数据流。每行以 `data: ` 开头，内容为 JSON。

```text
data: {"content": "正在", "intent": "processing"}
data: {"content": "查询", "intent": "processing"}
data: [DONE]
```

### 2.4 查询历史记录 (SQL)
支持分页、筛选查询持久化的对话历史。

*   **URL**: `/v1/chat/history`
*   **Method**: `GET`

#### 参数 (Parameters)

| 参数名 | 位置 | 类型 | 说明 |
| :--- | :--- | :--- | :--- |
| `page` | Query | int | 页码，默认 1 |
| `page_size` | Query | int | 每页数量，默认 20 |
| `agent_id` | Query | string | 按智能体 ID 筛选 |
| `keyword` | Query | string | 关键词搜索 |
| `status` | Query | string | 状态 (success/error) |
| `start_date` | Query | string | 开始时间 (ISO 8601) |

#### 响应 (Response)

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `data.total` | int | 总记录数 |
| `data.items` | list | 历史记录列表 |
| `data.items[].trace_id` | string | 追踪 ID |
| `data.items[].query` | string | 用户提问 |
| `data.items[].summary` | string | 回复摘要 |

### 2.5 获取执行链路日志
获取单次对话的详细内部执行步骤（Trace）。

*   **URL**: `/v1/chat/logs/{trace_id}`
*   **Method**: `GET`

#### 参数 (Parameters)

| 参数名 | 位置 | 类型 | 说明 |
| :--- | :--- | :--- | :--- |
| `trace_id` | Path | string | 追踪 ID (从 History API 获取) |

#### 响应 (Response)

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `data.total_steps` | int | 总步数 |
| `data.steps` | list | 步骤列表 |
| `data.steps[].event_type` | string | 事件类型 (thought/tool_call) |
| `data.steps[].tool_name` | string | 工具名称 |
| `data.steps[].tool_input` | object | 工具入参 |
| `data.steps[].tool_output` | object | 工具出参 |

### 2.6 获取模型调用指标 (Model Calls)
获取会话中的大模型调用指标（如 Token 消耗、耗时、时间戳等）。

*   **URL**: `/v1/chat/conversation/{conversation_id}/model_calls`
*   **Method**: `GET`

#### 参数 (Parameters)

| 参数名 | 位置 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| `conversation_id` | Path | string | 是 | 会话 ID |
| `trace_id` | Query | string | 否 | 过滤指定的 trace_id |

### 2.7 删除历史记录
单条或批量删除持久化的对话历史。

*   **URL**: `/v1/chat/history/{trace_id}` (DELETE 单条)
*   **URL**: `/v1/chat/history/batch-delete` (POST 批量)

#### 请求体 (仅批量删除)
```json
{
  "trace_ids": ["uuid1", "uuid2"]
}
```

### 2.8 导出对话数据
获取单次对话的导出数据格式。

*   **URL**: `/v1/chat/export/data/{trace_id}`
*   **Method**: `GET`

### 2.9 上传附件
对话中的附件上传。

*   **URL**: `/v1/chat/upload`
*   **Method**: `POST`
*   **Body**: `multipart/form-data` 包含 `file`

---

## 3. 用户服务 (Users)

### 3.1 获取用户详情
查询当前用户或指定用户的详细信息（包括角色和权限）。

*   **URL**: `/v1/users/profile`
*   **Method**: `GET`

#### 参数 (Parameters)

| 参数名 | 位置 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| `username` | Query | string | 否 | 目标用户名。不传则返回当前 API Key 对应用户。 |

#### 响应 (Response)

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `data.id` | int | 用户 ID |
| `data.username` | string | 用户名 |
| `data.display_name` | string | 显示名称 |
| `data.role` | string | 角色 (admin/user) |
| `data.api_key` | string | **API Key (明文)** |
| `data.permissions` | list[string] | 权限列表 (如 `api:read`) |

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "id": 123,
    "username": "user",
    "display_name": "User Name",
    "role": "admin",
    "status": 1,
    "api_key": "sk-r8s7...",
    "roles": ["admin"],
    "permissions": ["api:read", "agent:write"]
  }
}
```

---

## 4. Schema 服务 (Schema)

### 4.1 检索元数据 Schema
统一的 Schema 检索接口。

*   **URL**: `/v1/schema`
*   **Method**: `POST`

#### 请求体 (Body)

| 参数名 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `query` | string | 否 | 检索关键词 (如 "销售数据") |

#### 响应 (Response)

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `data.schema_context` | string | YAML 格式的 Schema 定义 |
| `data.hits` | list | 命中的数据集列表 |
| `data.provider` | string | 数据提供方 (local/ragflow) |

---

## 5. 错误码与错误响应

所有接口在发生错误时，均会返回统一的 JSON 错误响应格式。

### 5.1 错误响应结构

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `code` | int | 业务错误码 (非 200) |
| `message` | string | 错误描述 |
| `trace_id` | string | 追踪 ID |
| `detail` | any | 调试详情 (生产环境通常为空) |

```json
{
  "code": 4011,
  "message": "API密钥缺失",
  "data": null,
  "trace_id": "a1b2c3..."
}
```

### 5.2 业务错误码表

| HTTP Status | 业务 Code | 枚举名称 | 说明 |
| :--- | :--- | :--- | :--- |
| **200** | 200 | `SUCCESS` | 请求成功 |
| **400** | 400 | `BAD_REQUEST` | 错误的请求 (通用) |
| **400** | 4001 | `INVALID_PARAMETER` | 参数校验失败 |
| **401** | 401 | `UNAUTHORIZED` | 未授权 |
| **401** | 4011 | `API_KEY_MISSING` | 请求头缺少 `X-API-Key` |
| **401** | 4012 | `API_KEY_INVALID` | API Key 无效 |
| **403** | 403 | `FORBIDDEN` | 禁止访问 |
| **403** | 4031 | `ACCESS_DENIED` | 访问被拒绝 (无权限) |
| **404** | 4004 | `RESOURCE_NOT_FOUND` | 资源未找到 |
| **429** | 429 | `TOO_MANY_REQUESTS` | 请求过于频繁 |
| **500** | 500 | `INTERNAL_SERVER_ERROR` | 服务器内部错误 |
