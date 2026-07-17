## Why

当前智能体平台（EmbedChat）主要依赖预定义或外部接口型工具，缺乏在 Docker 容器边界内直接与系统交互、读写文件系统、执行系统命令、管理系统进程和利用轻量数据沙箱进行自主计算的系统级执行能力（容器自治）。同时，现有的对话记忆机制主要局限于会话级短期上下文，缺乏一个支持长期性、结构化存储的用户偏好与事实记忆系统（LTM）。

为了让智能体平台能够像高级系统工程师和数据分析师一样工作，我们需要扩展 4 个系统自治核心工具、4 个高阶辅助工具、2 个长短期记忆管理工具，并构建基于 Redis 结构化存储的长短期记忆无感注入管道。这在 Docker 容器天然物理隔离的加持下，不仅能够大幅提高智能体的主动解决问题、执行代码与系统自愈能力，还能为用户带来极高拟人化的长期个性化体验。

## What Changes

1. **系统自治静态工具 (System Executive Tools)**:
   - 新增 `read_local_file`（本地文件分页/Tail 读取，防止 Token 爆仓）。
   - 新增 `write_local_file`（本地文件写入与覆盖）。
   - 新增 `execute_system_command`（系统命令执行，带超时与最大输出大小保护）。
   - 新增 `manage_system_process`（系统进程查看与安全管理，防误杀核心主进程）。
2. **高阶进阶工具 (Advanced Auxiliary Tools)**:
   - 新增 `sqlite_scratchpad`（会话级轻量 SQLite 数据临时沙箱，用于执行数据多维联查，对主库无污染）。
   - 新增 `directory_tree_navigator`（目录树深度检索导航）。
   - 新增 `web_renderer_and_snapshot`（基于 Playwright 的网页渲染与视觉截图工具，配合 Vision LLM 进行识图分析）。
   - 新增 `code_syntax_linter`（代码静态语法与 lint 检查，在写入前确保代码合规性）。
3. **长期记忆 (Long-Term Memory, LTM) 机制**:
   - 新增基于 Redis 哈希（Hash）数据结构的长期记忆设计，Key 规范为 `nanzi:agent:ltm:{user_id}`，包含用户个性偏好、核心事实等字段。
   - 新增两个记忆工具：`update_user_preference`（智能更新用户偏好）及 `fetch_user_long_term_memory`（主动拉取长期记忆）。
   - 实现 **无感记忆加载管道**：在向大模型发送请求时，自动并发从 Redis 异步读取长期记忆，无感拼接至 System Prompt 头部，保证智能体在后续对话中具备稳定且准确的个性化认知。
4. **前端工具能力集 UI 重构 (Tools Capability UI Refactoring)**:
   - 将原有的工具平铺列表重构为卡片式双列网格（Grid）布局，并配以微动画与精致的选中状态，提升界面交互质感。
   - 实现前端工具名称的智能正规分类：自动将系统工具与动态工具划分为 “ChatBI 数据分析”、“知识库检索 (RAG)”、“容器系统自治工具”、“办公协作与消息通知”、“长期事实与记忆引擎” 等分组展现，提升页面结构化信息检索效率。


## Capabilities

### New Capabilities
- `embedchat-system-executive-tools`: 提供容器内高度自治的 8 大系统与进阶执行工具，以及基于 Redis 的长短期记忆读写与 Prompt 无感注入引擎。

### Modified Capabilities
<!-- 暂无已有 Spec 级变更，保留为空 -->

## Impact

1. **服务架构 (Service Architecture)**:
   - 修改智能体工具注册与加载逻辑，使 10 个核心工具能无缝挂载。
   - 在 LLM 对话管道中引入异步 LTM 记忆拉取与 Prompt 拼装流程。
2. **三方依赖 (Dependencies)**:
   - 增加对 Redis 的哈希读写依赖。
   - 引入 Playwright 用于网页异步渲染与截图输出。
3. **性能与安全 (Performance & Security)**:
   - 工具内置超时机制（如 `execute_system_command` 默认强制限制在 30 秒内）。
   - 物理文件操作严格限制在容器内特定路径（如 `/app/data/uploads`、`/app/data/skills`）下。
