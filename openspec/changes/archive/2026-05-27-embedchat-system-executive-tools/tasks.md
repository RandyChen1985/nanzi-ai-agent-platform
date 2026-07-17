## 1. 基础环境与第三方依赖配置

- [x] 1.1 在 `requirements.txt` 中添加 `redis`、`playwright`、`psutil` 三方依赖
- [x] 1.2 运行依赖安装，并执行 `playwright install --with-deps` 初始化无头浏览器内核

## 2. 系统自治静态工具实现

- [x] 2.1 实现 `read_local_file` 工具，支持分页、tail 尾部读取与严格的沙箱路径穿越校验
- [x] 2.2 实现 `write_local_file` 工具，支持自动创建缺失的父级目录和路径穿越校验
- [x] 2.3 实现 `execute_system_command` 工具，配置 `subprocess` 默认 30 秒超时拦截以及输出日志大小裁剪
- [x] 2.4 实现 `manage_system_process` 工具，通过 `psutil` 列出进程，并对核心 Web 服务器及守护进程 PID 进行防误杀过滤防护

## 3. 高阶辅助工具实现

- [x] 3.1 实现 `sqlite_scratchpad` 临时 SQLite 会话数据库自动创建、数据导入及 SQL 多维计算沙箱
- [x] 3.2 实现 `directory_tree_navigator` 工具，递归检索目标目录下文件结构并支持按后缀、关键字过滤
- [x] 3.3 实现 `web_renderer_and_snapshot` 工具，结合 Playwright 实现无头网页加载、截图保存及 HTML 转干净 Markdown 纯文本输出
- [x] 3.4 实现 `code_syntax_linter` 工具，通过 Python `ast` 及编译模块对输入的源码进行静态 Lint 检测

## 4. 长期记忆 (LTM) 引擎与无感注入管道

- [x] 4.1 实现 Redis `Hash` 读写服务，支持 `nanzi:agent:ltm:{user_id}` 长期事实与个性偏好存储
- [x] 4.2 编写 `update_user_preference` 与 `fetch_user_long_term_memory` 工具，允许智能体显式管理长期记忆
- [x] 4.3 在智能体 LLM 交互的 Message 组装入口构建无感加载管道，并发读取 Redis LTM 并格式化注入 System Prompt 的 `[Memory Profile]` 部分

## 5. 前端工具能力集 UI 重构

- [x] 5.1 在 `AgentManagement.vue` 中编写 `groupedTools` 智能分类的 `computed` 属性
- [x] 5.2 将原来的单列平铺列表重构为精美的双列卡片（Grid）布局，并应用平滑的 Hover 缩放与底色微动画
- [x] 5.3 渲染带有气泡图标的胶囊型小标题区隔，并与动态工具、系统工具做完整联调

## 6. 测试与验证

- [x] 6.1 编写八大容器自治工具的安全边界测试用例（包括路径越界拦截、进程防误杀拦截、命令执行超时阻断等）
- [x] 6.2 编写 Redis 长期记忆哈希读写及无感 Prompt 注入管道的集成测试用例，提供高并发 Redis 抖动时的兜底逻辑
- [x] 6.3 严格按照开发规范，将本次新增的测试用例更新至 `tests/CHECKLIST.md` 中

