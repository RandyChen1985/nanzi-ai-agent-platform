# ChatBI 4轮会话提示词整体结构与大小估算表 (精确核算版)

本文件基于对南孜平台源码（包括 `agent_prompts.py` 中的提示词瘦身机制、`data_api.py` 的元数据拉取格式以及 `chatbi_example_service.py` 的 Few-Shot 组织方式）的深度代码走读，为一轮典型的 ChatBI 链条进行精确的 Token 和字符消耗核算。

---

## 💡 核心算力瘦身机制说明

在真实环境中，系统在装配 Prompt 时有以下几点自动瘦身，使得 Token 开销显著小于预估：
1. **全局提示词瘦身 (`prepend_platform_global_system_prompt`)**：
   ChatBI 智能体仅挂载了查数类工具，**不含** `Bash`/`Read`/`Write`/`Grep`/`Glob` 等高敏感系统操作工具。因此，全局守则中长达近 **800 字符** 的敏感工具安全准则及相应对照行会被自动剔除，瘦身后的全局守则仅占 **~1,000 字符**。
2. **数据集菜单瘦身 (`dataset_menu`)**：
   `{dataset_menu}` 仅包含数据集名称、中文名描述、以及包含的表物理名（不含 DDL），大小在 **~500 字符** 左右。
3. **元数据 Schema 上下文 (DDL)**：
   在 RAG 模式下，系统通过语义搜索只召回最相关的 `top_k=5`（默认配置为3-5）个 DDL 片段。在本地模式下会拉取 YAML 文本。按平均 3 个表 DDL 召回计算，约为 **~3,000 字符**。
4. **历史对话精简**：
   进入 runners 推理前，历史会话会剔除多余的辅助 logs，只保留精炼后的 User 提问和 AI 摘要，且在 synthesis 阶段会完全**丢弃 AI 的历史输出**，防止大模型复读。

> [!NOTE]
> **Token 转换比率说明**：中英混合语境下，我们按业内通用模型经验采用 `1 Token ≈ 1.8 字符 (Chars)` 的换算比例。

---

## 第一轮会话：新查询 (NEW_DATA_QUERY)
* **用户输入**：“查一下我们公司上个月空调的销售额是多少，按省份汇总。”
* **运行机制**：首轮短路分类器。RAG 自动预检索 3 张表的 DDL。激活第一轮 `ToolChoice(mode="execute_sql_query")`。

### 1. 整体消息结构 (Messages Shape)
1. **SystemMessage #1**（系统提示词）：
   * `[Few-Shot]` 向量库检索到的 3 条相似 SQL 案例（已包含专家逻辑与 SQL）。
   * `[Platform Global Prompt]` 瘦身后的南孜全局守则。
   * `[Time Anchor]` 锁定上月具体日期的系统锚点。
   * `[SQL Plan & Reuse Constraints]` SQL 生成与追问复用门禁规范。
   * `[Agent System Prompt]` 智能体本尊 Prompt + 替换后的简易 `{dataset_menu}` 数据集列表。
   * `[Prefetched Schema]` 3 张表自动预检索出的真实 DDL 结构。
2. **HumanMessage**：
   * 本轮空调提问。

### 2. 真实大小估算
| 提示词模块 | 实际字符数 (Chars) | Token 消耗估算 | 备注 |
| :--- | :--- | :--- | :--- |
| **平台全局守则 (瘦身后)** | 1,000 Chars | ~550 Tokens | 剔除了无关的高危工具规则 |
| **GLOBAL_GUARDRAILS 等查数门禁**| 1,250 Chars | ~700 Tokens | 包含 SQL Plan 强规范与复用约束 |
| **时间锚点 (Time Anchor)** | 400 Chars | ~220 Tokens | 当前年月日相对日期锁定 |
| **智能体 Prompt + 数据集菜单** | 1,500 Chars | ~830 Tokens | 仅包含表简述菜单而非 DDL |
| **3 个 Few-Shot 案例** | 1,200 Chars | ~670 Tokens | 3条完整优质 SQL 示例 |
| **3 张表预检索 Schema DDL** | 3,000 Chars | ~1,670 Tokens | 空调/销售额关联表 DDL |
| **本轮用户空调提问** | 100 Chars | ~60 Tokens | 原始问题 |
| **总计 (First Turn)** | **8,450 字符** | **约 4,700 Tokens** | **远低于初版预估，有效控制在 5K Tokens 内** |

