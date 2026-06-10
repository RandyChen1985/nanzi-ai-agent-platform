## ADDED Requirements

### Requirement: ChatBI DataAgentRunner 阶段化执行

ChatBI MUST 由 AgentScope native Agent + Toolkit 承接，并由 `DataAgentRunner` 以显式运行状态处理自然语言查数请求。

#### Scenario: 完整数据查询链路

- **当** 用户发起新的 ChatBI 数据查询
- **那么** `DataAgentRunner` 必须完成问题归一化、元数据检索、SQL 生成、权限/顺序守卫、SQL 执行、结果审查和最终总结
- **并且** 每个阶段必须产生可审计的 trace 或内部状态记录

#### Scenario: 追问复用上一轮结果

- **当** 用户问题是对上一轮结构化查询结果的追问或导出、保存、记忆等上下文动作
- **那么** `DataAgentRunner` 必须优先加载上一轮结构化结果
- **并且** 不得机械地重新查询数据库

### Requirement: 元数据与上下文准备

DataAgentRunner MUST 保留现有授权数据集过滤、dataset menu 和 ChatBI 上下文准备能力。技能匹配和必须读取技能不作为本轮 ChatBI native runner 的强制范围。

#### Scenario: 授权数据集过滤

- **当** 构建 ChatBI 上下文
- **那么** dataset menu 只能包含当前用户有权访问且启用的数据集

#### Scenario: 技能必须读取

- **当** 系统提示词中包含已挂载技能摘要或用户问题显式触发技能执行
- **那么** 系统可以通过后续独立能力要求恢复必须读取完整技能说明的约束

### Requirement: SQL 生成与权限审查

DataAgentRunner MUST 在执行 SQL 前完成计划、权限和安全守卫。

#### Scenario: 执行前计划

- **当** AgentScope native Agent 准备调用 SQL 执行工具
- **那么** 输出必须包含可读的查询计划或分析步骤
- **并且** 缺少计划时运行状态守卫必须阻止一次并要求补充

#### Scenario: SELECT-only

- **当** SQL 包含 `DROP`、`DELETE`、`UPDATE`、`INSERT`、`ALTER` 等非只读操作
- **那么** 现有 SQL 工具权限和安全校验必须拒绝执行
- **并且** 返回用户可读的安全拦截说明

#### Scenario: 行级权限

- **当** 用户访问受行级权限约束的数据集或表
- **那么** 现有 SQL 工具权限和行级权限链路必须注入或验证权限过滤条件
- **并且** 用户不得访问未授权维度的数据

### Requirement: 结果审查与自纠错

DataAgentRunner MUST 保留 SQL 错误自纠、空结果复查和 schema miss 复查能力。异常比例复查不作为本轮 ChatBI native runner 的强制范围。

#### Scenario: SQL 语法错误自纠

- **当** SQL 执行返回语法错误或字段不存在错误
- **那么** AgentScope native Agent 必须接收错误信息并尝试重写 SQL
- **并且** 重试次数必须受最大步骤数限制

#### Scenario: 空结果复查

- **当** SQL 执行成功但返回零行
- **那么** 运行状态守卫必须判断是否需要重新检查条件、时间范围、schema 或口径
- **并且** 不得直接把空结果包装成确定性业务结论

#### Scenario: schema 缺失复查

- **当** SQLAgent 在未读取相关 schema 的情况下试图执行 SQL
- **那么** 运行状态守卫必须要求先调用元数据工具

### Requirement: 最终总结与结构化输出

DataAgentRunner MUST 保留最终中文回答、结构化结果摘要、上一轮结果追问总结和失败兜底。

#### Scenario: 正常总结

- **当** SQL 执行返回结构化数据
- **那么** AgentScope native Agent 或总结模型必须基于原始问题、执行 trace 和工具结果生成中文回答
- **并且** 不得编造工具结果中不存在的指标或数据

#### Scenario: 总结失败兜底

- **当** 总结模型失败、超时或流式输出中断
- **那么** 系统必须返回平台级兜底文案
- **并且** 已经查询到的结构化结果不得丢失
