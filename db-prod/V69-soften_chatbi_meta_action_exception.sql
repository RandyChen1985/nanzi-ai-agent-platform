-- V69: 为 ChatBI 系统提示词注入“本轮请求分类（K1/K2/K3）”，软化“每轮必须查库”的过严约束
-- Date: 2026-05-30
-- Purpose:
-- 原 ChatBI 提示词只有“追问复用例外”（K2：可视化/分析/总结/画图），未涵盖
-- “基于已有对话/结果做保存、导出、发送、记忆、创建技能”等动作（K3），导致这类本
-- 不需要查数的请求被“禁止直接回答 / 禁止跳过元数据查询”护栏拖入冗余的
-- 查Schema -> 执行SQL 流程。此处在 {dataset_menu} 之后注入“请求分类”说明：
--   K1 新查数（保留强制护栏）/ K2 纯加工复用结果 / K3 对已有上下文做动作（直接调工具或作答）。
-- 配套代码改动：dispatcher 元操作短路 + intent_service.looks_like_context_action
--   + DataQueryExecutor 的 _requires_fresh_data 护栏开关、补全系统隐式工具。
-- 影响: 仅更新内置 chat-bi 智能体各版本；幂等（已注入新版分类块则跳过）。
--
-- 注意：ai_agent_versions.system_prompt 为 utf8mb4；会话/用户变量须显式对齐，否则
-- REPLACE/CONCAT 会触发 Error 3854 (Cannot convert string from utf8mb3 to utf8mb4)。

SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 统一的“本轮请求分类”块（K1/K2/K3）。\n 在 MySQL 字符串字面量中解析为真实换行。
-- 使用 _utf8mb4 字符集前缀，避免与列 utf8mb4 混用时触发 #3854。
SET @nanzi_turn_classify_block = _utf8mb4'> 🧭 **本轮请求分类（先判类，再裁剪流程）**：在执行任何动作前，先判断用户本轮属于以下哪一类：\n> - **K1 新数据查询**：要查询/统计新的业务数据（新指标、新条件、新时间范围、切换数据集等）。→ 严格执行下文标准工作流：`get_dataset_schema` → 构建 SQL → `execute_sql_query` → 汇总，不得跳步。\n> - **K2 对上一轮结果的纯加工**：可视化 / 画图 / 换图形 / 分析 / 总结 / 解读"刚才、上面的结果"，且无新增查询对象或条件。→ 直接复用上一轮结构化结果，**禁止**重新检索 Schema 或执行 SQL。\n> - **K3 对已有上下文的动作**：基于已有对话 / 上一轮结果做"保存或导出结果、发送、记住偏好、把流程沉淀为技能"等管理类动作，本身**不需要查询新数据**。→ **禁止**机械重跑 `get_dataset_schema` / `execute_sql_query`；应直接调用对应工具（如 `create_skills`、写文件、记忆等）完成，或基于上下文直接作答。\n> 仅 **K1** 适用下文"禁止跳过元数据查询 / 禁止直接回答"等强制护栏；**K2、K3 属例外，不受其约束**。';

-- 历史遗留的旧版“元操作例外”单行说明（若此前手动跑过旧版 V69 则存在），整体替换为新版分类块。
SET @nanzi_old_meta_note = _utf8mb4'> ⚙️ **元操作例外 (Meta-Action Exception)**：当用户请求的是基于**已有对话/上一轮结果**的"管理类操作"（例如：创建技能、保存为技能或模板、把流程固定下来、记住某项偏好），且本身**不需要查询新的业务数据**时，禁止机械地重新执行 `get_dataset_schema` / `execute_sql_query`。应直接基于已有上下文完成，或调用对应的管理工具（如 `create_skills`）。这类请求不属于数据查询，**不受**下文"禁止直接回答 / 禁止跳过元数据查询"等约束。';

SET @dataset_menu = _utf8mb4'{dataset_menu}';

-- 1) 已存在旧版“元操作例外”说明：替换为新版 K1/K2/K3 分类块。
UPDATE `ai_agent_versions`
SET `system_prompt` = REPLACE(`system_prompt`, @nanzi_old_meta_note, @nanzi_turn_classify_block)
WHERE `agent_id` = 'sys-agent-chatbi'
  AND `system_prompt` LIKE '%元操作例外%'
  AND `system_prompt` NOT LIKE '%本轮请求分类%';

-- 2) 从未注入过任何说明：在 {dataset_menu} 之后注入新版 K1/K2/K3 分类块。
UPDATE `ai_agent_versions`
SET `system_prompt` = REPLACE(
    `system_prompt`,
    @dataset_menu,
    CONCAT(@dataset_menu, _utf8mb4'\n\n', @nanzi_turn_classify_block)
)
WHERE `agent_id` = 'sys-agent-chatbi'
  AND `system_prompt` LIKE '%{dataset_menu}%'
  AND `system_prompt` NOT LIKE '%本轮请求分类%'
  AND `system_prompt` NOT LIKE '%元操作例外%';
