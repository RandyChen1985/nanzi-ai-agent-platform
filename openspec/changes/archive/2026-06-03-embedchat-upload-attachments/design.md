## Context

当前云枢智能体平台是一个基于 **FastAPI (Python)** 后端和 **Vue 3 (Vite + TS + Tailwind)** 前端构建的多智能体中台。EmbedChat 是供外部系统嵌入的核心对话窗。
本设计旨在为 EmbedChat 对话输入端快速、安全地引入多模态本地文件上传、托管展示以及会话历史回显，同时提供知识库和技能工作流的高阶占位设计。

## Goals / Non-Goals

**Goals:**
- **安全高效的后端上传与托管**：实现带安全阻断（20MB 限制、危险类型过滤）的文件上传端点，并通过静态资产托管支持极速文件访问及图片预览。
- **高颜值的极客前端交互**：使用纯 Tailwind CSS 手写毛玻璃质感下拉菜单、附件挂载滚动区与 Chip 实体徽标，支持点击、拖拽、粘贴粘贴板图片三种上传方式。
- **多模态直连智能交互**：支持在发送消息时识别并打包已挂载的图片，以多模态（Vision Mode）消息格式传输给后端大模型，使智能体即刻获得看图说话与多模态分析能力。
- **容器持久化目录规划**：合理规划宿主机与容器内的目录结构关系，统一在本地/容器 `/app/data/uploads` 进行文件存储，在 `/app/data/skills` 进行技能工作空间存储，确保容器重启不丢失数据。
- **高阶模块占位**：在交互中优雅地为“知识库”、“技能工作流”组件进行高对比度的未就绪占位，保证架构兼容性。
- **会话持久化集成**：使上传或挂载的附件元数据随消息一同打包，在 Redis 中持久化并在历史对话回显时成功复原。

**Non-Goals:**
- 本期**不**打通真实的知识库检索绑定与工作流代码执行逻辑（仅做占位展示）。
- **不**提供复杂的文件管理控制台或上传资产的分组分级删除功能。

## Decisions

### 1. 静态文件托管方案选型
- **技术决定**：使用 FastAPI 原生的 `StaticFiles` 挂载 `data/uploads` 目录至 `/static/uploads` HTTP 路径下。
- **备选方案**：开发专用的 `/download/{filename}` 鉴权流式下载端点。
- **选择理由**：使用 `StaticFiles` 的效率极高，完全由内核/异步 IO 处理，无需在每次加载图片附件时频繁访问数据库和进行复杂的 session 校验。对于 EmbedChat 这种高频、敏捷的交互式图表预览非常适配。

### 2. 前端组件手写 Tailwind 化设计
- **技术决定**：前端 `ChatInput.vue` 的 `+` 菜单以及预览栏全部采用纯 Tailwind CSS 手写与 SVG 组合实现，不引入任何重型 UI 库。
- **备选方案**：引入 `Ant Design Vue` 或 `Element Plus`。
- **选择理由**：云枢前端打包体积需保持绝对轻量。手写 Tailwind 能够做到 100% 精确契合已有页面的圆角、暗色模式及玻璃质感（如 `backdrop-blur-md px-3 bg-white/90`），且保证了完美的平台视觉一致性。

### 3. 多维附件元数据与持久化设计
- **技术决定**：
  - 在 Redis 消息存储结构（`MemoryService`）中，为消息字典对象新增 `files: List[Dict[str, Any]]` 字段，用于存放本次对话所挂载的所有附件。
  - 每个文件以 `FileInfo` 格式表达：`{ url: str, filename: str, size: int, ext: str }`。
- **选择理由**：该元数据设计极度精简，不仅天然兼容当前的文件上传，也为未来接入知识库（type: "kb"）和技能（type: "skill"）打下了标准的数据结构基石（未来它们仅需多传一个 type 属性即可复用同一套发送和回显渲染机制）。

### 4. 多模态图片直连 (Vision Mode) 策略
- **技术决定**：
  - 在大模型交互执行器（如 `data_executor` 或消息转换层）的 `_convert_history` 中，如果发现单条消息绑定了图片附件，则将 `HumanMessage` 或消息载荷包装为多模态内容列表：
    ```python
    HumanMessage(content=[
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": absolute_or_proxy_url}}
    ])
    ```
  - 将处理后的多模态载荷传给 LangChain 绑定的 Vision 模型，使用户本轮能直接与上传的图像进行分析互动。

### 5. 容器持久化目录规划与挂载
- **技术决定**：
  - 将本地开发的静态托管根目录及容器内部路径统一规划为 `/app/data/uploads`。
  - 将技能（Workflows/Skills）目录统一规划为 `/app/data/skills`。
  - 在 `docker/docker-compose.ai-agent.yml` 中挂载：
    ```yaml
    volumes:
      - ./data/uploads:/app/data/uploads
      - ./data/skills:/app/data/skills
    ```
  - 后端在启动或保存上传文件时，自动从系统配置或绝对路径获取 `/app/data/uploads`。

## Risks / Trade-offs

- **[Risk 1]：未鉴权的公开静态路径 `/static/uploads` 存在信息泄漏风险。**
  - *Mitigation*：在后端保存文件时，文件名采用 `<nano_timestamp>_<uuid>_cleaned_filename` 的混淆形式存储。由于带高精度纳秒时间戳与高强度 UUID，URL 属于不可被暴力猜解的字符串，具备天然的事实安全隔离屏障。
- **[Risk 2]：上传恶意脚本（如 webshell）造成服务器被侵入。**
  - *Mitigation*：在接口层实施非黑即白的后缀拦截校验（拦截 `.exe`, `.bat`, `.sh`, `.cmd`, `.msi`, `.php` 等），并在文件成功保存到硬盘前实施拦截并进行 `403 Forbidden` 响应。
