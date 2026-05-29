# system-config Specification

## Purpose
TBD - created by archiving change centralize-system-config. Update Purpose after archive.
## Requirements
### Requirement: 动态系统配置 (Dynamic System Configuration)
系统必须 (MUST) 支持通过系统管理界面管理关键的基础设施配置，而无需重启服务。

#### Scenario: 管理员更新 LLM 模型
- **WHEN** 管理员在系统配置中将 "LLM Model Name" 修改为 "gpt-4-turbo" 并保存
- **THEN** 系统验证输入有效性
- **AND** 系统更新数据库并刷新 Redis 缓存
- **AND** 后续的对话请求立即使用 "gpt-4-turbo" 模型
- **AND** API Key 如果未修改，在 UI 上保持掩码状态。

#### Scenario: 环境变量兜底 (Fallback to Environment Variables)
- **WHEN** 数据库配置中不存在特定的配置项（例如 `LLM_API_KEY`）
- **THEN** 系统回退使用运行环境 (`.env`) 中定义的值
- **AND** 如果两者都未找到（对于必填项），记录警告日志。

### Requirement: 配置安全性 (Configuration Security)
UI 和 API 必须 (MUST) 保护敏感配置值（如 API Key）。

#### Scenario: 查看敏感配置
- **WHEN** 管理员请求当前配置列表
- **THEN** 标记为 "Secret" 的字段（如 API Keys）返回部分掩码的值（例如 `sk-****1234`）或完全隐藏
- **AND** 除非明确必要且经过授权，否则永远不应将真实值以明文形式发送到前端。

### Requirement: 配置范围 (Configuration Scope)
以下配置应 (SHALL) 可以通过 UI 进行管理：
- **LLM 网关**: Base URL, API Key, Model Name, Temperature
- **数据查询 API**: Endpoint URL, API Key

#### Scenario: 编辑数据源 API
- **WHEN** 管理员更新 "External SQL API URL"
- **THEN** `execute_sql_query` 工具立即将流量引导至新的端点。

### Requirement: Configuration Audit Trail (配置审计追踪)
系统 **MUST** 为所有系统级配置（`system_configs`）的变更保留历史记录。

#### Scenario: 记录变更
- **WHEN** 用户或系统更新某个配置项（如 `llm_model_name` 或 `llm_temperature`）
- **THEN** 系统在 `system_config_history` 表中创建一条新记录，包含：变更前的旧值、变更后的新值、操作人用户名、变更时间及变更原因（如有）。

### Requirement: Configuration History Query (配置历史查询)
系统 **MUST** 提供接口查询特定配置项的历史版本列表。

#### Scenario: 查询历史
- **WHEN** 管理员请求查询 `llm_model_name` 的历史
- **THEN** 系统按时间倒序返回所有变更记录。

### Requirement: 模型注册管理
系统 **MUST** 提供统一的 AI 模型注册表，允许管理员动态管理不同提供商的模型及其对应的 API 凭据。

#### Scenario: 管理员添加并使用新模型
- **WHEN** 管理员在“模型管理”标签页中添加了一个名为“DeepSeek-V3”的新模型，并配置了对应的 API Key 和 Base URL
- **THEN** 该模型应该立即出现在可用的模型列表中
- **AND** 其他系统组件（如智能体配置）可以引用此模型并使用其专属凭据

### Requirement: 动态模型参数配置
系统参数配置中的默认模型设置 **SHALL** 与模型注册表同步。

#### Scenario: 默认模型下拉选择
- **WHEN** 管理员在“参数配置”中编辑 `llm_model_name`
- **THEN** 系统应展示一个下拉列表，包含所有在注册表中处于“启用”状态的 LLM 类型模型
- **AND** 选中保存后，系统的全局默认模型将切换为该模型

### Requirement: 执行历史模型追踪
系统在记录智能体执行历史时，**MUST** 保留当时使用的模型快照信息。

#### Scenario: 审计日志记录模型信息
- **WHEN** 智能体执行完成并保存历史记录
- **THEN** `agent_execution_history` 中必须记录 `model_id` (模型标识) 和 `model_config_id` (引用 ID)
- **AND** 审计界面应能展示该次对话具体使用的模型名称

