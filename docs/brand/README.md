# NanZi N Icon 品牌规范

## 1. 设计结论

本次 icon 采用 **A「稳重品牌型」× C「智能连接型」** 的融合方向：

- 用一个清晰、几何化的 `N` 作为第一识别，保证产品头像、App 图标和 favicon 在小尺寸下仍然可读。
- 用少量节点和连线暗示智能体、MCP、知识、工具与协作生态，但不把 icon 做成复杂的网络拓扑图。
- 保留 NanZi 现有视觉中已经形成认知的蓝紫渐变，令新 icon 能自然承接产品截图、发布物料和后台界面。

核心判断是：**先读出 N，再感知“智能连接”；不要让连接元素抢过品牌字母。**

## 2. 资产文件

| 文件 | 用途 | 说明 |
| --- | --- | --- |
| `nanzi-n-icon.svg` | 主品牌 icon、产品头像、App 图标、宣传物料 | 512×512，保留 3 个节点和高光层 |
| `nanzi-n-icon-favicon.svg` | 浏览器 favicon、小尺寸入口 | 512×512 源画布，实际使用时导出 16/32/48px；只保留 2 个节点 |
| `nanzi-n-icon.png` | 512×512 位图预览 | 从主 SVG 导出，不作为编辑源 |
| `nanzi-n-icon-favicon-512.png` | 项目默认 favicon 的 512×512 位图源 | 从 favicon SVG 导出，供 `frontend/public/favicon.png` 使用 |
| `nanzi-n-icon-favicon.png` | 32×32 favicon 预览 | 从 favicon SVG 导出，不作为编辑源 |
| `nanzi-n-icon-apple-touch-180.png` | iPhone 添加到主屏幕的图标 | 由主 icon 导出的 180×180 满版不透明 PNG，交由 iOS 套用圆角 |

SVG 是唯一的可编辑源。后续要调整颜色、节点数量或导出尺寸时，优先修改 SVG，再重新生成 PNG。

## 3. 构成与理念

### 3.1 N 的骨架

- 使用圆角端点和圆角连接，表达开放、友好、可协作，而不是尖锐的安全或硬件风格。
- 左竖、斜向连接、右竖组成稳定的 N 形骨架；斜向连接带来向前、向上的动势。
- N 的白色主体确保在蓝紫背景上拥有高对比度，缩小后仍然不会和背景融合。

### 3.2 智能连接层

- 节点位于 N 的起点、斜向连接附近和延伸方向，代表从输入到推理、工具调用和结果输出的连接关系。
- 节点采用青色与淡紫色区分，分别对应开放连接和智能能力，但不引入第三种主色。
- 主 icon 保留 3 个节点；favicon 只保留 2 个节点，避免 16–32px 下出现细线噪声。

### 3.3 圆角方形容器

- 512 画布中，背景区域为 `x=24, y=24, width=464, height=464`，圆角半径为 `112`。
- 外留约 4.7% 的安全边距，避免在系统头像、浏览器标签和圆形裁切中贴边。
- 不在背景外额外添加投影；投影只用于主 icon 的展示预览，favicon 版本保持干净。

## 4. 颜色 Token

| Token | Hex | 用途 |
| --- | --- | --- |
| `nanzi-blue-600` | `#2563EB` | 渐变起点、主要品牌蓝 |
| `nanzi-indigo-600` | `#4F46E5` | 渐变中段、智能能力过渡 |
| `nanzi-indigo-500` | `#6366F1` | 渐变终点 |
| `nanzi-node-cyan` | `#A5F3FC` | 开放连接节点、辅助连线 |
| `nanzi-node-violet` | `#C4B5FD` | 智能能力节点 |
| `nanzi-highlight` | `#DBEAFE` | N 主体边缘高光 |
| `nanzi-white` | `#FFFFFF` | N 主体和节点描边 |

主背景渐变方向为左下到右上：`#2563EB → #4F46E5 → #6366F1`。不要把主背景改成多色霓虹、金属渐变或高饱和粉色，否则会削弱 NanZi 的稳定感。

## 5. 尺寸与使用规则

### 推荐尺寸

- 产品头像 / App 图标：`512×512` 或 `1024×1024`，使用 `nanzi-n-icon.svg`。
- 浏览器 favicon：优先导出 `16×16`、`32×32`、`48×48`，使用 `nanzi-n-icon-favicon.svg`。
- 导航栏或消息头像：`24–40px` 时使用 favicon 简化版；`64px` 以上可使用主 icon。
- 导出 PNG 时保留 RGBA，不要把圆角外的透明区域填成白色。

### 安全区

