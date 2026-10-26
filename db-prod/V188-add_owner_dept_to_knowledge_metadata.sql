-- V188: 知识库「业务归属部门」改为部门维度
--
-- 背景：`knowledge_base_metadata.owner` 原本是 VARCHAR(100) 自由文本（注释「业务归属/负责人」），
--   实际只用于展示与搜索，既不参与权限判定，也无法按部门维度浏览。
--
-- 本次改动：
--   新增 `owner_dept_id` 指向 `ai_agent_departments.id`，供左侧知识库树做**部门分组**
--   与「包含下级部门」过滤。
--
-- 明确边界（重要）：
--   该列**不是纯展示字段**。在 public / private 下它只用于左侧树分组与搜索；
--   但在 team（部门级）下它是**自动只读权限的判定依据**：归属该部门（含下级部门）
--   的成员据此自动获得只读权（见 PermissionService.resolve_knowledge_dataset_access）。
--   换言之，改归属部门 = 改可见人群。可写判定仍由 created_by / 管理员决定。
--
-- 存量数据：
--   历史 `owner` 文本原样保留（继续展示在详情页），`owner_dept_id` 保持 NULL，
--   在左侧树中归入「未关联部门」分组。不做按名称自动回填——「张三」「数据团队」
--   这类文本与部门名称的匹配极易错配，宁可由人工在编辑弹窗重新选择部门。
--
-- 幂等：apply_sql.py 会跳过 1060（列已存在）/ 1061（索引已存在），可重复执行。

ALTER TABLE `knowledge_base_metadata`
    ADD COLUMN `owner_dept_id` BIGINT NULL COMMENT '业务归属部门 ID（仅浏览过滤，不参与权限）' AFTER `owner`;

ALTER TABLE `knowledge_base_metadata`
    ADD INDEX `idx_kb_owner_dept_id` (`owner_dept_id`);
