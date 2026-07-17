# knowledge-platform Specification

## Purpose

提供基于 RAGFlow 的知识库开发平台能力，支持在南孜平台内完成知识库生命周期管理、文档管理、检索测试、本地扩展元数据维护和 RAGFlow Portal API 代理。

## Requirements

### Requirement: 知识库开发平台导航

系统 SHALL 在控制台左侧导航中提供「知识库开发平台」分组，并在该分组下展示「知识库管理」与「检索测试」两个模块入口。

#### Scenario: 展示知识库开发平台菜单

- **WHEN** 用户拥有知识库开发平台相关菜单权限并进入控制台
- **THEN** 系统在「CHATBI 开发平台」分组之后展示「知识库开发平台」分组
- **AND** 系统在该分组下展示用户有权限访问的「知识库管理」和「检索测试」菜单项

#### Scenario: 无权限用户不展示菜单

- **WHEN** 用户没有知识库开发平台相关菜单权限并进入控制台
- **THEN** 系统不展示「知识库开发平台」分组及其子菜单

### Requirement: 知识库列表与创建

系统 SHALL 允许有权限的用户在平台内查看 RAGFlow 知识库列表并创建新的知识库，并 SHALL 在本地维护知识库扩展元数据。

#### Scenario: 查看知识库列表

- **WHEN** 用户进入「知识库管理」页面
- **THEN** 系统调用后端 Portal API 获取 RAGFlow 知识库列表
- **AND** 系统合并本地知识库元数据
- **AND** 页面展示知识库名称、描述、业务归属、标签、文档数、Chunk 数、更新时间和 Dataset ID

#### Scenario: 创建知识库

- **WHEN** 有创建权限的用户输入知识库名称、描述和切片方式并提交
- **THEN** 系统通过后端 Portal API 调用 RAGFlow 创建知识库
- **AND** RAGFlow 创建成功后系统写入本地知识库元数据
- **AND** 创建成功后刷新知识库列表并展示成功提示

#### Scenario: 维护本地扩展元数据

- **WHEN** 有编辑权限的用户更新知识库业务归属、标签、备注或扩展配置
- **THEN** 系统更新本地知识库元数据
- **AND** 不要求同步修改 RAGFlow 原生 Dataset 字段

#### Scenario: RAGFlow 配置缺失

- **WHEN** 用户进入「知识库管理」页面且系统缺少 RAGFlow 地址或 API Key
- **THEN** 系统展示明确的配置缺失提示
- **AND** 页面不应出现空白或未处理异常

### Requirement: 知识库删除

系统 SHALL 允许有权限的用户删除 RAGFlow 知识库，并在执行前要求二次确认。

#### Scenario: 删除知识库

- **WHEN** 有删除权限的用户点击知识库删除操作并确认
- **THEN** 系统通过后端 Portal API 调用 RAGFlow 真实删除对应知识库
- **AND** 系统将本地知识库元数据标记为已删除或删除本地映射
- **AND** 删除成功后刷新知识库列表

#### Scenario: 取消删除知识库

- **WHEN** 用户点击知识库删除操作但在确认弹窗中取消
- **THEN** 系统不调用删除接口
- **AND** 知识库列表保持不变

### Requirement: 文档管理

系统 SHALL 允许有权限的用户查看知识库内文档、上传文档、删除文档并手动触发文档解析。

#### Scenario: 查看文档列表

- **WHEN** 用户在「知识库管理」页面选择某个知识库查看详情
- **THEN** 系统调用后端 Portal API 获取该知识库的文档列表
- **AND** 页面展示文档名称、解析状态、Chunk 数、大小和更新时间

#### Scenario: 上传文档

- **WHEN** 有上传权限的用户在知识库详情中选择文件并提交
- **THEN** 系统通过后端 Portal API 将文件上传到对应 RAGFlow 知识库
- **AND** 上传成功后刷新文档列表
- **AND** 系统不在平台侧限制文件类型

#### Scenario: 上传后不自动解析

- **WHEN** 用户上传文档成功
- **THEN** 系统不自动触发 RAGFlow 文档解析
- **AND** 页面提供手动解析入口

#### Scenario: 触发文档解析

- **WHEN** 有解析权限的用户选择一个或多个文档并点击解析
- **THEN** 系统调用 RAGFlow 文档解析接口
- **AND** 页面展示解析已触发提示并允许用户刷新查看状态

#### Scenario: 删除文档

- **WHEN** 有删除文档权限的用户选择文档并确认删除
- **THEN** 系统通过后端 Portal API 真实删除对应文档
- **AND** 删除成功后刷新文档列表

### Requirement: 检索测试

系统 SHALL 提供检索测试页面，允许用户选择知识库、输入查询、配置检索参数并查看 RAGFlow chunk 命中结果。

#### Scenario: 执行检索测试

- **WHEN** 有检索测试权限的用户选择一个或多个知识库、输入查询并提交
- **THEN** 系统调用后端 Portal API 执行 RAGFlow retrieval
- **AND** 页面展示命中文档名、相似度、Chunk ID 和内容片段

#### Scenario: 调整检索参数

- **WHEN** 用户调整 top_k、similarity_threshold 或 vector_similarity_weight 后再次提交检索
- **THEN** 系统使用新的参数执行检索
- **AND** 页面展示本次参数对应的检索结果

#### Scenario: 未选择知识库

- **WHEN** 用户未选择任何知识库并尝试执行检索
- **THEN** 页面提示必须至少选择一个知识库
- **AND** 系统不调用检索接口

### Requirement: RAGFlow Portal API 代理

系统 SHALL 通过后端 Portal API 代理所有知识库开发平台需要的 RAGFlow 操作，前端不得直接访问 RAGFlow API Key。

#### Scenario: 前端通过平台 API 管理知识库

- **WHEN** 前端执行知识库列表、创建、删除、文档管理或检索测试操作
- **THEN** 请求发送到南孜平台后端 Portal API
- **AND** 后端读取系统配置中的 RAGFlow 地址与 API Key 后调用 RAGFlow

#### Scenario: RAGFlow 返回错误

- **WHEN** RAGFlow API 返回 HTTP 错误或业务错误
- **THEN** 后端将错误转换为前端可展示的错误响应
- **AND** 前端展示用户可理解的失败提示

### Requirement: 知识库本地元数据

系统 SHALL 为 RAGFlow 知识库维护本地元数据记录，以支持平台侧业务管理信息。

#### Scenario: 创建本地元数据记录

- **WHEN** 平台成功创建 RAGFlow 知识库
- **THEN** 系统创建本地知识库元数据记录
- **AND** 本地记录包含 RAGFlow Dataset ID、名称、描述、业务归属、标签、创建人、更新人、状态和扩展配置

#### Scenario: RAGFlow 数据与本地元数据合并展示

- **WHEN** 用户查看知识库列表
- **THEN** 系统基于 RAGFlow Dataset ID 合并 RAGFlow 数据和本地元数据
- **AND** 页面优先展示平台侧业务元数据，同时保留 RAGFlow 文档数、Chunk 数和更新时间

#### Scenario: RAGFlow Dataset 缺失

- **WHEN** 本地存在知识库元数据但 RAGFlow 列表中找不到对应 Dataset
- **THEN** 系统在列表中标记该知识库为异常或失联
- **AND** 不应静默隐藏本地记录导致管理人员无法排查
