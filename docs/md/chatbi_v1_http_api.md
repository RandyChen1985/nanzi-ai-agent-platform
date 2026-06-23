# ChatBI 外部 HTTP API（`/api/v1/chatbi`）

本文描述 `app/api/v1/endpoints/chatbi.py` 暴露的接口（含 Schema、`sql/execute`、`sql/checkauth`）：**路径、认证、入参、出参以及示例**。

> 说明：示例 JSON 的字段结构以当前代码的实际响应包装为准（`StandardResponse`）。具体业务数据内容随环境（DB / 外部 SQL API / RAGFlow）变化。

---

## 认证方式

与全局 V1 依赖一致（见 `app/core/dependencies.py` 的 `require_api_key`）：

- **Header `X-API-Key`**（推荐）
- **Header `Authorization`**：`Bearer <token>` 或直接 `<token>`
- **Cookie `admin_token`**

服务端会解析当前调用者用户信息（`user_id`、`role`、部门等），无需在 Body/Query 里传用户名。

**例外**：`POST /api/v1/chatbi/sql/checkauth` **不要求**上述 API Key；在 JSON Body 中传 `username`（平台登录名，对应 `ai_agent_users.user_name`），服务端据此加载用户后再做 SQL 权限校验（见 `AuthService.resolve_user_by_username`）。该能力易被滥用探测权限，建议仅在内网或经网关鉴权后开放。

---

## 通用响应包装（成功）

成功时使用 `StandardResponse[T]`（`app/schemas/response.py`）：

- `code`: `200`
- `message`: `"success"`
- `data`: 业务数据（各接口不同）
- `timestamp`: ISO8601
- `trace_id`: 可选

---

## 1）获取 ChatBI 数据集 Schema

### 路径

`GET /api/v1/chatbi/schema`

### Query 入参

- `keywords` *(string, optional)*：当 `metadata_provider=ragflow` 时作为语义检索关键词；`local` 模式下忽略，返回当前用户可访问数据集的 YAML。

### 出参 `data`

- `content` *(string)*：与工具 `get_dataset_schema` 一致的 Schema 文本（local 为 YAML；ragflow 为目录或检索块）。

### curl 示例

```bash
curl -sS -G 'http://<host>:<port>/api/v1/chatbi/schema' \
  --data-urlencode 'keywords=订单 区域' \
  -H 'X-API-Key: <your-api-key>'
```

### 返回示例（结构示意）

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "content": "--- Dataset: xxx (xxx) ---\n..."
  },
  "timestamp": "2026-05-06T10:28:02.196803",
  "trace_id": null
}
```

---

## 2）执行 ChatBI 只读 SQL

### 路径

`POST /api/v1/chatbi/sql/execute`

### Body（JSON）

- `sql` *(string, required)*：仅允许**单条 SELECT**（服务端用 sqlglot 校验）
- `data_source` *(string, required)*：外部 SQL 执行服务的数据源标识（如 `default_clickhouse`、`mysql_oa`）
- `sessionid` *(string, required)*：OpenClaw 会话 ID；当格式为 `agent:<agent_name>:openai-user:<username>-<uuid>` 时，会先按 `<username>` 复用 `sql/checkauth` 等价链路做权限校验，通过后才执行 SQL；其它格式仅作为必填会话参数保留。

### 出参 `data`

对齐外部 SQL API 返回的 `data` 结构（常见为 `columns` / `items`）。外层通过 `ChatBiSqlExecuteData(extra="allow")` 兼容扩展字段。

### curl 示例

```bash
curl -sS -X POST 'http://<host>:<port>/api/v1/chatbi/sql/execute' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: <your-api-key>' \
  -d '{
    "sql": "SELECT 1",
    "data_source": "default_clickhouse",
    "sessionid": "openclaw-session-id"
  }'
```

### 返回示例（结构示意）

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "columns": ["a", "b"],
    "items": [{"a": 1, "b": 2}]
  },
  "timestamp": "2026-05-06T10:28:02.183808",
  "trace_id": null
}
```

### 常见错误（HTTP）