---

## 第二轮会话：复用结果可视化 (REUSE_PREVIOUS_RESULT)
* **用户输入**：“把这些销售额数据画一个柱状图看看。”
* **运行机制**：判定为复用结果。**短路 ReAct 步骤**，直接进入合成（Synthesis）阶段。

### 1. 整体消息结构 (Messages Shape)
1. **SystemMessage #1**：
   * 瘦身后的平台全局守则。
   * 数据查询通用约束（包含 `{dataset_menu}` 被替换为静态复用提示）。
   * **（去除了 Few-Shot 案例、时钟锚点与 Schema DDL）**
2. **HumanMessage (第一轮历史)**：
   * 裁剪后的第 1 轮 User 问题。（**去除了第 1 轮大模型的 SQL 及回复长文**）。
3. **HumanMessage (本轮输入合成)**：
   * `followup_synthesis_user_message` 模板，含上轮数据 JSON（截取前 50 行，防括号损坏）。

### 2. 真实大小估算
| 提示词模块 | 实际字符数 (Chars) | Token 消耗估算 | 备注 |
| :--- | :--- | :--- | :--- |
| **全局守则与查数通用约束** | 2,250 Chars | ~1,250 Tokens | 仅保留基本底线规范 |
| **裁剪后的历史 User 问题** | 100 Chars | ~60 Tokens | 第1轮提问备份 |
| **上一轮结构化数据 JSON** | 1,200 Chars | ~670 Tokens | 前50行数据 |
| **合成可视化约束提示词** | 1,000 Chars | ~550 Tokens | 绘图格式限制与只分析增量规范 |
| **总计 (Second Turn)** | **4,550 字符** | **约 2,530 Tokens** | **轻量化，无多轮历史堆叠压力** |

---

## 第三轮会话：上下文动作管理 (CONTEXT_ACTION)
* **用户输入**：“帮我把这个销售额柱状图和表格数据导出为 Excel 文件。”
* **运行机制**：判定为上下文动作。注入动作引导指南，驱动大模型去调用相应的文件工具，限制其重新检索表或跑 SQL。

### 1. 整体消息结构 (Messages Shape)
1. **SystemMessage #1**：
   * 全局守则 + 通用约束。
   * `context_action_guide` 动作引导指南。
2. **HumanMessage (裁剪历史)**：
   * 第1、2轮 User 问题精简。
3. **HumanMessage (本轮输入合成)**：
   * 导出提问 + 上一轮的 JSON 结果数据（供 Excel 导出工具获取）。

### 2. 真实大小估算
| 提示词模块 | 实际字符数 (Chars) | Token 消耗估算 | 备注 |
| :--- | :--- | :--- | :--- |
| **全局守则与动作引导指南** | 2,750 Chars | ~1,530 Tokens | 包含阻止重新查数的动作要求 |
| **裁剪后的历史 User 问题** | 200 Chars | ~110 Tokens | 前面2轮的提问 |
| **上一轮结构化数据 JSON** | 1,200 Chars | ~670 Tokens | 供 Excel 工具转换的源数据 |
| **本轮导出提问** | 100 Chars | ~60 Tokens | 动作指令 |
| **总计 (Third Turn)** | **4,250 字符** | **约 2,370 Tokens** | **主要开销依然是传递的 JSON 数据体** |

---

## 第四轮会话：换条件新数据查询 (NEW_DATA_QUERY - 多轮)
* **用户输入**：“那冰箱的销售额又是多少呢？”
* **运行机制**：判定为新数据查询。使用 LLM 改写器将本轮提问改写为独立完整的冰箱查询提问。重新规划 Schema 并重新召回冰箱相关 DDL。

### 1. 整体消息结构 (Messages Shape)
1. **SystemMessage #1**（新查数提示词栈）：
   * `[Few-Shot]` 重新向量检索得到的 3 条“冰箱/销售额”相关 SQL 案例。
   * 全局守则 + 通用约束 + 时间锚点。
   * 智能体 Prompt + `{dataset_menu}` 菜单简述。
   * `[Prefetched Schema]` 重新自动检索出的冰箱相关表 DDL。