最小安全区为 icon 外框边缘到任何外部文字、边框或其他图形的 `0.12 × icon 高度`。如果放在圆形头像容器中，保持 icon 完整，不再二次裁切 N 的主体。

### 背景

- 首选：白色、浅灰、深蓝或透明背景。
- 深色背景上可以直接使用完整 icon；不要额外给 N 添加黑色描边。
- 浅色背景上不要把 icon 的蓝紫渐变替换成纯白，否则会失去品牌识别。

## 6. 禁止变体

- 不要拉伸、压扁或旋转 N。
- 不要给 N 添加阴影、外描边或复杂纹理，尤其是在 favicon 版本中。
- 不要增加大量节点、网格、芯片、机器人脸或电路板图案。
- 不要把节点放到 N 轮廓之外形成独立装饰物；节点必须服务于 N 的连接关系。
- 不要在 icon 内放置 `NanZi`、`AI Agent Platform` 或中文标题；文字应在 icon 外单独排版。

## 7. 复刻提示词

下面的描述可用于后续设计师、图像模型或前端 SVG 实现复刻。若使用图像模型，生成结果应当继续人工整理为 SVG，以保证几何和小尺寸一致性。

> Create a vector-friendly square app icon for NanZi, an open intelligent agent platform. Use a rounded square with a blue-to-indigo gradient from lower-left to upper-right. Place a clean geometric white capital N in the center, with rounded stroke ends and a strong diagonal connecting stroke. Add only a few restrained cyan and pale-violet network nodes along the N's lower-left start, diagonal connection, and upper-right direction, suggesting agents, tools, MCP, knowledge, and collaboration. The N must remain the first and clearest visual read. Minimal, modern, trustworthy, open-source developer platform aesthetic. No text, no robot face, no circuit-board pattern, no dense network, no watermark.

## 8. 复刻检查清单

- [ ] 第一眼能读出大写 `N`。
- [ ] 32px 下 N 仍完整，节点没有变成噪点。
- [ ] 蓝紫渐变和青/淡紫节点的关系保持不变。
- [ ] 主 icon 与 favicon 的差异只体现在细节简化，不改变品牌骨架。
- [ ] 导出 SVG 无外部字体、图片、滤镜资源或网络依赖。
- [ ] 导出 PNG 为 RGBA，并检查透明边缘没有白边或色边。

## 9. 项目接入

默认资源已经同步到前端公共目录：

- `frontend/public/favicon.svg`：浏览器优先使用的 SVG favicon。
- `frontend/public/favicon.png`：PNG favicon fallback 的位图资源。
- `frontend/public/apple-touch-icon.png`：iPhone 添加到主屏幕专用的满版不透明 PNG；iOS 会在此基础上套用圆角，不会暴露透明黑边。
- `frontend/public/logo.png`：项目公共 logo 位图，使用主 icon 的 320×320 导出。
- `frontend/index.html`：同时声明 SVG favicon、PNG fallback 和独立的 Apple touch icon。

`useBranding` 与后端品牌配置仍然保留动态覆盖能力：只有在没有配置自定义 `icon_url` 时，才回退到透明的 `/favicon.svg`；PNG 只承担兼容和 Apple touch icon 场景。浏览器标题文案继续由品牌配置控制，默认值为 `NanZi Agent`，不会把文字标题误当成图标资源。

## 10. 扩展 VI 资源

| 文件 | 用途 |
| --- | --- |
| `nanzi-wordmark-on-light.svg` | 浅色页面、登录页、文档页的横版 Logo |
| `nanzi-wordmark-on-dark.svg` | 深色侧栏、深色封面、发布物料的横版 Logo |
| `nanzi-n-icon-monochrome.svg` | 打印、黑白文档、低彩度场景 |
| `nanzi-agent-avatar.svg` | 默认智能体头像、聊天气泡、任务中心 |
| `nanzi-social-cover.svg` / `.png` | GitHub、公众号、分享卡片，画布为 1200×630 |
| `nanzi-community-banner-with-new-logo.png` | 社区关注 / AIGC 活动横幅，已将笔记本和杯子上的旧 N 标识替换为新 Logo |
| `nanzi-state-empty.svg` | 空数据、未创建资源、无检索结果 |
| `nanzi-state-loading.svg` | 加载、构建、检索和异步任务状态 |
| `nanzi-state-error.svg` | 请求失败、连接异常、执行失败 |
| `nanzi-capability-icons.svg` | ChatBI、Knowledge、MCP、Shell、Governance、Memory 能力入口图标族 |

扩展资源继续遵守同一原则：N 是主识别，节点只表达智能连接；状态插画可以使用语义色，但不能改变主 Logo 的蓝紫品牌渐变。
