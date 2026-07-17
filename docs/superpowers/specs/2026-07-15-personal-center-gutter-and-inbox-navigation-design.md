# PersonalCenter 留白与站内通知跳转修复设计

## 目标

1. PersonalCenter 页面恢复轻量的左右留白，但不恢复卡片边框、圆角和上下外距。
2. 站内报表通知无论从其他页面还是当前 Chat 页面点击，都能打开对应报表及运行记录。
3. 标记已读失败时不阻断报表跳转。

## 现状与根因

- `Dashboard.vue` 将 `AIChat` 和 `PersonalCenter` 都设置为 `p-0`，因此 PersonalCenter 的白色页面完全贴住主内容区左右边缘。
- 通知点击会跳转到 `/dashboard/chat` 并携带 `report_id`、`run_id` 查询参数。
- `Dashboard.vue` 以 `$route.path` 作为页面组件 key；当用户已经位于 `/dashboard/chat` 时，只有查询参数变化，Chat 组件不会重新挂载。
- `Chat.vue` 只在 iframe 就绪时发送一次 `INIT_CONFIG`，没有监听后续查询参数变化，因此 iframe 收不到新的 `open_saved_report`。
- 未读通知会先等待标记已读接口成功，再执行跳转；接口异常或并发已读导致的失败会中断后续导航。

## 设计

### PersonalCenter 布局

- 在 Dashboard 主内容容器上区分三种页面：
  - `AIChat`：继续使用 `p-0`，保持全屏聊天布局。
  - `PersonalCenter`：仅增加响应式水平留白，移动端约 12px、桌面约 16px；不增加垂直留白。
  - 其他页面：保留当前通用间距。
- PersonalCenter 自身继续使用白色背景和无外框布局。

### 通知跳转

- 保留通知组件通过路由查询参数表达目标报表的现有协议。
- 通知完成路由导航后，同时发布一次宿主窗口事件；即使目标路由与当前路由完全相同，每次点击也有独立信号。
- `Chat.vue` 在 iframe 已就绪时将宿主事件转换为轻量 `OPEN_SAVED_REPORT` 消息，不重复发送 `INIT_CONFIG`。
- EmbedChat 复用现有打开报表函数处理初始化配置与轻量消息，但轻量消息不会执行 `initChat()`，避免重新鉴权、重新加载会话和覆盖当前消息。
- 每次点击生成唯一 `request_id`。EmbedChat 用它合并同一次点击可能产生的初始化与运行时信号，再通过响应式属性把目标传到已挂载的 `DatasetCapabilityMenu`。
- `DatasetCapabilityMenu` 监听新的 `request_id`，直接打开目标报表、运行历史页签和指定运行记录，不依赖重新拉取列表或 `localStorage`。
- 通知点击时先计算导航目标；标记已读失败仅记录警告，仍关闭通知面板并执行导航。
- 不使用 `$route.fullPath` 强制重建 Chat，避免 iframe 重载、对话丢失和页面闪烁。

## 错误处理

- 通知没有 `report_id` 时只执行已读，不进行无目标跳转。
- iframe 尚未就绪时，Chat 挂载后的既有 `NANZI_WIDGET_READY` 流程会读取最新路由参数并发送首次配置。
- iframe 已就绪时，每次通知点击都通过宿主事件发送轻量打开消息，包括重复点击同一报表和运行记录。
- 同一次点击的初始化信号与运行时信号共享 `request_id`，只处理一次；用户再次点击会生成新序号并再次处理。

## 验证范围

- PersonalCenter 页面拥有水平留白，AIChat 仍保持全屏。
- 从 PersonalCenter 等其他页面点击报表通知，进入 Chat 并打开对应报表。
- 已处于 Chat 页面时点击另一条报表通知，不重载 iframe，也能打开目标报表。
- 重复点击同一个报表通知仍会再次打开目标，不触发 Chat 重新初始化。
- 标记已读接口失败时，报表跳转仍执行。
- 无 `report_id` 的普通通知只标记已读，不产生错误跳转。
