# ChatBI 门禁（Guards）完整性分析

> 基于 `data_agent_runner.py` + `executors/prompts.py` + `CHAT_BI_DESIGN.md` 整理

---

## 一、整体流程结构（门禁位置速览）

```
用户请求
  │
  ├─ [G0] 轮次分类门禁（DataQueryTurnClassifier）
  │     判断：NEW_DATA_QUERY / REUSE_PREVIOUS_RESULT / CONTEXT_ACTION / SKILL_EXECUTION
  │
  ├─ REUSE_PREVIOUS_RESULT 分支
  │     ├─ [G1] 复用前置检查：无上一轮结果 → 拒绝，返回说明
  │     └─ → 直接合成，不走 Schema/SQL
  │
  └─ NEW_DATA_QUERY / CONTEXT_ACTION / SKILL_EXECUTION 分支
        │
        ├─ [G2] 平台自动预取 Schema（新查数路径）
        │     ├─ schema_service_unavailable → 硬终止
        │     ├─ no_authorized_schema → 硬终止
        │     ├─ rag_not_synced → 硬终止
        │     └─ schema_miss → 标记，进入修复轮
        │
        ├─ ReAct 主循环（agentscope_session_lock 保护并发）
        │     │
        │     ├─ [G3] Schema Gate（工具层拦截器）
        │     │     未取 Schema 就调用 execute_sql_query → 返回 SCHEMA_GATE_PREFIX 错误
        │     │
        │     ├─ [G4] SQL 重复 Gate（工具层拦截器）
        │     │     已成功执行过相同 SQL → 禁止重复，注入缓存结果
        │     │
        │     ├─ [G5] 文本输出阻断（ready_to_answer 守卫）
        │     │     Schema/SQL 条件未满足时，把模型输出缓冲，不下发给用户
        │     │
        │     └─ [G6] SQL Plan 门禁（高风险查询）
        │           含比率/趋势/排名/分组 → 要求先输出 <sql_plan>
        │
        └─ 修复轮（MAX_DATA_REPAIR_ROUNDS=2）
              ├─ [G7] Schema 顺序修复：先取 Schema 再执行 SQL
              ├─ [G8] Schema Miss 修复：换关键词重试
              ├─ [G9] SQL 错误修复：修正 SQL 重试
              ├─ [G10] 空结果修复：诊断 SQL → 修正 SQL
              └─ [G11] SQL Plan 缺失修复：要求先补 Plan 再执行
```

---

## 二、门禁逐条详情

### [G0] 轮次分类门禁

| 属性 | 内容 |
|------|------|
| 实现位置 | `data_query_turn_classifier.py` |
| 触发时机 | 每轮请求最开始 |
| 分类结果 | `NEW_DATA_QUERY` / `REUSE_PREVIOUS_RESULT` / `CONTEXT_ACTION` / `SKILL_EXECUTION` |
| 主判方式 | LLM 结合对话上下文判断 |
| 兜底方式 | LLM 失败时用轻量规则 |
| 硬约束 | 判断为 REUSE 但无历史结果 → 强制切回 NEW_DATA_QUERY |

---

### [G1] 复用前置检查

| 属性 | 内容 |
|------|------|
| 实现位置 | `execute()` ~L469 |
| 触发条件 | `turn_type == REUSE_PREVIOUS_RESULT` |
| 无历史结果时 | 返回错误 log + `NO_REUSABLE_RESULT` 提示，直接 return |
| 有历史结果时 | 跳过 Schema/SQL，进入 `_synthesize_from_last_data_result` |

---

### [G2] Schema 预取硬终止门禁

| 属性 | 内容 |
|------|------|
| 实现位置 | `_auto_invoke_get_dataset_schema()` + `_is_schema_fatal()` |
| 触发条件 | 新查数路径下平台自动调用 `get_dataset_schema` |
| 三种硬终止 | ① `schema_service_unavailable`（RAGFlow 不可用）<br>② `no_authorized_schema`（无授权数据集）<br>③ `rag_not_synced`（已授权但未同步 RAG） |
| 后续动作 | 立即调用 `_yield_schema_fatal_abort`，不进入 ReAct |

---

### [G3] Schema Gate（工具层拦截器）

| 属性 | 内容 |
|------|------|
| 实现位置 | `_wrap_tools_with_schema_gate()` ~L319 |
| 触发条件 | 模型调用 `execute_sql_query` 但 `schema_completed=False` |
| 返回内容 | `SCHEMA_GATE_PREFIX` 错误字符串，SQL 不实际执行 |
| 作用 | 在工具函数层面强制顺序：Schema → SQL |

---

### [G4] SQL 重复 Gate（工具层拦截器）

| 属性 | 内容 |
|------|------|
| 实现位置 | `_wrap_tools_with_schema_gate()` ~L344 |
| 触发条件 | 模型在同一轮 ReAct 中提交了已成功执行过的相同 SQL |
| 返回内容 | `SQL_REPEAT_GATE_PREFIX` + 缓存结果注入 |
| 作用 | 防止重复查询、Token 浪费和幻觉循环 |

---

### [G5] 文本输出阻断（ready_to_answer 守卫）

| 属性 | 内容 |
|------|------|
| 实现位置 | `on_text_block_delta()` ~L1482，`_emit_final_guard()` ~L1640 |
| 触发条件 | `state.ready_to_answer == False` 时模型输出文字 |
| 实现方式 | 把 delta 存入 `blocked_content`，不 yield 给用户 |
| 最终兜底 | 整个 ReAct 结束后 `_emit_final_guard` 输出拦截提示 |
| 覆盖场景 | Schema 未完成 / SQL 未执行 / SQL 失败 / 空结果 / SQL Plan 缺失 |

