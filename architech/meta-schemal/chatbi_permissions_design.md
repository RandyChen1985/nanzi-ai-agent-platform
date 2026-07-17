# NanZi ChatBI 精细化数据权限架构指南 (RLS)

本文档详尽描述了南孜智能体平台中 ChatBI 的权限控制架构。该架构基于 **SQL 动态 AST 改写** 技术，实现了工业级的行级数据隔离 (Row-Level Security)。

## 一、 核心设计理念

传统的权限控制（如物理库表隔离）在 ChatBI 场景下显得力不从心。本系统采用 **“后期注入”** 模式：
1.  **解耦生成与安全**：大模型只负责理解业务意图生成 SQL，无需感知复杂的权限规则。
2.  **强制性约束**：安全拦截发生在执行器 (Executor) 的最底层，绕过大模型的幻觉或 Prompt 注入。
3.  **多方言透明**：通过 AST（抽象语法树）中转，确保同一套逻辑适配 ClickHouse, MySQL, Oracle。

---

## 二、 核心组件架构

### 1. 变量流转 (Variable Lifecycle)
*   **采集层**：在用户登录或 API 调用时，`AuthService` 从 `ai_agent_users` 表预加载 `dept_id`, `org_path`, `extra_data`。
*   **上下文层**：这些信息被封装进 `AgentContext` 的 `user_dimensions` 字典中，随 Request 生命周期流转。
*   **注入层**：`SQLRewriter` 通过正则提取占位符（如 `{user.dept_id}`），并根据上下文进行强类型替换。

### 2. SQL 重写引擎 (`SQLRewriter`)
这是系统的大脑，基于 `sqlglot` 库实现。其改写算法如下：
*   **解析阶段**：将 Raw SQL 转换为结构化的 AST 树。
*   **策略解析阶段**：
    *   **优先级 1 (User Override)**: 若 `user_overrides` 中存在当前 UID，直接取其规则，忽略所有角色。
    *   **优先级 2 (Role Merge)**: 获取用户所有角色的规则列表。若角色 A 要求 `region='SH'`，角色 B 要求 `dept=101`，则最终注入 `(region='SH' AND dept=101)`。
    *   **优先级 3 (Default)**: 若无上述匹配，注入 `default_policy`（生产环境通常设为 `1=0` 保证安全）。
*   **递归改写算法**：
    *   使用 `expression.transform()` 深度优先遍历所有节点。
    *   匹配 `exp.Select` 节点（包含顶级查询、子查询、WITH 语句的分支）。
    *   利用 `node.where(condition, append=True)` 在原有的 `WHERE` 子句上追加过滤片段。`sqlglot` 会自动处理 `AND` 运算符的嵌套。

---

## 三、 详细配置规范

### 1. 过滤片段示例
| 场景 | 配置示例 (condition) | 注入效果 |
| :--- | :--- | :--- |
| **等值过滤** | `dept_id = {user.dept_id}` | `WHERE ... AND dept_id = 500` |
| **层级匹配** | `org_path LIKE '{user.org_path}%'` | `WHERE ... AND org_path LIKE 'yovole/sh/%'` |
| **硬编码限制**| `status = 1` | `WHERE ... AND status = 1` |
| **管理员例外**| `1=1` | 无实际过滤动作 |

### 2. 多表 Join 与别名处理
引擎具备 **“别名感知 (Alias Awareness)”** 能力。
当用户查询 `SELECT * FROM assets a JOIN rooms r ON a.rid = r.id` 且配置了针对 `assets` 的 `dept_id` 过滤时，引擎会自动识别 `a` 为 `assets` 的代理，并注入 `a.dept_id = {user.dept_id}`。

---

## 四、 安全性保障 (Security Hardening)

### 1. SQL 注入防御
*   所有 `{user.xxx}` 变量在替换时，字符串类型会被强制包裹单引号 `'` 并转义。
*   非字符串类型（数字、NULL）按照字面量处理。
*   改写后的 SQL 必须再次经过 `validate_sql` 检查方言正确性。

### 2. 空值 (NULL) 处理
若策略引用了 `{user.dept_id}` 但该用户字段为空：
*   系统默认将其替换为 `NULL`。
*   在 SQL 中 `dept_id = NULL` 永远为假，从而实现 **“默认拒绝”** 逻辑，防止数据泄露。

### 3. 复杂 SQL 逃逸拦截
由于采用了递归改写，任何试图通过 `UNION`, `CTE`, 或多层嵌套子查询来绕过权限的手段都会失败。每一层 `SELECT` 都会被强行加上当前的权限约束。

---

## 五、 性能优化考虑

*   **缓存机制**：`SQLRewriter` 仅处理 AST 逻辑，不涉及 IO 操作。解析耗时通常在 5-20ms 级别。
*   **元数据缓存**：用户的权限维度在 `AgentContext` 初始化时一次性加载，避免在 SQL 改写过程中重复查询数据库。
*   **索引建议**：管理员应确保在权限策略中引用的列（如 `org_path`, `dept_id`）在底层数据库（ClickHouse/Oracle）中已建立索引或排序键。

---

## 六、 运维与调试 (Observability)

*   **Trace 日志**：前端 “思考过程” 中显示的 `🔒 数据权限校验` 节点包含命中的过滤规则条数。
*   **Debug Mode**：在 `AgentDebug` 页面中，管理员可以开启干跑模式（Dry Run），查看重写前后的 SQL 对比。
*   **审计审计**：`execution_traces` 表中永久存储 `rewrite_details`，用于安全合规审计。