2. **历史 Messages**：
   * 包含前几轮的 User 提问（已裁剪附件）与 **未裁剪的 Assistant 历史回复**。
3. **HumanMessage**：
   * 改写后的冰箱独立提问。

### 2. 真实大小估算与裁剪分析
> [!CAUTION]
> **代码底层对历史消息（History）的精简漏洞**：
> 走读 `executors/common.py` 中的 `convert_history_to_messages` 可知：
> * **User 消息**：确实被精简了。通过 `_plain_user_text(content)`，过滤剥离了前端用 `---` 拼接的庞大附件绝对路径与系统提示词，只保留了用户的一句话，确实起到了瘦身作用。
> * **Assistant 消息**：**完全没有经过任何裁剪或精简**。系统直接通过 `messages.append(AIMessage(content=content))` 将前几轮大模型回复的全文原封不动地发给 LLM。
> * 
> **这意味着**：在第一轮中，大模型写 SQL 的 Thought 思考过程、`<function_calls>` 结构化工具调用段，以及最终返回给用户的庞大 Markdown 表格、长文以及图表配置数据，在第四轮主 LLM 发起请求时，**依然全量堆叠在 AIMessage 历史里**。若前几轮产生了大量报表，第 4 轮的 Prompt Token 消耗实际上会产生显著膨胀，原先估算的“精简对话”字符数严重偏低。

| 提示词模块 | 实际字符数 (Chars) | Token 消耗估算 | 备注 |
| :--- | :--- | :--- | :--- |
| **全局守则与通用约束** | 2,650 Chars | ~1,470 Tokens | 包含全局守则与时间锚点 |
| **智能体 Prompt + 数据集菜单** | 1,500 Chars | ~830 Tokens | 包含表简述菜单 |
| **冰箱 Few-Shot 案例** | 1,200 Chars | ~670 Tokens | 冰箱 SQL 示例 |
| **冰箱新预检索 Schema DDL** | 3,000 Chars | ~1,670 Tokens | 冰箱相关表 DDL 结构 |
| **裁剪后的历史 User 问题** | 400 Chars | ~220 Tokens | 共3轮，仅含文字问题 |
| **未裁剪的历史 Assistant 回复**| **8,000 Chars** | **~4,440 Tokens** | **第一轮 SQL 过程、第二轮 Markdown 表格、第三轮导出日志等** |
| **改写后的独立冰箱提问** | 150 Chars | ~80 Tokens | 改写后的独立问题 |
| **总计 (Fourth Turn)** | **16,900 字符** | **约 9,380 Tokens** | **因 Assistant 历史未精简，第四轮起始 Token 相比首轮翻倍** |

---

## ⚠️ ReAct 多步迭代时的 Token 放大效应（真实情况剖析）

上述核算代表的是**理想情况（即大模型仅调用 1 次 SQL 且成功返回非空结果）**。但在实际生产中，大模型经常因为 SQL 语法错误、字段缺失或空结果而触发 ReAct（推理-执行-观察）的多轮自愈迭代。

在 ReAct 的循环中，每一次大模型的 Tool Call 和 Tool Result 都会以新消息的形式追加到历史上下文（`messages`）中，发起下一次 LLM 的调用，形成**滚雪球式**的 Token 膨胀。

### 1. 模拟一个典型“SQL 纠错”的多次迭代链路

我们以第一轮查询空调销售额为例，大模型在运行中经历了 1 次表结构补充检索和 1 次 SQL 语法纠错，整个主 LLM 的调用开销会被成倍放大：

#### 第一次主 LLM 调用 (决定初始动作)
* **Prompt 输入**：SystemMessage + 预检索的 A 表 DDL + 用户问题 = **~8,550 字符 (约 4,750 Tokens)**。
* **LLM 响应**：Thought（写 SQL Plan） + 调用 `execute_sql_query(SQL-1)`。

#### 第二次主 LLM 调用 (SQL-1 报错，尝试修复)
* **Prompt 输入**：
  * 初始 Prompt 内容：~8,550 字符
  * 第一次 LLM 输出的 Thought + Tool Call 声明：~400 字符
  * 数据库返回的 SQL 报错日志（如：Unknown column 'sales'）：~1,000 字符