---

### [G6] SQL Plan 门禁（高风险查询）

| 属性 | 内容 |
|------|------|
| 实现位置 | `_should_require_sql_plan()` ~L1809，`on_text_block_delta` 内检测 |
| 触发条件 | 问题包含：率/占比/同比/环比/趋势/Top/排名/JOIN/分组 等关键词 |
| 要求 | 执行 SQL 前须在 `<thought>` 中输出 `<sql_plan>{...}</sql_plan>` |
| 若缺失 | `sql_plan_missing=True` → 进入修复轮 G11 |

---

### [G7] Schema 顺序修复

| 属性 | 内容 |
|------|------|
| 实现位置 | `_build_repair_message()` ~L1673 |
| 触发条件 | `sql_before_schema=True`（SQL 在 Schema 之前被触发） |
| 修复动作 | 注入强约束提示 + `ToolChoice(mode="get_dataset_schema")` 强制首选 |

---

### [G8] Schema Miss 修复

| 属性 | 内容 |
|------|------|
| 实现位置 | `_build_repair_message()` ~L1680 |
| 触发条件 | `schema_miss=True` 且非无授权 |
| 修复动作 | 要求换更宽泛关键词重试 + `ToolChoice(mode="get_dataset_schema")` |

---

### [G9] SQL 错误修复

| 属性 | 内容 |
|------|------|
| 实现位置 | `_build_repair_message()` ~L1695 |
| 触发条件 | `sql_error=True` |
| 修复动作 | 注入错误信息 + `ToolChoice(mode="required")` 强制调用工具 |
| 致命错误 | `sql_fatal_error=True`（权限拒绝/表不存在）→ 直接终止，不进修复轮 |

---

### [G10] 空结果修复

| 属性 | 内容 |
|------|------|
| 实现位置 | `_build_repair_message()` ~L1702 |
| 触发条件 | `empty_sql_result=True` |
| 修复动作 | 要求先用诊断 SQL 复查筛选条件/JOIN/CTE |
| 诊断 SQL 识别 | `_is_diagnostic_sql()` 识别 SHOW/DISTINCT LIMIT/COUNT without GROUP/LIMIT<=10 |

---

### [G11] SQL Plan 缺失修复

| 属性 | 内容 |
|------|------|
| 实现位置 | `_build_repair_message()` ~L1687 |
| 触发条件 | `sql_plan_missing=True`（高风险查询未提供计划） |
| 修复动作 | 要求补充 `<sql_plan>` 再执行 SQL，tool_choice=None（让模型自由输出计划） |

---

## 三、门禁完整性评估

### ✅ 已覆盖的场景

| 场景 | 门禁 |
|------|------|
| 未取 Schema 就执行 SQL | G3 (工具层) + G5 (输出层) + G7 (修复层) |
| Schema 服务不可用 | G2 硬终止 |
| 用户无数据集权限 | G2 硬终止 |
| 元数据未同步 RAG | G2 硬终止 |
| Schema 未命中，换词重试 | G8 修复轮 |
| SQL 语法/执行错误 | G9 修复轮 |
| SQL 返回空结果 | G10 修复轮（诊断 SQL 流程）|
| 复用追问但无历史结果 | G1 前置检查 |
| 高风险查询缺 SQL Plan | G6 + G11 |
| 重复 SQL 执行 | G4 缓存复用 |
| 并发会话冲突 | `agentscope_session_lock` |
| 致命权限/表不存在 | `sql_fatal_error` 立即终止 |

---

### ⚠️ 潜在缺口 / 值得讨论的点

| # | 潜在问题 | 当前处理 | 建议 |
|---|----------|----------|------|
| 1 | **Schema 多次命中但质量差**（返回了 Schema 但完全无关） | 无相关质量检查，仅判断"是否命中" | 可考虑在 schema_miss 基础上增加"schema_relevance_score" |
| 2 | **修复轮上限（MAX=2）后仍无有效结果** | `_emit_final_guard` 输出兜底错误提示 | 目前只输出通用提示，未区分最终失败原因给用户看 |
| 3 | **空结果诊断 SQL 后，模型可能仍不发起最终 SQL** | 只有 1 次空结果修复机会 | 若第二轮修复仍为空，没有专门处理路径，直接走 final guard |
| 4 | **CONTEXT_ACTION 放宽护栏后的 SQL 滥用** | CONTEXT_ACTION 放宽了"必须查数"护栏 | 若模型误判 CONTEXT_ACTION，护栏完全不生效 |
| 5 | **多 SQL 并发（同一轮调用多次 execute_sql_query）** | G4 通过 SQL 去重拦截，但依赖 SQL 字符串完全一致 | SQL 文本不同但语义相同的重复查询仍可通过 |
| 6 | **SQL Plan 检测纯靠关键词** | `_should_require_sql_plan` 仅关键词匹配 | 有可能遗漏非中文表达的高风险查询（如纯英文 `ratio` 拼写变体） |

---

## 四、结论

**门禁覆盖程度：高（主流程完整）**

- **核心查数流程**（Schema → SQL → 汇总）的顺序约束：✅ 三层防护（工具层 G3、输出层 G5、修复层 G7）
- **失败恢复**（Schema Miss / SQL Error / Empty Result）：✅ 修复轮覆盖 3 种主要失败
- **硬终止边界**（服务不可用、无权限、致命 SQL 错误）：✅ 立即终止
- **追问分支**（复用 / 上下文动作 / 技能执行）：✅ 基本覆盖

潜在缺口集中在**边缘场景**（Schema 质量、多轮空结果、CONTEXT_ACTION 误判），不影响主流程健壮性，可以按优先级评估是否补充。
