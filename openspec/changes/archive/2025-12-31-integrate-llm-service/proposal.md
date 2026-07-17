# 提案：集成 LangChain 与 LLM 服务 (Integrating LangChain & LLM Service)

## 摘要
本提案旨在通过集成 **LangChain** 框架构建“南孜・智能体平台”的 AI 编排层。利用 LangChain 的表达语言 (LCEL) 和工具抽象，为后续的 ChatBI、RAG 和多智能体意图识别提供标准化的底座。

## 背景
作为智能体平台，基础的 LLM 调用已无法满足复杂的业务逻辑（如多步查询、知识库检索增强）。引入 LangChain 可以极大地降低 AI 插件和业务工具集成的复杂度，并提供工业级的模型切换与编排能力。

## 目标
- **框架集成**：引入 LangChain (Community & Core) 作为核心编排层。
- **统一适配**：封装兼容内部 LLM Gateway 的 LangChain 模型实例。
- **意图引擎升级**：使用 LCEL (LangChain Expression Language) 实现鲁棒的意图识别链。
- **工具化准备**：为 Data API 提供标准的 LangChain Tool 封装预览。
- **结构化输出**：利用 LangChain 的解析器确保 AI 返回严格的业务数据模型。
