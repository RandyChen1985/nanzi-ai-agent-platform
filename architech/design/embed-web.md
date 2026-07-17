# 嵌入式 Web 组件设计 (Embedded Web Widget Design)

## 1. 背景与目标
为了降低第三方系统集成南孜智能体平台的成本，避免重复开发前端聊天界面，我们需要提供一种“低代码”的嵌入式解决方案。

**目标**:
- 提供一个**极简、纯净**的聊天页面，专门用于被 `<iframe>` 引用。
- 支持通过 URL 参数进行配置（Agent ID, Theme, Token）。
- 最终形态可封装为 JS SDK，实现“一行代码接入”。

---

## 2. 页面形态设计

我们需要在前端项目中新增一个独立的路由入口，区别于现有的管理后台界面。

### 2.1 路由定义
- **Path**: `/embed/chat`
- **特点**:
  - **无 Chrome**: 移除侧边栏 (Sidebar)、顶部导航 (Header)、调试面板 (Debug Panel)。
  - **全屏自适应**: 布局宽高设置为 `100vw` / `100vh`，完全填充宿主容器。
  - **沉浸式**: 仅保留核心对话流 (Message List) 和输入框 (Input Area)。

### 2.2 URL 参数配置 (Query Parameters)

| 参数名 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `agent_id` | 否 | `null` (自动路由) | 指定默认连接的智能体 ID。 |
| `token` | 是 | - | 用户鉴权 Token (Bearer Token)。 |
| `theme` | 否 | `light` | 主题模式：`light` / `dark` / `auto`。 |
| `show_logs` | 否 | `false` | 是否展开显示“思考过程”日志。普通用户建议关闭。 |
| `primary_color`| 否 | `#1677ff` | 主题色，用于匹配宿主系统的品牌色。 |
| `welcome` | 否 | `true` | 是否自动发送欢迎语。 |

---

## 3. 技术架构 (Technical Architecture)

### 3.1 前端改造 (Vue 3)
1. **新建 Layout**: `layouts/EmbedLayout.vue`
   - 极其简单的 `div` 容器，无任何多余样式。
2. **新建 View**: `views/EmbedChat.vue`
   - 复用现有的 `ChatWindow` 或 `MessageList` 组件逻辑。
   - 在 `onMounted` 钩子中解析 `route.query` 参数。
   - 初始化时自动使用 `token` 进行 API 连接。
3. **路由配置**:
   ```javascript
   {
     path: '/embed/chat',
     component: () => import('@/views/EmbedChat.vue'),
     meta: { layout: 'embed' } // 标记使用极简布局
   }
   ```

### 3.2 鉴权处理
- 嵌入页通常由宿主系统生成，Token 可能需要通过 URL 传递（注意安全风险）或通过 `postMessage` 通信传递。
- **推荐**: URL 传递短期 Token (Short-lived Token)，或者在初始化后通过 `window.postMessage` 握手交换 Token。

---

## 4. 集成方式 (Integration Methods)

### 方案 A: IFrame 嵌入 (MVP 阶段)
第三方开发者直接在 HTML 中插入：
```html
<div style="width: 400px; height: 600px; position: fixed; bottom: 20px; right: 20px; box-shadow: 0 0 20px rgba(0,0,0,0.1); border-radius: 12px; overflow: hidden; z-index: 9999;">
  <iframe 
    src="https://ai.yovole.com/embed/chat?agent_id=sys-agent-chatbi&theme=light&token=EYJhb..."
    width="100%" 
    height="100%" 
    frameborder="0">
  </iframe>
</div>
```

### 方案 B: JS SDK (Widget 模式 - 后续规划)
提供 `nanzi-widget.js`，自动注入 DOM 和样式。
```html
<script>
  window.NanZiAI = {
    config: {
      agent_id: "sys-agent-chatbi",
      token: "..."
    }
  };
</script>
<script src="https://ai.yovole.com/assets/widget.js" async></script>
```

---

## 5. UI/UX 细节规范

1. **响应式**: 必须完美适配移动端 (`width < 450px`)，此时输入框和按钮布局应调整。
2. **思考过程**: 
   - 默认折叠：`[Thinking...]`
   - 点击可展开查看详细日志 (Log Steps)。
   - Markdown 表格必须支持横向滚动，防止撑破 IFrame。
3. **图表交互**: 
   - 如果智能体返回 ECharts 图表，需确保 Tooltip 不会被 IFrame 边界截断。

## 6. 开发计划
1. [前端] 创建 `EmbedChat.vue` 及对应路由。
2. [前端] 适配 URL 参数解析逻辑。
3. [前端] 样式精简与 Mobile 适配测试。
4. [文档] 更新 `integration_guide.md` 提供 IFrame 示例代码。