* **输入总大小**：8,550 + 400 + 1,000 = **~9,950 字符 (约 5,530 Tokens)**。
* **LLM 响应**：Thought（意料到字段拼错） + 调用 `get_dataset_schema("空调字段明细")`。

#### 第三次主 LLM 调用 (获取新 DDL 后重新生成 SQL-2)
* **Prompt 输入**：
  * 之前的全部上下文：~9,950 字符
  * 第二次 LLM 输出 of Thought + 检索调用：~300 字符
  * 元数据服务返回的补充 DDL 块：~3,000 字符
* **输入总大小**：9,950 + 300 + 3,000 = **~13,250 字符 (约 7,360 Tokens)**。
* **LLM 响应**：Thought（对齐字段） + 调用 `execute_sql_query(SQL-2)`。

#### 第四次主 LLM 调用 (SQL-2 执行成功，最终回复合成)
* **Prompt 输入**：
  * 之前的全部上下文：~13,250 字符
  * 第三次 LLM 输出的 Thought + SQL-2 调用：~400 字符
  * 数据库成功返回的 JSON 数据（前 15 行）：~1,200 字符
* **输入总大小**：13,250 + 400 + 1,200 = **~14,850 字符 (约 8,250 Tokens)**。
* **LLM 响应**：Thought + 最终 Markdown 回复（包含 ECharts 配置等）= ~800 字符。

### 2. 本轮多步 ReAct 带来的 Token 累加核算

虽然最终大模型给用户的只有一句话加一张柱状图，但在这一轮中，系统向大模型 API 发起了 **4 次请求**，Token 总花费为每次请求的累加值：

$$\text{总 Token 消耗} = \sum (\text{每次输入的 Tokens} + \text{每次输出的 Tokens})$$

* 第一次：输入 4,750 -> 输出 220 (合计: 4,970 Tokens)
* 第二次：输入 5,530 -> 输出 170 (合计: 5,700 Tokens)
* 第三次：输入 7,360 -> 输出 220 (合计: 7,580 Tokens)
* 第四次：输入 8,250 -> 输出 500 (合计: 8,750 Tokens)
* **单轮主 LLM 总消耗：约 27,000 Tokens！**

> [!WARNING]
> **结论**：如果 ReAct 推理过程中出现了错误纠正（多次 `get_dataset_schema` + `execute_sql_query`），主 LLM 的 Token 消费相比于单步理想情况（~11,000 Tokens 左右），**会被直接放大 2.5 倍以上**。

---

## 🛠️ 南孜代码层面的 Token 放大防洪限制

为了防止大模型在出错重试时陷入死循环、或者因为单次返回数据过多导致上下文瞬间暴涨，南孜系统在底层构建了三个关键水闸：

1. **硬性步数封顶 (`DATA_QUERY_MAX_STEPS_CAP`)**：
   在 `data_agent_runner.py` 的 `_resolve_max_steps` 中，系统会去读配置 `agent_max_iterations`，但硬性规定了 `min(raw_max, 6)`。即使大模型想继续纠错，达到 6 步也会被系统强制终止（Emit Final Guard 警告），彻底掐断无休止 of Token 消耗。
2. **工具结果最大行数限制与截断 (`_format_sql_result_for_display`)**：
   SQL 查询工具在返回内容记录进 trace/history 时，强制限制为只展示前 15 行（`_SQL_RESULT_DISPLAY_MAX_ROWS = 15`），并在此阶段通过 `truncate_for_context(output, max_len=1000)` 截断。即使数据库查出 10 万行，塞进 Prompt 的数据细节也会被强行压在 1000 字符内，保障下一次调用的上下文不爆仓。
3. **经验库二次提醒的精简化设计 (`build_few_shot_reminder`)**：
   在 ReAct 循环进行到后续步骤（如已经拿到 Schema DDL，大模型准备写 SQL 前），系统在注入二次提醒时，会调用 `build_few_shot_reminder`（只提取表名和前 3 行 SQL 线索，舍弃了长 SQL 和背景），避免重复塞入庞大的 Few-Shot 块，单次缩减了约 **3,000 字符** 的体积。
