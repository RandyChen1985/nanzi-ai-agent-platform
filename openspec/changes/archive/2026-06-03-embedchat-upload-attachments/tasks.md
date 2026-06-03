## 1. 后端上传与静态托管基础设施搭建

- [x] 1.1 在 `app/main.py` 中，使用 `StaticFiles` 挂载 `data/uploads` 目录至 `/static/uploads` 路由，并自动创建目录。
- [x] 1.2 在 `app/api/v1/endpoints/chat.py` 中增加文件上传接口 `POST /upload`。
- [x] 1.3 在 `/upload` 上传接口中，编写 20MB 大小拦截、可执行可疑后缀安全阻断，以及文件名防冲突（精细时间戳 + UUID）清洗逻辑。
- [x] 1.4 在 `app/api/v1/endpoints/chat.py` 中扩展 `ChatCompletionRequest` 的 Pydantic 定义，支持传入 `files`（包含 `url`、`filename`、`size`、`ext`）可选附件载荷。
- [x] 1.5 在 `app/services/ai/memory_service.py` 内存服务中更新 Redis 历史会话的序列化与反序列化，确保消息的 `files` 字段被归档保存。
- [x] 1.6 在 `app/api/v1/endpoints/chat.py` 的会话历史 `/conversation/{conversation_id}` 中，将已保存的 `files` 字段完整回显。
- [x] 1.7 在 `app/services/ai/executors/data_executor.py`（以及 `app/services/ai/agent_service.py`）中，编写对包含图片的 HumanMessage 转化为 LangChain 多模态多元素列表（`[{"type":"text","text":"..."},{"type":"image_url","image_url":...}]`）的 Vision Mode 转换处理。
- [x] 1.8 修改 `docker/docker-compose.ai-agent.yml`，在 `ai-agent` 容器服务下增设 `/app/data/uploads` 和 `/app/data/skills` 的持久化数据卷挂载。

## 2. 前端 ChatInput 输入面板重构 (Plus 菜单与上传)
 
- [x] 2.1 修改 `frontend/src/components/embed/ChatInput.vue`，加入极客 `+` 按钮及隐藏的 `<input type="file" multiple />` 节点。
- [x] 2.2 在 `ChatInput.vue` 中纯手写 Tailwind CSS 毛玻璃质地下拉菜单弹出层，完成“上传本地文件”、“知识库 (筹备中)”、“技能工作流 (筹备中)”的精细布局。
- [x] 2.3 在 `ChatInput.vue` 输入框上方新增附件预览水平滚动条，支持图片的缩略图展现、文档的文件卡片展现，及右上角 `X` 移除按钮的点击绑定。
- [x] 2.4 在输入框 `textarea` 绑定 `onPaste` 与整个包裹层的 `onDrop` 事件，实现截图粘贴及文件拖拽自动上传挂载的多通道支持。
- [x] 2.5 扩展 `ChatInput.vue` 的 `@send` 事件，当用户点击发送或按下回车时，同时传出已上传的附件 `files` 列表。
 
## 3. 联调、测试与验证
 
- [x] 3.1 在 `frontend/src/views/EmbedChat.vue` 中修改 `sendMessage` 方法，支持将挂载的附件元数据塞入请求 body 并通过 completions 发给后端。
- [x] 3.2 运行 `./dev.sh`，在浏览器中测试点击 `+` 菜单上传合法大文件，验证上传流程及 `/static/uploads` 预览的通畅性。
- [x] 3.3 验证拖拽文件上传和剪贴板粘贴贴图的捕获、上传和回显。
- [x] 3.4 验证 Redis 对话历史的读写，重新打开会话或刷新页面时，历史对话气泡中的图片和文件卡片是否能成功渲染恢复。
- [x] 3.5 自动更新 `tests/CHECKLIST.md` 验收清单，新增有关“EmbedChat 附件上传与多模态直连 (Vision Mode)”的自动化测试验收项。

