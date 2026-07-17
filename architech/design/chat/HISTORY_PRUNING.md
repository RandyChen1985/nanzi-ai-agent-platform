# 智能体平台历史消息裁剪与 Token 优化设计方案

在南孜智能体平台中，大模型的多轮交互会面临滚雪球式的 Token 膨胀。本文件深入评估了当前平台中所有智能体（Assistant、DataQuery、Knowledge）对历史消息的处理现状，并提出了针对性的历史裁剪优化方案。

---

## 1. 历史消息处理现状审计 (Current Audit)

通过对 `app/services/ai/runners` 目录下的三个核心智能体 Runner（`AssistantAgentRunner``DataAgentRunner``KnowledgeAgentRunner`）的源码走读，发现它们在处理历史消息（`history`）时存在以下特征：

### 1.1 统一转换真相源
三个 Runner 最终都是通过 `app/services/ai/executors/common.py` 中的 `convert_history_to_messages(history)` 将数据库中存储的 KV 对话历史转换为 LLM 识别的消息：
* **用户消息 (`HumanMessage`) ── **已裁剪精简****：
  * 代码中使用 `_plain_user_text(content)`，基于 `\n\n---\n\n` 分隔符进行切片。
  * 成功剥离了前端每轮拼接的非图片附件详情、服务器文件绝对路径、以及各种 RAG 强制指令，只保留了用户最原始说的纯文本字句。
* **助手消息 (`AIMessage`) ── **完全无裁剪****：
  * 直接执行 `messages.append(AIMessage(content=content))`。
  * 导致大模型前几轮输出的完整 Thought 思考过程、`<function_calls>` XML 工具调用大块、渲染的完整 Markdown 数据表格、以及前端图表的 ECharts JSON 代码被原封不动发回 LLM，这部分信息在多轮会话中消耗了 70% 以上的历史 Token。

### 1.2 各智能体在运行时的细分表现
* **AssistantAgent (通用助手)**：
  * 在 Simple 模式和 ReAct 模式下，直接传递转换后的全量 `runtime_messages`，历史 Assistant 消息没有进行任何处理。
* **KnowledgeAgent (知识库)**：
  * 直接传入全量历史消息，缺乏对历史召回文本的清洗或对 AI 历史答复的精炼。
* **DataAgent (ChatBI / 数据查询)**：
  * **在 `REUSE_PREVIOUS_RESULT`（复用上一轮结果）支线**：进行了**手动过滤**。在 `_synthesize_from_last_data_result` 中专门把历史所有的 `AIMessage` 过滤掉，只向大模型传递历史的 `HumanMessage` 提问，配合上一轮的 JSON 结果进行增量可视化或分析。
  * **在 `NEW_DATA_QUERY`（新查询）多轮支线**：**完全没有过滤/剪裁**。在 native AgentScope 运行时直接发送包含全量历史 `AIMessage` 的 `inputs`。

---

## 2. 为什么要裁剪 Assistant 消息？

在大模型多轮交互（特别是 ReAct 推理）中，历史中携带的 Assistant 信息价值并不均等：

```
┌───────────────────────────────────────────────┐
│ [核心价值] 历史 User 提问 (做指代消解和上下文对齐) │ ── 必须保留
├───────────────────────────────────────────────┤
│ [核心价值] 历史工具执行结论 (如执行成功的 SQL)     │ ── 必须保留 (防止重复跑数)
├───────────────────────────────────────────────┤
│ [极低价值] 历史 thought 思考过程 (中间碎碎念)     │ ── 建议裁剪 (大模型推理完就没用了)
├───────────────────────────────────────────────┤
│ [极低价值] 历史工具调用 XML 块 (<function_calls>)│ ── 建议裁剪 (占用大段 Token 且干扰模型)
├───────────────────────────────────────────────┤
│ [垃圾信息] 历史庞大的 Markdown 数据表格          │ ── 建议压缩/截断 (只保留结构和前N行)
└───────────────────────────────────────────────┘
```