- `400`: `[Validation Failed] ...`
- `403`: `[Permission Denied] ...`（会指出无权限的表名）
- `404`: 数据集不存在
- `502`: `[TOOL_ERROR] ...`（外部 SQL API 报错）

---

## 3）校验 SQL 权限（不执行）

**不要求** `X-API-Key` / `Authorization` / `admin_token`。在 Body 中传 `username`（平台登录名），服务端从库表解析用户身份后，与「执行只读 SQL」走同一套校验链：数据集存在性、物理表权限、行级权限 SQL 重写、`sqlglot` 只读语法校验；**不会**调用外部 SQL 执行服务。

### 路径

`POST /api/v1/chatbi/sql/checkauth`

### Body（JSON）

- `username` *(string, required)*：平台用户登录名（`ai_agent_users.user_name`）
- `sql` *(string, required)*：仅允许**单条 SELECT**
- `data_source` *(string, required)*：数据源标识

### 出参 `data`

- `allowed` *(boolean)*：校验通过时为 `true`

### curl 示例

```bash
curl -sS -X POST 'http://<host>:<port>/api/v1/chatbi/sql/checkauth' \
  -H 'Content-Type: application/json' \
  -d '{
    "username": "zhangsan",
    "sql": "SELECT 1",
    "data_source": "default_clickhouse"
  }'
```

### 返回示例（结构示意）

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "allowed": true
  },
  "timestamp": "2026-05-06T10:28:02.196803",
  "trace_id": null
}
```

### 常见错误（HTTP）

- `404`：`用户不存在或已禁用`（`username` 未命中或账户非启用）
- 其余与「执行只读 SQL」一致：`400`（校验失败）、`403`（表或行级权限）、`404`（数据集不存在）；本接口**不会**返回 `502` 的外部 SQL API 执行错误（因不发起执行）。

---

## OpenClaw `AUTH_CONTEXT` 说明

OpenClaw 通路的 system prompt 中会生成 `<AUTH_CONTEXT>` JSON（见 `app/services/ai/openclaw_client.py` 的 `build_openclaw_auth_system_content`）。**不会**在 JSON 中下发 `apiKey`；服务端仍可在 `user_info` 中保留 Key 供其它内部链路使用，但不写入该标签。

# ChatBI 外部 HTTP API（`/api/v1/chatbi`）

本文描述 `app/api/v1/endpoints/chatbi.py` 暴露的接口（数据集 Schema、只读 SQL 执行、SQL 权限校验）：**路径、认证、入参、成功响应结构及示例**。示例 JSON 使用 FastAPI `TestClient` 对真实路由做请求得到的 **序列化结果**（底层 `execute_sql_query_core` / `fetch_dataset_schema_core` 在抓示例时用 patch 固定返回值；**字段结构与线上响应一致**，具体业务数值以实际环境与外部 SQL API 为准）。

> **与 Agent 查数路径的差异**：HTTP/门户直连 `execute_sql_query_core` 时**不会**注入 Agent 侧的 `SqlQueryBinding` ContextVar；表归属、权限与字段校验仍走 Core 内 MetaTable / Schema 回退逻辑。Agent ReAct 路径会先经 `get_dataset_schema` 构建 binding 再预检与执行（见 `app/services/ai/runners/chatbi/README.md`）。

---

## 认证说明

与全局 V1 依赖一致（见 `app/core/dependencies.py`）：

| 方式 | 说明 |
|------|------|
| `X-API-Key` | 请求头携带 API Key（常用） |
| `Authorization` | `Bearer <token>` 或直接 `<token>` |
| Cookie | `admin_token` |

用户信息由 Key 解析得到，**无需**在 Body / Query 里传用户名。

**例外**：`POST /api/v1/chatbi/sql/checkauth` 不经上述认证与 `verify_v1_api_access`，Body 必填 `username` 用于解析用户（见 `AuthService.resolve_user_by_username`）；建议仅在内网或由上游网关完成调用方鉴权后使用。

当前 `verify_v1_api_access` 对路径中包含 `"/chat"` 的路由会放行（其余需 API Key 的 `/api/v1/chatbi/...` 仍命中该规则）；`sql/checkauth` 已单独挂载在无全局 Key 依赖的路由树上。

---

## 通用响应包装（成功）

成功时使用 `StandardResponse[T]`（`app/schemas/response.py`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | `int` | 业务码，成功为 `200` |
| `message` | `string` | 一般为 `"success"` |
| `data` | `object \| null` | 业务体，见各接口 |
| `timestamp` | `string` (ISO8601) | 响应时间 |
| `trace_id` | `string \| null` | 追踪 ID（当前多为 `null`） |

---

## 1. 获取数据集 Schema

### 路径与方法

`GET /api/v1/chatbi/schema`

### Query 入参

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `keywords` | `string` | 否 | `metadata_provider=ragflow` 时作为语义检索关键词；`local` 模式下仍会忽略关键词，返回当前用户**有权访问**的全部数据集 YAML |

### 成功响应 `data`

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | `string` | 与工具 `get_dataset_schema` 一致的文本：`local` 为多段 `--- Dataset: ... ---` + YAML；`ragflow` 时为目录或带置信度的检索块 |

### `curl` 示例

```bash
curl -sS -G 'http://<host>:<port>/api/v1/chatbi/schema' \
  --data-urlencode 'keywords=订单 区域' \
  -H 'X-API-Key: <your-api-key>'
