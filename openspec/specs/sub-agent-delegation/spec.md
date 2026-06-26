# sub-agent-delegation Specification

## Purpose
TBD - created by archiving change sub-agent-delegation. Update Purpose after archive.
## Requirements
### Requirement: 子代理委托工具注册与发现
系统必须 (MUST) 支持在工具注册表中注册名为 `sub_agent_call` 的委托工具，且该工具默认对主助手启用。

#### Scenario: 注册并对外暴露 sub_agent_call 工具
- **WHEN** 通用智能体初始化时
- **THEN** 系统工具包中必须包含 `sub_agent_call` 工具，且该工具能够正常识别平台中已配置的其他智能体。

### Requirement: 委托会话的上下文隔离
子代理在执行委托任务时，系统必须 (MUST) 为其分配一个独立的、干净的消息历史上下文，不能掺杂主智能体当前会话的无关上下文。

#### Scenario: 隔离的上下文调用
- **WHEN** 主助手调用 `sub_agent_call(agent_name="data-agent", query="查询销售额")`
- **THEN** 子智能体 `data-agent` 接收到的 `messages` 仅包含其专属的 `system_prompt` 与本次 `query`，不包含主会话历史。

### Requirement: 子代理执行进度日志实时穿透
在委托调用期间，子代理在后端产生的所有执行日志（`type: "log"`）必须 (MUST) 被捕获，并在重写标识（以指明来源智能体）后实时穿透并发送至主会话的前端流式 SSE 中。

#### Scenario: 实时穿透日志
- **WHEN** 子代理在后端执行并产出 `type="log"` 的事件包时
- **THEN** 委托工具将日志包的标题修改为 `[子代理名称] 原始日志标题`，并立即将其写入当前主会话的 SSE 消息队列中。

### Requirement: 子代理输出内容过滤去噪
子代理执行完成后，其输出中的多模态、卡片标签等干扰文本必须 (MUST) 被剥离或转换，然后再将纯文本或结构化数据返回给主助手。

#### Scenario: 过滤图形化卡片标签
- **WHEN** 查数子代理执行完毕并返回包含 `<sql_plan>...</sql_plan>` 的结果文本时
- **THEN** 委托工具在将文本交付给主助手前，将 `<sql_plan>` 标签段过滤掉或转换为纯文本格式，防止上下文污染。