---

## 3. 推荐的历史裁剪方案设计 (Proposed Solutions)

针对以上痛点，我们提出以下三层裁剪方案。可以在 `executors/common.py` 或特定 Runner 中进行改造。

### 方案一：正则剥离 Assistant 消息中的中间过程 (Thought & XML Cleanup)
在 `convert_history_to_messages` 转换历史时，针对 `role == "assistant"` 的 `AIMessage` 做正则数据清洗：
1. **剥离 XML 工具调用**：用正则匹配过滤掉 `<function_calls>...</function_calls>`。
2. **剥离思考过程**：过滤掉 `<thought>...</thought>` 的内容（由于部分模型使用 XML 作为思考包裹，部分模型用 `<think>` 或 Markdown 引用，需兼容多模型前缀）。
3. **保留结果**：仅保留 Assistant 消息中用于向用户展示的自然语言陈述性总结。

* **代码改造示意**：
  ```python
  def _clean_assistant_text(content: str) -> str:
      if not content:
          return ""
      # 剥离 XML 工具调用
      content = re.sub(r"<function_calls>.*?</function_calls>", "", content, flags=re.DOTALL | re.IGNORECASE)
      # 剥离 XML 思考过程
      content = re.sub(r"<thought>.*?</thought>", "", content, flags=re.DOTALL | re.IGNORECASE)
      content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE)
      return content.strip()
  ```
* **收益评估**：**减少 30% - 50% 的多轮历史 Token 膨胀**，完全不影响语义。

---

### 方案二：压缩历史数据表格与大段 JSON (Markdown / Table Compression)
大模型在上一轮中为用户返回的 20 行 Markdown 表格，到了本轮只具备“背景参考价值”，不再需要全量明细：
1. **表格行数封顶**：当检测到历史消息中包含 Markdown 表格（由 `|` 和 `-` 构成的多行结构），仅保留表头 + 前 3 行数据，后续行替换为 `... [此处省略历史表格明细 %d 行] ...`。
2. **图表 JSON 过滤**：如果大模型在上一轮中输出了大段 ```chart JSON``` 格式的 ECharts 图表参数，直接在历史中清空或替换为 `[已渲染柱状图]` 占位符。

* **收益评估**：能够将数千字符的无用明细表格压缩在 200 字符内，**单轮可省下 1K - 5K Tokens**。

---

### 方案三：DataAgent 独立改写后自动短路历史 (Standalone Query Pruning)
在 ChatBI 模式下，当检测到是 `NEW_DATA_QUERY` 且已经通过 `standalone_query_rewrite` 生成了改写后的独立完整问题后：
* **优化逻辑**：既然改写后的提问（如“查一下 B 部门上个月空调销售额，按省份汇总”）已经**完全包含了所需的时序和筛选代词**，那么第 1 - 3 轮的空调表格和 Excel 导出对话，对本轮冰箱查数流程基本是**零价值**的。
* **裁剪做法**：在此情况下，Runner 传给 AgentScope Native 运行时的 `inputs`，**可以直接丢弃 N 轮前的 Assistant 历史**，或者只保留最近一轮的 User 问题。
* **收益评估**：**从根本上打破多轮 Token 累积**，使多轮新查询的 Token 消耗和首轮会话几乎一致。

---

## 4. 实现建议与步骤 (Implementation Strategy)

建议按照**渐进式**方案进行优化：
1. **第一阶段 (低风险高收益)**：
   在 `executors/common.py` 中引入 `_clean_assistant_text` 剥离 `thought` 和 `function_calls`。这不会损坏上下文的语义结果，也不会破坏指代消解。
2. **第二阶段 (针对 ChatBI 深度瘦身)**：
   在 `data_agent_runner.py` 的新查数路径中，当 `standalone_query` 生效时，限制传入 AgentScope 的历史轮数，例如只传入最近 1 轮历史，切断前面庞大空调表格的继承。