```

### 返回示例（真实序列化；`content` 内为演示 YAML）

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "content": "--- Dataset: 销售明细 (sales_detail) ---\ndataset: sales_detail\ndata_source: default_clickhouse\ndescription: 订单与销售指标示例\ntables:\n  - physical_name: dim_region\n    term: 区域维度\n    columns:\n      - physical_name: region_code\n        term: 区域编码\n      - physical_name: region_name\n        term: 区域名称\nmetrics:\n  - name: daily_orders\n    calculation: SELECT count(*) FROM fact_orders WHERE dt = today()\n"
  },
  "timestamp": "2026-05-06T10:28:02.196803",
  "trace_id": null
}
```

`content` 展开后的可读形态示例：

```yaml
--- Dataset: 销售明细 (sales_detail) ---
dataset: sales_detail
data_source: default_clickhouse
description: 订单与销售指标示例
tables:
  - physical_name: dim_region
    term: 区域维度
    columns:
      - physical_name: region_code
        term: 区域编码
      - physical_name: region_name
        term: 区域名称
metrics:
  - name: daily_orders
    calculation: SELECT count(*) FROM fact_orders WHERE dt = today()
```

### 典型错误 HTTP 状态

| 条件 | HTTP | `detail` 形态 |
|------|------|----------------|
| 无任何授权数据集 | `403` | `No authorized datasets found...` |
| 服务端异常包装 | `502` | `[Tool Error] Failed to retrieve metadata: ...` |
| RAG 侧错误 | `502` | `[RAG Error] ...` / `[RAG Connection Error] ...` |

---

## 2. 执行只读 SQL

### 路径与方法

`POST /api/v1/chatbi/sql/execute`

### Body JSON（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `sql` | `string` | 是 | 仅允许 **单条 `SELECT`**（经 sqlglot 校验） |
| `data_source` | `string` | 是 | 外部 SQL 执行服务使用的数据源标识，如 `default_clickhouse`、`mysql_oa` |
| `sessionid` | `string` | 是 | OpenClaw 会话 ID；当格式为 `agent:<agent_name>:openai-user:<username>-<uuid>` 时，会先按 `<username>` 复用 `sql/checkauth` 等价链路做权限校验，通过后才执行 SQL；其它格式仅作为必填会话参数保留 |

### 成功响应 `data`

与外部 SQL API 返回的 `data` 字段对齐，外层任意额外字段由 `ChatBiSqlExecuteData`（`extra="allow"`）吞入；常见形态：

| 字段 | 类型 | 说明 |
|------|------|------|
| `columns` | `string[]` | 列名 |
| `items` | `object[]` | 行对象数组 |

若底层返回 JSON **数组**（非对象），接口会包一层 `{ "rows": [...] }` 再放入 `data`。

### `curl` 示例

