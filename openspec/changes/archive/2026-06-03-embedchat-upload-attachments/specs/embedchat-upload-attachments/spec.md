## ADDED Requirements

### Requirement: 本地文件上传接口与安全校验
系统 **SHALL** 提供 `POST /api/v1/chat/upload` 接口以供用户上传会话附件，并对上传的大小及安全性进行严格防护。

#### Scenario: 上传合法的常规文件
- **WHEN** 用户通过 `POST /api/v1/chat/upload` 发送一个 2MB 的图片 `chart.png`
- **THEN** 系统将文件安全保存至本地目录，去除原始文件名中的特殊字符，重命名为唯一的文件名，并返回 200 响应，包含 `url`, `filename`, `size`, `ext` 的结构化元数据

#### Scenario: 拦截危险脚本和可执行后缀
- **WHEN** 用户尝试上传 `malicious.exe` 或 `exploit.sh`
- **THEN** 系统检测到黑名单危险后缀，立即阻断，并抛出 403 Forbidden 错误

#### Scenario: 拦截超出大小限制的文件
- **WHEN** 用户尝试上传一个大于 20MB 的文件
- **THEN** 系统阻断上传，抛出 400 (或 413) 状态码及清晰的错误提醒

### Requirement: 静态资产公开托管
系统 **SHALL** 将物理存储目录挂载在 Web 服务路由下，以便前端能够随时通过 URL 进行图片预览与文件下载。

#### Scenario: 静态文件托管访问
- **WHEN** 前端向静态文件路径 `/static/uploads/1716800000_chart.png` 发起 GET 请求
- **THEN** 系统以二进制流形式正常输出文件，且不加重内部接口鉴权负担，方便嵌入式页面即时预览

### Requirement: 前端 Plus 菜单与多通道挂载交互
前端 **SHALL** 在输入框的输入条左侧新增极客 `+` 按钮，提供下拉菜单功能和多通道上传挂载能力。

#### Scenario: 点击加号菜单展开分流
- **WHEN** 用户点击输入条左侧的 `+` 按钮
- **THEN** 界面弹起具有毛玻璃特效的下拉菜单，呈现“上传本地文件 (Active)”、“选择知识库 (Disabled/筹备中)”、“技能工作流 (Disabled/筹备中)”

#### Scenario: 挂载已上传的附件进行预览
- **WHEN** 用户选择本地文件上传并成功后
- **THEN** 输入框上方渲染附件挂载预览栏，对于图片显示精致的缩略图，非图片显示规范的文件卡片，并在悬浮时呈现 `X` 删除图标以供一键移除

#### Scenario: 粘贴和拖拽附件智能挂载
- **WHEN** 用户直接往输入框拖拽本地文件释放，或者将系统剪贴板中的截图粘贴进输入区
- **THEN** 前端智能捕获对应事件，自动上传至后端，并顺畅地将生成的文件实体挂载在附件预览区

### Requirement: 发送载荷扩容与会话内存回显
系统 **SHALL** 支持在发送消息时携带附件，并在 Redis 会话历史中保存和回显这些信息，使会话具备持久化的附件上下文记忆。

#### Scenario: 携带附件发送消息
- **WHEN** 用户在挂载 2 个附件后输入“分析刚才的数据”并点击发送
- **THEN** 前端将 userInput 文本和包含文件 url/size/filename 等的 files 数组打包送入 `/completions`，后端持久化该轮的 files，且不影响 SSE 消息流的正常生成

#### Scenario: 会话历史回显附件
- **WHEN** 刷新页面或重新打开对话，前端 GET 请求 `/conversation/{conversation_id}` 获取历史记录
- **THEN** 服务端返回的消息明细中完整包含已持久化的 files 附件数组，前端依据此将图片或文件卡片在对话流历史气泡中精准渲染出来

### Requirement: 本地与容器持久化目录映射
系统 **SHALL** 统一规划在宿主机及容器内的存储目录架构，并在 `docker-compose.ai-agent.yml` 中挂载它们以避免重启丢失数据。

#### Scenario: 容器持久卷映射成功建立
- **WHEN** 容器使用 `docker-compose.ai-agent.yml` 启动时
- **THEN** 宿主机的 `./data/uploads` 自动映射为容器内的 `/app/data/uploads`，宿主机的 `./data/skills` 自动映射为 `/app/data/skills`，所有文件在物理介质上进行持久化保存

### Requirement: 大模型多模态图片直连交互 (Vision Mode)
核心智能体执行引擎 **SHALL** 能自动识别消息链中的图片附件，并将其转化为符合大模型规范的多模态格式（含文本与图片 url），供支持 Vision 能力的模型读取和交互。

#### Scenario: 发送包含图片的多模态消息
- **WHEN** 用户挂载了一张图片并发送“解释一下这幅图画”
- **THEN** 执行器将带有图像的 HumanMessage 改装为 `[{"type": "text", "text": "解释一下这幅图画"}, {"type": "image_url", "image_url": {"url": "..."}}]`，使 Vision 大模型能直接识别图像并返回正确的解读结论

