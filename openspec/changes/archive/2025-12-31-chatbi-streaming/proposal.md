# 提案：ChatBI 查数模块与流式交互升级 (ChatBI & Streaming Integration)

## 摘要
本提案旨在为“南孜・智能体平台”引入两个核心增强功能：
1. **ChatBI 核心能力**：通过 LangChain Tools 整合现有的 Data API，实现“自然语言 -> 指标查询 -> 结论生成”的闭环。
2. **流式交互体验**：将 API V1 升级为流式输出（Streaming），参照 OpenAI 标准提供逐字生成的丝滑体验。

## 背景
用户目前仅能获得静态回复，且无法直接通过自然语言获取实时的机房指标。实现 ChatBI 是平台从“聊天框”向“决策大脑”跨越的关键一步，而流式输出则是现代 AI 产品的交互标配。

## 目标
- **工具化集成**：封装第一个 ChatBI 工具，能够查询数据中心的核心指标。
- **流式架构**：后端支持 SSE (Server-Sent Events) 协议，前端可实时接收生成内容。
- **意图闭环**：当 `IntentService` 识别为 `DATA_QUERY` 时，自动调用 ChatBI 链处理并流式返回。