```bash
curl -sS -X POST 'http://<host>:<port>/api/v1/chatbi/sql/execute' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: <your-api-key>' \
  -d '{
    "sql": "SELECT region_name AS region, count(*) AS order_cnt FROM fact_orders GROUP BY region_name LIMIT 10",
    "data_source": "default_clickhouse",
    "sessionid": "openclaw-session-id"
  }'
```

### 返回示例（真实序列化；行列值为演示数据）

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "columns": [
      "region",
      "order_cnt"
    ],
    "items": [
      {
        "region": "华东",
        "order_cnt": 12840
      }
    ]
  },
  "timestamp": "2026-05-06T10:28:02.183808",
  "trace_id": null
}
```

### 典型错误 HTTP 状态

| 条件 | HTTP | 说明 |
|------|------|------|
| 数据集不存在 | `404` | `Error: Dataset '...' not found...` |
| SQL 校验失败等 | `400` | `[Validation Failed] ...` |
| 表无权限 / 未注册 | `403` | `[Permission Denied] ...`（含具体表名） |
| 行级权限应用失败等 | `403` | `[Security Error] ...` |
| 外部 SQL API 失败 | `502` | `[TOOL_ERROR] ...` |
| 其它未归类错误串 | `500` | `detail` 为原始字符串 |

---

## 3. 校验只读 SQL 权限（不执行）

**不要求 API Key**。Body 在 **§2** 的 `sql`、`data_source`、`dataset_name` 基础上增加 **`username`**（`ai_agent_users.user_name`），服务端解析用户后复用 `execute_sql_query_core` 在调用外部 API **之前**的整段逻辑；**不**调用外部 SQL 执行服务。

### 路径与方法

`POST /api/v1/chatbi/sql/checkauth`

### Body JSON（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `username` | `string` | 是 | 平台登录名，用于加载用户维度与权限 |
| `sql` | `string` | 是 | 仅允许 **单条 `SELECT`** |
| `data_source` | `string` | 是 | 数据源标识 |

### 成功响应 `data`

| 字段 | 类型 | 说明 |
|------|------|------|
| `allowed` | `boolean` | 恒为 `true`（能返回 200 即表示校验通过） |

### `curl` 示例

```bash
curl -sS -X POST 'http://<host>:<port>/api/v1/chatbi/sql/checkauth' \
  -H 'Content-Type: application/json' \
  -d '{
    "username": "zhangsan",
    "sql": "SELECT region_name AS region, count(*) AS order_cnt FROM fact_orders GROUP BY region_name LIMIT 10",
    "data_source": "default_clickhouse"
  }'
```

### 返回示例（真实序列化形态示意）

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "allowed": true
  },
  "timestamp": "2026-05-06T10:28:02.183808",
  "trace_id": null
}
```

### 典型错误 HTTP 状态

| 条件 | HTTP | 说明 |
|------|------|------|
| 用户不存在或已禁用 | `404` | `用户不存在或已禁用` |

其余与 **§2** 相同，但**不会出现**因外部 SQL API 失败而产生的 `502`（本路由不触发执行）。

---

## 测试情况说明

| 范围 | 说明 |
|------|------|
| 底层逻辑 | `pytest tests/ai/tools/test_data_api.py`、`tests/services/test_sql_metadata_tables.py` 等已通过（含 `get_dataset_schema` / `execute_sql_query` 工具路径及 SQL 表名解析）。 |
| 本文件涉及的 ChatBI HTTP 路由 | **暂无**独立 `tests/api/test_chatbi_v1.py`；上文示例响应由 **`TestClient` 调用真实路由** + patch 核心服务生成，用于核对 **`StandardResponse` 序列化与路由接线**。 |

建议在具备数据库与外部 SQL API 的集成环境中补一轮手工或契约测试后再上线。

---

## 相关代码

- 路由：`app/api/v1/endpoints/chatbi.py`（`router`：需 Key；`public_router`：`sql/checkauth`）
- 用户解析：`app/services/auth_service.py`（`resolve_user_by_username`）
- Schema 核心：`app/services/chatbi_dataset_schema_service.py`
- SQL 核心：`app/services/sql_query_execution_service.py`
- V1 挂载：`app/api/v1/api.py`（`v1_secured` + `chatbi.public_router` 均带 `prefix="/chatbi"`）
