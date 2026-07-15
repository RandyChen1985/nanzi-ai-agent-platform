# 运行历史推送内容 Markdown 预览设计

## 目标

黄金报表运行历史的“推送内容”以 Markdown 预览显示，不再向用户暴露 `###`、`-` 等源码标记。

## 设计

- 仅替换 `DatasetCapabilityMenu.vue` 中 delivery content 的展示方式，标题、渠道、状态和错误信息保持不变。
- 新增安全 Markdown 预览函数，支持标题、列表、强调、引用、代码、表格和链接。
- 禁止 Markdown 中的原始 HTML，避免持久化内容通过 `v-html` 注入页面。
- 外部链接添加 `target="_blank"` 与 `rel="noopener noreferrer"`。
- 预览区域保留最大高度和内部滚动，并提供浅色、深色及移动端友好的紧凑排版。

## 验证

- 契约测试确认运行历史使用安全 Markdown 渲染，不再使用纯文本 `<pre>`。
- 行为测试确认 Markdown 被渲染、原始 HTML 被转义、危险链接不生成可点击地址。
- 运行前端无基础设施回归、Vue SFC 编译解析和 `git diff --check`。
